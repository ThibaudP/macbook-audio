#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Exercise the built LV2 ABI offline, with no audio device or Apple binary."""
import argparse
import ctypes as ct
import json
import math
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def check(library):
    native = ct.CDLL(str(library))

    class Descriptor(ct.Structure):
        pass

    Create = ct.CFUNCTYPE(ct.c_void_p, ct.POINTER(Descriptor), ct.c_double, ct.c_char_p, ct.c_void_p)
    Connect = ct.CFUNCTYPE(None, ct.c_void_p, ct.c_uint32, ct.c_void_p)
    Action = ct.CFUNCTYPE(None, ct.c_void_p)
    Run = ct.CFUNCTYPE(None, ct.c_void_p, ct.c_uint32)
    Descriptor._fields_ = [('uri', ct.c_char_p), ('create', Create), ('connect', Connect),
                          ('activate', Action), ('run', Run), ('deactivate', ct.c_void_p),
                          ('cleanup', Action), ('extension', ct.c_void_p)]
    native.lv2_descriptor.argtypes = [ct.c_uint32]
    native.lv2_descriptor.restype = ct.POINTER(Descriptor)
    pointer = native.lv2_descriptor(0)
    descriptor = pointer.contents
    assert descriptor.uri == b'urn:macbook-audio:apple-stack-profile0'
    assert not native.lv2_descriptor(1)
    assert not descriptor.create(pointer, 44100, b'', None)
    assert not descriptor.create(pointer, 96000, b'', None)
    handle = descriptor.create(pointer, 48000, b'', None)
    assert handle
    count = 4096
    arrays = [(ct.c_float * count)() for _ in range(6)]
    controls = [ct.c_float(1) for _ in range(27)]
    controls[17].value = 0  # headphones
    for index, array in enumerate(arrays):
        descriptor.connect(handle, index, array)
    for index, value in enumerate(controls):
        descriptor.connect(handle, index + 6, ct.byref(value))

    def run():
        descriptor.run(handle, count)
        assert all(math.isfinite(x) for a in arrays[2:] for x in a)

    try:
        for i in range(count):
            arrays[0][i] = 4 * math.sin(2 * math.pi * 90 * i / 48000) + 2 * math.sin(i * .417)
            arrays[1][i] = 3 * math.sin(i * .193)
        for _ in range(8):
            run()
            for channel in range(4):
                ceiling = .794328235 * (1.003530979 if channel == 1 else 1)
                assert max(abs(x) for x in arrays[channel + 2]) <= ceiling + 2e-6
        for stage in range(14):
            controls[stage].value = 0
            run()
            controls[stage].value = 1
            run()
        # Reset, then settle volume and bypass fades. Both output pairs carry stereo.
        descriptor.activate(handle)
        for i in range(count):
            arrays[0][i], arrays[1][i] = .2, -.3
        controls[16].value = 0  # master bypass
        controls[14].value, controls[15].value = .4, .7
        for _ in range(20):
            run()
        for channel, expected in enumerate((.08, -.21, .08, -.21)):
            assert abs(arrays[channel + 2][-1] - expected) < 2e-5
        controls[17].value = 1
        run()
        assert max(abs(x) for x in arrays[4]) == 0
        assert max(abs(x) for x in arrays[5]) == 0
        assert abs(arrays[2][-1] - .08) < 2e-5
        assert abs(arrays[3][-1] + .21) < 2e-5
    finally:
        descriptor.cleanup(handle)
    return {'passed': True, 'stages_toggled': 14, 'unsupported_rates_rejected': True,
            'overload_ceilings': True, 'volume_preserving_bypass': True, 'headphone_rear_silence': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plugin', type=Path,
                        default=ROOT / 'build/macbook-apple-stack.lv2/macbook-apple-stack.so')
    args = parser.parse_args()
    # Keep test telemetry away from the running control panel.
    with tempfile.TemporaryDirectory(prefix='macbook-audio-test-') as directory:
        os.environ['XDG_RUNTIME_DIR'] = directory
        result = check(args.plugin.resolve())
    report = ROOT / 'build/reports/plugin-check.json'
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
