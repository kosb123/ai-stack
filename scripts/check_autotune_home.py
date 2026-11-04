"""Compare the canonical autotune home with a preserved pre-migration checkout."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / 'omniroute/autotune'
CODE = ('autotune.py', 'verify_cycle.py', 'test_preferred.py', 'bulk_discover.py',
        'measure.py', 'sweep.py', 'selected_models.json', 'survey/survey.py', 'survey/hs_probe.py')
TASKS = {'OmniAutotune-Check': ('autotune.py', 'check'),
         'OmniAutotune-Tune': ('autotune.py', 'tune'),
         'OmniAutotune-Verify': ('verify_cycle.py', '')}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def policy(path):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    return {target.id: ast.literal_eval(node.value)
            for node in tree.body if isinstance(node, ast.Assign)
            for target in node.targets if isinstance(target, ast.Name)
            and target.id in ('CONFIG', 'QUALITY_TIERS')}


def compare(source, target, check_state=True):
    checks = {'policy_unchanged': policy(source / 'autotune.py') == policy(target / 'autotune.py')}
    for name in CODE:
        a, b = source / name, target / name
        if name.endswith('.json'):
            same = json.loads(a.read_text(encoding='utf-8-sig')) == json.loads(b.read_text(encoding='utf-8-sig'))
        else:
            same = a.read_text(encoding='utf-8-sig') == b.read_text(encoding='utf-8-sig')
        checks[name] = same
    if check_state:
        for name in ('state.json', 'logs', 'backups', 'reports', 'survey'):
            original = source / name
            files = [original] if original.is_file() else [p for p in original.rglob('*') if p.is_file()
                     and '__pycache__' not in p.parts and (name != 'survey' or p.suffix != '.py')]
            checks[name + '_preserved'] = bool(files) and all(
                (target / p.relative_to(source)).is_file()
                and digest(p) == digest(target / p.relative_to(source)) for p in files)
    return checks


def tasks():
    script = "Get-ScheduledTask -TaskName 'OmniAutotune-*' | ForEach-Object { foreach ($a in $_.Actions) { [pscustomobject]@{ name=$_.TaskName; executable=$a.Execute; arguments=$a.Arguments; directory=$a.WorkingDirectory; actions=$_.Actions.Count; enabled=$_.Settings.Enabled } } } | ConvertTo-Json"
    proc = subprocess.run(['powershell', '-NoProfile', '-Command', script], capture_output=True,
                          text=True, encoding='utf-8', timeout=30, check=True)
    rows = json.loads(proc.stdout)
    return {row['name']: row for row in (rows if isinstance(rows, list) else [rows])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='Preserved original checkout, not the compatibility junction')
    parser.add_argument('--allow-state-changes', action='store_true', help='After resuming the scheduler, compare policy/code rather than historical state bytes')
    args = parser.parse_args()
    checks = compare(args.source, HOME, not args.allow_state_changes)
    current = tasks()
    for name, (script, command) in TASKS.items():
        row = current.get(name, {})
        expected = '"' + str(HOME / script) + '"' + (' ' + command if command else '')
        checks[name] = (row.get('actions') == 1 and row.get('enabled') is True
                        and row.get('arguments') == expected
                        and row.get('directory') == str(HOME)
                        and Path(row.get('executable', '')).name.lower() == 'pythonw.exe')
    legacy = Path.home() / 'Desktop/TEST/omni-autotune'
    checks['legacy_resolves_to_canonical'] = legacy.resolve() == HOME.resolve()
    ignored = ['omniroute/autotune/' + name for name in ('state.json', '.lock', 'logs/check.log', 'backups/test.json', 'reports/latest.md', 'survey/models.json')]
    proc = subprocess.run(['git', '-C', str(ROOT), 'check-ignore', *ignored], capture_output=True, text=True, check=False)
    checks['runtime_data_ignored'] = set(proc.stdout.splitlines()) == set(ignored)
    manifest = json.loads((ROOT / 'snapshot-manifest.json').read_text(encoding='utf-8-sig'))
    checks['capture_cannot_replace_canonical_code'] = not any(
        e['target'].startswith('omniroute/autotune/') for e in manifest['entries'])
    print(json.dumps({'checks': checks, 'verified': all(checks.values())}, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
