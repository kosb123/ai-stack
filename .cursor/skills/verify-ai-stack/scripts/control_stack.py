"""Drive the installed ai-stack with owned scratch state and persistent evidence."""
import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = Path(__file__).resolve().parent
RUNS = ROOT / 'evidence/ai-stack/runs'
HOME = Path('C:/Users/jun/AppData/Local/Hermes')
MANUAL_GAPS = ['Hermes Desktop model switch and resume UI', 'Dashboard rendered charts and period/theme controls',
               'Codex and Claude hook execution in new sessions', 'Backup restore into a disposable database',
               'Fresh-machine provisioning from this snapshot repository']
JOURNEYS = ('settings', 'operations', 'dashboard', 'local-tool', 'blog-direct', 'hermes-tool', 'combos', 'memory', 'plugin-memory')


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


legacy = module('stack_journeys', ROOT / 'skills/verify-hermes-stack/scripts/verify_stack.py')
legacy.RUNS = RUNS
sys.path.insert(0, str(ROOT / 'scripts'))
from snapshot import is_secret_key, scrub_text


def evidence(path, filename, value):
    values = [v for k, v in legacy.secrets().items() if is_secret_key(k) and len(v) >= 8]
    keyfile = HOME / 'runtimes/llamacpp/.api_key'
    if keyfile.is_file():
        values.append(keyfile.read_text(encoding='utf-8').strip())
    text = json.dumps(value, ensure_ascii=False, indent=2)
    (path / filename).write_text(scrub_text(text, [v for v in values if len(v) >= 8]), encoding='utf-8')


def result(path, manifest, name, checks, scope, inconclusive=False):
    verdict = 'VERIFIED' if all(c['ok'] for c in checks) else ('INCONCLUSIVE' if inconclusive else 'NOT VERIFIED')
    legacy.record(path, manifest, name, verdict, checks, scope=scope)


def runtime_python():
    source = HOME / 'hermes-agent'
    key = hashlib.sha256(str(source.resolve()).encode()).hexdigest()[:16]
    facts = json.loads((HOME / 'installs' / key / 'facts.json').read_text(encoding='utf-8'))
    return Path(facts['packages']['venv']['environment']) / 'Scripts/python.exe'


def process_inventory():
    proc = subprocess.run([str(runtime_python()), str(SCRIPTS / 'host_probe.py')], capture_output=True,
                          text=True, encoding='utf-8', timeout=30, check=True)
    return json.loads(proc.stdout)


def drive_ready(path, manifest):
    if manifest['results'].get('doctor', {}).get('verdict') != 'VERIFIED':
        return False
    previous = json.loads((path / 'host-identity.json').read_text(encoding='utf-8'))
    current = process_inventory()
    return all(previous[key] == current[key] for key in ('listeners', 'model_processes'))


def local_idle(path):
    import urllib.request
    counters = {}
    required = {'llamacpp:requests_processing', 'llamacpp:requests_deferred'}
    try:
        credential = (HOME / 'runtimes/llamacpp/.api_key').read_text(encoding='utf-8').strip()
        request = urllib.request.Request('http://127.0.0.1:18434/metrics', headers={'Authorization': 'Bearer ' + credential})
        with urllib.request.urlopen(request, timeout=10) as response:
            for line in response.read().decode('utf-8').splitlines():
                fields = line.split()
                if len(fields) == 2 and fields[0] in required:
                    counters[fields[0]] = float(fields[1])
    except (OSError, ValueError):
        pass
    idle = set(counters) == required and all(value == 0 for value in counters.values())
    evidence(path, 'blog-local-idle.json', {'idle': idle, 'counters': counters})
    return idle


