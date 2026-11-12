# SPDX-License-Identifier: GPL-3.0-only
"""Exercise real file transactions against a temporary home and a fake audio host."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from installer_core import Artifact, Installation, Paths, artifacts, atomic


class FakeHost:
    def __init__(self, fail=None):
        self.fail = fail
        self.events = []
        self.running = False

    def event(self, name):
        self.events.append(name)
        if self.fail == name:
            raise RuntimeError('injected ' + name)

    def preflight(self):
        self.event('preflight')

    def snapshot(self, backup):
        self.event('snapshot')
        (backup / 'alsa.state').write_text('original mixer')
        return {'original': True}

    def arm(self, manifest, path):
        self.event('arm')

    def cancel(self, manifest):
        self.event('cancel')

    def activate(self, paths, snapshot):
        self.running = True
        self.event('activate')

    def quiesce(self):
        self.running = False
        self.event('quiesce')

    def restore(self, snapshot):
        assert snapshot == {'original': True}
        self.event('restore')


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        home = Path(self.temporary.name)
        self.paths = Paths(home, home / '.config', home / '.local/share', home / '.local/state')
        self.plugin = self.paths.assets / 'plugin.so'
        self.entries = [Artifact(self.plugin, b'candidate'),
                        Artifact(self.paths.settings, b'{"enabled":true}', 0o600, True)]
        self.host = FakeHost()
        self.installation = Installation(self.paths.manifest, self.host)

    def test_install_uninstall_and_idempotency(self):
        result = self.installation.install(self.entries, self.paths)
        self.assertEqual(result['phase'], 'installed')
        self.assertEqual(self.plugin.read_bytes(), b'candidate')
        self.assertTrue(self.host.running)
        self.paths.settings.write_text('{"enabled":false}')
        self.assertEqual(self.installation.restore()['phase'], 'uninstalled')
        self.assertFalse(self.plugin.exists())
        self.assertFalse(self.host.running)
        self.assertEqual(self.paths.settings.read_text(), '{"enabled":false}')
        before = list(self.host.events)
        self.installation.restore()
        self.assertEqual(self.host.events, before)

    def test_legacy_files_are_never_overwritten(self):
        atomic(self.plugin, b'legacy')
        with self.assertRaisesRegex(ValueError, 'fresh install only'):
            self.installation.install(self.entries, self.paths)
        self.assertEqual(self.plugin.read_bytes(), b'legacy')
        self.assertFalse(self.paths.manifest.exists())
        self.assertEqual(self.host.events, [])

    def test_preflight_failure_does_not_create_installation(self):
        self.host.fail = 'preflight'
        with self.assertRaisesRegex(RuntimeError, 'preflight'):
            self.installation.install(self.entries, self.paths)
        self.assertFalse(self.plugin.exists())
        self.assertFalse(self.paths.manifest.exists())

    def test_activation_failure_restores_original_settings(self):
        atomic(self.paths.settings, b'original')
        self.host.fail = 'activate'
        with self.assertRaisesRegex(RuntimeError, 'activate'):
            self.installation.install(self.entries, self.paths)
        self.assertFalse(self.plugin.exists())
        self.assertEqual(self.paths.settings.read_bytes(), b'original')
        self.assertEqual(json.loads(self.paths.manifest.read_text())['phase'], 'uninstalled')
        self.assertIn('restore', self.host.events)

    def test_failure_to_arm_timer_restores_files(self):
        self.host.fail = 'arm'
        with self.assertRaisesRegex(RuntimeError, 'arm'):
            self.installation.install(self.entries, self.paths)
        self.assertFalse(self.plugin.exists())
        self.assertFalse(self.paths.settings.exists())

    def test_uninstall_preserves_an_edited_file_before_forced_restore(self):
        self.installation.install(self.entries, self.paths)
        self.plugin.write_bytes(b'user edited')
        with self.assertRaisesRegex(ValueError, 'have changed'):
            self.installation.restore()
        self.assertEqual(self.plugin.read_bytes(), b'user edited')
        result = self.installation.restore(force=True)
        saved = list(Path(result['backup']).glob('changed-*'))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].read_bytes(), b'user edited')
        self.assertFalse(self.plugin.exists())

    def test_recover_does_not_undo_committed_installation(self):
        self.installation.install(self.entries, self.paths)
        before = list(self.host.events)
        self.installation.restore(recover=True)
        self.assertTrue(self.plugin.exists())
        self.assertEqual(self.host.events, before)

    def test_timer_cleanup_failure_does_not_undo_a_committed_install(self):
        self.host.fail = 'cancel'
        result = self.installation.install(self.entries, self.paths)
        self.assertEqual(result['phase'], 'installed')
        self.assertTrue(self.plugin.exists())
        self.assertTrue(self.host.running)
        self.assertIn('cleanup_warning', result)

    def test_recover_after_interrupted_first_install_removes_new_settings(self):
        self.installation.install(self.entries, self.paths)
        manifest = json.loads(self.paths.manifest.read_text())
        manifest.update(phase='installing', committed=False)
        self.paths.manifest.write_text(json.dumps(manifest))
        self.assertEqual(self.installation.restore(recover=True)['phase'], 'uninstalled')
        self.assertFalse(self.paths.settings.exists())
        self.assertFalse(self.plugin.exists())

    def test_failed_recovery_can_be_retried(self):
        self.installation.install(self.entries, self.paths)
        self.host.fail = 'restore'
        with self.assertRaisesRegex(RuntimeError, 'Recovery incomplete'):
            self.installation.restore()
        self.assertEqual(json.loads(self.paths.manifest.read_text())['phase'], 'restoring')
        self.host.fail = None
        recovered = self.installation.restore(recover=True)
        self.assertEqual(recovered['phase'], 'uninstalled')
        self.assertNotIn('recovery_error', recovered)

    def test_staging_has_correct_routes_and_no_legacy_inline_graph(self):
        bundle = self.paths.home / 'bundle'
        bundle.mkdir()
        for name in ('macbook-apple-stack.so', 'manifest.ttl', 'plugin.ttl'):
            (bundle / name).write_bytes(b'test input')
        acp = self.paths.home / 'acp'
        (acp / 'profile-sets').mkdir(parents=True)
        (acp / 'paths').mkdir()
        (acp / 'profile-sets/default.conf').write_text('[Mapping analog-surround-40]\npaths-output = original\n')
        (acp / 'paths/analog-output-speaker.conf').write_text(
            '# upstream licensing marker\n[General]\npriority = 100\n[Element Speaker]\n'
            'override-map.1 = all-left\noverride-map.2 = all-left,all-right\n[Element Bass Speaker]\n'
            'override-map.1 = lfe\noverride-map.2 = lfe,lfe\n.include analog-output.conf.common\n')
        (acp / 'paths/analog-output.conf.common').write_text('# common path\n')
        entries = artifacts(ROOT, bundle, self.paths, acp)
        by_name = {e.target.name: e for e in entries}
        speaker = by_name['macbook-output-speaker.conf'].data.decode()
        self.assertIn('override-map.2 = rear-left,rear-right', speaker)
        self.assertIn('override-map.2 = front-left,front-right', speaker)
        self.assertIn('# upstream licensing marker', speaker)
        rule = by_name['65-macbook-apple-audio.conf'].data.decode()
        self.assertNotIn('filter-graph', rule)
        self.assertIn('audio.allowed-rates', rule)
        self.assertIn('ensure-profile', by_name['macbook-apple-stack.service'].data.decode())
        self.assertNotIn('mute', by_name['stack.conf'].data.decode())


if __name__ == '__main__':
    unittest.main()
