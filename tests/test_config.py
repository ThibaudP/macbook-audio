# SPDX-License-Identifier: GPL-3.0-only
"""Check the routing and volume invariants without contacting PipeWire."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import stack_config as config


class ConfigTests(unittest.TestCase):
    def test_volume_is_owned_once_by_the_dsp(self):
        initial = {'enabled': True, 'stages': {str(i): True for i in range(14)}}
        chain = config.chain_config(initial=initial)
        args = chain['context.modules'][-1]['args']
        capture = args['capture.props']
        self.assertEqual(capture['channelmix.min-volume'], 1.0)
        self.assertEqual(capture['channelmix.max-volume'], 1.0)
        self.assertEqual([x['control'] for x in args['filter.graph']['capture.volumes']],
                         ['apple:volume_l', 'apple:volume_r'])
        self.assertEqual(args['playback.props']['audio.position'], ['FL', 'FR', 'RL', 'RR'])
        self.assertEqual(len(args['filter.graph']['outputs']), 4)
        self.assertEqual(args['audio.rate'], 48000)

    def test_controls_persist_without_resetting_other_stages(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            path.write_text(json.dumps({'enabled': True, 'stages': {'3': False}}))
            with patch.object(config, 'SETTINGS', path):
                config.update(stage=(1, False))
                config.update({'enabled': False})
                saved = config.settings()
                self.assertFalse(saved['stages']['3'])
                self.assertFalse(saved['stages']['1'])
                self.assertFalse(saved['enabled'])
                self.assertFalse(saved['first_compressor'])
                params = dict(zip(config.params(saved, headphones=True)[::2],
                                  config.params(saved, headphones=True)[1::2]))
                self.assertEqual(params['apple:headphones'], 1.0)
                self.assertEqual(params['apple:stage_3'], 0.0)
                self.assertEqual(params['apple:stage_4'], 1.0)


if __name__ == '__main__':
    unittest.main()