def doctor(path, manifest):
    checks = []
    inventory = process_inventory()
    evidence(path, 'host-identity.json', inventory)
    hashes = {}
    for target in (HOME / 'hermes-agent/gateway/session_local_inventory.py', Path('E:/hindsight/run_api.py'),
                   ROOT / 'skills/verify-hermes-stack/scripts/verify_stack.py'):
        hashes[str(target)] = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
    evidence(path, 'tested-builds.json', hashes)
    expected = {18434: 'llama-server.exe', 20128: 'node.exe', 8888: 'run_api.py', 20140: 'server.py'}
    for port, owner in expected.items():
        items = inventory['listeners'][str(port)]
        identities = {item['pid'] for item in items}
        def owned(item):
            if port == 18434:
                return Path(item['exe']).resolve() == (HOME / 'tools/llamacpp-cuda-11370-win32-x64/llama-server.exe').resolve()
            if port == 20128:
                return Path(item['exe']).name.lower() == owner and any('omniroute' in p.lower() for p in item['scripts'])
            expected_script = Path('E:/hindsight/run_api.py') if port == 8888 else Path('C:/Users/jun/Desktop/TEST/omni-autotune-dashboard/dashboard/server.py')
            return str(expected_script.resolve()) in item['scripts']
        matches = bool(items) and all(owned(item) for item in items)
        checks.append({'name': f'port_{port}_owner', 'ok': len(identities) == 1 and matches})
    checks.append({'name': 'legacy_port_absent', 'ok': not inventory['listeners']['18435']})
    checks.append({'name': 'one_model_process', 'ok': len(inventory['model_processes']) == 1})
    if not all(check['ok'] for check in checks):
        return result(path, manifest, 'doctor', checks, 'Identity preflight failed; authenticated probes were not sent.')
    # Existing HTTP checks include authenticated local properties and OmniRoute models.
    with contextlib.redirect_stdout(io.StringIO()):
        legacy.doctor(path, manifest)
    checks.extend(manifest['results']['doctor']['checks'])
    status, data, _ = legacy.request('GET', 'http://127.0.0.1:20140/api/overview?range=24h', timeout=10)
    checks.append({'name': 'dashboard_readable', 'ok': status == 200 and isinstance(data.get('requests'), int)})
    result(path, manifest, 'doctor', checks, 'Read-only identities, metadata and authentication; no inference or service launch.')


def settings(path, manifest):
    audit = module('stack_settings', ROOT / 'skills/verify-hermes-stack/scripts/audit_settings.py')
    state = audit.collect()
    evidence(path, 'settings-audit.json', state)
    checks = [{'name': c['name'], 'ok': c['ok']} for c in state['checks']]
    # Hooks are configuration proof only; execution has its own explicitly unverified entry point.
    hooks = {'codex': Path.home() / '.codex/hooks.json', 'claude': Path.home() / '.claude/settings.json'}
    for name, location in hooks.items():
        text = location.read_text(encoding='utf-8') if location.is_file() else ''
        checks.append({'name': name + '_hindsight_hook_configured', 'ok': 'hindsight' in text.lower()})
    result(path, manifest, 'settings', checks, 'Effective Hermes/Hindsight/OmniRoute boundaries and hook configuration; not hook execution.')


