# SPDX-License-Identifier: GPL-3.0-only
"""File staging and recoverable user installation, independent of the live host."""
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import time


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, data, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def save(path, value):
    atomic(path, (json.dumps(value, indent=2) + '\n').encode(), 0o600)


@dataclass
class Paths:
    home: Path
    config: Path
    data: Path
    state: Path

    @classmethod
    def current(cls):
        home = Path.home()
        result = cls(home, Path(os.environ.get('XDG_CONFIG_HOME', home / '.config')),
                     Path(os.environ.get('XDG_DATA_HOME', home / '.local/share')),
                     Path(os.environ.get('XDG_STATE_HOME', home / '.local/state')))
        for path in (result.home, result.config, result.data, result.state):
            if not path.is_absolute():
                raise ValueError('XDG paths must be absolute')
        return result

    @property
    def assets(self):
        return self.data / 'macbook-apple-audio'

    @property
    def settings(self):
        return self.config / 'macbook-apple-audio/settings.json'

    @property
    def units(self):
        return self.config / 'systemd/user'

    @property
    def manifest(self):
        return self.state / 'macbook-audio/install/manifest.json'


@dataclass
class Artifact:
    target: Path
    data: bytes
    mode: int = 0o644
    persistent: bool = False


def systemd_quote(value):
    if any(c in str(value) for c in '\n\r\0'):
        raise ValueError('Control characters are unsupported in installation paths')
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def edit_section(text, header, changes):
    pattern = re.compile(r'(?ms)^' + re.escape(header) + r'\n.*?(?=^\[|\Z)')
    match = pattern.search(text)
    if not match:
        raise ValueError(f'Unsupported ACP template: missing {header}')
    block = match[0]
    for key, value in changes.items():
        entry = re.compile(r'(?m)^' + re.escape(key) + r'\s*=.*$')
        if entry.search(block):
            block = entry.sub(lambda _: f'{key} = {value}', block)
        else:
            block = block.rstrip() + f'\n{key} = {value}\n\n'
    return text[:match.start()] + block + text[match.end():]


