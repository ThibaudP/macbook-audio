# SPDX-License-Identifier: GPL-3.0-only
"""Shared paths and settings for the Apple audio service and control panel."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time

HOME=Path.home()
ASSETS=Path(os.environ.get('XDG_DATA_HOME',HOME/'.local/share'))/'macbook-apple-audio'
STATE=Path(os.environ.get('XDG_STATE_HOME',HOME/'.local/state'))/'macbook-audio/apple-integration'
SETTINGS=Path(os.environ.get('XDG_CONFIG_HOME',HOME/'.config'))/'macbook-apple-audio/settings.json'
HARDWARE='alsa_output.pci-0000_00_1b.0.analog-surround-40'
SINK='macbook_apple_sink'
DEVICE='alsa_card.pci-0000_00_1b.0'
STATUS=STATE/'stack-status.json'
STAGES=[
 ('Common EQ','Rolls off deep bass and trims resonances before compression.','Common path'),
 ('Main compressor','Controls stereo dynamics and brings up quieter detail.','Common path'),
 ('Loudness','Boosts bass and treble at low volume. Volume still works when off.','Common path'),
 ('Two-band compressor','Controls bass and treble dynamics around 262 Hz.','Common path'),
 ('Common gain','Applies Apple’s fixed gain to both speaker pairs.','Common path'),
 ('Stereo duplication','Feeds both speaker pairs. Off silences the bass pair.','Common path'),
 ('Tweeter EQ A','Removes bass below about 2 kHz and trims tweeter resonances.','Tweeters'),
 ('Bass EQ A','Bass protection around 100 Hz, plus resonance and treble correction.','Bass speakers'),
 ('Tweeter EQ B','Further reduces upper-mid and treble resonances.','Tweeters'),
 ('Bass EQ B','Cuts broad midrange peaks in the bass-speaker feed.','Bass speakers'),
 ('Tweeter balance','Reduces the tweeter feed by 4.5 dB.','Tweeters'),
 ('Bass limiter','Limits peaks to −2 dBFS. Off can allow clipping.','Bass speakers'),
 ('Tweeter limiter','Limits peaks to −2 dBFS. Off can allow clipping.','Tweeters'),
 ('Output alignment','Delays bass 13 samples and finely trims the right tweeter.','Output'),
]

def run(args,timeout=8):
    p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if p.returncode or 'Error:' in p.stderr:raise RuntimeError(p.stderr.strip() or p.stdout.strip() or str(args))
    return p.stdout.strip()

def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(value,indent=2)+'\n');tmp.replace(path)

def settings():
    config=json.loads(SETTINGS.read_text())
    config.setdefault('stages',{str(i):True for i in range(14)})
    return config

def update(changes=None,stage=None):
    SETTINGS.parent.mkdir(parents=True,exist_ok=True)
    with SETTINGS.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        config=settings()
        if changes:config.update(changes)
        if stage is not None:
            i,enabled=stage;config['stages'][str(i)]=bool(enabled)
            if i==1:config['first_compressor']=bool(enabled)
            if i==13:config['output_stage']=bool(enabled)
        save(SETTINGS,config)
    return config

def dump():return json.loads(run(['pw-dump']))
def find(objects,prop,value):
    return next((o for o in objects if o.get('info',{}).get('props',{}).get(prop)==value),None)

def params(config,headphones=False):
    values=[]
    for i in range(14):values.extend([f'apple:stage_{i}',float(config['stages'].get(str(i),True))])
    values.extend(['apple:enabled',float(config.get('enabled',True)),'apple:headphones',float(headphones)])
    return values

def apply(node,config,headphones=False):
    run(['pw-cli','set-param',str(node),'Props',json.dumps({'params':params(config,headphones)})])

def spa(value):
    if isinstance(value,dict):return '{ '+' '.join(json.dumps(k)+' = '+spa(v) for k,v in value.items())+' }'
    if isinstance(value,list):return '[ '+' '.join(spa(v) for v in value)+' ]'
    return json.dumps(value)

def chain_config(name=SINK,target=HARDWARE,initial=None,description='MacBook audio · Apple'):
    initial=initial or settings()
    control={f'stage_{i}':float(initial['stages'].get(str(i),True)) for i in range(14)}
    control.update({'enabled':float(initial.get('enabled',True)),'headphones':0.0})
    graph={'nodes':[{'type':'lv2','name':'apple','plugin':'urn:macbook-audio:apple-stack-profile0','control':control}],
           'inputs':['apple:in_l','apple:in_r'],
           'outputs':['apple:tweeter_l','apple:tweeter_r','apple:bass_l','apple:bass_r'],
           'capture.volumes':[{'control':'apple:volume_l','min':0,'max':1,'scale':'linear'},
                              {'control':'apple:volume_r','min':0,'max':1,'scale':'linear'}]}
    args={'node.description':description,'audio.rate':48000,'filter.graph':graph,
          'capture.props':{'node.name':name,'node.description':description,'media.class':'Audio/Sink',
               'audio.channels':2,'audio.position':['FL','FR'],'node.virtual':True,'priority.session':1,
               'node.pause-on-idle':True,'stream.dont-remix':True,
               # capture.volumes owns the gain. Keep converter gain at unity
               # even when the session manager repeats an unchanged volume.
               'channelmix.min-volume':1.0,'channelmix.max-volume':1.0},
          'playback.props':{'node.name':name+'_playback','target.object':target,
               'audio.channels':4,'audio.position':['FL','FR','RL','RR'],'node.passive':True,
               'node.dont-move':True,'node.virtual':True,'stream.dont-remix':True,
               'media.role':'DSP','application.id':'com.github.wwmm.easyeffects'}}
    return {'context.properties':{'log.level':2},
            'context.spa-libs':{'audio.convert.*':'audioconvert/libspa-audioconvert','support.*':'support/libspa-support'},
            'context.modules':[{'name':n} for n in ['libpipewire-module-protocol-native','libpipewire-module-client-node','libpipewire-module-adapter']]+
                              [{'name':'libpipewire-module-filter-chain','args':args}]}
