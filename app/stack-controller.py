#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-only
"""Persist live stage controls, pin neutral hardware gain and bypass headphones."""
import codecs
import json
import os
import selectors
import signal
import subprocess
import sys
import time
from stack_config import *

def daemon():
    objects={};last=None;hardware_last=None
    monitor=subprocess.Popen(['pw-dump','--monitor'],stdout=subprocess.PIPE,bufsize=0)
    selector=selectors.DefaultSelector();selector.register(monitor.stdout,selectors.EVENT_READ)
    buffer='';decoder=json.JSONDecoder();utf8=codecs.getincrementaldecoder('utf-8')()
    def stop(signum,frame):raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:
        while monitor.poll() is None:
            for key,_ in selector.select(.15):
                chunk=os.read(key.fd,65536)
                if not chunk:return
                buffer+=utf8.decode(chunk)
                while buffer.strip():
                    buffer=buffer.lstrip()
                    try:updates,end=decoder.raw_decode(buffer)
                    except json.JSONDecodeError:break
                    buffer=buffer[end:]
                    for obj in updates:
                        if obj.get('info','absent') is None:objects.pop(obj['id'],None);continue
                        existing=objects.setdefault(obj['id'],{})
                        for name,value in obj.items():
                            if name=='info':
                                info=existing.setdefault('info',{})
                                for field,data in value.items():
                                    if field in ('props','params'):info.setdefault(field,{}).update(data)
                                    else:info[field]=data
                            else:existing[name]=value
            values=list(objects.values())
            hardware=find(values,'node.name',HARDWARE);device=find(values,'device.name',DEVICE)
            sink=find(values,'node.name',SINK)
            if not hardware or not device or not sink:last=None;continue
            card=int(hardware['info']['props']['card.profile.device'])
            route=next((r for r in device['info'].get('params',{}).get('Route',[])
                        if r.get('direction')=='Output' and r.get('device')==card),None)
            if not route:continue
            headphones=route['name']=='analog-output-headphones'
            # The hardware stays at its documented 0 dB point. User volume is
            # controlled directly by the filter's capture.volumes mapping.
            identity=(hardware['id'],hardware['info']['props'].get('object.serial'),route['name'])
            new_route=identity!=hardware_last
            base=route['props'].get('volumeBase',1.0)
            volumes=route['props'].get('channelVolumes',[1,1,1,1])
            gain_changed=any(abs(v-base)>1e-5 for v in volumes)
            if new_route:
                run(['pw-cli','set-param',str(hardware['id']),'Props',
                     json.dumps({'params':['audioconvert.filter-graph.0','']})])
            if new_route or gain_changed:
                run(['pw-cli','set-param',str(device['id']),'Route',json.dumps({
                    'index':route['index'],'device':card,
                    'props':{'mute':False if new_route else route['props'].get('mute',False),
                             'channelVolumes':[base]*len(volumes)},'save':False})])
                hardware_last=identity
            config=settings()
            state_id=(sink['id'],sink['info']['props'].get('object.serial'),headphones,
                      config.get('enabled',True),tuple(config['stages'].get(str(i),True) for i in range(14)))
            if state_id==last:continue
            apply(sink['id'],config,headphones);last=state_id
            status={'backend':'stack','mode':'headphones' if headphones else 'apple' if config.get('enabled',True) else 'flat',
                    'route':route['name'],'sink_id':sink['id'],'sink_name':SINK,
                    'sink_serial':sink['info']['props'].get('object.serial'),'hardware_id':hardware['id'],
                    'enabled':config.get('enabled',True),'stages':config['stages'],'profile':0,'sample_rate':48000,
                    'complete_selected_pipeline':True,'updated_at':time.time(),'controller_pid':os.getpid()}
            save(STATUS,status);save(STATE/'status.json',status)
            print(json.dumps(status),flush=True)
    finally:monitor.terminate();monitor.wait(timeout=3)

def command(cmd,args):
    if cmd=='status':print(STATUS.read_text() if STATUS.exists() else 'Starting Apple audio.');return
    if cmd=='stage':
        if len(args)!=2 or not args[0].isdigit() or int(args[0]) not in range(14) or args[1] not in ('on','off'):
            raise SystemExit('Usage: macbook-eq stage 0..13 on|off')
        update(stage=(int(args[0]),args[1]=='on'));print('Saved stage '+args[0]+' '+args[1]+'.');return
    if cmd in ('compressor','output-stage'):
        index=1 if cmd=='compressor' else 13
        if args==['status']:print('on' if settings()['stages'].get(str(index),True) else 'off');return
        return command('stage',[str(index),*args])
    if cmd=='limiter':
        if args==['status']:print('Apple spline limiters');return
        if args==['apple']:
            update(stage=(11,True));update(stage=(12,True));return
        raise SystemExit('The full stack uses the reconstructed Apple limiters. Toggle stages 11 and 12 to compare.')
    if cmd in ('on','off','toggle'):
        active=not settings().get('enabled',True) if cmd=='toggle' else cmd=='on'
        update({'enabled':active});print('Apple processing '+('on.' if active else 'bypassed.'));return
    if cmd=='all-on':update({'enabled':True,'stages':{str(i):True for i in range(14)}});return
    raise SystemExit('Usage: macbook-eq on|off|toggle|status|all-on|stage 0..13 on|off|compressor on|off|output-stage on|off')

if __name__=='__main__':
    try:
        if sys.argv[1:]==['--daemon']:daemon()
        else:command(sys.argv[1] if len(sys.argv)>1 else 'status',sys.argv[2:])
    except KeyboardInterrupt:pass