def artifacts(repo, bundle, paths, acp_root):
    repo, bundle, acp_root = Path(repo), Path(bundle), Path(acp_root)
    if any(c.isspace() or c in '\\"' for c in str(paths.assets)):
        raise ValueError('ACP mixer paths cannot contain whitespace, quotes or backslashes; use a plain XDG_DATA_HOME')
    module_path = repo / 'app/stack_config.py'
    spec = importlib.util.spec_from_file_location('macbook_stage_config', module_path)
    config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config)
    settings = {'backend': 'stack', 'enabled': True, 'profile': 0,
                'stages': {str(i): True for i in range(14)}}
    if paths.settings.is_file():
        saved = json.loads(paths.settings.read_text())
        if not isinstance(saved, dict) or not isinstance(saved.get('stages', {}), dict):
            raise ValueError('Existing settings are invalid')
        settings.update(saved)
        settings['backend'] = 'stack'
    chain = config.chain_config(initial=settings)
    entries = []

    def add(target, value, mode=0o644, persistent=False):
        entries.append(Artifact(target, value.encode() if isinstance(value, str) else value, mode, persistent))

    for name in ('stack-controller.py', 'stack-panel.py', 'stack_config.py'):
        add(paths.assets / name, (repo / 'app' / name).read_bytes())
    for source, name in [('install.py', 'setup.py'), ('installer_core.py', 'installer_core.py'),
                         ('installer_host.py', 'installer_host.py')]:
        add(paths.assets / name, (repo / 'tools' / source).read_bytes())
    add(paths.assets / 'LICENSE', (repo / 'LICENSE').read_bytes())
    add(paths.assets / 'LV2-LICENSE', (repo / 'third_party/lv2/LICENSE').read_bytes())
    for name in ('macbook-apple-stack.so', 'manifest.ttl', 'plugin.ttl'):
        add(paths.home / '.lv2/macbook-apple-stack.lv2' / name, (bundle / name).read_bytes())
    add(paths.assets / 'stack.conf', config.spa(chain)[2:-2] + '\n')
    add(paths.settings, json.dumps(settings, indent=2) + '\n', 0o600, True)

    profile = (acp_root / 'profile-sets/default.conf').read_text()
    custom_path = paths.assets / 'macbook-output-speaker.conf'
    profile = edit_section(profile, '[Mapping analog-surround-40]',
                           {'paths-output': str(custom_path.with_suffix('')) + ' analog-output-headphones'})
    speaker = (acp_root / 'paths/analog-output-speaker.conf').read_text()
    speaker = edit_section(speaker, '[General]', {'type': 'speaker'})
    for control, channels in [('Speaker', 'front-left,front-right'), ('Bass Speaker', 'rear-left,rear-right')]:
        speaker = edit_section(speaker, f'[Element {control}]', {'override-map.1': 'all', 'override-map.2': channels})
    common = acp_root / 'paths/analog-output.conf.common'
    if not common.is_file():
        raise ValueError('Missing ACP common mixer path')
    speaker = speaker.replace('.include analog-output.conf.common', '.include ' + str(common))
    add(paths.assets / 'mbp102.conf', profile)
    add(custom_path, speaker)
    rule = {'monitor.alsa.rules': [
        {'matches': [{'device.name': config.DEVICE}], 'actions': {'update-props': {
            'api.alsa.use-acp': True, 'api.alsa.use-ucm': False,
            'device.profile-set': str(paths.assets / 'mbp102.conf')}}},
        {'matches': [{'node.name': config.HARDWARE}], 'actions': {'update-props': {
            'audio.rate': 48000, 'audio.allowed-rates': [48000], 'channelmix.upmix': False}}}]}
    add(paths.config / 'wireplumber/wireplumber.conf.d/65-macbook-apple-audio.conf', config.spa(rule)[2:-2] + '\n')
    environment = '\n'.join('Environment=' + systemd_quote(f'{k}={v}') for k, v in {
        'XDG_CONFIG_HOME': paths.config, 'XDG_DATA_HOME': paths.data, 'XDG_STATE_HOME': paths.state,
        'LV2_PATH': str(paths.home / '.lv2') + ':/usr/local/lib/lv2:/usr/lib/lv2'}.items())
    for name in ('stack', 'audio'):
        unit = (repo / 'config/systemd' / f'macbook-apple-{name}.service').read_text()
        if name == 'stack':
            command = '/usr/bin/pipewire -c ' + systemd_quote(paths.assets / 'stack.conf')
            prefix = 'ExecStartPre=/usr/bin/python3 ' + systemd_quote(paths.assets / 'setup.py') + ' ensure-profile\n'
        else:
            command = '/usr/bin/python3 ' + systemd_quote(paths.assets / 'stack-controller.py') + ' --daemon'
            prefix = ''
        unit = re.sub(r'(?m)^ExecStart=.*$', lambda _: environment + '\n' + prefix + 'ExecStart=' + command, unit)
        add(paths.units / f'macbook-apple-{name}.service', unit)
    for name, script in [('macbook-audio', 'stack-panel.py'), ('macbook-eq', 'stack-controller.py'),
                         ('macbook-audio-setup', 'setup.py')]:
        add(paths.home / '.local/bin' / name,
            '#!/usr/bin/python3\nimport os,sys\n' +
            f'os.execv(sys.executable,[sys.executable,{str(paths.assets / script)!r},*sys.argv[1:]])\n', 0o755)
    launcher = str(paths.home / '.local/bin/macbook-audio').replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    add(paths.data / 'applications/macbook-audio.desktop',
        '[Desktop Entry]\nType=Application\nName=MacBook Audio\nComment=Control the speaker processing stages\n'
        f'Exec="{launcher}"\nIcon=audio-speakers-symbolic\nTerminal=false\nCategories=AudioVideo;Audio;Settings;\n')
    return entries


def fingerprint(path):
    path = Path(path)
    if path.is_symlink():
        return {'link': os.readlink(path)}
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError(f'Expected a file: {path}')
    return {'sha256': digest(path.read_bytes()), 'mode': stat.S_IMODE(path.stat().st_mode)}


