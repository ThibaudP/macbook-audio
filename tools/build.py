#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Build a local LV2 bundle; never write into the live audio installation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
URI = 'urn:macbook-audio:apple-stack-profile0'


def metadata(bundle):
    (bundle / 'manifest.ttl').write_text(
        '@prefix lv2: <http://lv2plug.in/ns/lv2core#> .\n'
        '@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n'
        f'<{URI}> a lv2:Plugin ; lv2:binary <macbook-apple-stack.so> ; '
        'rdfs:seeAlso <plugin.ttl> .\n')
    symbols = ['in_l', 'in_r', 'tweeter_l', 'tweeter_r', 'bass_l', 'bass_r']
    symbols += [f'stage_{i}' for i in range(14)]
    symbols += ['volume_l', 'volume_r', 'enabled', 'headphones', 'peak_tl',
                'peak_tr', 'peak_bl', 'peak_br', 'clipped', 'latency',
                'common_gain', 'low_gain', 'high_gain']
    ports = []
    for index, symbol in enumerate(symbols):
        direction = 'InputPort' if index < 2 or 6 <= index <= 23 else 'OutputPort'
        kind = 'AudioPort' if index < 6 else 'ControlPort'
        port = (f'[ a lv2:{direction}, lv2:{kind} ; lv2:index {index} ; '
                f'lv2:symbol "{symbol}" ; lv2:name "{symbol}"')
        if 6 <= index <= 23:
            port += ' ; lv2:default ' + ('0' if index == 23 else '1')
        if 6 <= index <= 19 or index in (22, 23):
            port += ' ; lv2:portProperty lv2:toggled ; lv2:minimum 0 ; lv2:maximum 1'
        if index in (20, 21):
            port += ' ; lv2:minimum 0 ; lv2:maximum 4'
        if index == 29:
            port += ' ; lv2:designation lv2:latency ; lv2:portProperty lv2:reportsLatency, lv2:integer'
        ports.append(port + ' ]')
    (bundle / 'plugin.ttl').write_text(
        '@prefix lv2: <http://lv2plug.in/ns/lv2core#> .\n'
        '@prefix doap: <http://usefulinc.com/ns/doap#> .\n'
        f'<{URI}> a lv2:Plugin ; doap:name "MacBook Audio, profile 0 at 48 kHz" ;\n'
        'lv2:optionalFeature lv2:hardRTCapable ; lv2:port ' + ',\n'.join(ports) + ' .\n')


def compiler_command(requested):
    if requested:
        return shlex.split(requested)
    for name in ('cc', 'gcc', 'clang', 'tcc'):
        if shutil.which(name):
            return [name]
    # Existing development machine: preserve its isolated compiler, untracked.
    local = ROOT / '.private/workshop/tools/tcc/usr/bin/tcc'
    if local.exists():
        return [str(local)]
    raise SystemExit('No C compiler found. Install a C toolchain or set CC.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    parser.add_argument('--output', type=Path, default=ROOT / 'build')
    parser.add_argument('--cc', default=os.environ.get('CC'))
    args = parser.parse_args()
    data = args.data_dir.resolve()
    for name in ('compressor-data.h', 'stack-data.h'):
        if not (data / name).is_file():
            raise SystemExit(f'Missing {data / name}. See data/README.md; no Apple data is downloaded.')
    compiler = compiler_command(args.cc)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    target = output / 'macbook-apple-stack.lv2'
    flags = ['-std=c99', '-fPIC', '-I' + str(data), '-I' + str(ROOT / 'third_party/lv2')]
    source = ROOT / 'src/stack-lv2.c'
    with tempfile.TemporaryDirectory(prefix='.macbook-build-', dir=output) as temporary:
        staging = Path(temporary)
        library = staging / 'macbook-apple-stack.so'
        if Path(compiler[-1]).name == 'tcc':
            local_lib = Path(compiler[-1]).resolve().parents[1] / 'lib/x86_64-linux-gnu/tcc'
            if local_lib.is_dir():
                flags += ['-B' + str(local_lib), '-I/usr/include/x86_64-linux-gnu']
            obj = staging / 'stack-lv2.o'
            subprocess.run(compiler + flags + ['-c', str(source), '-o', str(obj)], check=True)
            subprocess.run(['ld', '-shared', '-z', 'noexecstack', '-z', 'relro', '-z', 'now',
                            '-z', 'defs', '-o', str(library), str(obj), '-lm', '-lc'], check=True)
        else:
            subprocess.run(compiler + flags + ['-O2', '-fno-fast-math', '-ffp-contract=off', '-fno-strict-aliasing', '-shared',
                            '-Wl,-z,noexecstack,-z,relro,-z,now,-z,defs',
                            str(source), '-o', str(library), '-lm'], check=True)
        metadata(staging)
        # Replace individual build artifacts only after compilation succeeds.
        target.mkdir(parents=True, exist_ok=True)
        for name in ('macbook-apple-stack.so', 'manifest.ttl', 'plugin.ttl'):
            (staging / name).replace(target / name)
    inputs = [*sorted((ROOT / 'src').glob('*.c')), ROOT / 'third_party/lv2/lv2.h',
              data / 'compressor-data.h', data / 'stack-data.h']
    report = {'sample_rate': 48000, 'profile': 0,
              'library_sha256': hashlib.sha256((target / 'macbook-apple-stack.so').read_bytes()).hexdigest(),
              'inputs': {str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else p.name:
                         hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}}
    (output / 'build-info.json').write_text(json.dumps(report, indent=2) + '\n')
    print(target)


if __name__ == '__main__':
    main()
