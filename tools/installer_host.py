# SPDX-License-Identifier: GPL-3.0-only
"""PipeWire/WirePlumber activation and restoration for the supported MacBook."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

DEVICE = 'alsa_card.pci-0000_00_1b.0'
HARDWARE = 'alsa_output.pci-0000_00_1b.0.analog-surround-40'
SINK = 'macbook_apple_sink'
PROFILE = 'output:analog-surround-40+input:analog-stereo'
UNITS = ['macbook-apple-audio.service', 'macbook-apple-stack.service']


def props(obj):
    return obj.get('info', {}).get('props', {})


def find(objects, key, value):
    return next((o for o in objects if props(o).get(key) == value), None)


class Host:
    def run(self, args, check=True):
        process = subprocess.run([str(a) for a in args], capture_output=True, text=True, timeout=12)
        if check and (process.returncode or 'Error:' in process.stderr):
            raise RuntimeError(process.stderr.strip() or process.stdout.strip() or str(args))
        return process.stdout.strip()

    def dump(self):
        return json.loads(self.run(['pw-dump']))

    def wait(self, function, description, seconds=18):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            result = function()
            if result:
                return result
            time.sleep(.1)
        raise RuntimeError(description)

    def preflight(self):
        if os.geteuid() == 0:
            raise ValueError('Run as the desktop user, without sudo')
        if os.environ.get('PIPEWIRE_REMOTE') not in (None, '', 'pipewire-0'):
            raise ValueError('Install into the standard desktop PipeWire session, without PIPEWIRE_REMOTE overrides')
        missing = [n for n in ('pipewire', 'wireplumber', 'pw-dump', 'pw-cli', 'pw-metadata',
                               'wpctl', 'systemctl', 'systemd-run', 'alsactl') if not shutil.which(n)]
        if missing:
            raise ValueError('Missing runtime tools: ' + ', '.join(missing))
        for command, minimum in [('pipewire', (1, 6)), ('wireplumber', (0, 5))]:
            versions = re.findall(r'(\d+)\.(\d+)\.\d+', self.run([command, '--version']))
            if not versions or tuple(map(int, versions[-1])) < minimum:
                raise ValueError(f'{command} {minimum[0]}.{minimum[1]} or newer is required')
        if Path('/sys/class/dmi/id/product_name').read_text().strip() != 'MacBookPro10,2':
            raise ValueError('Only MacBookPro10,2 is supported')
        for unit in ('pipewire.service', 'wireplumber.service'):
            if self.run(['systemctl', '--user', 'is-active', unit]) != 'active':
                raise ValueError(f'{unit} must be running')
        objects = self.dump()
        device = find(objects, 'device.name', DEVICE)
        if not device:
            raise ValueError('The internal ALSA device was not found')
        card = int(props(device)['api.alsa.card'])
        codec = Path(f'/proc/asound/card{card}/codec#0').read_text()
        if 'Vendor Id: 0x10134206' not in codec or 'Subsystem Id: 0x106b3b00' not in codec:
            raise ValueError('The CS4206 codec/subsystem does not match the supported machine')
        if find(objects, 'node.name', SINK):
            raise ValueError('A MacBook Audio processor is already running; this command supports fresh installation')
        hardware = find(objects, 'node.name', HARDWARE)
        for parameter in (hardware or {}).get('info', {}).get('params', {}).get('Props', []):
            values = parameter.get('params', {})
            if isinstance(values, list):
                values = dict(zip(values[::2], values[1::2]))
            if values.get('audioconvert.filter-graph.0'):
                raise ValueError('The hardware already has inline DSP; remove that setup before a fresh installation')
        self.run(['/usr/bin/python3', '-c',
                  'import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk'])
        return card

    def metadata(self, objects=None):
        objects = self.dump() if objects is None else objects
        metadata = next((o for o in objects if o.get('type') == 'PipeWire:Interface:Metadata' and
                         o.get('props', {}).get('metadata.name') == 'default'), None)
        if metadata is None:
            raise RuntimeError('WirePlumber default metadata is unavailable')
        result = {}
        for entry in metadata.get('metadata', []):
            kind, value = entry.get('type', ''), entry['value']
            value = json.dumps(value) if kind == 'Spa:String:JSON' else str(value)
            result[(entry['subject'], entry['key'])] = {'value': value, 'type': kind}
        return result

    def snapshot(self, backup):
        objects = self.dump()
        device = find(objects, 'device.name', DEVICE)
        profile = device['info']['params']['Profile'][0]['name']
        card = int(props(device)['api.alsa.card'])
        internal = [o for o in objects if props(o).get('device.id') == device['id'] and
                    props(o).get('media.class') == 'Audio/Sink']
        # Some PipeWire properties represent IDs as strings.
        internal += [o for o in objects if props(o).get('device.id') == str(device['id']) and
                     props(o).get('media.class') == 'Audio/Sink']
        internal_ids = {o['id'] for o in internal}
        routes = [r for r in device['info']['params'].get('Route', []) if r.get('direction') == 'Output']
        metadata = self.metadata(objects)
        streams = []
        by_id = {o['id']: o for o in objects}
        for link in objects:
            info = link.get('info', {})
            if link.get('type') != 'PipeWire:Interface:Link' or info.get('input-node-id') not in internal_ids:
                continue
            stream = by_id.get(info.get('output-node-id'))
            if not stream or props(stream).get('media.class') != 'Stream/Output/Audio':
                continue
            record = {'id': stream['id'], 'serial': props(stream).get('object.serial'),
                      'target': props(by_id[info['input-node-id']])['node.name'],
                      'target_id': info['input-node-id'],
                      'target_serial': props(by_id[info['input-node-id']]).get('object.serial'),
                      'metadata': {key: metadata.get((stream['id'], key)) for key in ('target.object', 'target.node')}}
            if record not in streams:
                streams.append(record)
        mixer = backup / 'alsa.state'
        self.run(['alsactl', '-f', mixer, 'store', str(card)])
        # A readable backup avoids ever needing elevated permissions for recovery.
        if not mixer.is_file():
            raise RuntimeError('ALSA state snapshot was not created')
        return {'profile': profile, 'card': card, 'routes': routes, 'streams': streams,
                'default_configured': metadata.get((0, 'default.configured.audio.sink')),
                'default_active': metadata.get((0, 'default.audio.sink')),
                'alsa_state': str(mixer)}

    def arm(self, manifest, path):
        # A transient timer covers process failure/SIGKILL while files/routing change.
        if self.run(['systemctl', '--user', 'show', manifest['timer'] + '.timer',
                     '--property=ActiveState', '--value'], check=False) == 'active':
            return
        self.run(['systemd-run', '--user', '--collect', '--unit=' + manifest['timer'],
                  '--on-active=90s', '--timer-property=AccuracySec=1s', '/usr/bin/python3',
                  str(Path(manifest['backup']) / 'install.py'), 'recover', '--manifest', path])

    def cancel(self, manifest):
        self.run(['systemctl', '--user', 'stop', manifest['timer'] + '.timer'], check=False)

    def ensure_profile(self):
        device = self.wait(lambda: find(self.dump(), 'device.name', DEVICE), 'Internal device did not appear')
        profiles = device['info']['params']['EnumProfile']
        selected = next((p for p in profiles if p.get('name') == PROFILE), None)
        if not selected:
            raise RuntimeError('The four-channel profile is unavailable')
        current = device['info']['params'].get('Profile', [{}])[0]
        if current.get('name') != PROFILE:
            self.run(['wpctl', 'set-profile', str(device['id']), str(selected['index'])])
        return self.wait(lambda: find(self.dump(), 'node.name', HARDWARE), 'Four-channel sink did not appear')

    def activate(self, paths, snapshot):
        self.run(['systemctl', '--user', 'daemon-reload'])
        self.run(['systemctl', '--user', 'restart', 'wireplumber.service'])
        hardware = self.ensure_profile()
        self.run(['wpctl', 'set-mute', str(hardware['id']), '1'])
        # Start the virtual sink muted; write the normal startup config after handoff.
        from installer_core import atomic
        path = paths.assets / 'stack.conf'
        original = path.read_bytes()
        text = original.decode()
        marker = '"capture.props" = { '
        if marker not in text:
            raise RuntimeError('Unexpected filter-chain configuration')
        atomic(path, text.replace(marker, marker + '"mute" = true ', 1).encode())
        self.run(['systemctl', '--user', 'enable', '--now', 'macbook-apple-stack.service'])
        sink = self.wait(lambda: find(self.dump(), 'node.name', SINK), 'The DSP sink did not appear')
        self.run(['wpctl', 'set-volume', '--limit', '1.0', str(sink['id']), '0.5'])
        self.run(['wpctl', 'set-default', str(sink['id'])])
        # Move only the pre-existing feeds to the internal hardware. The new DSP's
        # own playback stream is never in this list, preventing a feedback loop.
        current = {o['id']: o for o in self.dump()}
        for stream in snapshot['streams']:
            obj = current.get(stream['id'])
            if obj and props(obj).get('object.serial') == stream['serial']:
                self.run(['pw-metadata', '-n', 'default', str(stream['id']), 'target.object', SINK, 'Spa:String'])
        self.run(['systemctl', '--user', 'enable', '--now', 'macbook-apple-audio.service'])
        status = paths.state / 'macbook-audio/apple-integration/stack-status.json'

        def ready():
            if not status.is_file():
                return False
            result = json.loads(status.read_text())
            return result if result.get('sink_serial') == props(sink).get('object.serial') else False

        self.wait(ready, 'Route controller did not become ready')
        self.run(['wpctl', 'set-mute', str(sink['id']), '0'])
        atomic(path, original)
        live = self.dump()
        playback = find(live, 'node.name', SINK + '_playback')
        if not playback or not any(o.get('type') == 'PipeWire:Interface:Link' and
                o.get('info', {}).get('output-node-id') == playback['id'] and
                o.get('info', {}).get('input-node-id') == hardware['id'] for o in live):
            # Passive streams may be idle until an application plays. At minimum
            # require both endpoints and the exact nonmoving hardware target.
            if not playback or props(playback).get('target.object') != HARDWARE:
                raise RuntimeError('DSP playback is not targeted at the internal hardware')

    def quiesce(self):
        sink = find(self.dump(), 'node.name', SINK)
        if sink:
            self.run(['wpctl', 'set-mute', str(sink['id']), '1'], check=False)
        self.run(['systemctl', '--user', 'disable', '--now', *UNITS], check=False)

    def restore(self, snapshot):
        self.run(['systemctl', '--user', 'daemon-reload'])
        self.run(['systemctl', '--user', 'restart', 'wireplumber.service'])
        device = self.wait(lambda: find(self.dump(), 'device.name', DEVICE), 'Internal device did not return')
        profile = next((p for p in device['info']['params']['EnumProfile'] if p['name'] == snapshot['profile']), None)
        if not profile:
            raise RuntimeError('The original hardware profile is unavailable')
        self.run(['wpctl', 'set-profile', str(device['id']), str(profile['index'])])

        def profile_ready():
            obj = find(self.dump(), 'device.name', DEVICE)
            return obj if obj and obj['info']['params'].get('Profile', [{}])[0].get('name') == snapshot['profile'] else False

        device = self.wait(profile_ready, 'Original profile did not settle')
        # Let the session manager finish profile/route restoration before restoring
        # explicit saved routes and the exact ALSA control state.
        time.sleep(.5)
        device = find(self.dump(), 'device.name', DEVICE)
        for original in snapshot['routes']:
            route = next((r for r in device['info']['params'].get('EnumRoute', [])
                          if r.get('name') == original.get('name')), None)
            if route:
                values = {k: v for k, v in original.get('props', {}).items() if k in ('mute', 'channelVolumes')}
                self.run(['pw-cli', 'set-param', str(device['id']), 'Route', json.dumps({
                    'index': route['index'], 'device': original['device'], 'props': values, 'save': True})])
        original_default = snapshot['default_configured']
        if original_default:
            self.run(['pw-metadata', '-n', 'default', '0', 'default.configured.audio.sink',
                      original_default['value'], original_default['type']])
        else:
            self.run(['pw-metadata', '-n', 'default', '-d', '0', 'default.configured.audio.sink'])
        current = {o['id']: o for o in self.dump()}
        for stream in snapshot['streams']:
            if stream['id'] not in current or props(current[stream['id']]).get('object.serial') != stream['serial']:
                continue
            for key, value in stream['metadata'].items():
                if value:
                    restored = value['value']
                    target = find(list(current.values()), 'node.name', stream['target'])
                    if target and key == 'target.node' and restored == str(stream['target_id']):
                        restored = str(target['id'])
                    elif target and key == 'target.object' and restored == str(stream['target_serial']):
                        restored = str(props(target)['object.serial'])
                    self.run(['pw-metadata', '-n', 'default', str(stream['id']), key, restored, value['type']])
                else:
                    self.run(['pw-metadata', '-n', 'default', '-d', str(stream['id']), key])
        self.run(['alsactl', '-f', snapshot['alsa_state'], 'restore', str(props(device)['api.alsa.card'])])
