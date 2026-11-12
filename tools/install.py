#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Stage, install or restore MacBook Audio for the current desktop user."""
import argparse
import json
import os
from pathlib import Path
import sys

from installer_core import Installation, Paths, artifacts, atomic, digest, save
from installer_host import Host


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    repo = Path(__file__).resolve().parents[1]
    paths = Paths.current()
    for name in ('plan', 'stage', 'install'):
        sub = commands.add_parser(name)
        sub.add_argument('--bundle', type=Path, default=repo / 'build/macbook-apple-stack.lv2')
        sub.add_argument('--acp-root', type=Path, default=Path('/usr/share/alsa-card-profile/mixer'))
        if name == 'stage':
            sub.add_argument('--output', type=Path, required=True)
    for name in ('uninstall', 'recover', 'status'):
        sub = commands.add_parser(name)
        sub.add_argument('--manifest', type=Path, default=paths.manifest)
        if name == 'uninstall':
            sub.add_argument('--force', action='store_true', help='Back up edited installed files before restoring')
    commands.add_parser('ensure-profile', help=argparse.SUPPRESS)
    args = parser.parse_args()
    host = Host()
    try:
        if args.command == 'ensure-profile':
            host.ensure_profile()
            return
        if args.command in ('uninstall', 'recover'):
            result = Installation(args.manifest, host).restore(force=getattr(args, 'force', False),
                                                               recover=args.command == 'recover')
        elif args.command == 'status':
            result = json.loads(args.manifest.read_text()) if args.manifest.exists() else {'phase': 'uninstalled'}
        else:
            entries = artifacts(repo, args.bundle, paths, args.acp_root)
            plan = [{'target': str(e.target), 'sha256': digest(e.data), 'mode': oct(e.mode),
                     'persistent': e.persistent} for e in entries]
            if args.command == 'plan':
                conflicts = [str(e.target) for e in entries if not e.persistent and os.path.lexists(e.target)]
                result = {'files': plan, 'conflicts': conflicts, 'live_changes': False}
            elif args.command == 'stage':
                args.output.mkdir(parents=True, exist_ok=False)
                for index, entry in enumerate(entries):
                    name = f'files/{index:02d}-{entry.target.name}'
                    atomic(args.output / name, entry.data, entry.mode)
                    plan[index]['staged'] = name
                save(args.output / 'plan.json', {'schema': 1, 'files': plan, 'live_changes': False})
                result = {'staged': str(args.output), 'live_changes': False}
            else:
                result = Installation(paths.manifest, host).install(entries, paths)
        if args.command in ('install', 'uninstall', 'recover', 'status'):
            manifest_path = getattr(args, 'manifest', paths.manifest)
            result = {'phase': result['phase'], 'manifest': str(manifest_path),
                      **{k: result[k] for k in ('backup', 'recovery_error', 'cleanup_warning') if k in result}}
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