def operations(path, manifest):
    names = ['Local LlamaCpp Server (Qwen3.8 27B)', 'Local LlamaCpp Watchdog (Qwen3.8 27B)',
             'OmniRouteServer', 'OmniAutotune-Check', 'OmniAutotune-Tune', 'OmniAutotune-Verify', 'Hindsight Daily Backup']
    script = ("Get-ScheduledTask | Where-Object { $_.TaskName -match 'Llama|Omni|Hindsight' } | ForEach-Object { "
              "$i = $_ | Get-ScheduledTaskInfo; [pscustomobject]@{name=$_.TaskName; state=[string]$_.State; "
              "last_result=$i.LastTaskResult} } | ConvertTo-Json -Compress")
    proc = subprocess.run(['powershell', '-NoProfile', '-Command', script], capture_output=True, text=True,
                          encoding='utf-8', timeout=30, check=True)
    tasks = json.loads(proc.stdout)
    if isinstance(tasks, dict):
        tasks = [tasks]
    evidence(path, 'scheduled-task-status.json', tasks)
    by_name = {task['name']: task for task in tasks}
    checks = [{'name': name, 'ok': name in by_name and by_name[name]['state'] != 'Disabled'
               and by_name[name]['last_result'] in (0, 267009)} for name in names]
    for name in ('Start-External-LlamaCpp.ps1', 'Watch-LocalLlama.ps1', 'ensure_route_patch.py', 'local-provider-identity.patch'):
        source = ROOT / 'hermes/llamacpp' / name
        live = HOME / 'runtimes/llamacpp' / name
        checks.append({'name': name + '_deployed', 'ok': live.is_file() and source.read_bytes() == live.read_bytes()})
    applied = subprocess.run(['git', '-C', str(HOME / 'hermes-agent'), 'apply', '--reverse', '--check',
                              str(ROOT / 'hermes/llamacpp/local-provider-identity.patch')], capture_output=True, timeout=15)
    checks.append({'name': 'route_patch_applied', 'ok': applied.returncode == 0})
    backups = sorted((Path.home() / '.hindsight/backups').glob('hindsight-data-*.dump'), key=lambda p: p.stat().st_mtime, reverse=True)
    backup = backups[0] if backups else None
    fresh = bool(backup and backup.stat().st_size and datetime.now(timezone.utc).timestamp() - backup.stat().st_mtime < 48 * 3600)
    checks.append({'name': 'backup_newer_than_48h', 'ok': fresh})
    if backup:
        archive = subprocess.run([str(Path.home() / '.pg0/installation/18.1.0/bin/pg_restore.exe'), '--list', str(backup)],
                                 capture_output=True, timeout=20)
        checks.append({'name': 'backup_archive_readable', 'ok': archive.returncode == 0 and b'TABLE DATA' in archive.stdout})
        evidence(path, 'backup-metadata.json', {'name': backup.name, 'size': backup.stat().st_size, 'modified': backup.stat().st_mtime})
    result(path, manifest, 'operations', checks, 'Read-only task/patch/deployment and archive checks; no failover injection, backup creation or restore.')


def dashboard(path, manifest):
    checks = []
    root = 'http://127.0.0.1:20140'
    import urllib.request
    with urllib.request.urlopen(root, timeout=10) as response:
        html = response.read().decode('utf-8')
        checks.append({'name': 'dashboard_document', 'ok': response.status == 200 and '<h1>OmniRoute 대시보드</h1>' in html})
        (path / 'dashboard-document.html').write_text(html, encoding='utf-8')
    routes = ('overview', 'timeseries', 'candidates', 'quota', 'models', 'errors', 'connections')
    for period in ('24h', '7d', '30d', 'all'):
        for route in routes:
            status, body, _ = legacy.request('GET', root + f'/api/{route}?range={period}', timeout=20)
            checks.append({'name': f'{route}_{period}', 'ok': status == 200 and isinstance(body, dict)})
            evidence(path, f'dashboard-{route}-{period}.json', {'http': status, 'body': body})
            if route == 'overview':
                when = body.get('last_collect_at')
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(when.replace('Z', '+00:00'))).total_seconds() if when else None
                checks.append({'name': 'collector_fresh_' + period, 'ok': age is not None and -60 <= age <= 600 and not body.get('last_collect_error')})
    invalid, _, _ = legacy.request('GET', root + '/api/overview?range=invalid')
    missing, _, _ = legacy.request('GET', root + '/api/not-a-route')
    checks.append({'name': 'invalid_range_and_unknown_route', 'ok': invalid == 400 and missing == 404})
    result(path, manifest, 'dashboard', checks, 'Live HTTP document, all data routes/ranges, freshness and error contract; rendered UI is separate.')


