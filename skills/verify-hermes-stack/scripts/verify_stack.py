"""Isolated, evidence-preserving Hermes stack checks. Never deletes a bank."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import urllib.parse
import uuid
import sqlite3
import contextlib
import hashlib

ROOT = Path(__file__).resolve().parents[3]
RUNS = ROOT / 'evidence' / 'runs'
HERMES_HOME = Path('C:/Users/jun/AppData/Local/Hermes')
HERMES = HERMES_HOME / 'bin/hermes.exe'
COMBOS = ('hermes-fast-agent', 'hindsight-structured', 'hindsight-refresh')
OMNI = 'http://127.0.0.1:20128'
HINDSIGHT = 'http://127.0.0.1:8888'
SCRATCH = Path('C:/vh')


def request(method, url, payload=None, headers=None, timeout=20):
    req = urllib.request.Request(url, method=method, data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            return response.status, json.loads(raw or b'null'), {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read() or b'null')
        except ValueError:
            body = {}
        return exc.code, body, {k.lower(): v for k, v in exc.headers.items()}


def secrets():
    values = {}
    path = HERMES_HOME / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip().strip('"\'')
    return values


def run_dir(run_id):
    canonical = str(uuid.UUID(run_id))
    if canonical != run_id:
        raise ValueError('run must be a canonical UUID')
    root = RUNS.resolve()
    path = (root / canonical).resolve()
    if path.parent != root:
        raise ValueError('invalid run path')
    return path


def load_run(run_id):
    path = run_dir(run_id)
    manifest = json.loads((path / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('run_id') != run_id or manifest.get('bank_id') != 'verify-hermes-' + run_id:
        raise ValueError('manifest identity mismatch')
    return path, manifest


def scratch_dir(run_id):
    if str(uuid.UUID(run_id)) != run_id:
        raise ValueError('run must be a canonical UUID')
    path = SCRATCH / run_id
    if SCRATCH.is_symlink() or path.is_symlink() or path.resolve().parent != SCRATCH.resolve():
        raise ValueError('scratch path escapes root')
    return path


def profile_dir(run_id, feature, attempt=''):
    if str(uuid.UUID(run_id)) != run_id or feature not in ('hermes-tool', 'local-tool', 'plugin-memory') or (attempt and (not attempt.isdigit() or len(attempt) > 3)):
        raise ValueError('invalid profile identity')
    root = HERMES_HOME / 'profiles'
    profile = root / ('verify-' + run_id + '-' + feature + ('-' + attempt if attempt else ''))
    if root.is_symlink() or profile.is_symlink() or profile.resolve().parent != root.resolve():
        raise ValueError('profile path escapes root')
    return profile


def create_profile(path, manifest, feature):
    attempt = 0
    while True:
        home = profile_dir(manifest['run_id'], feature, str(attempt) if attempt else '')
        if not home.exists():
            break
        attempt += 1
        if attempt > 999:
            raise RuntimeError('profile attempts exhausted')
    manifest.setdefault('profiles', []).append(str(home))
    save(path, manifest)
    home.mkdir(parents=True)
    return home


def save(path, manifest):
    (path / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')


def record(path, manifest, name, verdict, checks, **details):
    result = {'id': name, 'verdict': verdict, 'checks': checks, **details}
    manifest['results'][name] = result
    save(path, manifest)
    print(json.dumps(result, ensure_ascii=False))


def runtime_ready(home, frozen=False):
    source = HERMES_HOME / 'hermes-agent'
    key = hashlib.sha256(str(source.resolve()).encode()).hexdigest()[:16]
    try:
        facts = json.loads((HERMES_HOME / 'installs' / key / 'facts.json').read_text(encoding='utf-8'))
        python = Path(facts['packages']['venv']['environment']) / 'Scripts' / 'python.exe'
        if not python.is_file():
            return False
    except (OSError, ValueError, KeyError, TypeError):
        return False
    code = ("import json,sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);"
            "import pm,openai,aiohttp,hermes_yaml;from hermes_cli.venv_sync import completion_pending_path;"
            "r=Path(sys.argv[1]);print(json.dumps({'current':pm.venv_is_current(project_root=r),"
            "'pending':completion_pending_path(r).exists()}))")
    env = {k: v for k, v in os.environ.items() if k.upper() in
           {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC', 'PATHEXT', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA'}}
    env['HERMES_HOME'] = str(home)
    try:
        result = subprocess.run([str(python), '-c', code, str(source)], env=env,
                                capture_output=True, text=True, timeout=30, check=True)
        state = json.loads(result.stdout.strip().splitlines()[-1])
        if frozen:
            return isinstance(state.get('current'), bool) and isinstance(state.get('pending'), bool)
        return state == {'current': True, 'pending': False}
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return False


def launch():
    run_id = str(uuid.uuid4())
    path = run_dir(run_id)
    path.mkdir(parents=True)
    scratch = scratch_dir(run_id)
    (scratch / 'home').mkdir(parents=True)
    (scratch / 'runtime').mkdir()
    (scratch / 'work').mkdir()
    manifest = {'run_id': run_id, 'bank_id': 'verify-hermes-' + run_id, 'results': {}, 'subprocess_ids': []}
    save(path, manifest)
    print(json.dumps({'run_id': run_id, 'manifest': str(path / 'manifest.json'), 'bank_id': manifest['bank_id']}))


def tool_ok(events, nonce, filepath):
    calls = [e for e in events if e.get('type') == 'tool_use' and e.get('name') == 'read_file'
             and filepath in str(e.get('input', {}).get('path', ''))]
    results = [e for e in events if e.get('type') == 'tool_result' and e.get('name') == 'read_file'
               and not e.get('is_error') and nonce in e.get('output', '')]
    matched = any(c.get('tool_call_id') and c.get('tool_call_id') == r.get('tool_call_id') for c in calls for r in results)
    terminals = [e for e in events if e.get('type') == 'result']
    return bool(matched and len(terminals) == 1 and terminals[0].get('exit_code') == 0 and nonce in terminals[0].get('text', ''))


def recalled(body, marker, value, document_id):
    return any(isinstance(item, dict) and item.get('document_id') == document_id
               and marker in item.get('text', '') and value in item.get('text', '')
               for item in body.get('results', []))


def combo_tool_ok(body, nonce=None):
    try:
        calls = body['choices'][0]['message']['tool_calls']
        for call in calls:
            fn = call['function']
            args = json.loads(fn['arguments'])
            if (fn['name'] == 'search_observations' and isinstance(args.get('query'), str)
                    and args['query'].strip() and (nonce is None or nonce in args['query'])
                    and body['choices'][0].get('finish_reason') != 'length'):
                return True
    except (KeyError, TypeError, ValueError):
        pass
    return False


def route_verdict(checks):
    if any(not c.get('structured') or not c.get('required_tool_call') for c in checks):
        return 'NOT VERIFIED'
    if any(not c.get('served') or not c.get('primary') or c.get('fallback') is not False
           or c.get('served') != c.get('primary') for c in checks):
        return 'INCONCLUSIVE'
    return 'VERIFIED'


def doctor(path, manifest):
    import yaml
    checks = []
    key = secrets().get('OMNIROUTE_API_KEY', '')
    for name, url, headers in [
        ('local_props', 'http://127.0.0.1:18434/props', {'Authorization': 'Bearer ' + yaml.safe_load((HERMES_HOME / 'config.yaml').read_text(encoding='utf-8')).get('providers', {}).get('llamacpp', {}).get('api_key', '')}),
        ('omni_models', OMNI + '/v1/models', {'Authorization': 'Bearer ' + key}),
        ('hindsight_health', HINDSIGHT + '/health', {})]:
        try:
            status, body, _ = request('GET', url, headers=headers, timeout=10)
            checks.append({'name': name, 'http': status, 'ok': status == 200})
        except Exception as exc:
            checks.append({'name': name, 'ok': False, 'error_type': type(exc).__name__})
    checks.append({'name': 'hermes_executable', 'ok': HERMES.is_file()})
    record(path, manifest, 'doctor', 'VERIFIED' if all(c['ok'] for c in checks) else 'NOT VERIFIED', checks,
           scope='Health and executable presence only; no inference or production writes.')


def combos(path, manifest):
    key = secrets().get('OMNIROUTE_API_KEY', '')
    checks = []
    with sqlite3.connect('file:C:/Users/jun/.omniroute/storage.sqlite?mode=ro', uri=True) as db:
        primary = {name: json.loads(data)['models'][0]['model'] for name, data in db.execute('select name,data from combos') if name in COMBOS}
    for combo in COMBOS:
        item = {'requested': combo, 'served': None, 'primary': primary.get(combo), 'fallback': 'unknown'}
        try:
            headers = {'Authorization': 'Bearer ' + key, 'x-omniroute-no-memory': 'true', 'x-omniroute-no-cache': 'true'}
            nonce = uuid.uuid4().hex
            tool = {'type': 'function', 'function': {'name': 'search_observations', 'description': 'Search observations',
                    'parameters': {'type': 'object', 'properties': {'query': {'type': 'string'}}, 'required': ['query']}}}
            base = {'model': combo, 'max_tokens': 256}
            structured_request = {**base,
                'messages': [{'role': 'user', 'content': 'Return JSON object with key nonce and value ' + nonce}],
                'response_format': {'type': 'json_schema', 'json_schema': {'name': 'verify_nonce', 'strict': True, 'schema': {'type': 'object', 'properties': {'nonce': {'type': 'string'}}, 'required': ['nonce'], 'additionalProperties': False}}}}
            status, body, hdr = request('POST', OMNI + '/v1/chat/completions', structured_request, headers, 180)
            try:
                structured = status == 200 and body['choices'][0].get('finish_reason') != 'length' and json.loads(body['choices'][0]['message']['content']) == {'nonce': nonce}
            except (KeyError, TypeError, ValueError):
                structured = False
            tool_nonce = uuid.uuid4().hex
            tool_request = {**base, 'messages': [{'role': 'user', 'content': 'Search observations for verification ' + tool_nonce}],
                'tools': [tool], 'tool_choice': {'type': 'function', 'function': {'name': 'search_observations'}}}
            st2, b2, h2 = request('POST', OMNI + '/v1/chat/completions', tool_request, headers, 180)
            safe_headers = lambda h: {k: v for k, v in h.items() if k.startswith('x-omniroute-')}
            (path / (combo + '.json')).write_text(json.dumps({'structured_request': structured_request, 'structured_response': body, 'structured_headers': safe_headers(hdr), 'tool_request': tool_request, 'tool_response': b2, 'tool_headers': safe_headers(h2)}, ensure_ascii=False, indent=2), encoding='utf-8')
            served, tool_served = hdr.get('x-omniroute-model'), h2.get('x-omniroute-model')
            def same(a, b):
                return bool(a and b and (a == b or a.split('/', 1)[-1] == b.split('/', 1)[-1]))
            matches = same(served, item['primary']) and same(tool_served, item['primary'])
            item.update({'http_structured': status, 'http_tool': st2, 'structured': structured,
                         'required_tool_call': st2 == 200 and combo_tool_ok(b2, tool_nonce), 'served': served,
                         'tool_served': tool_served, 'cache': hdr.get('x-omniroute-cache'),
                         'fallback': (not matches) if served and tool_served and item['primary'] else 'unknown'})
            if matches:
                item['served'] = item['primary']
        except Exception as exc:
            item['error_type'] = type(exc).__name__
        checks.append(item)
    # Upstream identity is not established merely by a requested combo name.
    verdict = route_verdict(checks)
    record(path, manifest, 'combos', verdict, checks, scope='Primary comparison uses persisted priority order and both response model headers. A working fallback is inconclusive for primary health.')


def hermes_tool(path, manifest, local, candidate_model=None):
    import yaml
    name = 'local-tool' if local else 'hermes-tool'
    attempt = uuid.uuid4().hex
    scratch = scratch_dir(manifest['run_id'])
    home, work = create_profile(path, manifest, name), scratch / 'work'
    if not runtime_ready(home, frozen=True):
        record(path, manifest, name, 'NOT VERIFIED', [{'prepared_runtime_imports_available': False}],
               scope='Hermes launch stopped to avoid automatic dependency or shared source update work.')
        return
    conf = yaml.safe_load((HERMES_HOME / 'config.yaml').read_text(encoding='utf-8'))
    provider = 'llamacpp' if local else 'omni'
    alias = 'local' if local else 'omni'
    selected = conf.get('providers', {}).get(provider)
    model_alias = conf.get('model_aliases', {}).get(alias)
    if not selected or not model_alias:
        raise ValueError('selected provider or model alias missing')
    if not local and candidate_model is not None:
        model_alias = dict(model_alias)
        model_alias['model'] = candidate_model
    if local:
        counters = {}
        try:
            req = urllib.request.Request('http://127.0.0.1:18434/metrics',
                                         headers={'Authorization': 'Bearer ' + selected.get('api_key', '')})
            with urllib.request.urlopen(req, timeout=10) as response:
                metrics = response.read().decode('utf-8')
            required = {'llamacpp:requests_processing', 'llamacpp:requests_deferred'}
            for line in metrics.splitlines():
                fields = line.split()
                if len(fields) == 2 and fields[0] in required:
                    counters[fields[0]] = float(fields[1])
            idle = set(counters) == required and all(value == 0 for value in counters.values())
        except Exception:
            idle = False
        (path / ('local-idle-' + attempt + '.json')).write_text(json.dumps({'idle': idle, 'counters': counters}), encoding='utf-8')
        if not idle:
            record(path, manifest, name, 'INCONCLUSIVE', [{'local_requests_confirmed_idle': False}], scope='Local inference skipped because idle state was not established from metrics.')
            return
    config = {'providers': {provider: selected}, 'model_aliases': {alias: model_alias},
              'model': {'default': model_alias.get('model'), 'provider': provider},
              'agent': {'run_budget_seconds': 180},
              'fallback_providers': [], 'plugins': {'enabled': [], 'disabled': ['hindsight']},
              'memory': {'provider': 'local', 'memory_enabled': False, 'user_profile_enabled': False},
              'auth': {'adopt_external_logins': False}, 'security': {'allow_lazy_installs': False},
              'terminal': {'backend': 'local', 'cwd': str(work)},
              'onboarding': {'seen': True}}
    (home / 'config.yaml').write_text(yaml.safe_dump(config, allow_unicode=True), encoding='utf-8')
    (home / '.env').write_text('OMNIROUTE_API_KEY=' + secrets().get('OMNIROUTE_API_KEY', '') + '\n', encoding='utf-8')
    nonce = 'VERIFY-' + uuid.uuid4().hex
    note = work / 'secret.txt'
    note.write_text(nonce, encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if k.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC', 'PATHEXT', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA'}}
    env.update({k: v for k, v in secrets().items() if k == 'OMNIROUTE_API_KEY'})
    env = {k: v for k, v in env.items() if not k.upper().startswith(('HINDSIGHT_', 'HERMES_'))}
    runtime_env = dict(env)
    runtime_env.pop('HERMES_HOME', None)
    runtime = json.loads(subprocess.check_output([str(HERMES), '--print-runtime-command'], env=runtime_env, text=True, timeout=15))
    env.update({'HERMES_HOME': str(home), 'HERMES_DISABLE_LAZY_INSTALLS': '1'})
    prompt = f'Read {note} with read_file. Reply with the exact content only.'
    agent_log = home / 'logs' / 'agent.log'
    log_start = agent_log.stat().st_size if agent_log.exists() else 0
    process = subprocess.Popen(runtime + ['chat', '--oneshot', '-q', prompt, '-m', alias,
                                '--source', 'tool', '--toolsets', 'file', '--max-turns', '4', '--format', 'stream-json'], cwd=work, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
    manifest['subprocess_ids'].append(process.pid)
    save(path, manifest)
    try:
        stdout, stderr = process.communicate(timeout=240)
    except subprocess.TimeoutExpired:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
        else:
            process.kill()
        stdout, stderr = process.communicate()
        for secret in list(secrets().values()) + [selected.get('api_key', ''), conf.get('providers', {}).get('llamacpp', {}).get('api_key', '')]:
            if len(secret) >= 8:
                stdout, stderr = stdout.replace(secret, '[REDACTED]'), stderr.replace(secret, '[REDACTED]')
        (path / (name + '-' + attempt + '-stdout.jsonl')).write_text(stdout, encoding='utf-8')
        (path / (name + '-' + attempt + '-stderr.txt')).write_text(stderr, encoding='utf-8')
        record(path, manifest, name, 'NOT VERIFIED', [{'timeout': True}])
        return
    for secret in list(secrets().values()) + [selected.get('api_key', ''), conf.get('providers', {}).get('llamacpp', {}).get('api_key', '')]:
        if len(secret) >= 8:
            stdout, stderr = stdout.replace(secret, '[REDACTED]'), stderr.replace(secret, '[REDACTED]')
    (path / (name + '-' + attempt + '-stdout.jsonl')).write_text(stdout, encoding='utf-8')
    (path / (name + '-' + attempt + '-stderr.txt')).write_text(stderr, encoding='utf-8')
    log = ''
    if agent_log.exists():
        with agent_log.open('rb') as f:
            f.seek(log_start)
            log = f.read().decode('utf-8', errors='replace')
    for secret in list(secrets().values()) + [selected.get('api_key', ''), conf.get('providers', {}).get('llamacpp', {}).get('api_key', '')]:
        if len(secret) >= 8:
            log = log.replace(secret, '[REDACTED]')
    (path / (name + '-' + attempt + '-agent.log')).write_text(log, encoding='utf-8')
    events = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
            if isinstance(event, dict):
                events.append(event)
        except ValueError:
            pass
    ok = process.returncode == 0 and tool_ok(events, nonce, str(note))
    endpoint = selected.get('base_url', '')
    endpoint_seen = bool(endpoint and ('base_url=' + endpoint) in log)
    record(path, manifest, name, ('VERIFIED' if endpoint_seen else 'INCONCLUSIVE') if ok else 'NOT VERIFIED',
           [{'process_exit': process.returncode, 'read_file_and_exact_nonce': ok,
             'event_types': [e.get('type') for e in events], 'terminal_result_count': sum(e.get('type') == 'result' for e in events), 'isolated_log_endpoint_seen': endpoint_seen}],
           attempt=attempt, route={'requested_alias': alias, **({'requested_model': model_alias.get('model')} if not local else {}),
                  'provider': provider, 'fallback_disabled': True,
                  'actual_upstream': 'unknown'}, scope='Endpoint from this invocation’s isolated agent log. Downstream upstream model is not established.')


def memory(path, manifest):
    bank = manifest['bank_id']
    base = HINDSIGHT + '/v1/default/banks/' + bank
    marker, value, doc = 'marker' + uuid.uuid4().hex, 'value' + uuid.uuid4().hex, 'doc-' + uuid.uuid4().hex
    tag = 'run:' + manifest['run_id']
    create_status, create_body, _ = request('PUT', base, {}, timeout=30)
    submission = {'async': True, 'items': [{'document_id': doc,
        'content': f'The verification identifier {marker} has the stored value {value}.', 'tags': [tag]}]}
    status, body, _ = request('POST', base + '/memories', submission, timeout=30)
    op = body.get('operation_id')
    if not op:
        record(path, manifest, 'memory', 'NOT VERIFIED', [{'submission_http': status, 'operation_id_present': False}])
        return
    state = ''
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        _, state_body, _ = request('GET', base + '/operations/' + op, timeout=15)
        state = state_body.get('status', '')
        if state in ('completed', 'failed', 'cancelled', 'not_found'):
            break
        time.sleep(3)
    if state != 'completed':
        record(path, manifest, 'memory', 'INCONCLUSIVE' if state in ('', 'pending', 'processing') else 'NOT VERIFIED',
               [{'submission_http': status, 'operation_id': op, 'operation_status': state}])
        return
    ds, document, _ = request('GET', base + '/documents/' + doc, timeout=20)
    recall_request = {'query': 'What is the stored value of ' + marker + '?',
        'types': ['world', 'experience'], 'max_tokens': 800}
    rs, recalled_body, _ = request('POST', base + '/memories/recall', recall_request, timeout=120)
    (path / 'memory-api.json').write_text(json.dumps({'bank_create_http': create_status, 'bank_create': create_body,
        'submission_request': submission, 'submission_http': status, 'submission': body, 'operation': state_body, 'document_http': ds,
        'document': document, 'recall_request': recall_request, 'recall_http': rs, 'recall': recalled_body}, ensure_ascii=False, indent=2), encoding='utf-8')
    doc_ok = document.get('id') == doc and document.get('bank_id') == bank and tag in document.get('tags', []) and document.get('memory_unit_count', 0) > 0
    recall_ok = recalled(recalled_body, marker, value, doc)
    record(path, manifest, 'memory', 'VERIFIED' if ds == 200 and rs == 200 and doc_ok and recall_ok else 'NOT VERIFIED',
           [{'operation_id': op, 'operation_status': state, 'document_http': ds, 'document_id': doc,
             'document_tags_and_positive_units': doc_ok, 'recall_http': rs, 'linked_result_marker_and_value': recall_ok}],
           scope='Hindsight API only. Hermes Hindsight plugin integration is not verified.')


def plugin_tool(events, name, marker, value, recall=False):
    terminals = [e for e in events if e.get('type') == 'result']
    if len(terminals) != 1 or terminals[0].get('exit_code') != 0 or not terminals[0].get('session_id'):
        return None
    for call in events:
        args = call.get('input', {})
        if call.get('type') != 'tool_use' or call.get('name') != name or not isinstance(args, dict):
            continue
        query = args.get('query' if recall else 'content', '')
        if not isinstance(query, str) or marker not in query or (value in query if recall else value not in query):
            continue
        for result in events:
            if not call.get('tool_call_id') or result.get('tool_call_id') != call['tool_call_id'] or result.get('type') != 'tool_result' or result.get('name') != name or result.get('is_error'):
                continue
            try:
                output = json.loads(result.get('output', ''))
            except (ValueError, TypeError):
                continue
            text = output.get('result', '') if isinstance(output, dict) else ''
            if not isinstance(text, str):
                continue
            if recall and value in text and value in terminals[0].get('text', ''):
                return terminals[0]['session_id']
            if not recall and text in ('Memory stored successfully.', 'Memory queued for storage.'):
                return terminals[0]['session_id']
    return None


def plugin_memory(path, manifest):
    import yaml
    source = HERMES_HOME / 'hermes-agent'
    bank = manifest['bank_id']
    if bank != 'verify-hermes-' + str(uuid.UUID(manifest['run_id'])):
        raise ValueError('unsafe bank identity')
    home = create_profile(path, manifest, 'plugin-memory')
    work = scratch_dir(manifest['run_id']) / 'work'
    attempt = uuid.uuid4().hex
    checks = []
    stage = 'profile'
    selected_secrets = []
    tracked = []
    before = None
    verdict = 'NOT VERIFIED'
    details = {}
    def fingerprints():
        return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None for p in tracked}
    def redact(text):
        for secret in selected_secrets:
            if len(secret) >= 8:
                text = text.replace(secret, '[REDACTED]')
        return text
    def evidence(name, content):
        (path / ('plugin-memory-' + attempt + '-' + name)).write_text(redact(content), encoding='utf-8')
    def run_chat(prompt, label, env, runtime):
        agent_log = home / 'logs' / 'agent.log'
        log_start = agent_log.stat().st_size if agent_log.exists() else 0
        process = subprocess.Popen(runtime + ['chat', '--oneshot', '-q', prompt, '-m', 'omni', '--source', 'tool', '--max-turns', '4', '--format', 'stream-json'], cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
        manifest['subprocess_ids'].append(process.pid)
        save(path, manifest)
        try:
            stdout, stderr = process.communicate(timeout=240)
        except subprocess.TimeoutExpired:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
            else:
                process.kill()
            stdout, stderr = process.communicate()
            evidence(label + '-stdout.jsonl', stdout)
            evidence(label + '-stderr.txt', stderr)
            if agent_log.exists():
                with agent_log.open('rb') as log:
                    log.seek(log_start)
                    evidence(label + '-agent.log', log.read().decode('utf-8', errors='replace'))
            raise TimeoutError(label + ' timed out')
        evidence(label + '-stdout.jsonl', stdout)
        evidence(label + '-stderr.txt', stderr)
        if agent_log.exists():
            with agent_log.open('rb') as log:
                log.seek(log_start)
                evidence(label + '-agent.log', log.read().decode('utf-8', errors='replace'))
        events = []
        for line in stdout.splitlines():
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    events.append(item)
            except ValueError:
                continue
        if process.returncode:
            raise RuntimeError(label + ' process failed: ' + str(process.returncode))
        return events
    try:
        conf = yaml.safe_load((HERMES_HOME / 'config.yaml').read_text(encoding='utf-8'))
        provider, alias = conf['providers']['omni'], conf['model_aliases']['omni']
        key = secrets().get('OMNIROUTE_API_KEY', '')
        if not key or not provider or not alias:
            raise ValueError('missing selected provider, alias or credential')
        selected_secrets = [key, str(provider.get('api_key', ''))]
        config = {'providers': {'omni': provider}, 'model_aliases': {'omni': alias},
                  'model': {'default': alias['model'], 'provider': 'omni'}, 'fallback_providers': [],
                  'plugins': {'enabled': ['hindsight']}, 'memory': {'provider': 'hindsight'},
                  'auth': {'adopt_external_logins': False}, 'security': {'allow_lazy_installs': False},
                  'terminal': {'backend': 'local', 'cwd': str(work)}, 'onboarding': {'seen': True}}
        (home / 'config.yaml').write_text(yaml.safe_dump(config, allow_unicode=True), encoding='utf-8')
        (home / '.env').write_text('OMNIROUTE_API_KEY=' + key + '\n', encoding='utf-8')
        (home / 'gateway.parked').touch()
        plugin = HERMES_HOME / 'plugins' / 'hindsight'
        if not (plugin / '__init__.py').is_file():
            raise ValueError('installed Hindsight plugin missing')
        (home / 'plugins').mkdir()
        plugin_link = home / 'plugins' / 'hindsight'
        junction_code = "New-Item -ItemType Junction -Path '" + str(plugin_link).replace("'", "''") + "' -Target '" + str(plugin).replace("'", "''") + "' | Out-Null"
        junction = subprocess.run(['powershell', '-NoProfile', '-Command', junction_code],
                                 capture_output=True, text=True, timeout=15)
        if junction.returncode != 0 or plugin_link.resolve() != plugin.resolve():
            raise RuntimeError('installed plugin junction could not be validated')
        (home / 'hindsight').mkdir()
        settings = {'mode': 'local_external', 'api_url': HINDSIGHT, 'bank_id': bank, 'bank_id_template': '',
                    'additional_banks': [], 'recall_additional_banks': [], 'trusted_project_dirs': [],
                    'mirror_to_own_bank': False, 'recall_types': ['observation', 'world', 'experience'],
                    'retain_tags': ['agent:hermes'], 'memory_mode': 'tools'}
        (home / 'hindsight' / 'config.json').write_text(json.dumps(settings), encoding='utf-8')
        env = {k: v for k, v in os.environ.items() if k.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC', 'PATHEXT', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA'}}
        env.update({'HERMES_HOME': str(home), 'HINDSIGHT_BANK_ID': bank,
                    'HERMES_DISABLE_LAZY_INSTALLS': '1', 'OMNIROUTE_API_KEY': key})
        key_hash = hashlib.sha256(str(source.resolve()).encode('utf-8')).hexdigest()[:16]
        facts = HERMES_HOME / 'installs' / key_hash / 'facts.json'
        tracked = [facts, facts.parent / 'source-completion-pending', source / 'install-stamp.json']
        before = fingerprints()
        environment = Path(json.loads(facts.read_text(encoding='utf-8'))['packages']['venv']['environment'])
        python = environment / 'Scripts' / 'python.exe'
        if not python.is_file():
            raise ValueError('active environment python missing')
        stage = 'preflight'
        probe = ("import sys,json;sys.path[:0]=[sys.argv[1],sys.argv[2]];import pm;"
                 "from hermes_cli.venv_sync import completion_pending_path;from pathlib import Path;"
                 "from hindsight import HindsightMemoryProvider;p=HindsightMemoryProvider();"
                 "p.initialize('verify-probe',cwd=sys.argv[3]);r=Path(sys.argv[1]);"
                 "print(json.dumps({'current':pm.venv_is_current(project_root=r),'pending':completion_pending_path(r).exists(),"
                 "'banks':p._write_bank_ids,'recall_banks':p._recall_additional_bank_ids,'api_url':p._api_url}))")
        result = subprocess.run([str(python), '-c', probe, str(source), str(plugin.parent), str(work)], cwd=work, env=env, capture_output=True, text=True, timeout=40, check=True)
        state = json.loads(result.stdout.strip().splitlines()[-1])
        checks.append({'preflight': state, 'lazy_install_disabled': True,
                       'installed_plugin_reused_read_only': str(plugin)})
        if state.get('banks') != [bank] or state.get('recall_banks') != [] or state.get('api_url') != HINDSIGHT:
            raise ValueError('unsafe provider or runtime state')
        runtime = json.loads(subprocess.check_output([str(HERMES), '--print-runtime-command'], env=env, text=True, timeout=30))
        if not isinstance(runtime, list) or not runtime:
            raise ValueError('runtime command invalid')
        marker, value = 'marker' + uuid.uuid4().hex, 'value' + uuid.uuid4().hex
        base = HINDSIGHT + '/v1/default/banks/' + bank
        stage = 'bank creation'
        status, _, _ = request('PUT', base, {}, timeout=30)
        if status not in (200, 201):
            raise RuntimeError('bank creation HTTP ' + str(status))
        stage = 'retain'
        (home / 'gateway.parked').unlink(missing_ok=True)
        events = run_chat(f'Use hindsight_retain to store this exact fact: {marker} has the value {value}.', 'retain', env, runtime)
        sid = plugin_tool(events, 'hindsight_retain', marker, value)
        checks.append({'retain_tool_and_result': bool(sid), 'session_id': sid})
        if not sid:
            raise ValueError('retain tool invocation or result missing')
        stage = 'persistence'
        recall_request = {'query': 'What is the stored value of ' + marker + '?', 'types': ['world', 'experience'], 'max_tokens': 800}
        deadline = time.monotonic() + 180
        linked = None
        api_evidence = {}
        while time.monotonic() < deadline:
            rs, body, _ = request('POST', base + '/memories/recall', recall_request, timeout=30)
            if rs == 200 and isinstance(body, dict):
                for item in body.get('results', []):
                    if not isinstance(item, dict) or marker not in item.get('text', '') or value not in item.get('text', '') or not item.get('document_id'):
                        continue
                    doc_id = item['document_id']
                    ds, document, _ = request('GET', base + '/documents/' + urllib.parse.quote(doc_id, safe=''), timeout=20)
                    api_evidence = {'recall': body, 'document': document, 'document_http': ds}
                    if ds == 200 and document.get('id') == doc_id and document.get('bank_id') == bank and 'agent:hermes' in document.get('tags', []) and document.get('memory_unit_count', 0) > 0:
                        linked = doc_id
                        break
            if linked:
                break
            time.sleep(3)
        evidence('api.json', json.dumps(api_evidence, ensure_ascii=False, indent=2))
        checks.append({'linked_document': linked, 'marker_and_value_in_recall': bool(linked)})
        if not linked:
            raise ValueError('persisted linked document not established')
        stage = 'cross-session recall'
        events = run_chat(f'Use hindsight_recall to find the value associated with {marker}. Reply with the value.', 'recall', env, runtime)
        second = plugin_tool(events, 'hindsight_recall', marker, value, recall=True)
        checks.append({'recall_tool_and_value': bool(second), 'session_id': second, 'different_session': bool(second and second != sid)})
        if not second or second == sid:
            raise ValueError('cross-session recall not established')
        verdict = 'VERIFIED'
        details['pending_update'] = state.get('pending')
    except Exception as exc:
        checks.append({'stage': stage, 'error_type': type(exc).__name__, 'error': redact(str(exc))})
    finally:
        if before is not None:
            after = fingerprints()
            details['installation_fingerprints'] = {'before': before, 'after': after}
            if before != after:
                verdict = 'NOT VERIFIED'
                checks.append({'installation_state_unchanged': False})
        record(path, manifest, 'plugin-memory', verdict, checks, attempt=attempt, **details)


def profile_released(item):
    answer = item.get('answer')
    status = item.get('status')
    if (isinstance(answer, dict)
            and answer.get('error') == "profile '" + item.get('profile', '') + "' is not served"):
        return (isinstance(status, dict) and isinstance(status.get('answered_at'), (int, float))
                and isinstance(status.get('served_profiles'), list)
                and item['profile'] not in status['served_profiles'])
    return (isinstance(answer, dict) and answer.get('unserved') == item.get('profile')
            and isinstance(answer.get('served_profiles'), list)
            and item['profile'] not in answer['served_profiles'])


def cleanup(path, manifest):
    scratch = scratch_dir(manifest['run_id'])
    profile_names = []
    for entry in manifest.get('profiles', []):
        profile = Path(entry)
        if profile.is_symlink() or profile.resolve().parent != (HERMES_HOME / 'profiles').resolve():
            raise ValueError('profile path escapes root')
        if not profile.name.startswith('verify-' + manifest['run_id'] + '-'):
            owner = profile / 'verify-owner.json'
            if profile.exists() and (not owner.is_file() or json.loads(owner.read_text(encoding='utf-8')).get('run_id') != manifest['run_id']):
                raise ValueError('profile ownership missing')
        if profile.exists():
            (profile / 'gateway.parked').touch()
            profile_names.append(profile.name)
    if profile_names:
        source = HERMES_HOME / 'hermes-agent'
        key = hashlib.sha256(str(source.resolve()).encode()).hexdigest()[:16]
        facts = HERMES_HOME / 'installs' / key / 'facts.json'
        python = Path(json.loads(facts.read_text(encoding='utf-8'))['packages']['venv']['environment']) / 'Scripts' / 'python.exe'
        code = ("import sys,json;from pathlib import Path;sys.path.insert(0,sys.argv[1]);"
                "from gateway.control_socket import request_unserve_profile,query_gateway_control;"
                "items=[{'profile':n,'answer':request_unserve_profile(Path(sys.argv[2]),n)} for n in json.loads(sys.argv[3])];"
                "status=query_gateway_control(Path(sys.argv[2]),'status');"
                "print(json.dumps([{**item,'status':status} for item in items]))")
        env = dict(os.environ, HERMES_DISABLE_LAZY_INSTALLS='1')
        result = subprocess.run([str(python), '-c', code, str(source), str(HERMES_HOME), json.dumps(profile_names)],
                                env=env, capture_output=True, text=True, timeout=15 + 9 * len(profile_names), check=True)
        (path / ('cleanup-unserve-' + uuid.uuid4().hex + '.json')).write_text(result.stdout, encoding='utf-8')
        replies = json.loads(result.stdout.strip().splitlines()[-1])
        if (not isinstance(replies, list) or {x.get('profile') for x in replies} != set(profile_names)
                or not all(profile_released(x) for x in replies)):
            raise RuntimeError('owned profile release not confirmed; scratch preserved')
    if os.name == 'nt':
        for pidfile in [scratch / 'home' / 'gateway.pid'] + [Path(p) / 'gateway.pid' for p in manifest.get('profiles', [])]:
            if not pidfile.exists():
                continue
            info = json.loads(pidfile.read_text(encoding='utf-8'))
            if Path(info.get('hermes_home', '')).resolve() != pidfile.parent.resolve():
                raise RuntimeError('gateway ownership cannot be established')
            pid, started = int(info['pid']), int(info['start_time'])
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
            kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x1000 | 0x0001 | 0x00100000, False, pid)
            if not handle:
                if ctypes.get_last_error() == 87:
                    continue
                raise RuntimeError('gateway process check failed')
            try:
                creation, exit_time, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
                if not kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(kernel_time), ctypes.byref(user_time)):
                    raise RuntimeError('gateway process identity unavailable')
                ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
                centiseconds = round((ticks - 116444736000000000) / 10000000 * 100)
                if abs(centiseconds - started) < 2:
                    if not kernel.TerminateProcess(handle, 0) or kernel.WaitForSingleObject(handle, 10000) != 0:
                        raise RuntimeError('owned gateway did not stop')
            finally:
                kernel.CloseHandle(handle)
        script = "$p='C:\\vh\\" + manifest['run_id'] + "\\'; @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($p, [StringComparison]::OrdinalIgnoreCase) }).Count"
        check = subprocess.run(['powershell', '-NoProfile', '-Command', script], capture_output=True, text=True, timeout=20)
        if check.returncode != 0 or not check.stdout.strip().isdigit() or int(check.stdout.strip()) != 0:
            raise RuntimeError('scratch process check failed or active process exists')
    for entry in manifest.get('profiles', []):
        candidate = Path(entry)
        prefix = 'verify-' + manifest['run_id'] + '-'
        name = candidate.name
        if not name.startswith(prefix):
            import re
            if not re.fullmatch(r'verify-[0-9a-f]{12}-(hermes-tool|local-tool|plugin-memory)', name):
                raise ValueError('invalid profile ownership')
            profile = HERMES_HOME / 'profiles' / name
            if candidate != profile or profile.is_symlink() or profile.resolve().parent != (HERMES_HOME / 'profiles').resolve():
                raise ValueError('invalid profile')
            if profile.exists():
                owner = json.loads((profile / 'verify-owner.json').read_text(encoding='utf-8'))
                if owner != {'run_id': manifest['run_id'], 'feature': name.split('-', 2)[2]}:
                    raise ValueError('invalid profile ownership marker')
                shutil.rmtree(profile)
            continue
        suffix = name[len(prefix):]
        feature = next((f for f in ('hermes-tool', 'local-tool', 'plugin-memory') if suffix == f or suffix.startswith(f + '-')), None)
        if feature is None:
            raise ValueError('invalid profile feature')
        attempt = suffix[len(feature):].lstrip('-')
        if len(attempt) == 32:
            uuid.UUID(attempt)
            profile = HERMES_HOME / 'profiles' / name
            if profile.is_symlink() or profile.resolve().parent != (HERMES_HOME / 'profiles').resolve():
                raise ValueError('invalid profile')
        else:
            profile = profile_dir(manifest['run_id'], feature, attempt)
        if candidate != profile:
            raise ValueError('invalid profile ownership')
        if profile.exists():
            shutil.rmtree(profile)
    if scratch.exists():
        shutil.rmtree(scratch)
    record(path, manifest, 'cleanup', 'VERIFIED', [{'scratch_removed': not scratch.exists(), 'bank_preserved': manifest['bank_id']}])


@contextlib.contextmanager
def run_lock(path):
    lock = (path / '.lock').open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError('run is in use') from exc
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['launch', 'doctor', 'combos', 'hermes-tool', 'local-tool', 'memory', 'plugin-memory', 'cleanup', 'all'])
    parser.add_argument('--run', help='UUID returned by launch')
    parser.add_argument('--model', help='direct model name for hermes-tool')
    args = parser.parse_args()
    if args.model is not None and args.command != 'hermes-tool':
        parser.error('--model is only supported for hermes-tool')
    if args.command == 'launch':
        launch()
        return
    if not args.run:
        parser.error('--run is required')
    try:
        path, manifest = load_run(args.run)
    except Exception as exc:
        print(json.dumps({'verdict': 'NOT VERIFIED', 'error_type': type(exc).__name__, 'stage': 'load_run'}))
        return 1
    tasks = {'doctor': lambda: doctor(path, manifest), 'combos': lambda: combos(path, manifest),
             'hermes-tool': lambda: hermes_tool(path, manifest, False, args.model), 'local-tool': lambda: hermes_tool(path, manifest, True),
             'memory': lambda: memory(path, manifest), 'plugin-memory': lambda: plugin_memory(path, manifest), 'cleanup': lambda: cleanup(path, manifest)}
    selected = ['doctor', 'combos', 'hermes-tool', 'local-tool', 'memory', 'plugin-memory'] if args.command == 'all' else [args.command]
    try:
        with run_lock(path):
            _, manifest = load_run(args.run)
            for name in selected:
                try:
                    tasks[name]()
                except Exception as exc:
                    record(path, manifest, name, 'NOT VERIFIED', [{'error_type': type(exc).__name__}])
    except (OSError, RuntimeError) as exc:
        print(json.dumps({'verdict': 'NOT VERIFIED', 'stage': 'run_lock', 'error_type': type(exc).__name__}))
        return 1
    verdicts = [manifest['results'][name]['verdict'] for name in selected]
    return 1 if 'NOT VERIFIED' in verdicts else 2 if 'INCONCLUSIVE' in verdicts else 0


if __name__ == '__main__':
    raise SystemExit(main())
