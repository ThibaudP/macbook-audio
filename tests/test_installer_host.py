# SPDX-License-Identifier: GPL-3.0-only
"""Check metadata restoration across changing PipeWire object/card IDs."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from installer_host import DEVICE, Host


class StubHost(Host):
    def __init__(self, objects):
        self.objects, self.commands = objects, []

    def dump(self):
        return self.objects

    def run(self, args, check=True):
        self.commands.append([str(x) for x in args])
        return ''


class HostTests(unittest.TestCase):
    def test_metadata_uses_structured_values_including_quotes(self):
        host = StubHost([{'type': 'PipeWire:Interface:Metadata', 'props': {'metadata.name': 'default'},
                          'metadata': [{'subject': 0, 'key': 'default.configured.audio.sink',
                                        'type': 'Spa:String:JSON', 'value': {'name': "speaker's output"}},
                                       {'subject': 7, 'key': 'target.node', 'type': 'Spa:Id', 'value': 55}]}])
        metadata = host.metadata()
        self.assertIn("speaker's output", metadata[(0, 'default.configured.audio.sink')]['value'])
        self.assertEqual(metadata[(7, 'target.node')], {'value': '55', 'type': 'Spa:Id'})

    def test_restore_resolves_new_numeric_targets_and_current_alsa_index(self):
        profile = 'output:analog-stereo+input:analog-stereo'
        host = StubHost([
            {'id': 20, 'info': {'props': {'device.name': DEVICE, 'api.alsa.card': 7}, 'params': {
                'EnumProfile': [{'index': 2, 'name': profile}], 'Profile': [{'name': profile}],
                'EnumRoute': [{'index': 40, 'name': 'analog-output-speaker'}]}}},
            {'id': 99, 'info': {'props': {'node.name': 'original-speakers', 'object.serial': 9999}}},
            {'id': 5, 'info': {'props': {'object.serial': 55}}}])
        snapshot = {'profile': profile, 'card': 0, 'alsa_state': '/private/alsa.state',
                    'routes': [{'index': 3, 'device': 0, 'name': 'analog-output-speaker',
                                'props': {'mute': True, 'channelVolumes': [.2, .3]}}],
                    'default_configured': None, 'streams': [{
                        'id': 5, 'serial': 55, 'target': 'original-speakers', 'target_id': 1,
                        'target_serial': 2, 'metadata': {
                            'target.node': {'value': '1', 'type': 'Spa:Id'},
                            'target.object': {'value': '2', 'type': 'Spa:Id'}}}]}
        with patch('installer_host.time.sleep'):
            host.restore(snapshot)
        self.assertIn(['pw-metadata', '-n', 'default', '5', 'target.node', '99', 'Spa:Id'], host.commands)
        self.assertIn(['pw-metadata', '-n', 'default', '5', 'target.object', '9999', 'Spa:Id'], host.commands)
        self.assertIn(['alsactl', '-f', '/private/alsa.state', 'restore', '7'], host.commands)
        self.assertIn(['pw-metadata', '-n', 'default', '-d', '0', 'default.configured.audio.sink'], host.commands)

    def test_restore_leaves_a_reused_stream_id_alone(self):
        # Numeric IDs can be reused after an application exits. A mismatched
        # object serial must prevent changing the new application's target.
        host = StubHost([
            {'id': 20, 'info': {'props': {'device.name': DEVICE, 'api.alsa.card': 0}, 'params': {
                'EnumProfile': [{'index': 2, 'name': 'original'}], 'Profile': [{'name': 'original'}]}}},
            {'id': 5, 'info': {'props': {'object.serial': 999}}}])
        snapshot = {'profile': 'original', 'card': 0, 'alsa_state': '/private/alsa.state', 'routes': [],
                    'default_configured': None, 'streams': [{'id': 5, 'serial': 55,
                        'metadata': {'target.object': {'value': 'old', 'type': 'Spa:String'}}}]}
        with patch('installer_host.time.sleep'):
            host.restore(snapshot)
        self.assertFalse(any(command[:4] == ['pw-metadata', '-n', 'default', '5'] for command in host.commands))


if __name__ == '__main__':
    unittest.main()
