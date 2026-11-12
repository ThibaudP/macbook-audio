#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-only
"""Native GTK control panel for every selected Apple speaker DSP instance."""
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import time
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk,Gdk,GLib
from stack_config import *

class Panel(Gtk.Application):
    def __init__(self):
        super().__init__(application_id='local.macbook.AppleAudio')
        self.connect('activate',self.open);self.refreshing=False;self.volume_task=0;self.last_poll=0
        self.meter_sequence=None;self.meter_updated=0
    def label(self,text,style=None):
        label=Gtk.Label(label=text,xalign=0);label.set_wrap(True)
        if style:label.add_css_class(style)
        return label
    def open(self,application):
        if hasattr(self,'window'):self.window.present();return
        self.window=Gtk.ApplicationWindow(application=self,title='MacBook Audio')
        self.window.set_default_size(1130,760)
        css=Gtk.CssProvider();css.load_from_data(b'.stage-row {padding: 8px 10px;} .stage-description {opacity: 0.8;} .section-title {font-weight: bold; font-size: 1.05em;} .audio-card {border: 1px solid alpha(currentColor,0.16); border-radius: 10px;} .error-text {color: #e01b24;}')
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(),css,Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        outer=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=14)
        for side in ['top','bottom','start','end']:getattr(outer,'set_margin_'+side)(18)
        self.window.set_child(outer)
        header=Gtk.Box(spacing=20);titles=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=4)
        titles.append(self.label('MacBook Audio','title-1'))
        titles.append(self.label('Apple speaker correction · toggle stages to hear what each contributes.','dim-label'))
        titles.set_hexpand(True);header.append(titles)
        self.master=Gtk.Switch();self.master.set_valign(Gtk.Align.CENTER)
        self.master.set_tooltip_text('Bypass all speaker processing while keeping normal volume control.')
        self.master.connect('notify::active',self.master_changed)
        header.append(self.label('Processing'));header.append(self.master);outer.append(header)
        controls=Gtk.Box(spacing=12)
        self.route=self.label('Connecting to audio service…');self.route.set_hexpand(True);controls.append(self.route)
        button=Gtk.Button(label='All Apple stages on');button.connect('clicked',self.all_on);controls.append(button)
        button=Gtk.Button(label='Use MacBook audio');button.connect('clicked',self.use_output);controls.append(button)
        outer.append(controls)
        volume=Gtk.Box(spacing=12);volume.append(self.label('Volume'))
        self.volume=Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,0,100,1)
        self.volume.set_draw_value(False);self.volume.set_hexpand(True);self.volume.connect('value-changed',self.volume_changed)
        volume.append(self.volume);self.volume_label=self.label('—');self.volume_label.set_width_chars(5);volume.append(self.volume_label)
        outer.append(volume)
        scroll=Gtk.ScrolledWindow();scroll.set_policy(Gtk.PolicyType.NEVER,Gtk.PolicyType.AUTOMATIC);scroll.set_vexpand(True)
        outer.append(scroll)
        content=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=14);scroll.set_child(content)
        grid=Gtk.Grid(column_spacing=14,row_spacing=14);grid.set_column_homogeneous(True);content.append(grid)
        self.switches={}
        for column,(title,indices) in enumerate([('Input & volume',[0,1,2]),('Dynamics & routing',[3,4,5]),
                                                ('Tweeters',[6,8,10,12]),('Bass & output',[7,9,11,13])]):
            box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8);box.set_valign(Gtk.Align.START)
            box.append(self.label(title,'section-title'))
            card=Gtk.Box(orientation=Gtk.Orientation.VERTICAL);card.add_css_class('audio-card');box.append(card)
            for position,index in enumerate(indices):
                if position:card.append(Gtk.Separator())
                card.append(self.stage_row(index))
            grid.attach(box,column,0,1,1)
        meterbox=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=6)
        meterbox.append(self.label('Digital output peaks','section-title'))
        meters=Gtk.Grid(column_spacing=14,row_spacing=6);meters.set_column_homogeneous(True);meterbox.append(meters)
        self.meters=[]
        for ch,title in enumerate(['Tweeter L','Tweeter R','Bass L','Bass R']):
            box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=4);box.append(self.label(title,'dim-label'))
            level=Gtk.LevelBar.new_for_interval(0,1);box.append(level);value=self.label('—');box.append(value)
            self.meters.append((level,value));meters.attach(box,ch,0,1,1)
        outer.append(meterbox)
        self.message=self.label('');self.message.add_css_class('dim-label');outer.append(self.message)
        GLib.timeout_add(200,self.refresh);self.refresh();self.window.present()
        if os.environ.get('MACBOOK_AUDIO_PREVIEW'):GLib.timeout_add(1200,self.preview)
    def preview(self):
        try:
            paintable=Gtk.WidgetPaintable.new(self.window);snapshot=Gtk.Snapshot()
            paintable.snapshot(snapshot,self.window.get_width(),self.window.get_height())
            node=snapshot.to_node()
            texture=self.window.get_renderer().render_texture(node,None)
            texture.save_to_png(os.environ['MACBOOK_AUDIO_PREVIEW'])
            print('Panel preview saved.',flush=True)
        except Exception as error:print('Panel preview:',error,flush=True)
        return False
    def stage_row(self,index):
        title,description,_=STAGES[index]
        row=Gtk.Box(spacing=10);row.add_css_class('stage-row')
        text=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=4);text.set_hexpand(True)
        text.append(self.label(f'{index+1:02d} · {title}','heading'))
        label=self.label(description,'stage-description');label.set_max_width_chars(28);text.append(label);row.append(text)
        switch=Gtk.Switch();switch.set_valign(Gtk.Align.START)
        switch.connect('notify::active',self.stage_changed,index);row.append(switch);self.switches[index]=switch
        return row
    def report_error(self,error):self.message.set_label(str(error));self.message.add_css_class('error-text')
    def stage_changed(self,switch,param,index):
        if not self.refreshing:
            try:update(stage=(index,switch.get_active()))
            except Exception as error:self.report_error(error)
    def master_changed(self,switch,param):
        if not self.refreshing:
            try:update({'enabled':switch.get_active()})
            except Exception as error:self.report_error(error)
    def all_on(self,button):
        update({'enabled':True,'stages':{str(i):True for i in range(14)}});self.refresh()
    def use_output(self,button):
        try:
            node=find(dump(),'node.name',SINK)
            if not node:raise RuntimeError('The Apple audio service is not running.')
            run(['wpctl','set-default',str(node['id'])])
        except Exception as error:self.report_error(error)
    def volume_changed(self,scale):
        self.volume_label.set_label(f'{round(scale.get_value())}%')
        if self.refreshing:return
        if self.volume_task:GLib.source_remove(self.volume_task)
        self.volume_task=GLib.timeout_add(60,self.apply_volume)
    def apply_volume(self):
        self.volume_task=0
        try:
            node=find(dump(),'node.name',SINK)
            if node:run(['wpctl','set-volume','--limit','1.0',str(node['id']),str(self.volume.get_value()/100)])
        except Exception as error:self.report_error(error)
        return False
    def refresh(self):
        try:
            self.refreshing=True;config=settings()
            self.master.set_active(config.get('enabled',True))
            for i,switch in self.switches.items():switch.set_active(config['stages'].get(str(i),True))
            status=json.loads(STATUS.read_text()) if STATUS.exists() else {}
            self.route.set_label('Headphones · speaker correction bypassed' if status.get('mode')=='headphones'
                                 else 'Speakers · Apple processing active' if config.get('enabled',True) else 'Speakers · all processing bypassed')
            now=time.monotonic()
            if now-self.last_poll>1:
                self.last_poll=now
                node=find(dump(),'node.name',SINK)
                if node and not self.volume_task:
                    props=next((p for p in node['info'].get('params',{}).get('Props',[]) if 'channelVolumes' in p),{})
                    volumes=props.get('channelVolumes',[1]);value=100*max(volumes)**(1/3)
                    self.volume.set_value(min(100,value))
            meter=Path(os.environ.get('XDG_RUNTIME_DIR','/run/user/'+str(os.getuid())))/'macbook-apple-audio.meters'
            if meter.exists():
                raw=meter.read_bytes()[:128]
                if len(raw)==128:
                    seq,*values=struct.unpack('<I31f',raw)
                    if seq!=self.meter_sequence:self.meter_updated=now;self.meter_sequence=seq
                    active=now-self.meter_updated<1.2
                    for ch,(bar,label) in enumerate(self.meters):
                        peak=values[1+ch] if active else 0
                        db=20*math.log10(peak) if peak>1e-5 else -100
                        bar.set_value(max(0,min(1,(db+60)/60)))
                        label.set_label(f'{db:.1f} dBFS' if db>-100 else '—')
                    clipped=values[12] if active else 0
                    self.message.set_label('Digital clipping detected. Check the limiter switches.' if clipped else 'Changes are saved automatically. Headphones bypass all fourteen speaker stages.')
                    if clipped:self.message.add_css_class('error-text')
                    else:self.message.remove_css_class('error-text')
        except Exception as error:self.report_error(error)
        finally:self.refreshing=False
        return True

Panel().run([])
