#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-only
import ctypes as ct
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct

REPO=Path(__file__).resolve().parents[2]
ROOT=REPO/'build/reports';ROOT.mkdir(parents=True,exist_ok=True)
spec=importlib.util.spec_from_file_location('reference',Path(__file__).with_name('apple-reference.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
np=module.np;P=ct.POINTER(ct.c_float)
native=ct.CDLL(os.environ.get('MACBOOK_PLUGIN',str(REPO/'build/macbook-apple-stack.lv2/macbook-apple-stack.so')))
for name in ['dual','loudness']:
    getattr(native,f'apple_{name}_create').restype=ct.c_void_p
    getattr(native,f'apple_{name}_free').argtypes=[ct.c_void_p]
    getattr(native,f'apple_{name}_reset').argtypes=[ct.c_void_p]
native.apple_dual_process.argtypes=[ct.c_void_p,P,P,ct.c_ulong]
native.apple_loudness_target.argtypes=[ct.c_void_p,ct.c_float]
native.apple_loudness_coefficients.argtypes=[ct.c_void_p,P]
native.apple_loudness_process.argtypes=[ct.c_void_p,P,P,ct.c_ulong,ct.c_float,ct.c_float,ct.c_int]
def ptr(a):return a.ctypes.data_as(P)

def dual_case(name,x):
    ref=module.Reference();obj=ref.dualband();handle=native.apple_dual_create()
    reference=[];actual=[];offset=0;count=0
    while offset<len(x):
        size=min([256,31,1,127,512][count%5],len(x)-offset)
        chunk=np.ascontiguousarray(x[offset:offset+size],dtype=np.float32)
        y=np.empty_like(chunk);native.apple_dual_process(handle,ptr(chunk),ptr(y),size)
        reference.append(ref.process_dualband(obj,chunk));actual.append(y)
        offset+=size;count+=1
    reference=np.concatenate(reference);actual=np.concatenate(actual);error=np.abs(actual-reference)
    report={'name':name,'frames':len(x),'max_error':float(error.max()),'rms_error':float(np.sqrt(np.mean(error**2)))}
    print(report,flush=True)
    if error.max()>.0001:
        np.savez(ROOT/'dualband-debug.npz',input=x,actual=actual,reference=reference)
        print('Worst',np.unravel_index(error.argmax(),error.shape),flush=True)
    native.apple_dual_free(handle)
    assert error.max()<.0001
    return report

def loudness_case():
    ref=module.Reference();obj=ref.loudness();handle=native.apple_loudness_create()
    rng=np.random.default_rng(214);worst=0
    for db in rng.uniform(-90,0,200):
        volume=float(np.float32(10**(db/20)))
        for off in [0xb0,0xb4,0xb8]:ref.write(obj+off,'<f',volume)
        ref.call(0x4a70c,obj)
        expected=np.array(struct.unpack('<10f',ref.uc.mem_read(obj+0x140,40)),dtype=np.float32)
        actual=np.empty(10,dtype=np.float32);native.apple_loudness_target(handle,volume)
        native.apple_loudness_coefficients(handle,ptr(actual));worst=max(worst,float(np.max(abs(expected-actual))))
    assert worst<5e-6,worst
    native.apple_loudness_reset(handle)
    # Compare the original scalar two-section loop, with its coefficient slew.
    current=np.array([1,0,0,0,0,1,0,0,0,0],dtype=np.float32)
    ref.uc.mem_write(obj+0x118,current.tobytes());ref.uc.mem_write(obj+0x174,b'\0'*32)
    native_state=np.ctypeslib.as_array(ct.cast(handle,P),shape=(32,))
    native_state[0:10]=current
    ct.cast(handle,ct.POINTER(ct.c_uint32))[31]=1  # configured, keep identity start
    volume_state=np.zeros(2,dtype=np.float32);retention=np.float32(ref.read(obj+0xa4,'<f'));weight=np.float32(1-retention)
    maximum=0
    for volume in [.05,.1,.5,.02,.8]:
        volume=float(np.float32(volume))
        for off in [0xb0,0xb4,0xb8]:ref.write(obj+off,'<f',volume)
        ref.call(0x4a70c,obj)
        x=rng.normal(0,.1,(1024,2)).astype(np.float32);src=ref.alloc(x.nbytes);ref.uc.mem_write(src,x.tobytes())
        rbp=0x100ff000;ref.write(rbp-0x2c,'<f',float(weight))
        ref.call(0x4a0fa,obj,until=0x4a435,RBX=obj,RBP=rbp,R12=src,R15=len(x))
        expected=ref.vector(src,1,x.size).reshape(x.shape)
        for i in range(len(x)):
            for ch in range(2):
                volume_state[ch]=np.float32(np.float32(retention*volume_state[ch])+np.float32(weight*volume))
                expected[i,ch]*=volume_state[ch]
        actual=np.empty_like(x);native.apple_loudness_process(handle,ptr(x),ptr(actual),len(x),volume,volume,1)
        maximum=max(maximum,float(np.max(abs(expected-actual))))
    native.apple_loudness_free(handle)
    assert maximum<.0001,maximum
    return {'coefficient_max_error':worst,'dynamic_loop_max_error':maximum,'volume_changes':5}

if __name__=='__main__':
    report={'loudness':loudness_case(),'dualband':[]};print('Loudness',report['loudness'],flush=True)
    rng=np.random.default_rng(333);t=np.arange(144000)/48000
    for name,x in [('silence',np.zeros((4096,2))),('multitone',np.column_stack([.3*np.sin(2*np.pi*100*t)+.1*np.sin(2*np.pi*5000*t),.2*np.sin(2*np.pi*800*t)])),
                   ('level_steps',rng.normal(0,1,(144000,2))*np.repeat([.001,.3,1.0],48000)[:,None])]:
        report['dualband'].append(dual_case(name,x))
    report['passed']=True
    (ROOT/'remaining-validation.json').write_text(json.dumps(report,indent=2)+'\n')
