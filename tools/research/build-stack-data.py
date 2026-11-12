#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-only
"""Extract coefficient data for the remaining selected Apple DSP modules."""
import importlib.util
import json
import os
from pathlib import Path
import struct

ROOT=Path(os.environ.get('MACBOOK_DATA_DIR',Path(__file__).resolve().parents[2]/'data'))
ROOT.mkdir(parents=True,exist_ok=True)
spec=importlib.util.spec_from_file_location('reference',Path(__file__).with_name('apple-reference.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
np=module.np

def array(name,values,typ='float'):
    vals=np.asarray(values).reshape(-1)
    def literal(x):
        s=format(float(x),'.17g' if typ=='double' else '.9g')
        if '.' not in s and 'e' not in s:s+='.0'
        return s+('f' if typ=='float' else '')
    return 'static const '+typ+' '+name+'['+str(len(vals))+']={\n'+',\n'.join(','.join(literal(x) for x in vals[i:i+8]) for i in range(0,len(vals),8))+'\n};\n'

r=module.Reference();obj=r.dualband();header='/* Selected profile 0, 48000 Hz: coefficient data, no Apple executable code. */\n'
data={}
for index,offset in enumerate([0x868,0x870]):
    child=r.read(obj+offset,'<Q');rm=r.read(child+0x3f8,'<Q')
    initial=list(struct.unpack('<69f',r.uc.mem_read(child+0x414,276)))
    header+=array(f'dual_initial_{index}',initial)
    header+=array(f'dual_rms_{index}',[r.read(rm+p,'<f') for p in [0x6c,0x74,0x78]])
    levels=np.linspace(-160,80,24001,dtype=np.float32)
    src=r.alloc(levels.nbytes);attack=r.alloc(levels.nbytes);release=r.alloc(levels.nbytes)
    r.uc.mem_write(src,levels.tobytes())
    r.call(0x858b2,child,RSI=child+0x464,RDX=src,RCX=attack,R8=release,R9=len(levels))
    curves=np.column_stack([r.vector(attack,1,len(levels)),r.vector(release,1,len(levels))])
    header+=array(f'dual_curves_{index}',curves)
for index,offset in enumerate([0x620,0x628,0x5d0]):
    child=r.read(obj+offset,'<Q');setup=r.read(child+0x60,'<Q')
    coeffs=r.biquads[setup]['coeffs'][:,0,:]
    header+=array(f'dual_filter_{index}',coeffs,'double')
    data[f'filter_{index}']=coeffs.tolist()
src=r.alloc(4096*8);dst=r.alloc(4096*8)
down=r.read(obj+0x848,'<Q');up=r.read(obj+0x858,'<Q')
m=r.call(0x8d58e,down,RSI=src,RDX=dst,RCX=512)
r.call(0x8d58e,r.read(obj+0x850,'<Q'),RSI=src,RDX=dst,RCX=512)
x=np.zeros(m,dtype=np.float32);x[0]=1;r.uc.mem_write(src,x.tobytes())
n=r.call(0x8d58e,up,RSI=src,RDX=dst,RCX=m)
kernel=r.vector(dst,1,n)[:124]
header+=array('dual_interpolator',kernel)
data['sections']=r.read(obj+0x630,'<I')
delay=r.read(obj+0x878,'<Q');data['delay']=r.read(delay+0x6c,'<I')
data['interpolator_nonzero']=int(np.count_nonzero(kernel))

loud=r.loudness()
coefs=[];gains=[]
for db in np.linspace(-100,6,10601):
    gain=np.float32(10**(db/20))
    for off in [0xb0,0xb4,0xb8]:r.write(loud+off,'<f',float(gain))
    r.call(0x4a70c,loud)
    coefs.append(struct.unpack('<10f',r.uc.mem_read(loud+0x140,40)))
    gains.append([r.read(loud+0x170,'<f'),r.read(loud+0x16c,'<f')])
header+=array('loudness_coefficients',coefs)
header+=array('loudness_smoothing',[r.read(loud+0xa4,'<f')])
data['loudness_volume_adjustment']=[r.read(loud+0x1a0,'<d'),r.read(loud+0x1a8,'<d')]
data['loudness_example_boosts']={str(db):gains[int((db+100)*100)] for db in [-60,-40,-20,-12,-7,0]}

selected=json.loads((module.SOURCE/'original-layout59/selected-eq.json').read_text())
for instance in [0,6,7,8,9]:
    f=selected['variant']['functions'][instance]
    for channel in range(2):
        filters=[row['sos_48000'] for row in f['filters'] if row['channel']==channel and not row['bypass']]
        values=[[v[0],v[1],v[2],v[4],v[5]] for v in filters]
        header+=array(f'eq_{instance}_{channel}',values,'double')
        data[f'eq_{instance}_{channel}']=len(values)
(ROOT/'stack-data.h').write_text(header)
(ROOT/'stack-data.json').write_text(json.dumps(data,indent=2)+'\n')
print(json.dumps({k:v for k,v in data.items() if not k.startswith('filter_')},indent=2))
