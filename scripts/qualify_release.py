#!/usr/bin/env python3
"""Offline release qualification through the shared Hermes runner (run inside WSL/Linux).

Runs the exact committed source named by --commit using release-qualification.json.
It never deploys, signs, publishes or contacts a paid service. Native OS acceptance
listed under "nativeLanes" in the manifest is NOT executed or implied by this run.

Timing weights (--timings) are a stable placement input: they are part of the runner's
reuse identity, so this script never overwrites them. Refresh them explicitly with
--write-timings PATH (a separate file), review it, then commit it as the new input.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'release-qualification.json'


def check_runner(runner, pin):
    """Require a committed runner revision containing the pinned commit; refuse dirty runner sources."""
    git = ['git', '-C', str(runner)]
    for name in ('release_qualification.py', 'release_unittest.py'):
        if subprocess.run([*git, 'diff', '--quiet', 'HEAD', '--', name]).returncode:
            sys.exit(f'Runner file {name} has uncommitted edits; use a committed revision')
    if subprocess.run([*git, 'merge-base', '--is-ancestor', pin, 'HEAD']).returncode:
        sys.exit(f'Runner checkout does not contain pinned revision {pin}')


def main():
    manifest = json.loads(MANIFEST.read_text())
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--runner', type=Path, default=Path(os.environ.get('HERMES_RELEASE_RUNNER', '/mnt/c/GitHub/hermes-release-opt')))
    parser.add_argument('--commit', default='HEAD')
    parser.add_argument('--state', type=Path, default=Path.home() / '.local/state/release-qualification' / manifest['project'])
    parser.add_argument('--reuse', action='store_true')
    parser.add_argument('--max-age', type=float, default=86400)
    parser.add_argument('--class-shards', type=int, default=1, choices=range(1, 33))
    parser.add_argument('--timings', type=Path, default=ROOT / 'release-timings.json',
                        help='Stable placement input; read only')
    parser.add_argument('--write-timings', type=Path, help='Explicitly write this run\'s receipt for later review/commit')
    args = parser.parse_args()
    if sys.platform != 'linux':
        sys.exit('Run inside WSL/Linux from a repository on the native Linux filesystem or /mnt/c worktree')
    has_unittest = any(s.get('runner') == 'unittest' for s in manifest['suites'])
    if args.class_shards > 1 and not has_unittest:
        sys.exit('Class sharding is N/A: this project has no Python unittest suite')
    check_runner(args.runner, manifest['runnerPin'])
    sys.path.insert(0, str(args.runner))
    from release_qualification import qualify
    timings = args.timings if args.class_shards > 1 and args.timings.exists() else None
    run = {k: v for k, v in manifest.items() if k in ('scope', 'suites')}
    result = qualify(ROOT, run, args.state, commit=args.commit,
                     reuse=args.reuse, max_age=args.max_age, class_shards=args.class_shards, timings=timings)
    if args.write_timings and result['result'] == 'passed':
        args.write_timings.write_text(json.dumps(result, indent=2))
    print(json.dumps({'result': result['result'], 'commit': result['commit'], 'scope': result['scope'],
                      'reused': result['reused'], 'wallSeconds': result.get('wallSeconds'),
                      'receipt': result['receipt'],
                      'suites': [{k: s.get(k) for k in ('name', 'exitCode', 'seconds', 'timedOut')} for s in result['suites']],
                      'nativeLanesNotRun': manifest.get('nativeLanes', [])}, indent=2))
    return 0 if result['result'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
