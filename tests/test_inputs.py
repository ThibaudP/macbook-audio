# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/research'))
from macho import MachO
from apple_profile import coefficients, read_layout


def synthetic_macho():
    # One external branch relocation, a local setter and an imported symbol.
    names = b'\0__ZN23DspFuncMozartCompressor10setParam02E8DspValue\0_expf\0'
    symoff, stroff, reloff = 32 + 24 + 80, 32 + 24 + 80 + 32, 256
    binary = bytearray(320)
    struct.pack_into('<8I', binary, 0, 0xFEEDFACF, 0x01000007, 3, 11, 2, 104, 0, 0)
    struct.pack_into('<6I', binary, 32, 2, 24, symoff, 2, stroff, len(names))
    values = [11, 80] + [0] * 18
    values[16:18] = [reloff, 1]
    struct.pack_into('<20I', binary, 56, *values)
    struct.pack_into('<IBBHQ', binary, symoff, 1, 0xF, 1, 0, 280)
    struct.pack_into('<IBBHQ', binary, symoff + 16, names.index(b'_expf'), 1, 0, 0, 0)
    binary[stroff:stroff + len(names)] = names
    struct.pack_into('<II', binary, reloff, 288, 1 | (1 << 24) | (2 << 25) | (1 << 27) | (2 << 28))
    return binary


class InputTests(unittest.TestCase):
    def test_external_relocations_are_decoded_without_llvm(self):
        binary = MachO(synthetic_macho())
        self.assertEqual(binary.relocations, [{'address': 288, 'symbol': '_expf',
                         'pcrel': True, 'length': 4, 'type': 2}])

    def test_truncated_or_invalid_symbols_fail_closed(self):
        original = synthetic_macho()
        for size in (0, 31, 90, 150, 259):
            with self.assertRaises(ValueError):
                MachO(original[:size])
        struct.pack_into('<I', original, 32 + 24 + 80, 999999)
        with self.assertRaises(ValueError):
            MachO(original)

    def test_other_layouts_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'fingerprint'):
            read_layout(b'<plist version="1.0"><dict/></plist>')

    def test_bypass_and_out_of_range_filter_are_identity(self):
        identity = [1., 0., 0., 1., 0., 0.]
        self.assertEqual(coefficients(4, 1000, 1, 6, bypass=True), identity)
        self.assertEqual(coefficients(4, 24000, 1, 6), identity)
        result = coefficients(4, 1000, 1, 0)
        self.assertAlmostEqual(result[0], 1.)
        self.assertEqual(result[1:3], result[4:6])


if __name__ == '__main__':
    unittest.main()
