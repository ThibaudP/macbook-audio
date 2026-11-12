#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Regenerate all private build/reference data from two pinned Apple input files."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/research'))
from apple_profile import DSP_SHA256, read_layout, select_profile
from macho import MachO


def impulses(selected, output):
    import numpy as np
    functions = {f['instance']: f for f in selected['variant']['functions']}
    for name, stages in [('common', [0]), ('tweeter-branch', [6, 8]), ('bass-branch', [7, 9])]:
        for channel in range(2):
            result = np.zeros(16384, dtype=np.float64)
            result[0] = 1
            for stage in stages:
                for filt in functions[stage]['filters']:
                    if filt['channel'] != channel or filt['bypass']:
                        continue
                    b0, b1, b2, _, a1, a2 = filt['sos_48000']
                    if max(abs(np.roots([1, a1, a2]))) >= 1:
                        raise ValueError('Unstable EQ section')
                    state1 = state2 = 0.
                    for i, value in enumerate(result):
                        y = b0 * value + state1
                        state1 = b1 * value - a1 * y + state2
                        state2 = b2 * value - a2 * y
                        result[i] = y
            payload = result.astype('<f4').tobytes()
            fmt = struct.pack('<HHIIHH', 3, 1, 48000, 192000, 4, 32)
            wav = (b'RIFF' + struct.pack('<I', 36 + len(payload)) + b'WAVEfmt '
                   + struct.pack('<I', 16) + fmt + b'data' + struct.pack('<I', len(payload)) + payload)
            (output / f'{name}-{channel}.wav').write_bytes(wav)


def prepare(dsp, layout, output):
    binary = dsp.read_bytes()
    if hashlib.sha256(binary).hexdigest() != DSP_SHA256:
        raise ValueError('Unsupported DspFuncLib fingerprint; see docs/extraction.md')
    parsed = MachO(binary)
    parsed.setters('DspFuncMozartCompressor')
    parsed.setters('DspFuncMozartCompressorDualBand')
    selected = select_profile(read_layout(layout.read_bytes()))
    # Import the documented Python packages before writing any output.
    import numpy
    import unicorn
    versions = {'numpy': numpy.__version__, 'unicorn': unicorn.__version__}
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    # Keep temporary Apple inputs under the same ignored data tree as the results.
    with tempfile.TemporaryDirectory(prefix='.macbook-data-', dir=output) as temporary:
        staging = Path(temporary)
        apple = staging / 'apple'
        (apple / 'original-layout59').mkdir(parents=True)
        (apple / 'DspFuncLib').write_bytes(binary)
        (apple / 'layout59.input').write_bytes(layout.read_bytes())
        (apple / 'original-layout59/selected-eq.json').write_text(json.dumps(selected, indent=2) + '\n')
        env = dict(os.environ, APPLE_INPUT_DIR=str(apple), MACBOOK_DATA_DIR=str(staging))
        for script in ('build-compressor-data.py', 'build-stack-data.py'):
            subprocess.run([sys.executable, str(ROOT / 'tools/research' / script)], env=env, check=True)
        impulses(selected, staging)
        files = sorted(p for p in staging.rglob('*') if p.is_file())
        report = {'schema': 1, 'profile': 0, 'sample_rate': 48000, 'python_packages': versions,
                  'files': {str(p.relative_to(staging)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
        # Publish completed results only; provenance is the final completion marker.
        output.mkdir(parents=True, exist_ok=True)
        for path in files:
            target = output / path.relative_to(staging)
            target.parent.mkdir(parents=True, exist_ok=True)
            path.replace(target)
        temporary_report = output / '.provenance.json.tmp'
        temporary_report.write_text(json.dumps(report, indent=2) + '\n')
        temporary_report.replace(output / 'provenance.json')
    print(f'Prepared private data in {output}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--applehda', type=Path, help='Local AppleHDA.kext bundle from the supported High Sierra build')
    parser.add_argument('--dsp', type=Path, help='Original DspFuncLib executable (alternative to --applehda)')
    parser.add_argument('--layout', type=Path, help='Original layout59.xml.zlib or decompressed XML')
    parser.add_argument('--output', type=Path, default=ROOT / 'data')
    args = parser.parse_args()
    if args.applehda:
        if args.dsp or args.layout:
            parser.error('Choose --applehda or the --dsp/--layout pair')
        args.dsp = args.applehda / 'Contents/PlugIns/DspFuncLib.kext/Contents/MacOS/DspFuncLib'
        args.layout = args.applehda / 'Contents/Resources/layout59.xml.zlib'
    if not args.dsp or not args.layout:
        parser.error('Provide --applehda, or both --dsp and --layout')
    try:
        prepare(args.dsp, args.layout, args.output)
    except (OSError, ValueError, ImportError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
