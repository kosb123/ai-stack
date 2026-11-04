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
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = Path(__file__).resolve().parent
RUNS = ROOT / 'evidence/ai-stack/runs'
HOME = Path('C:/Users/jun/AppData/Local/Hermes')
MANUAL_GAPS = ['Hermes Desktop model switch and resume UI', 'Dashboard rendered charts and period/theme controls',
               'Codex and Claude hook execution in new sessions', 'Backup restore into a disposable database',
               'Fresh-machine provisioning from this snapshot repository', 'Automatic Hermes memory in normal chats',
               'Paperclip browser login, provider authentication and adapter work', 'Complete OmniRoute fallback-attempt trace']
JOURNEYS = ('settings', 'operations', 'autotune', 'services', 'dashboard', 'local-tool', 'blog-direct', 'hermes-tool', 'combos', 'memory', 'plugin-memory')


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
        if port == 20140 and not items:
            checks.append({'name': 'optional_dashboard_stopped', 'ok': True})
            continue
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
    if inventory['listeners']['20140']:
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
        same = live.is_file() and source.read_text(encoding='utf-8-sig') == live.read_text(encoding='utf-8-sig')
        checks.append({'name': name + '_deployed', 'ok': same})
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
    identity = path / 'host-identity.json'
    if identity.is_file() and not json.loads(identity.read_text(encoding='utf-8'))['listeners']['20140']:
        legacy.record(path, manifest, 'dashboard', 'INCONCLUSIVE', [], coverage='verified-unreachable',
                      prerequisite='The manually started dashboard must already listen on 127.0.0.1:20140.',
                      attempted_route='http://127.0.0.1:20140/', scope='No dashboard listener; no server or collector was started.')
        return
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
        checks.append({'name': 'real_local_response', 'ok': response.status == 200 and answer['choices'][0]['message'].get('content', '').strip() == 'OK'})
        evidence(path, 'blog-direct-response.json', {'endpoint': request.full_url, 'http': response.status, 'response': answer})
    with socket.socket() as connection:
        checks.append({'name': 'legacy_port_closed', 'ok': connection.connect_ex(('127.0.0.1', 18435)) != 0})
    result(path, manifest, 'blog-direct', checks, 'Real deployed blog authentication/request boundary; full article pipeline and ranking are not covered.')


def autotune(path, manifest):
    code = module('canonical_home_check', ROOT / 'scripts/check_autotune_home.py')
    current = code.tasks()
    home = ROOT / 'omniroute/autotune'
    checks = []
    for name, (script, command) in code.TASKS.items():
        task = current.get(name, {})
        args = '"' + str(home / script) + '"' + (' ' + command if command else '')
        checks.append({'name': name + '_canonical_action', 'ok': task.get('actions') == 1
                       and task.get('enabled') is True and task.get('arguments') == args
                       and task.get('directory') == str(home)
                       and Path(task.get('executable', '')).name.lower() == 'pythonw.exe'})
    old = Path.home() / 'Desktop/TEST/omni-autotune'
    checks.append({'name': 'one_canonical_home', 'ok': old.resolve() == home.resolve()})
    policy = code.policy(home / 'autotune.py')
    with sqlite3.connect('file:' + (Path.home() / '.omniroute/storage.sqlite').as_posix() + '?mode=ro', uri=True) as db:
        combos = {name: json.loads(data) for name, data in db.execute('SELECT name,data FROM combos')}
    for lane in policy['CONFIG']['lanes']:
        name = lane['combo']
        members = {item['model'] for item in combos.get(name, {}).get('models', [])}
        excluded = set(policy['CONFIG'].get('role_exclusions', {}).get(name, {}))
        checks.append({'name': name + '_role_exclusions', 'ok': bool(members) and not members.intersection(excluded)})
    task_status = {name: {'enabled': item.get('enabled'), 'actions': item.get('actions')}
                   for name, item in current.items()}
    evidence(path, 'autotune-home.json', {'tasks': task_status, 'canonical': str(home), 'checks': checks})
    result(path, manifest, 'autotune', checks, 'Read-only canonical scheduler actions and role exclusions; no tuning, probes or quota-recovery simulation.')


