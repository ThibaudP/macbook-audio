#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-only
"""Whole graph comparison and the complete LV2 control/port interface."""
import ctypes as ct
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import time

REPO=Path(__file__).resolve().parents[2]
ROOT=REPO/'build/reports';ROOT.mkdir(parents=True,exist_ok=True)
ASSETS=Path(os.environ.get('MACBOOK_DATA_DIR',REPO/'data'))
os.environ.pop('XDG_RUNTIME_DIR',None)  # Offline checks must not touch live meters.
spec=importlib.util.spec_from_file_location('reference',Path(__file__).with_name('apple-reference.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
np=module.np;P=ct.POINTER(ct.c_float)
PLUGIN=Path(os.environ.get('MACBOOK_PLUGIN',REPO/'build/macbook-apple-stack.lv2/macbook-apple-stack.so'));native=ct.CDLL(str(PLUGIN))
native.apple_stack_create.restype=ct.c_void_p
native.apple_stack_process.argtypes=[ct.c_void_p,P,P,ct.c_ulong,ct.c_float,ct.c_float,ct.c_int,ct.c_int]
native.apple_stack_mask.argtypes=[ct.c_void_p,ct.c_uint,ct.c_int]
native.apple_stack_free.argtypes=[ct.c_void_p]
native.macbook_spline_coefficients.argtypes=[P,ct.c_ulong,*([ct.c_float]*6)]
native.macbook_spline_process.argtypes=[P,P,P,P,ct.c_ulong]
def ptr(a):return a.ctypes.data_as(P)

def original_common(x,volume):
    corrected=np.empty_like(x);nfft=1<<(len(x)+16384-1).bit_length()
    for ch in range(2):
        impulse=np.frombuffer((ASSETS/f'common-{ch}.wav').read_bytes()[44:],dtype='<f4')
        corrected[:,ch]=np.fft.irfft(np.fft.rfft(x[:,ch],nfft)*np.fft.rfft(impulse,nfft),nfft)[:len(x)]
    x=corrected
    r=module.Reference();comp=r.compressor();r.call(0x83e04,comp)
    dual=r.dualband();loud=r.loudness(volume)
    coefs=bytes(r.uc.mem_read(loud+0x140,40));r.uc.mem_write(loud+0x118,coefs)
    retention=np.float32(r.read(loud+0xa4,'<f'));weight=np.float32(1-retention)
    vol=np.zeros(2,dtype=np.float32);result=[]
    for offset in range(0,len(x),512):
        chunk=r.process(comp,x[offset:offset+512]);buf=r.alloc(chunk.nbytes);r.uc.mem_write(buf,chunk.tobytes())
        rbp=0x100ff000;r.write(rbp-0x2c,'<f',float(weight))
        r.call(0x4a0fa,loud,until=0x4a435,RBX=loud,RBP=rbp,R12=buf,R15=len(chunk))
        chunk=r.vector(buf,1,chunk.size).reshape(chunk.shape)
        for i in range(len(chunk)):
            for ch in range(2):
                vol[ch]=np.float32(np.float32(retention*vol[ch])+np.float32(weight*volume))
                chunk[i,ch]*=vol[ch]
        result.append(r.process_dualband(dual,chunk))
    return np.concatenate(result)

def compare_full():
    rng=np.random.default_rng(932);n=48000;t=np.arange(n)/48000
    x=(rng.normal(0,.08,(n,2))+np.column_stack([.2*np.sin(2*np.pi*90*t)+.1*np.sin(2*np.pi*3700*t),.3*np.sin(2*np.pi*800*t)])).astype(np.float32)
    volume=np.float32(.3);common=original_common(x,volume);reference=np.zeros((n,4),dtype=np.float32)
    coeff=np.empty(12,dtype=np.float32)
    native.macbook_spline_coefficients(ptr(coeff),48000,-2,3.3036599159240723,3.3079700469970703,0,10,.063510000705719)
    nfft=1<<(n+16384-1).bit_length()
    for pair,branch in enumerate(['tweeter','bass']):
        audio=np.empty((n,2),dtype=np.float32)
        for ch in range(2):
            impulse=np.frombuffer((ASSETS/f'{branch}-branch-{ch}.wav').read_bytes()[44:],dtype='<f4')
            gain=[2.5118863582611084,2.5192835330963135][ch]
            if not pair:gain*=.5956621170043945
            y=np.fft.irfft(np.fft.rfft(common[:,ch],nfft)*np.fft.rfft(impulse,nfft),nfft)[:n]*gain
            audio[:,ch]=y
        states=np.ones(2,dtype=np.float32);out=np.empty_like(audio)
        native.macbook_spline_process(ptr(coeff),ptr(states),ptr(audio),ptr(out),n)
        reference[:,2*pair:2*pair+2]=out
    reference[:,1]*=np.float32(1.0035309791564941)
    reference[13:,2:]=reference[:-13,2:].copy();reference[:13,2:]=0
    handle=native.apple_stack_create();actual=np.empty((n,4),dtype=np.float32)
    native.apple_stack_process(handle,ptr(x),ptr(actual),n,volume,volume,1,0);native.apple_stack_free(handle)
    error=abs(actual-reference)
    result={'frames':n,'max_error':float(error.max()),'rms_error':float(np.sqrt(np.mean(error**2)))}
    print('Whole graph:',result,flush=True)
    if error.max()>.001:np.savez(ROOT/'stack-debug.npz',actual=actual,reference=reference,input=x)
    assert error.max()<.001 and np.sqrt(np.mean(error**2))<.0001,result
    return result

def interface():
    class Descriptor(ct.Structure):pass
    Create=ct.CFUNCTYPE(ct.c_void_p,ct.POINTER(Descriptor),ct.c_double,ct.c_char_p,ct.c_void_p)
    Connect=ct.CFUNCTYPE(None,ct.c_void_p,ct.c_uint32,ct.c_void_p)
    Action=ct.CFUNCTYPE(None,ct.c_void_p);Run=ct.CFUNCTYPE(None,ct.c_void_p,ct.c_uint32)
    Descriptor._fields_=[('uri',ct.c_char_p),('create',Create),('connect',Connect),('activate',Action),
                        ('run',Run),('deactivate',ct.c_void_p),('cleanup',Action),('extension',ct.c_void_p)]
    native.lv2_descriptor.argtypes=[ct.c_uint32];native.lv2_descriptor.restype=ct.POINTER(Descriptor)
    desc=native.lv2_descriptor(0);d=desc.contents
    assert not d.create(desc,44100,b'',None)
    h=d.create(desc,48000,b'',None);assert h
    count=4096;steps=[]
    x=np.random.default_rng(777).normal(0,.1,(count,2)).astype(np.float32)
    ins=[np.ascontiguousarray(x[:,c]) for c in range(2)];outs=[np.zeros(count,dtype=np.float32) for _ in range(4)]
    controls=[ct.c_float(1) for _ in range(27)];controls[17].value=0  # headphone port 23
    for i,a in enumerate(ins+outs):d.connect(h,i,ptr(a))
    for i,c in enumerate(controls):d.connect(h,6+i,ct.byref(c))
    for stage in range(14):
        controls[stage].value=0;d.run(h,count)
        assert all(np.all(np.isfinite(a)) for a in outs)
        controls[stage].value=1;steps.append(stage)
    # All-stage bypass keeps volume and feeds both pairs, with no additional gain.
    for i in range(14):controls[i].value=0
    controls[5].value=1  # keep stereo duplication while bypassing processing
    controls[14].value=controls[15].value=.4
    for _ in range(10):d.run(h,count)
    for ch in range(4):assert np.max(abs(outs[ch]-ins[ch%2]*.4))<2e-5
    controls[17].value=1
    for _ in range(3):d.run(h,count)
    assert np.max(abs(outs[2]))==0 and np.max(abs(outs[3]))==0
    assert np.max(abs(outs[0]-ins[0]*.4))<2e-5
    for i in range(14):controls[i].value=1
    controls[17].value=0;controls[14].value=controls[15].value=1
    started=time.process_time()
    for _ in range(100):d.run(h,count)
    elapsed=time.process_time()-started
    d.cleanup(h)
    return {'stage_toggles':steps,'bypass_and_headphones_passed':True,'one_core_percent':elapsed/(409600/48000)*100}

if __name__=='__main__':
    report={'graph':compare_full(),'lv2':interface(),'passed':True,
            'plugin_sha256':hashlib.sha256(PLUGIN.read_bytes()).hexdigest(),
            'original_sha256':hashlib.sha256((module.SOURCE/'DspFuncLib').read_bytes()).hexdigest()}
    text=json.dumps(report,indent=2)+'\n';(ROOT/'stack-validation.json').write_text(text)
    print(report['lv2'],flush=True)
