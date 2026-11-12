#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-only
"""Compare the native limiter against Apple's original setters and DSP loop."""
import ctypes as ct
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import time

REPO=Path(__file__).resolve().parents[2]
SOURCE=Path(os.environ.get('APPLE_INPUT_DIR',REPO/'data/apple'))
ROOT=REPO/'build/reports';ROOT.mkdir(parents=True,exist_ok=True)
PLUGIN=Path(os.environ.get('MACBOOK_PLUGIN',REPO/'build/macbook-apple-stack.lv2/macbook-apple-stack.so'))
sys.path.insert(0,str(SOURCE/'tools/python'))
import numpy as np
from unicorn import Uc, UC_ARCH_X86, UC_MODE_64, UC_HOOK_CODE
from unicorn.x86_const import *

binary=(SOURCE/'DspFuncLib').read_bytes()
if hashlib.sha256(binary).hexdigest()!='07abb2dbf7968fd6f46e0242edb00d752503b92815474a163a7603d7066863de':
    raise ValueError('Unsupported DspFuncLib binary.')
base,data,stack,stop=0x1000000,0x2000000,0x3000000,0x4000000
uc=Uc(UC_ARCH_X86,UC_MODE_64)
for address,length in [(base,0x200000),(data,0x400000),(stack,0x10000),(stop,0x1000)]:uc.mem_map(address,length)
uc.mem_write(base,binary)
wrapper,library,vtable,inp,out=data,data+0xc0,data+0x2000,data+0x10000,data+0x200000
libm=ct.CDLL('libm.so.6')
libm.expf.argtypes=[ct.c_float];libm.expf.restype=ct.c_float
native=ct.CDLL(str(PLUGIN))
FloatP=ct.POINTER(ct.c_float)
native.macbook_spline_coefficients.argtypes=[FloatP,ct.c_ulong,*([ct.c_float]*6)]
native.macbook_spline_process.argtypes=[FloatP,FloatP,FloatP,FloatP,ct.c_ulong]
coeff_offsets=[0x54,0x58,0x5c,0x60,0x6c,0x74,0x78,0x7c,0x80,0x84,0x88,0x70]
setters=[0x6bc88,0x6bcc4,0x6bcf2,0x6bd20,0x6bd42,0x6bd64]
branches=[0x6b92c+struct.unpack_from('<i',binary,0x6b92c+4*i)[0] for i in range(6)]
assert [p+7+struct.unpack_from('<i',binary,p+3)[0] for p in branches]==setters

def ret():
    rsp=uc.reg_read(UC_X86_REG_RSP)
    uc.reg_write(UC_X86_REG_RIP,struct.unpack('<Q',uc.mem_read(rsp,8))[0])
    uc.reg_write(UC_X86_REG_RSP,rsp+8)

def hook(machine,address,size,user):
    pc=address-base
    if pc in [0x693e8,0x6942b,0x69451,0x6947c,0x6bcb0]:
        value=struct.unpack('<f',uc.reg_read(UC_X86_REG_XMM0).to_bytes(16,'little')[:4])[0]
        result=libm.expf(value)
        uc.reg_write(UC_X86_REG_XMM0,int.from_bytes(struct.pack('<f',result),'little'))
        uc.reg_write(UC_X86_REG_RIP,address+5)
    elif pc==0x12aaa:
        value=struct.unpack('<d',uc.reg_read(UC_X86_REG_XMM0).to_bytes(16,'little')[:8])[0]
        uc.reg_write(UC_X86_REG_XMM0,int.from_bytes(struct.pack('<d',abs(value)),'little'))
        ret()
    elif pc==0x64d96:
        # Channel bookkeeping does not participate in the fixed stereo DSP loop.
        uc.reg_write(UC_X86_REG_RAX,0);ret()
    elif pc==0x68fb2:
        uc.reg_write(UC_X86_REG_RAX,1<<51)
        uc.reg_write(UC_X86_REG_RIP,address+5)