@contextmanager
def locked(manifest):
    manifest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (manifest.parent / '.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


class Installation:
    def __init__(self, manifest, host):
        self.manifest, self.host = Path(manifest), host

    def install(self, entries, paths):
        with locked(self.manifest):
            if self.manifest.exists() and json.loads(self.manifest.read_text())['phase'] != 'uninstalled':
                raise ValueError('An installation transaction already exists; use uninstall or recover first')
            conflicts = [str(e.target) for e in entries if not e.persistent and os.path.lexists(e.target)]
            if conflicts:
                raise ValueError('Existing installation/files would be overwritten; fresh install only:\n' + '\n'.join(conflicts))
            self.host.preflight()
            backup = self.manifest.parent / ('backup-' + str(time.time_ns()))
            backup.mkdir(mode=0o700)
            snapshot = self.host.snapshot(backup)
            recorded = []
            for index, entry in enumerate(entries):
                before = fingerprint(entry.target)
                if before and 'sha256' in before:
                    shutil.copy2(entry.target, backup / str(index))
                recorded.append({'target': str(entry.target), 'before': before,
                                 'after': {'sha256': digest(entry.data), 'mode': entry.mode},
                                 'persistent': entry.persistent, 'backup': str(backup / str(index))})
            # Recovery code is independent of both checkout and files being installed.
            for name in ('install.py', 'installer_core.py', 'installer_host.py'):
                source = Path(__file__).with_name(name)
                if not source.exists() and name == 'install.py':
                    source = Path(__file__).with_name('setup.py')
                shutil.copy2(source, backup / name)
            manifest = {'schema': 1, 'phase': 'installing', 'committed': False, 'backup': str(backup),
                        'files': recorded, 'snapshot': snapshot,
                        'timer': 'macbook-audio-recovery-' + str(time.time_ns())}
            save(self.manifest, manifest)
            try:
                self.host.arm(manifest, self.manifest)
                for entry in entries:
                    if not entry.persistent or not entry.target.exists():
                        atomic(entry.target, entry.data, entry.mode)
                self.host.activate(paths, snapshot)
                manifest['phase'] = 'installed'
                manifest['committed'] = True
                save(self.manifest, manifest)
            except BaseException:
                self._restore(manifest, failed_install=True)
                raise
            self._cancel(manifest)
            return manifest

    def _cancel(self, manifest):
        try:
            self.host.cancel(manifest)
        except Exception as error:
            # A committed/uninstalled manifest makes a late timer a no-op. Failure
            # to cancel it must not undo an otherwise successful transaction.
            manifest['cleanup_warning'] = str(error)
            save(self.manifest, manifest)

    def restore(self, force=False, recover=False):
        with locked(self.manifest):
            if not self.manifest.exists():
                return {'phase': 'uninstalled'}
            manifest = json.loads(self.manifest.read_text())
            if manifest['phase'] == 'uninstalled' or (recover and manifest['phase'] == 'installed'):
                return manifest
            modified = [e['target'] for e in manifest['files'] if not e['persistent'] and
                        fingerprint(e['target']) not in (e['before'], e['after'], None)]
            if modified and not force and manifest['phase'] == 'installed':
                raise ValueError('Installed files have changed; preserve them or use uninstall --force:\n' + '\n'.join(modified))
            manifest['phase'] = 'restoring'
            save(self.manifest, manifest)
            self.host.arm(manifest, self.manifest)
            return self._restore(manifest, failed_install=not manifest.get('committed', False))

    def _restore(self, manifest, failed_install):
        manifest['phase'] = 'restoring'
        save(self.manifest, manifest)
        try:
            self.host.quiesce()
            for index, entry in enumerate(manifest['files']):
                target, before = Path(entry['target']), entry['before']
                if entry['persistent'] and not failed_install:
                    continue
                current = fingerprint(target)
                if current and current not in (entry['after'], before):
                    changed = Path(manifest['backup']) / f'changed-{index}-{time.time_ns()}'
                    if 'link' in current:
                        changed.symlink_to(current['link'])
                    else:
                        shutil.copy2(target, changed)
                if before is None:
                    target.unlink(missing_ok=True)
                elif 'link' in before:
                    target.unlink(missing_ok=True)
                    target.symlink_to(before['link'])
                else:
                    atomic(target, Path(entry['backup']).read_bytes(), before['mode'])
            self.host.restore(manifest['snapshot'])
            manifest['phase'] = 'uninstalled'
            manifest.pop('recovery_error', None)
            save(self.manifest, manifest)
        except BaseException as error:
            manifest['recovery_error'] = str(error)
            save(self.manifest, manifest)
            raise RuntimeError(f'Recovery incomplete. Retry: python3 {Path(manifest["backup"]) / "install.py"} recover --manifest {self.manifest}') from error
        self._cancel(manifest)
        return manifest