def services(path, manifest):
    checks = []
    optional = {}
    script = "Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in 3001,3100,5433 } | Select-Object LocalPort,LocalAddress | ConvertTo-Json -Compress"
    proc = subprocess.run(['powershell', '-NoProfile', '-Command', script], capture_output=True, text=True, timeout=20, check=True)
    rows = json.loads(proc.stdout) if proc.stdout.strip() else []
    if isinstance(rows, dict):
        rows = [rows]
    checks.append({'name': 'optional_services_loopback_only', 'ok': all(row['LocalAddress'] in ('127.0.0.1', '::1') for row in rows)})
    for name, url, port in [('freellmapi', 'http://127.0.0.1:3001/livez', 3001),
                            ('paperclip', 'http://127.0.0.1:3100/api/health', 3100)]:
        if not any(row['LocalPort'] == port for row in rows):
            optional[name] = {'coverage': 'verified-unreachable', 'prerequisite': 'The installed service must already be running.', 'attempted_route': url}
            continue
        status, body, _ = legacy.request('GET', url, timeout=10)
        healthy = status == 200 and isinstance(body, dict) and body.get('status') in ('ok', 'healthy')
        checks.append({'name': name + '_health', 'ok': healthy})
        optional[name] = {'http': status, 'status': body.get('status') if isinstance(body, dict) else None}
    evidence(path, 'optional-services.json', {'listeners': rows, 'services': optional})
    missing = any(item.get('coverage') == 'verified-unreachable' for item in optional.values())
    verdict = 'NOT VERIFIED' if not all(item['ok'] for item in checks) else ('INCONCLUSIVE' if missing else 'VERIFIED')
    legacy.record(path, manifest, 'services', verdict, checks, services=optional,
                  scope='Loopback bindings and HTTP health only; not Paperclip login, provider auth, adapter work or recovery.')


def served_primary(primary, headers):
    if not primary or '/' not in primary:
        return False
    provider, model = primary.split('/', 1)
    served_provider, served_model = headers.get('x-omniroute-provider'), headers.get('x-omniroute-model')
    return (provider == served_provider
            and served_model in (model, primary))


def hermes_tool(path, manifest, local):
    import yaml
    provider, name = ('llamacpp', 'local-tool') if local else ('omni', 'hermes-tool')
    config = yaml.safe_load((HOME / 'config.yaml').read_text(encoding='utf-8'))
    expected = 'http://127.0.0.1:' + ('18434' if local else '20128') + '/v1'
    endpoint = config.get('providers', {}).get(provider, {}).get('base_url', '').rstrip('/')
    if endpoint != expected:
        return result(path, manifest, name, [{'name': 'configured_endpoint_is_expected', 'ok': False}],
                      'Provider endpoint differs from the mapped local service; no CLI request was sent.')
    legacy.hermes_tool(path, manifest, local)


def combos(path, manifest):
    request = legacy.request
    def shaped(method, url, payload=None, headers=None, timeout=20):
        if method == 'POST' and url == legacy.OMNI + '/v1/chat/completions':
            payload['reasoning_effort'] = 'low'
            payload['max_tokens'] = 4096 if 'response_format' in payload else 1024
            if 'tools' in payload and payload['model'].startswith('hindsight-'):
                payload['tool_choice'] = 'required'
        return request(method, url, payload, headers, timeout)
    legacy.request = shaped
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            legacy.combos(path, manifest)
    finally:
        legacy.request = request
    checks = manifest['results']['combos']['checks']
    for item in checks:
        saved = path / (item['requested'] + '.json')
        data = json.loads(saved.read_text(encoding='utf-8')) if saved.is_file() else {}
        item['served'] = data.get('structured_headers', {}).get('x-omniroute-model')
        item['served_provider'] = data.get('structured_headers', {}).get('x-omniroute-provider')
        item['tool_served'] = data.get('tool_headers', {}).get('x-omniroute-model')
        item['tool_provider'] = data.get('tool_headers', {}).get('x-omniroute-provider')
        item['served_primary_identity'] = all(served_primary(item.get('primary'), data.get(key, {}))
                                               for key in ('structured_headers', 'tool_headers'))
        item['fallback'] = 'not established; no attempt trace'
        item['ok'] = bool(item.get('required_tool_call') and (item.get('structured') or item['requested'] == 'hermes-fast-agent'))
    verdict = 'NOT VERIFIED' if not all(item['ok'] for item in checks) else (
        'VERIFIED' if all(item['served_primary_identity'] for item in checks) else 'INCONCLUSIVE')
    legacy.record(path, manifest, 'combos', verdict, checks,
                  scope='JSON and real tools; Hindsight uses required tool_choice. Provider+model headers establish served primary identity, not a complete fallback-attempt trace.')


def plugin_memory(path, manifest):
    with contextlib.redirect_stdout(io.StringIO()):
        legacy.plugin_memory(path, manifest)
    item = manifest['results']['plugin-memory']
    timed_out = any(check.get('stage') == 'persistence'
                    and check.get('error') == 'persisted linked document not established' for check in item['checks'])
    fingerprints = item.get('installation_fingerprints', {})
    installation_unchanged = (isinstance(fingerprints.get('before'), dict)
                              and fingerprints['before'] == fingerprints.get('after')
                              and not any(check.get('installation_state_unchanged') is False for check in item['checks']))
    if timed_out and installation_unchanged:
        item['verdict'] = 'INCONCLUSIVE'
        item['scope'] = 'Explicit retain acknowledged, but linked retrieval was not established within the 180-second polling window. Cross-session recall was not reached.'
        legacy.save(path, manifest)
    print(json.dumps(item, ensure_ascii=False))


def cleanup(path, manifest):
    try:
        legacy.cleanup(path, manifest)
    except PermissionError:
        evidence(path, 'cleanup-retry.json', {'error_type': 'PermissionError', 'action': 'Retry the same ownership-validated cleanup once after profile release.'})
        time.sleep(1)
        legacy.cleanup(path, manifest)