for pc in [0x693e8,0x6942b,0x69451,0x6947c,0x6bcb0,0x12aaa,0x64d96,0x68fb2]:
    uc.hook_add(UC_HOOK_CODE,hook,begin=base+pc,end=base+pc)

def call(pc,rdi=library,**registers):
    uc.mem_write(stack+0xfff8,struct.pack('<Q',stop))
    uc.reg_write(UC_X86_REG_RSP,stack+0xfff8)
    uc.reg_write(UC_X86_REG_RDI,rdi)
    for name,value in registers.items():uc.reg_write(globals()['UC_X86_REG_'+name],value)
    uc.emu_start(base+pc,stop,count=30000000)
    assert uc.reg_read(UC_X86_REG_RIP)==stop,hex(uc.reg_read(UC_X86_REG_RIP))

doc=json.loads((SOURCE/'original-layout59/selected-eq.json').read_text())
raw=doc['variant']['functions'][11]['raw_parameters']
assert raw==doc['variant']['functions'][12]['raw_parameters']
parameters=[struct.unpack('<f',struct.pack('<I',raw[str(i)]&0xffffffff))[0] for i in range(2,8)]
assert raw['1']==0

def setup(rate,params,optimized=True):
    uc.mem_write(wrapper,b'\0'*0x1000)
    call(0x68f18)
    uc.mem_write(library,struct.pack('<Q',vtable))
    uc.mem_write(vtable+0x78,struct.pack('<Q',base+0x69538))
    for pc,value in zip(setters,params):
        bits=struct.unpack('<I',struct.pack('<f',value))[0]
        call(pc,rdi=wrapper,RSI=bits)
    call(0x6939a,RSI=rate)
    uc.mem_write(library+0x90,bytes([int(optimized)]))
    original=np.array([struct.unpack('<f',uc.mem_read(library+p,4))[0] for p in coeff_offsets],dtype=np.float32)
    coefficients=np.empty(12,dtype=np.float32)
    native.macbook_spline_coefficients(coefficients.ctypes.data_as(FloatP),rate,*params)
    assert np.array_equal(original,coefficients),(original,coefficients)
    return coefficients

def reference(samples):
    samples=np.ascontiguousarray(samples,dtype=np.float32)
    uc.mem_write(inp,samples.tobytes())
    uc.mem_write(out,b'\0'*samples.nbytes)
    call(0x68fd2,RSI=inp,RDX=out,RCX=len(samples))
    return np.frombuffer(uc.mem_read(out,samples.nbytes),dtype=np.float32).copy().reshape(-1,2)

def reconstructed(coefficients,states,samples,in_place=False):
    samples=np.ascontiguousarray(samples,dtype=np.float32)
    output=samples.copy() if in_place else np.empty_like(samples)
    native.macbook_spline_process(coefficients.ctypes.data_as(FloatP),states.ctypes.data_as(FloatP),
        (output if in_place else samples).ctypes.data_as(FloatP),output.ctypes.data_as(FloatP),len(samples))
    return output

def chunks(n):
    offset=0;i=0
    sizes=[0,1,7,13,64,127,256,1024,4096]
    while offset<n:
        count=min(sizes[i%len(sizes)],n-offset)
        yield offset,count
        offset+=count;i+=1

report={'binary_sha256':hashlib.sha256(binary).hexdigest(),
        'plugin_sha256':hashlib.sha256(PLUGIN.read_bytes()).hexdigest(),
        'method':'Unicorn execution of original Apple parameter setters, coefficient calculation and stereo process; host libm expf/fabs math hooks; native reconstructed C plugin compared sample by sample',
        'apple_instances':[11,12],'raw_parameters':parameters,'cases':[]}
