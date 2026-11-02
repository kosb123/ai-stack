"""Reapply the reviewed Hermes patch only when all original hunks still match."""
import argparse
import json
from pathlib import Path
import subprocess


def ensure_patch(source, patch):
    def git(*args):
        return subprocess.run(['git', '-C', str(source), 'apply', *args, str(patch)],
                              capture_output=True, text=True, timeout=15)
    if git('--reverse', '--check').returncode == 0:
        return 'already_applied'
    if git('--check').returncode != 0:
        raise RuntimeError('Hermes source changed; patch needs review, source was not overwritten')
    if git().returncode != 0:
        raise RuntimeError('Patch could not be applied')
    marker = patch.with_suffix('.restart-required')
    marker.write_text('Hermes route patch restored. Restart gateway after active work drains.\n', encoding='utf-8')
    return 'applied_restart_required'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path.home() / 'AppData/Local/Hermes/hermes-agent')
    parser.add_argument('--patch', type=Path, default=Path(__file__).with_name('local-provider-identity.patch'))
    args = parser.parse_args()
    print(json.dumps({'route_patch': ensure_patch(args.source, args.patch.resolve())}))


if __name__ == '__main__':
    main()