def blog_direct(path, manifest):
    client_path = Path('E:/blog-rank-project/blog-rank/writer/local_llm.py')
    expected = ROOT / 'integrations/blog-writer/local_llm.py'
    checks = [{'name': 'deployed_client_matches', 'ok': client_path.is_file() and client_path.read_bytes() == expected.read_bytes()}]
    if not checks[0]['ok']:
        return result(path, manifest, 'blog-direct', checks, 'Deployed blog client boundary only; no photos, search, publishing or pipeline output.')
    if not local_idle(path):
        return result(path, manifest, 'blog-direct', [{'name': 'local_requests_confirmed_idle', 'ok': False}],
                      'Inference skipped because model idle was not established.', inconclusive=True)
    client = module('deployed_blog_client', client_path)
    body = {'model': 'Qwen3.8-27B-UD-Q4_K_M', 'messages': [{'role': 'user', 'content': 'Reply with OK only.'}], 'max_tokens': 256}
    request = client.make_request(client.DEFAULT_URL, body)
    import urllib.request
    with urllib.request.urlopen(request, timeout=120) as response:
        answer = json.load(response)
        checks.append({'name': 'real_local_response', 'ok': response.status == 200 and 'OK' in answer['choices'][0]['message'].get('content', '')})
        evidence(path, 'blog-direct-response.json', {'endpoint': request.full_url, 'http': response.status, 'response': answer})
    with socket.socket() as connection:
        checks.append({'name': 'legacy_port_closed', 'ok': connection.connect_ex(('127.0.0.1', 18435)) != 0})
    result(path, manifest, 'blog-direct', checks, 'Real deployed blog authentication/request boundary; full article pipeline and ranking are not covered.')


def report(path, manifest):
    missing = [name for name in ('doctor',) + JOURNEYS if name not in manifest['results']]
    failed = [name for name, item in manifest['results'].items() if item['verdict'] != 'VERIFIED']
    value = {'run_id': manifest['run_id'], 'results': {k: v['verdict'] for k, v in manifest['results'].items()},
             'not_run': missing, 'manual_or_unimplemented': MANUAL_GAPS,
             'environment_verdict': 'NOT VERIFIED' if failed else 'INCONCLUSIVE',
             'scope': 'An implemented journey pass never fills an unexecuted or manual entry point.'}
    evidence(path, 'coverage.json', value)
    print(json.dumps(value, ensure_ascii=False))
    return 1 if failed else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('launch', 'doctor', 'cleanup', 'report', 'all') + JOURNEYS)
    parser.add_argument('--run')
    args = parser.parse_args()
    if args.command == 'launch':
        RUNS.mkdir(parents=True, exist_ok=True)
        legacy.launch()
        return 0
    if not args.run:
        parser.error('--run is required')
    path, manifest = legacy.load_run(args.run)
    # One shared-service driver across this skill's runs, plus the legacy per-run lock.
    with legacy.run_lock(RUNS.parent), legacy.run_lock(path):
        _, manifest = legacy.load_run(args.run)
        if args.command == 'report':
            return report(path, manifest)
        functions = {'doctor': doctor, 'settings': settings, 'operations': operations, 'dashboard': dashboard,
                     'blog-direct': blog_direct, 'cleanup': legacy.cleanup, 'combos': legacy.combos,
                     'hermes-tool': lambda p, m: legacy.hermes_tool(p, m, False),
                     'local-tool': lambda p, m: legacy.hermes_tool(p, m, True),
                     'memory': legacy.memory, 'plugin-memory': legacy.plugin_memory}
        selected = ('doctor',) + JOURNEYS if args.command == 'all' else (args.command,)
        for name in selected:
            try:
                if name in {'dashboard', 'blog-direct', 'combos', 'local-tool', 'hermes-tool', 'memory', 'plugin-memory'} and not drive_ready(path, manifest):
                    result(path, manifest, name, [{'name': 'current_doctor_identity_required', 'ok': False}],
                           'Drive refused; run Doctor again before using changed or unaudited service owners.')
                    continue
                functions[name](path, manifest)
                if name == 'cleanup':
                    survives = (path / 'manifest.json').is_file() and any(path.glob('*.json'))
                    if not survives:
                        raise RuntimeError('Cleanup removed evidence')
            except Exception as exc:
                result(path, manifest, name, [{'name': type(exc).__name__, 'ok': False}], 'Failed; preserve evidence and run cleanup.')
        verdicts = [manifest['results'][name]['verdict'] for name in selected]
        return 1 if 'NOT VERIFIED' in verdicts else 2 if 'INCONCLUSIVE' in verdicts else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({'verdict': 'NOT VERIFIED', 'stage': 'control', 'error_type': type(exc).__name__}))
        raise SystemExit(1)
