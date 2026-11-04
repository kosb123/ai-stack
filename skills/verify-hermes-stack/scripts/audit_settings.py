"""Read the live stack's non-secret settings and compare integration boundaries."""
import ast
import hashlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import urllib.request
import urllib.parse

import yaml

HOME = Path('C:/Users/jun/AppData/Local/Hermes')
ROOT = Path(__file__).resolve().parents[3]
TUNER = Path(__file__).resolve().parents[3] / 'omniroute/autotune'
LIVE_KEYS = {
    'HINDSIGHT_API_LLM_BASE_URL', 'HINDSIGHT_API_LLM_MODEL',
    'HINDSIGHT_API_MENTAL_MODEL_REFRESH_LLM_MODEL', 'HINDSIGHT_API_LLM_REASONING_EFFORT',
    'HINDSIGHT_API_RERANKER_MAX_CANDIDATES', 'HINDSIGHT_API_REFLECT_MAX_CONTEXT_TOKENS',
    'HINDSIGHT_API_LLM_MAX_CONCURRENT', 'HINDSIGHT_API_MENTAL_MODEL_REFRESH_LLM_MAX_CONCURRENT',
    'HINDSIGHT_API_RETAIN_MAX_COMPLETION_TOKENS',
}


def collect():
    checks, state = [], {'captured_at': datetime.now(timezone.utc).isoformat()}

    def check(name, ok, detail):
        checks.append({'name': name, 'ok': bool(ok), 'detail': detail})

    configs = []
    for path in [HOME / 'config.yaml'] + [HOME / 'profiles' / name / 'config.yaml' for name in ('autotest', 'blog', 'campaign')]:
        cfg = yaml.safe_load(path.read_text(encoding='utf-8'))
        provider = cfg.get('providers', {}).get('llamacpp', {})
        models = provider.get('models', {})
        ctx = models.get('Qwen3.8-27B-UD-Q4_K_M', {}).get('context_length')
        compression = cfg.get('compression', {})
        cap, ratio = compression.get('threshold_tokens'), compression.get('threshold')
        item = {'path': str(path), 'default': cfg.get('model', {}).get('default'),
                'provider': cfg.get('model', {}).get('provider'), 'local_url': provider.get('base_url'),
                'local_context': ctx, 'compression_cap': cap, 'compression_ratio': ratio,
                'effective_compression_trigger': min(cap, int(ctx * ratio)) if cap and ctx and ratio else None,
                'memory_provider': cfg.get('memory', {}).get('provider'),
                'local_alias_provider': cfg.get('model_aliases', {}).get('local', {}).get('provider')}
        configs.append(item)
        check(path.parent.name + ' local context and memory routing',
              item['local_url'] == 'http://127.0.0.1:18434/v1' and ctx == 163840
              and item['local_alias_provider'] == 'llamacpp' and item['memory_provider'] == 'hindsight', item)
    state['hermes_configs'] = configs

    env_file = Path('E:/hindsight/hindsight.env')
    expected = {}
    for line in env_file.read_text(encoding='utf-8').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            if key in LIVE_KEYS:
                expected[key] = value.strip().strip('"')
    source = HOME / 'hermes-agent'
    key = hashlib.sha256(str(source.resolve()).encode()).hexdigest()[:16]
    facts = json.loads((HOME / 'installs' / key / 'facts.json').read_text(encoding='utf-8'))
    runtime = Path(facts['packages']['venv']['environment']) / 'Scripts' / 'python.exe'
    # Read only the allowlisted settings from the actual API listener, never dump its environment.
    probe = '''import json, sys, psutil
from pathlib import Path
keys = json.loads(sys.argv[1])
pids = {c.pid for c in psutil.net_connections(kind="tcp")
        if c.status == "LISTEN" and c.laddr.port == 8888}
if len(pids) != 1:
    raise RuntimeError("Expected one Hindsight listener")
p = psutil.Process(pids.pop())
if not any(Path(arg).resolve() == Path("E:/hindsight/run_api.py").resolve()
           for arg in p.cmdline()[1:]):
    raise RuntimeError("Unexpected process owns Hindsight port")
env = p.environ()
print(json.dumps({"pid": p.pid, "settings": {k: env[k] for k in keys if k in env},
                  "startup_fsync_on": "fsync=on" in env.get("HINDSIGHT_API_DATABASE_URL", "")}))
'''
    proc = subprocess.run([str(runtime), '-c', probe, json.dumps(sorted(LIVE_KEYS))],
                          capture_output=True, text=True, encoding='utf-8', timeout=20)
    if proc.returncode:
        raise RuntimeError('Could not inspect native Hindsight process')
    native = json.loads(proc.stdout)
    live = native['settings']
    state['native_process'] = {'pid': native['pid'], 'env_file': str(env_file)}
    state['hindsight_effective_settings'] = live
    check('Hindsight process environment matches configured routing',
          bool(live) and all(live.get(k) == v for k, v in expected.items()),
          {'mismatched_keys': [k for k, v in expected.items() if live.get(k) != v]})

    sql = ("SELECT json_build_object('fsync',current_setting('fsync'),"
           "'full_page_writes',current_setting('full_page_writes'),"
           "'synchronous_commit',current_setting('synchronous_commit'),"
           "'data_directory',current_setting('data_directory'))")
    storage_proc = subprocess.run(
        ['C:/Users/jun/.pg0/installation/18.1.0/bin/psql.exe', '-XAt', '-v', 'ON_ERROR_STOP=1',
         '-h', '127.0.0.1', '-p', '5432', '-U', 'hindsight', '-d', 'hindsight', '-c', sql],
        env=dict(os.environ, PGPASSWORD='hindsight', PGCONNECT_TIMEOUT='5'),
        capture_output=True, text=True, encoding='utf-8', timeout=10, check=True)
    storage = json.loads(storage_proc.stdout)
    state['hindsight_storage'] = storage
    check('Hindsight PostgreSQL durability and data directory',
          all(storage.get(k) == 'on' for k in ('fsync', 'full_page_writes', 'synchronous_commit'))
          and Path(storage['data_directory']).resolve() == Path('E:/hindsight/pg0/instances/hindsight/data').resolve(), storage)
    startup_url = next((line.partition('=')[2].strip() for line in env_file.read_text(encoding='utf-8').splitlines()
                        if line.startswith('HINDSIGHT_API_DATABASE_URL=')), '')
    startup_on = startup_url.startswith('pg0') and dict(urllib.parse.parse_qsl(startup_url.partition('?')[2])).get('fsync') == 'on'
    state['storage_startup'] = {'configured_fsync_on': startup_on,
                                'api_environment_refresh_pending': startup_on and not native['startup_fsync_on']}
    check('Hindsight startup DB durability policy', startup_on, state['storage_startup'])

    dbpath = Path('C:/Users/jun/.omniroute/storage.sqlite')
    with sqlite3.connect(dbpath.as_uri() + '?mode=ro', uri=True) as db:
        combos = {name: json.loads(data) for name, data in db.execute('select name,data from combos')}
    state['combos'] = {name: {'strategy': combo.get('strategy'),
                             'models': [m.get('model') for m in combo.get('models', [])],
                             'config': {k: combo.get('config', {}).get(k) for k in
                                        ('hedging', 'hedgeDelayMs', 'disableSessionStickiness', 'maxRetries')}}
                       for name, combo in combos.items()}
    for name in ('hermes-fast-agent', 'hindsight-structured', 'hindsight-refresh'):
        combo = combos.get(name, {})
        check(name + ' has priority fallback members', combo.get('strategy') == 'priority' and len(combo.get('models', [])) > 1,
              state['combos'].get(name, {}))

    tree = ast.parse((TUNER / 'autotune.py').read_text(encoding='utf-8'))
    tuner_cfg = next(ast.literal_eval(node.value) for node in tree.body
                     if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'CONFIG' for t in node.targets))
    tuner_state = json.loads((TUNER / 'state.json').read_text(encoding='utf-8'))
    pins = {}
    for lane in tuner_cfg.get('lanes', []):
        current = state['combos'].get(lane['combo'], {}).get('models', [])
        for pin in lane.get('pinned', []):
            status = tuner_state.get('preferred_status', {}).get(pin['model'], {})
            pins[pin['model']] = {'desired_rank': pin['rank'], 'status': status, 'in_runtime_combo': pin['model'] in current}
            check('suspended pin excluded from ' + lane['combo'],
                  not status.get('suspended') or pin['model'] not in current, pins[pin['model']])
    state['lane_pins'] = pins
    state['checks'] = checks
    state['verdict'] = 'VERIFIED' if all(x['ok'] for x in checks) else 'NOT VERIFIED'
    return state


if __name__ == '__main__':
    destination = ROOT / 'evidence' / 'settings-audit.json'
    if len(sys.argv) > 1:
        destination = Path(sys.argv[1]).resolve()
    try:
        snapshot = collect()
    except Exception as exc:
        snapshot = {'verdict': 'NOT VERIFIED', 'error_type': type(exc).__name__}
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'verdict': snapshot['verdict'], 'evidence': str(destination),
                      'failed_checks': [x['name'] for x in snapshot.get('checks', []) if not x['ok']]}, ensure_ascii=False))
    raise SystemExit(0 if snapshot['verdict'] == 'VERIFIED' else 1)
