#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Start staged files on private PipeWire/WirePlumber with a null audio output.

No real ALSA monitor, systemd service, desktop bus or installed file is used.
The route controller receives a synthetic Device record; its audio nodes and
plugin controls are real. File transaction/recovery tests are in test_installer.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from installer_core import Paths, artifacts, atomic
from installer_host import HARDWARE, SINK


def wait(function, description, seconds=12):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        result = function()
        if result:
            return result
        time.sleep(.1)
    raise AssertionError(description)


def check(bundle, work):
    paths = Paths(work / 'user', work / 'config', work / 'data', work / 'state')
    runtime = work / 'run'
    runtime.mkdir(mode=0o700)
    env = dict(os.environ, XDG_CONFIG_HOME=str(paths.config), XDG_DATA_HOME=str(paths.data),
               XDG_STATE_HOME=str(paths.state), XDG_RUNTIME_DIR=str(runtime),
               PIPEWIRE_RUNTIME_DIR=str(runtime), PIPEWIRE_REMOTE='macbook-test',
               LV2_PATH=str(paths.home / '.lv2'), DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(work / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(work / 'no-system-bus'))
    entries = artifacts(ROOT, bundle, paths, Path('/usr/share/alsa-card-profile/mixer'))
    for entry in entries:
        atomic(entry.target, entry.data, entry.mode)
    # Native policy only: hardware monitors are never loaded in this profile.
    wp_config = paths.config / 'wireplumber/wireplumber.conf.d/00-test.conf'
    atomic(wp_config, b'wireplumber.profiles = { policy = { support.dbus = disabled support.system-dbus = disabled } }\n')
    config = work / 'server.conf'
    config.write_text('''
context.properties = { core.daemon = true core.name = macbook-test default.clock.rate = 48000 }
context.spa-libs = { audio.convert.* = audioconvert/libspa-audioconvert support.* = support/libspa-support }
context.modules = [
 { name = libpipewire-module-protocol-native }
 { name = libpipewire-module-metadata }
 { name = libpipewire-module-spa-node-factory }
 { name = libpipewire-module-client-node }
 { name = libpipewire-module-access }
 { name = libpipewire-module-adapter }
 { name = libpipewire-module-link-factory }
]
context.objects = [
 { factory = spa-node-factory args = { factory.name = support.node.driver node.name = Test-Driver node.group = test priority.driver = 20000 } }
 { factory = adapter args = { factory.name = support.null-audio-sink node.name = "''' + HARDWARE + '''" media.class = Audio/Sink audio.channels = 4 audio.position = [ FL FR RL RR ] node.virtual = true audio.rate = 48000 } }
]
''')
    processes, logs = [], []

    def start(name, args, process_env=env):
        log = (work / (name + '.log')).open('wb')
        logs.append(log)
        process = subprocess.Popen(args, env=process_env, stdout=log, stderr=log, start_new_session=True)
        processes.append(process)
        return process

    def run(args):
        result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=8)
        if result.returncode:
            raise RuntimeError(result.stderr or result.stdout)
        return result.stdout

    def dump():
        return json.loads(run(['/usr/bin/pw-dump']))

    def node(name):
        return next((o for o in dump() if o.get('info', {}).get('props', {}).get('node.name') == name), None)

    try:
        server = start('server', ['pipewire', '-c', str(config)])
        wait(lambda: (runtime / 'macbook-test').exists(), 'Private PipeWire did not start')
        policy = start('policy', ['wireplumber', '-p', 'policy'])
        hardware = wait(lambda: node(HARDWARE), 'Null output missing')
        wait(lambda: any(o.get('props', {}).get('metadata.name') == 'default' for o in dump()),
             'Private WirePlumber policy did not start')
        host = start('dsp', ['pipewire', '-c', str(paths.assets / 'stack.conf')])
        sink = wait(lambda: node(SINK), 'Staged DSP did not load')
        serial = sink['info']['props']['object.serial']
        run(['wpctl', 'set-default', str(sink['id'])])
        run(['wpctl', 'set-volume', str(sink['id']), '.5'])
        # A narrow pw-dump shim supplies the absent ALSA Device to the unmodified
        # controller. Real nodes and parameters are obtained from the private server.
        shim = work / 'shim'
        shim.mkdir()
        device = {'id': 900000, 'type': 'PipeWire:Interface:Device', 'info': {'props': {
            'device.name': 'alsa_card.pci-0000_00_1b.0'}, 'params': {'Route': [{
                'index': 0, 'name': 'macbook-output-speaker', 'direction': 'Output', 'device': 9,
                'props': {'volumeBase': 1., 'channelVolumes': [1., 1., 1., 1.], 'mute': False}}]}}}
        shim_dump = '''#!/usr/bin/python3
import codecs,json,subprocess,sys
device=DEVICE
hardware=HARDWARE
def transform(objects):
    for obj in objects:
        info=obj.get('info') or {};props=info.get('props',{})
        if props.get('node.name')==hardware:props['card.profile.device']=9
    return objects
if '--monitor' not in sys.argv:
    objects=json.loads(subprocess.check_output(['/usr/bin/pw-dump']))
    print(json.dumps([device]+transform(objects)))
else:
    print(json.dumps([device]),flush=True)
    process=subprocess.Popen(['/usr/bin/pw-dump','--monitor'],stdout=subprocess.PIPE)
    decoder=json.JSONDecoder();utf8=codecs.getincrementaldecoder('utf-8')();buffer=''
    while True:
        chunk=process.stdout.read1(65536)
        if not chunk:break
        buffer+=utf8.decode(chunk)
        while buffer.strip():
            buffer=buffer.lstrip()
            try:objects,end=decoder.raw_decode(buffer)
            except json.JSONDecodeError:break
            buffer=buffer[end:]
            print(json.dumps(transform(objects)),flush=True)
'''.replace('device=DEVICE', 'device=' + repr(device)).replace('hardware=HARDWARE', 'hardware=' + repr(HARDWARE))
        atomic(shim / 'pw-dump', shim_dump.encode(), 0o755)
        atomic(shim / 'pw-cli', b'''#!/usr/bin/python3
import os,sys
if len(sys.argv)>2 and sys.argv[1:3]==['set-param','900000']:sys.exit(0)
os.execv('/usr/bin/pw-cli',['/usr/bin/pw-cli',*sys.argv[1:]])
''', 0o755)
        control_env = dict(env, PATH=str(shim) + ':' + env['PATH'])
        controller = start('controller', ['/usr/bin/python3', str(paths.assets / 'stack-controller.py'), '--daemon'], control_env)
        status = paths.state / 'macbook-audio/apple-integration/stack-status.json'
        wait(lambda: status.exists() and json.loads(status.read_text()).get('sink_serial') == serial,
             'Staged controller did not become ready')
        # Silence is enough to exercise actual native links without audible audio.
        samples = work / 'silence.f32'
        samples.write_bytes(b'\0' * 48000 * 2 * 4)
        player = start('player', ['pw-cat', '-p', '--target', SINK, '--rate', '48000', '--channels', '2',
                                 '--format', 'f32', '--raw', str(samples)])
        playback = wait(lambda: node(SINK + '_playback'), 'DSP playback missing')
        wait(lambda: any(o.get('type') == 'PipeWire:Interface:Link' and
                         o.get('info', {}).get('output-node-id') == playback['id'] and
                         o.get('info', {}).get('input-node-id') == hardware['id'] for o in dump()),
             'Staged DSP did not link to its four-channel output')
        # Exercise the actual installed CLI and controller without replacing the sink.
        run(['/usr/bin/python3', str(paths.assets / 'stack-controller.py'), 'stage', '3', 'off'])
        wait(lambda: json.loads(status.read_text())['stages']['3'] is False, 'Live stage switch did not apply')
        assert node(SINK)['info']['props']['object.serial'] == serial
        assert all(p.poll() is None for p in (server, policy, host, controller))
        assert player.wait(timeout=5) == 0
        # Unit parsing is checked without asking the real service manager to start it.
        units = list(paths.units.glob('*.service'))
        verification = subprocess.run(['systemd-analyze', '--user', 'verify', *map(str, units)],
                                      env=env, capture_output=True, text=True, timeout=10)
        assert verification.returncode == 0, verification.stderr
        return {'passed': True, 'staged_plugin_and_controller_started': True,
                'four_channel_native_link': True, 'live_stage_keeps_sink': True,
                'generated_systemd_units_parse': True, 'physical_alsa_used': False,
                'real_systemd_services_used': False}
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)
        for log in logs:
            log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, default=ROOT / 'build/macbook-apple-stack.lv2')
    args = parser.parse_args()
    # Keep failure logs for inspection; all destinations and sockets stay here.
    work = Path(tempfile.mkdtemp(prefix='macbook-install-test-'))
    try:
        result = check(args.bundle.resolve(), work)
    except BaseException:
        print(f'Isolated test logs: {work}', file=sys.stderr)
        raise
    destination = ROOT / 'build/reports/staged-runtime.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2) + '\n')
    shutil.rmtree(work)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
