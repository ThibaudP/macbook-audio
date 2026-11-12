#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-only
"""Numerical checks against the original first-compressor executable routines."""
import ctypes as ct
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import struct
import time

REPO=Path(__file__).resolve().parents[2]
ROOT=REPO/'build/reports';ROOT.mkdir(parents=True,exist_ok=True)
spec=importlib.util.spec_from_file_location('apple_reference',Path(__file__).with_name('apple-reference.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
np=module.np
PLUGIN=Path(os.environ.get('MACBOOK_PLUGIN',REPO/'build/macbook-apple-stack.lv2/macbook-apple-stack.so'))
native=ct.CDLL(str(PLUGIN))
P=ct.POINTER(ct.c_float)
native.macbook_compressor_create.restype=ct.c_void_p
native.macbook_compressor_reset.argtypes=[ct.c_void_p]
native.macbook_compressor_free.argtypes=[ct.c_void_p]
native.macbook_compressor_process.argtypes=[ct.c_void_p,P,P,ct.c_ulong]
native.macbook_compressor_gain.argtypes=[ct.c_void_p,ct.c_float,ct.c_float]
native.macbook_compressor_gain.restype=ct.c_float
native.macbook_compressor_curve.argtypes=[ct.c_float,P,P]

def ptr(array):return array.ctypes.data_as(P)
def state(ref,obj):return np.frombuffer(ref.uc.mem_read(obj+0x414,276),dtype=np.float32).copy()

def compare_gain():
    ref=module.Reference();obj=ref.compressor();ref.call(0x83e04,obj)
    handle=native.macbook_compressor_create()
    rng=np.random.default_rng(401)
    rms=np.concatenate([np.exp(rng.uniform(-23,5,2048)),np.full(4096,.001),
                        np.full(4096,.1),np.full(4096,10),np.full(4096,1e-10)]).astype(np.float32)
    peak=(rms*np.exp(rng.uniform(-3,3,len(rms)))).astype(np.float32)
    reference=[]
    for start in range(0,len(rms),64):
        a=ref.read(obj+0x390,'<Q');b=ref.read(obj+0x3a8,'<Q')
        ref.uc.mem_write(a,rms[start:start+64].tobytes());ref.uc.mem_write(b,peak[start:start+64].tobytes())
        ref.call(0x851ae,obj,RSI=64,until=0x854a0)
        reference.extend(ref.vector(a,1,64))
    actual=np.array([native.macbook_compressor_gain(handle,float(a),float(b)) for a,b in zip(rms,peak)],dtype=np.float32)
    delta=np.abs(actual-reference)
    result={'max_error':float(np.max(delta)),'rms_error':float(np.sqrt(np.mean(delta**2))),
            'minimum_gain':float(actual.min()),'maximum_gain':float(actual.max()),'steps':len(actual)}
    print('Gain controller:',result,flush=True)
    if np.max(delta)>5e-5:
        print('First mismatch',[(int(i),float(reference[i]),float(actual[i])) for i in np.flatnonzero(delta>5e-5)[:5]])
        original=state(ref,obj);ours=np.ctypeslib.as_array(ct.cast(handle,P),shape=(69,))
        print('State differences',[(hex(0x414+i*4),float(a),float(b)) for i,(a,b) in enumerate(zip(original,ours)) if abs(a-b)>1e-4])
    native.macbook_compressor_free(handle)
    assert np.max(delta)<5e-5
    return result

def compare_stream(name,samples,blocks):
    ref=module.Reference();obj=ref.compressor();ref.call(0x83e04,obj)
    handle=native.macbook_compressor_create();reference=[];output=[];start=0;iteration=0
    while start<len(samples):
        count=min(blocks[iteration%len(blocks)],len(samples)-start)
        chunk=np.ascontiguousarray(samples[start:start+count],dtype=np.float32)
        reference.append(ref.process(obj,chunk))
        y=np.empty_like(chunk);native.macbook_compressor_process(handle,ptr(chunk),ptr(y),count)
        output.append(y);start+=count;iteration+=1
    reference=np.concatenate(reference);actual=np.concatenate(output);error=np.abs(reference-actual)
    result={'name':name,'frames':len(samples),'max_error':float(np.max(error)),
            'rms_error':float(np.sqrt(np.mean(error**2))),'peak':float(np.max(np.abs(actual)))}
    print(json.dumps(result),flush=True)
    if np.max(error)>.001:
        np.savez(ROOT/'compressor-debug.npz',input=samples,reference=reference,actual=actual)
        print('Worst',np.unravel_index(np.argmax(error),error.shape))
    native.macbook_compressor_free(handle)
    assert np.max(error)<.001,result
    return result

if __name__=='__main__':
    report={'gain':compare_gain(),'streams':[]}
    rng=np.random.default_rng(621);t=np.arange(192000,dtype=np.float32)/48000
    cases=[('silence',np.zeros((4096,2),dtype=np.float32),[512]),
           ('tones',np.column_stack([.2*np.sin(2*np.pi*440*t),.1*np.sin(2*np.pi*997*t)]),[512]),
           ('noise',rng.normal(0,.3,(96000,2)).astype(np.float32),[256,512,1024,31,1,7]),
           ('level_steps',np.column_stack([np.sin(2*np.pi*440*t),np.sin(2*np.pi*997*t)])*
               np.repeat([.01,1.0,.001,0.0],48000)[:,None],[128,257,1024])]
    for name,samples,blocks in cases:report['streams'].append(compare_stream(name,samples,blocks))
    report.update({'passed':True,'plugin_sha256':hashlib.sha256(PLUGIN.read_bytes()).hexdigest(),
                   'original_sha256':hashlib.sha256((module.SOURCE/'DspFuncLib').read_bytes()).hexdigest(),
                   'coefficient_header_sha256':hashlib.sha256((Path(os.environ.get('MACBOOK_DATA_DIR',REPO/'data'))/'compressor-data.h').read_bytes()).hexdigest(),
                   'reference_math':'Unicorn original DSP; host libm and IEEE vector primitive substitutions'})
    text=json.dumps(report,indent=2)+'\n'
    (ROOT/'compressor-validation.json').write_text(text)
    print('PASS compressor core; full LV2 interface is checked by tests/check_plugin.py')
