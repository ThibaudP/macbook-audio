#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-only
"""Export selected 48 kHz coefficient data and sample the static gain curves."""
import importlib.util
import json
import os
from pathlib import Path
import struct

ROOT=Path(os.environ.get('MACBOOK_DATA_DIR',Path(__file__).resolve().parents[2]/'data'))
ROOT.mkdir(parents=True,exist_ok=True)
spec=importlib.util.spec_from_file_location('apple_reference',Path(__file__).with_name('apple-reference.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
np=module.np
r=module.Reference();obj=r.compressor();r.call(0x83e04,obj)

def array(name,values,typ='float'):
    vals=np.asarray(values).reshape(-1)
    def literal(x):
        s=format(float(x),'.17g' if typ=='double' else '.9g')
        if '.' not in s and 'e' not in s:s+='.0'
        return s+('f' if typ=='float' else '')
    return 'static const '+typ+' '+name+'['+str(len(vals))+']={\n'+',\n'.join(','.join(literal(x) for x in vals[i:i+8]) for i in range(0,len(vals),8))+'\n};\n'

# Keep only the actual coefficient/state region, excluding object pointers.
state=list(struct.unpack('<69f',r.uc.mem_read(obj+0x414,69*4)))
biquad=r.read(obj+0x3d8,'<Q');setup=r.read(biquad+0x60,'<Q')
coeffs=r.biquads[setup]['coeffs'][:,0,:]
down=r.read(obj+0x3e8,'<Q');up=r.read(obj+0x3e0,'<Q')
src=r.alloc(4096*8);dst=r.alloc(4096*8)
m=r.call(0x8d58e,down,RSI=src,RDX=dst,RCX=1024)
x=np.zeros(m,dtype=np.float32);x[0]=1;r.uc.mem_write(src,x.tobytes())
n=r.call(0x8d58e,up,RSI=src,RDX=dst,RCX=m)
kernel=r.vector(dst,1,n)[:372]
levels=np.linspace(-160,80,24001,dtype=np.float32)
inp=r.alloc(levels.nbytes);attack=r.alloc(levels.nbytes);release=r.alloc(levels.nbytes)
r.uc.mem_write(inp,levels.tobytes())
r.call(0x858b2,obj,RSI=obj+0x464,RDX=inp,RCX=attack,R8=release,R9=len(levels))
curves=np.column_stack([r.vector(attack,1,len(levels)),r.vector(release,1,len(levels))])
header='/* Profile 0, 48000 Hz. Coefficient data only; no Apple executable code. */\n'
header+=array('compressor_initial',state)
header+=array('compressor_sidechain',coeffs,'double')
header+=array('compressor_interpolator',kernel)
header+=array('compressor_curves',curves)
(ROOT/'compressor-data.h').write_text(header)
report={'sample_rate':48000,'profile':0,'input_gain':r.read(obj+0x3c8,'<f'),
 'delay_samples':r.read(obj+0x52c,'<I'),'decimation':r.read(obj+0x3f0,'<I'),
 'curve_min_db':-160,'curve_max_db':80,'curve_step_db':.01,
 'parameters':list(struct.unpack('<53f',r.uc.mem_read(obj+0x2b8,212)))}
(ROOT/'compressor-data.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k!='parameters'}))
