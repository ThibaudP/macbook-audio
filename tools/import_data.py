#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Import locally generated headers into the ignored data directory."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='Directory containing both generated headers')
    parser.add_argument('--output', type=Path, default=ROOT / 'data')
    args = parser.parse_args()
    names = ('compressor-data.h', 'stack-data.h')
    for name in names:
        if not (args.source / name).is_file():
            parser.error(f'Missing {args.source / name}')
    args.output.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name in names:
        destination = args.output / name
        if (args.source / name).resolve() != destination.resolve():
            shutil.copy2(args.source / name, destination)
        hashes[name] = hashlib.sha256(destination.read_bytes()).hexdigest()
    (args.output / 'import-info.json').write_text(json.dumps(hashes, indent=2) + '\n')
    print('Imported two local headers. They remain excluded from Git.')


if __name__ == '__main__':
    main()