start=time.monotonic()
rng=np.random.default_rng(20261112)
n=24000;t=np.arange(n)/48000
cases={
    'silence':np.zeros((2048,2),dtype=np.float32),
    'quiet_stereo':np.column_stack((.05*np.sin(2*np.pi*300*t),.03*np.sin(2*np.pi*6000*t))),
    'asymmetric_loud_left':np.column_stack((5*np.sin(2*np.pi*1000*t),.04*np.sin(2*np.pi*450*t))),
    'sustained_step':np.column_stack((np.r_[np.zeros(1000),np.full(9000,2),np.full(9000,.2),np.zeros(5000)],
                                      np.r_[np.zeros(1000),np.full(9000,-3),np.full(9000,-.1),np.zeros(5000)])),
    'random_hot':rng.normal(0,2,(32768,2)),
    'extreme_transients':np.zeros((4096,2),dtype=np.float32),
}
cases['extreme_transients'][[0,1,12,128,512,1024,2048]]=[[1,-1],[-10,10],[1e6,-1e6],[-1000,1000],[1e-20,-1e-20],[3,-.03],[-5,5]]
maximum_peak=0.
for rate in [44100,48000,96000]:
    for name,samples in cases.items():
        c=setup(rate,parameters)
        state=np.ones(2,dtype=np.float32)
        maximum_error=0.;peak=0.
        for offset,count in chunks(len(samples)):
            block=np.ascontiguousarray(samples[offset:offset+count],dtype=np.float32)
            expected=reference(block)
            actual=reconstructed(c,state,block)
            if count:
                maximum_error=max(maximum_error,float(np.max(abs(actual-expected))))
                peak=max(peak,float(np.max(abs(actual))))
            assert np.array_equal(actual,expected),(rate,name,offset,maximum_error)
        assert peak<=float(c[11])+2e-7,(name,peak,c[11])
        maximum_peak=max(peak,maximum_peak)
        report['cases'].append({'rate':rate,'signal':name,'frames':len(samples),'maximum_absolute_error':maximum_error,'peak':peak})
        print('verified',rate,name,len(samples),'error',maximum_error,flush=True)

# Exercise the alternate original implementation and state/reset behavior.
samples=np.ascontiguousarray(cases['random_hot'][:8192],dtype=np.float32)
c=setup(48000,parameters,optimized=False)
legacy=reference(samples)
actual=reconstructed(c,np.ones(2,dtype=np.float32),samples)
legacy_error=float(np.max(abs(legacy-actual)))
assert legacy_error<2e-5,legacy_error
report['legacy_maximum_absolute_difference']=legacy_error

for params in [[-6,1.5,5.5,0,8,.1],[-12,7,3,0,12,.5],[-1,2,6,0,6,1]]:
    c=setup(48000,params)
    expected=reference(samples)
    actual=reconstructed(c,np.ones(2,dtype=np.float32),samples,in_place=True)
    assert np.array_equal(actual,expected),float(np.max(abs(actual-expected)))
    report['cases'].append({'parameters':params,'signal':'random_hot_in_place','frames':len(samples),'maximum_absolute_error':0})

# A channel's state must be independent of the other channel.
c=setup(48000,parameters)
a=np.column_stack((samples[:,0],np.zeros(len(samples),dtype=np.float32)))
b=np.column_stack((samples[:,0],samples[:,1]*100))
aa=reconstructed(c,np.ones(2,dtype=np.float32),a)
bb=reconstructed(c,np.ones(2,dtype=np.float32),b)
assert np.array_equal(aa[:,0],bb[:,0])
report.update({'independent_channels_verified':True,'maximum_peak':maximum_peak,
               'coefficients_at_48000':setup(48000,parameters).tolist(),
               'elapsed_seconds':time.monotonic()-start,'passed':True,
               'scope':'Exact arithmetic match to the optimized Apple stereo limiter for the cases tested, using host math functions; this check covers the limiter only, not other DSP stages or acoustic response.'})
(ROOT/'limiter-validation.json').write_text(json.dumps(report,indent=2)+'\n')
print('PASS',len(report['cases']),'cases; legacy difference',legacy_error,'seconds',report['elapsed_seconds'])
