#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-only
"""Offline reference harness. Apple machine code runs only inside Unicorn.

Kernel allocation and Accelerate primitives are replaced by host operations;
the DSP algorithms and their state remain the original executable code.
"""
from pathlib import Path
import ctypes as ct
import hashlib
import json
import os
import re
import struct
import sys

ROOT=Path(__file__).resolve().parents[2]
SOURCE=Path(os.environ.get('APPLE_INPUT_DIR',ROOT/'data/apple'))
ANALYSIS=Path(os.environ.get('APPLE_ANALYSIS_DIR',ROOT/'.private/analysis'))
EXPECTED_SHA256='07abb2dbf7968fd6f46e0242edb00d752503b92815474a163a7603d7066863de'
sys.path.insert(0,str(SOURCE/'tools/python'))
import numpy as np
from unicorn import Uc, UC_ARCH_X86, UC_MODE_64, UC_HOOK_CODE
from unicorn.x86_const import *

class Reference:
    def __init__(self):
        self.binary=(SOURCE/'DspFuncLib').read_bytes()
        if hashlib.sha256(self.binary).hexdigest()!=EXPECTED_SHA256:
            raise ValueError('Unsupported DspFuncLib binary: this harness uses version-specific addresses.')
        self.uc=Uc(UC_ARCH_X86,UC_MODE_64)
        for address,size in [(0,0x200000),(0x1000000,0x8000000),(0x10000000,0x200000),(0x20000000,0x100000)]:
            self.uc.mem_map(address,size)
        self.uc.mem_write(0,self.binary)
        self.uc.reg_write(UC_X86_REG_MXCSR,0x1f80)
        self.heap=0x1000000
        self.stop=0x200ffff0
        self.stubs={}
        self.symbols={}
        self.calls={}
        self.biquads={}
        self.libm=ct.CDLL('libm.so.6')
        for name in ['expf','log10f','logf','sqrtf','cosf','sinf']:
            f=getattr(self.libm,name);f.argtypes=[ct.c_float];f.restype=ct.c_float
        self.libm.log.argtypes=[ct.c_double];self.libm.log.restype=ct.c_double
        relocs=(ANALYSIS/'apple-compressor-relocations.txt').read_text().splitlines()
        for line in relocs:
            fields=line.split()
            if len(fields)<7 or not re.fullmatch('[0-9a-f]{8}',fields[0]):continue
            addr=int(fields[0],16)
            if fields[3]!='True':continue
            name=fields[-1]
            if name not in self.symbols:
                stub=0x20000000+16*len(self.stubs)
                self.stubs[stub]=name;self.symbols[name]=stub
            stub=self.symbols[name]
            if fields[4]=='BRANCH':self.uc.mem_write(addr,struct.pack('<i',stub-(addr+4)))
            elif fields[4]=='UNSIGND' and fields[1]=='False':self.uc.mem_write(addr,struct.pack('<Q',stub))
        self.uc.hook_add(UC_HOOK_CODE,self.hook,begin=0x20000000,end=0x200ffff0)
        for name in ['sin','cos','log','log10','sqrt','pow','fabs']:
            f=getattr(self.libm,name);f.argtypes=[ct.c_double]*(2 if name=='pow' else 1);f.restype=ct.c_double
        for address,name in {0x19b5f:'sin',0x11dcf:'cos',0x1ddf5:'log',0x1de6f:'log10',
                             0x218d9:'sqrt',0x1dee9:'pow',0x12aaa:'fabs'}.items():
            self.uc.hook_add(UC_HOOK_CODE,self.math_hook,user_data=name,begin=address,end=address)
        self.uc.hook_add(UC_HOOK_CODE,self.manager_rate,begin=0x26a5a,end=0x26a5a)

    def math_hook(self,uc,address,size,name):
        def value(index):return struct.unpack('<d',self.get('XMM'+str(index)).to_bytes(16,'little')[:8])[0]
        args=[value(0),value(1)] if name=='pow' else [value(0)]
        self.result(getattr(self.libm,name)(*args),'<d');self.ret()

    def manager_rate(self,uc,address,size,user):self.set('RAX',48000);self.ret()

    def alloc(self,size):
        size=max(16,int(size));a=self.heap;self.heap+=(size+63)&~63
        assert self.heap<0x9000000
        return a

    def get(self,name):return self.uc.reg_read(globals()['UC_X86_REG_'+name])
    def set(self,name,value):self.uc.reg_write(globals()['UC_X86_REG_'+name],value)
    def read(self,address,fmt):return struct.unpack(fmt,self.uc.mem_read(address,struct.calcsize(fmt)))[0]
    def write(self,address,fmt,value):self.uc.mem_write(address,struct.pack(fmt,value))
    def scalar(self,index=0):return struct.unpack('<f',self.get('XMM'+str(index)).to_bytes(16,'little')[:4])[0]
    def result(self,value,fmt='<f'):self.set('XMM0',int.from_bytes(struct.pack(fmt,value),'little'))
    def vector(self,address,stride,count):
        if not count:return np.empty(0,dtype=np.float32)
        if stride>0x7fffffffffffffff:stride-=1<<64
        if stride<0:return np.array([self.read(address+4*stride*i,'<f') for i in range(count)],dtype=np.float32)
        return np.frombuffer(self.uc.mem_read(address,4*((count-1)*stride+1)),dtype=np.float32)[::stride].copy()
    def put_vector(self,address,stride,values):
        values=np.asarray(values,dtype=np.float32)
        if not len(values):return
        if stride==1:self.uc.mem_write(address,values.tobytes());return
        a=np.frombuffer(self.uc.mem_read(address,4*((len(values)-1)*stride+1)),dtype=np.float32).copy()
        a[::stride]=values;self.uc.mem_write(address,a.tobytes())
    def ret(self):
        rsp=self.get('RSP');self.set('RIP',self.read(rsp,'<Q'));self.set('RSP',rsp+8)
    def arg(self,index):
        return self.get(['RDI','RSI','RDX','RCX','R8','R9'][index]) if index<6 else self.read(self.get('RSP')+8*(index-5),'<Q')

    def hook(self,uc,address,size,user):
        if address==self.stop:return
        name=self.stubs.get(address)
        if name is None:raise RuntimeError('Unknown stub '+hex(address))
        self.calls[name]=self.calls.get(name,0)+1
        a=[self.arg(i) for i in range(8)]
        if name in ['_IOMalloc','_IOMallocAligned','__Znwm','__Znam']:
            self.set('RAX',self.alloc(a[0]))
        elif name in ['_IOFree','_IOFreeAligned','__ZdlPv','__ZdaPv','_IOLockLock','_IOLockUnlock']:
            pass
        elif name in ['___bzero','_bzero']:
            self.uc.mem_write(a[0],b'\0'*a[1])
        elif name in ['_memcpy','_memmove','___memcpy_chk']:
            self.uc.mem_write(a[0],bytes(self.uc.mem_read(a[1],a[2])));self.set('RAX',a[0])
        elif name=='_memset':
            self.uc.mem_write(a[0],bytes([a[1]&255])*a[2]);self.set('RAX',a[0])
        elif name in ['_expf','_log10f','_logf','_sqrtf','_cosf','_sinf']:
            self.result(getattr(self.libm,name[1:])(self.scalar()))
        elif name=='_log':
            value=struct.unpack('<d',self.get('XMM0').to_bytes(16,'little')[:8])[0]
            self.result(self.libm.log(value),'<d')
        elif name in ['_vDSP_vsmul','_vDSP_vsadd','_vDSP_svdiv']:
            count=a[5];scalar=self.read(a[2] if name!='_vDSP_svdiv' else a[0],'<f')
            v=self.vector(a[0],a[1],count) if name!='_vDSP_svdiv' else self.vector(a[1],a[2],count)
            result=v*scalar if name=='_vDSP_vsmul' else v+scalar if name=='_vDSP_vsadd' else scalar/v
            self.put_vector(a[3],a[4],result)
        elif name in ['_vDSP_vmul','_vDSP_vadd','_vDSP_vsub','_vDSP_vdiv','_vDSP_vmax','_vDSP_vmaxmg']:
            left=self.vector(a[0],a[1],a[6]);right=self.vector(a[2],a[3],a[6])
            if name=='_vDSP_vmul':r=left*right
            elif name=='_vDSP_vadd':r=left+right
            elif name=='_vDSP_vsub':r=right-left
            elif name=='_vDSP_vdiv':r=right/left
            elif name=='_vDSP_vmax':r=np.maximum(left,right)
            else:r=np.maximum(np.abs(left),np.abs(right))
            self.put_vector(a[4],a[5],r)
        elif name=='_vDSP_vfill':self.put_vector(a[1],a[2],np.full(a[3],self.read(a[0],'<f'),dtype=np.float32))
        elif name=='_vDSP_vclip':self.put_vector(a[4],a[5],np.clip(self.vector(a[0],a[1],a[6]),self.read(a[2],'<f'),self.read(a[3],'<f')))
        elif name in ['_vDSP_vabs']:
            self.put_vector(a[2],a[3],np.abs(self.vector(a[0],a[1],a[4])))
        elif name=='_vvexpf':
            n=self.read(a[2],'<i');v=self.vector(a[1],1,n)
            self.put_vector(a[0],1,[self.libm.expf(float(x)) for x in v])
        elif name=='_vDSP_conv':
            src=self.vector(a[0],a[1],a[6]+a[7]-1);kernel=self.vector(a[2],a[3],a[7])
            self.put_vector(a[4],a[5],np.correlate(src,kernel,'valid'))
        elif name=='_vDSP_biquadm_CreateSetup':
            handle=self.alloc(16)
            coeffs=np.frombuffer(self.uc.mem_read(a[0],8*5*a[1]*a[2]),dtype=np.float64).copy()
            self.biquads[handle]={'coeffs':coeffs.reshape(a[1],a[2],5),'state':np.zeros((a[1],a[2],2))};self.set('RAX',handle)
        elif name=='_vDSP_biquadm_ResetState':self.biquads[a[0]]['state'][:]=0
        elif name=='_vDSP_biquadm_DestroySetup':pass
        elif name=='_vDSP_biquadm_CopyState':
            self.biquads[a[0]]['state'][:]=self.biquads[a[1]]['state']
        elif name=='_vDSP_biquadm':
            obj=self.biquads[a[0]]
            for channel in range(obj['coeffs'].shape[1]):
                src=self.read(a[1]+channel*8,'<Q');dst=self.read(a[3]+channel*8,'<Q')
                v=self.vector(src,a[2],a[5])
                for section in range(obj['coeffs'].shape[0]):
                    b0,b1,b2,c1,c2=obj['coeffs'][section,channel]
                    z1,z2=obj['state'][section,channel]
                    result=[]
                    for x in v:
                        y=b0*float(x)+z1;z1=b1*float(x)-c1*y+z2;z2=b2*float(x)-c2*y
                        result.append(y)
                    v=np.asarray(result,dtype=np.float32);obj['state'][section,channel]=[z1,z2]
                self.put_vector(dst,a[4],v)
        elif name in ['_printf','_IOLog']:
            message=bytes(self.uc.mem_read(a[0],220)).split(b'\0')[0]
            raise RuntimeError('Original DSP assertion: '+str(message)+' args '+str(a[1:5]))
        else:raise RuntimeError('Unhandled import '+name+' args '+str(a))
        self.ret()

    def call(self,address,rdi=0,until=None,**registers):
        self.write(0x100ffff8,'<Q',self.stop)
        self.set('RSP',0x100ffff8);self.set('RDI',rdi)
        for name,value in registers.items():self.set(name,value)
        self.uc.emu_start(address,until or self.stop,count=30000000)
        assert self.get('RIP')==(until or self.stop),hex(self.get('RIP'))
        return self.get('RAX')

    def compressor(self,rate=48000,frames=4096):
        obj=self.alloc(0x540);self.call(0x83256,obj,RSI=frames,RDX=2)
        wrapper=self.alloc(0x100);self.write(wrapper+0xd0,'<Q',obj)
        asm=(ANALYSIS/'apple-compressor-setters.asm').read_text()
        entries={int(key):int(pc,16) for pc,key in re.findall(r'^([0-9a-f]+) <DspFuncMozartCompressor::setParam(\d+)\(DspValue\)>:',asm,re.M)}
        raw=json.loads((SOURCE/'original-layout59/selected-eq.json').read_text())['variant']['functions'][1]['raw_parameters']
        for key in sorted(entries):
            if str(key) in raw:self.call(entries[key],wrapper,RSI=raw[str(key)]&0xffffffff)
        self.call(0x8402a,obj,RSI=rate)
        return obj

    def dualband(self,rate=48000):
        obj=self.alloc(0x900);self.call(0x864ee,obj,RSI=2)
        wrapper=self.alloc(0x100);self.write(wrapper+0xd0,'<Q',obj)
        asm=(ANALYSIS/'apple-dualband-wrapper.asm').read_text()
        entries={int(key):int(pc,16) for pc,key in re.findall(r'^([0-9a-f]+) <DspFuncMozartCompressorDualBand::setParam(\d+)\(DspValue\)>:',asm,re.M)}
        raw=json.loads((SOURCE/'original-layout59/selected-eq.json').read_text())['variant']['functions'][3]['raw_parameters']
        for key in sorted(entries):
            if str(key) in raw:self.call(entries[key],wrapper,RSI=raw[str(key)]&0xffffffff)
        self.call(0x874a8,obj,RSI=rate);self.call(0x874f0,obj)
        return obj

    def process_dualband(self,obj,samples):
        samples=np.ascontiguousarray(samples,dtype=np.float32)
        src=self.alloc(samples.nbytes);dst=self.alloc(samples.nbytes)
        self.uc.mem_write(src,samples.tobytes())
        self.call(0x649c0,obj,RSI=src,RDX=dst,RCX=len(samples))
        return self.vector(dst,1,samples.size).reshape(samples.shape)

    def loudness(self,volume=1.0):
        obj=self.alloc(0x240)
        self.write(obj,'<Q',0xf1668);self.write(obj+0x10,'<Q',1)
        for off,val in {0x104:700,0x108:1400,0x10c:.4,0x110:.4,0x114:-7,
                        0x194:-11,0x198:-40,0x19c:-12,0xb0:volume,0xb4:volume,0xb8:volume}.items():
            self.write(obj+off,'<f',val)
        self.call(0x4a880,obj)
        return obj

    def process(self,obj,samples):
        samples=np.ascontiguousarray(samples,dtype=np.float32)
        src=self.alloc(samples.nbytes);dst=self.alloc(samples.nbytes)
        self.uc.mem_write(src,samples.tobytes())
        self.call(0x854c6,obj,RSI=src,RDX=dst,RCX=len(samples))
        return self.vector(dst,1,samples.size).reshape(samples.shape)

if __name__=='__main__':
    ref=Reference();obj=ref.alloc(0x540)
    ref.call(0x83256,obj,RSI=4096,RDX=2,until=0x83932)
    defaults=list(struct.unpack('<53f',ref.uc.mem_read(obj+0x1e0,53*4)))
    print('Defaults:',list(enumerate(defaults)))
    obj=ref.compressor()
    print('Selected params',list(enumerate(struct.unpack('<53f',ref.uc.mem_read(obj+0x2b8,212)))))
    y=ref.process(obj,np.zeros((512,2),dtype=np.float32))
    print('Process silence succeeded',float(np.max(np.abs(y))),'imports',ref.calls)