def report(path, manifest):
    missing = [name for name in ('doctor',) + JOURNEYS if name not in manifest['results']]
    failed = [name for name, item in manifest['results'].items() if item['verdict'] == 'NOT VERIFIED']
    value = {'run_id': manifest['run_id'], 'results': {k: v['verdict'] for k, v in manifest['results'].items()},
             'not_run': missing, 'manual_or_unimplemented': MANUAL_GAPS,
             'verified_unreachable': {name: {'prerequisite': item.get('prerequisite'), 'attempted_route': item.get('attempted_route')}
                                      for name, item in manifest['results'].items() if item.get('coverage') == 'verified-unreachable'},
             'environment_verdict': 'NOT VERIFIED' if failed else 'INCONCLUSIVE',
             'scope': 'An implemented journey pass never fills an unexecuted or manual entry point.'}
    evidence(path, 'coverage.json', value)
    print(json.dumps(value, ensure_ascii=False))
    return 1 if failed else 2


def preserve_attempt(path, name):
    archive = path / 'attempts' / (name + '-' + uuid.uuid4().hex)
    archive.mkdir(parents=True)
    for previous in path.iterdir():
        if previous.is_file() and not previous.name.startswith('.'):
            shutil.copy2(previous, archive / previous.name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('launch', 'doctor', 'cleanup', 'report', 'all', 'audit') + JOURNEYS)
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
        functions = {'doctor': doctor, 'settings': settings, 'operations': operations, 'autotune': autotune,
                     'services': services, 'dashboard': dashboard,
                     'blog-direct': blog_direct, 'cleanup': cleanup, 'combos': combos,
                     'hermes-tool': lambda p, m: hermes_tool(p, m, False),
                     'local-tool': lambda p, m: hermes_tool(p, m, True),
                     'memory': legacy.memory, 'plugin-memory': plugin_memory}
        selected = ('doctor',) + JOURNEYS if args.command == 'all' else (('doctor', 'settings', 'operations', 'autotune', 'services', 'dashboard') if args.command == 'audit' else (args.command,))
        for name in selected:
            try:
                if manifest.get('closed') and name != 'cleanup':
                    raise RuntimeError('Run already cleaned; use a fresh Launch.')
                if manifest.get('cli_cleanup_required') and name not in {'doctor', 'cleanup'}:
                    raise RuntimeError('Failed CLI residue requires Cleanup and a fresh Launch before more drives.')
                if name in manifest['results']:
                    preserve_attempt(path, name)
                if name not in {'doctor', 'cleanup'} and manifest.get('doctor_required'):
                    preserve_attempt(path, 'doctor')
                    doctor(path, manifest)
                    if manifest['results']['doctor']['verdict'] != 'VERIFIED':
                        result(path, manifest, name, [{'name': 'doctor_after_failure', 'ok': False}], 'No drive after a failed preflight.')
                        continue
                if name in {'dashboard', 'blog-direct', 'combos', 'local-tool', 'hermes-tool', 'memory', 'plugin-memory'} and not drive_ready(path, manifest):
                    result(path, manifest, name, [{'name': 'current_doctor_identity_required', 'ok': False}],
                           'Drive refused; run Doctor again before using changed or unaudited service owners.')
                    continue
                before_cleanup = {item.relative_to(path).as_posix(): hashlib.sha256(item.read_bytes()).hexdigest()
                                  for item in path.rglob('*') if item.is_file()
                                  and item.relative_to(path).as_posix() not in {'manifest.json', '.lock'}} if name == 'cleanup' else {}
                functions[name](path, manifest)
                if name == 'cleanup':
                    survives = (path / 'manifest.json').is_file() and all((path / filename).is_file()
                        and hashlib.sha256((path / filename).read_bytes()).hexdigest() == checksum
                        for filename, checksum in before_cleanup.items())
                    if not survives:
                        raise RuntimeError('Cleanup removed evidence')
                    manifest['closed'] = manifest['results']['cleanup']['verdict'] == 'VERIFIED'
                    legacy.save(path, manifest)
            except Exception as exc:
                result(path, manifest, name, [{'name': type(exc).__name__, 'ok': False}], 'Failed; preserve evidence and run cleanup.')
            finally:
                if name != 'cleanup' and name in manifest['results']:
                    manifest['doctor_required'] = manifest['results'][name]['verdict'] != 'VERIFIED'
                if name in {'local-tool', 'hermes-tool', 'plugin-memory'} and manifest['results'][name]['verdict'] != 'VERIFIED':
                    manifest['cli_cleanup_required'] = True
                legacy.save(path, manifest)
            if args.command == 'all' and manifest.get('cli_cleanup_required'):
                break
        verdicts = [manifest['results'][name]['verdict'] for name in selected if name in manifest['results']]
        return 1 if 'NOT VERIFIED' in verdicts else 2 if 'INCONCLUSIVE' in verdicts else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({'verdict': 'NOT VERIFIED', 'stage': 'control', 'error_type': type(exc).__name__}))
        raise SystemExit(1)
