"""Apply the repository's explicit adjustments to an exported snapshot."""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD = 'C:/Users/jun/Documents/Codex/2026-11-01/poteto-mode/.cursor/skills/verify-hermes-stack'
NEW = 'skills/verify-hermes-stack'
REPLACEMENTS = {
    'hermes/plugins/pstack/docs/guide/10-recipes-and-pitfalls.md': [
        ('![She tastes a finished dish while robots cook from a recipe box, with pinned cards reading /how, /tdd, and cronjob above the counter.](./images/recipes.jpg)', '')],
    'skills/verify-hermes-stack/scripts/verify_stack.py': [('parents[4]', 'parents[3]')],
    'skills/verify-hermes-stack/scripts/audit_settings.py': [('parents[4]', 'parents[3]')],
    'skills/verify-hermes-stack/SKILL.md': [(OLD, NEW), (OLD.replace('/', '\\'), NEW.replace('/', '\\')),
        ('C:/Users/jun/Documents/Codex/2026-11-01/poteto-mode/evidence/runs/', 'evidence/runs/'),
        ('# Verify the installed Hermes stack', '# Verify the installed Hermes stack\n\nRun commands from the repository root. Resolve these relative paths from that directory.')],
    'omniroute/dashboard/collect.py': [(r'Path(r"C:\Users\jun\Desktop\TEST\omni-autotune\selected_models.json")', "Path(__file__).resolve().parents[1] / 'autotune' / 'selected_models.json'")],
    'omniroute/dashboard/README.md': [('캐시 / (입력 + 캐시)', '캐시 / 입력 (입력 토큰에 캐시가 포함됨)')],
    'omniroute/dashboard/test_dashboard.py': [('SECRET = "SECRET-DO-NOT-COPY-9f3a"', 'SECRET = "SECRET-DO-NOT-" + "COPY-9f3a"')],
    'hindsight/ops/backup-hindsight.ps1': [("$ErrorActionPreference = 'Stop'", "$ErrorActionPreference = 'Stop'\nif ([string]::IsNullOrWhiteSpace($env:PGPASSWORD)) { throw 'Set PGPASSWORD locally before running the backup.' }")],
}
for name in ('claude-best.ps1', 'claude-free.ps1'):
    REPLACEMENTS['integrations/claude-code/scripts/' + name] = [
        ('$env:ANTHROPIC_API_KEY = "여기에-OmniRoute-API-키"',
         "if ([string]::IsNullOrWhiteSpace($env:ANTHROPIC_API_KEY)) { throw 'Set ANTHROPIC_API_KEY locally before running Claude.' }")]


def prepare():
    provenance = ROOT / 'provenance/sources.json'
    records = json.loads(provenance.read_text(encoding='utf-8-sig'))
    by_target = {record['target']: record for record in records}
    if len(by_target) != len(records):
        raise ValueError('duplicate provenance target')
    pending = {}
    for target, replacements in REPLACEMENTS.items():
        path = ROOT / target
        if target not in by_target or not path.is_file():
            raise ValueError(f'missing target or provenance: {target}')
        text = path.read_bytes().decode('utf-8')
        for old, new in replacements:
            remaining = text.replace(new, '') if old in new else text
            has_old = old in remaining
            if has_old and (new not in text or new in old):
                text = text.replace(old, new)
            elif has_old or new not in text:
                raise ValueError(f'unexpected content: {target}')
        if target == 'hindsight/ops/backup-hindsight.ps1':
            marker = '# PGPASSWORD is supplied by the caller.'
            text, count = re.subn(r'''(?m)^\$env:PGPASSWORD\s*=\s*(['"]).*?\1\s*$''', marker, text)
            if count > 1 or (count == 0 and marker not in text):
                raise ValueError('unexpected backup credential assignment')
        pending[target] = text.encode('utf-8')
    for target, content in pending.items():
        (ROOT / target).write_bytes(content)
        by_target[target]['snapshot_sha256'] = hashlib.sha256(content).hexdigest()
        by_target[target]['adjustments'] = ['repository portability and safety adjustments (scripts/prepare_snapshot.py)']
    provenance.write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(f'Prepared {len(pending)} snapshot files')


if __name__ == '__main__':
    prepare()
