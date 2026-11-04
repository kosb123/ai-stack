import json
import os
import sqlite3
import subprocess
import difflib
import tomllib
from pathlib import Path

home = Path.home()
repo = Path(__file__).resolve().parents[1]
inputs = Path(os.environ['LOCALAPPDATA']) / 'ai-stack/capture-inputs'
inputs.mkdir(parents=True, exist_ok=True)
hermes = home / 'AppData/Local/Hermes'
entries = []


def add(source, target, kind='copy'):
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(source)
    entries.append({'source': str(source.resolve()), 'target': target, 'kind': kind})


def generated(name, value, target):
    path = inputs / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    add(path, target, 'json')


tuner = home / 'Desktop/TEST/omni-autotune'
for name in ['autotune.py', 'bulk_discover.py', 'verify_cycle.py', 'test_preferred.py',
             'measure.py', 'sweep.py', 'selected_models.json', 'README.md']:
    add(tuner / name, 'omniroute/autotune/' + name, 'json' if name.endswith('.json') else 'copy')
for source in sorted((tuner / 'survey').glob('*.py')):
    add(source, 'omniroute/autotune/survey/' + source.name)
dashboard = home / 'Desktop/TEST/omni-autotune-dashboard/dashboard'
for source in sorted(dashboard.iterdir()):
    if source.is_file() and source.suffix in ('.py', '.js', '.html', '.css', '.md'):
        add(source, 'omniroute/dashboard/' + source.name)

add(hermes / 'config.yaml', 'hermes/config.example.yaml', 'yaml')
add(hermes / '.env', 'hermes/.env.example', 'env')
for profile in ['autotest', 'blog', 'campaign', 'test2']:
    config = hermes / 'profiles' / profile / 'config.yaml'
    if config.exists():
        add(config, 'hermes/profiles/' + profile + '/config.example.yaml', 'yaml')
add(hermes / 'hindsight/config.json', 'hermes/hindsight.example.json', 'json')
for name in ['omni_monitor.py', 'omni_report.py']:
    add(hermes / 'scripts' / name, 'hermes/ops/' + name)
for name in ['Start-External-LlamaCpp.ps1', 'Watch-LocalLlama.ps1', 'Launch-LocalLLM.ps1', 'presets.ini', 'server.json']:
    add(hermes / 'runtimes/llamacpp' / name, 'hermes/llamacpp/' + name,
        'json' if name.endswith('.json') else 'copy')

plugin = hermes / 'plugins/pstack'
for source in sorted(plugin.rglob('*')):
    relative = source.relative_to(plugin)
    if any(part.startswith('.') or part == '__pycache__' for part in relative.parts):
        continue
    if source.is_file() and (source.suffix in ('.py', '.md', '.yaml', '.sh', '.tsv') or source.name == 'LICENSE'):
        add(source, 'hermes/plugins/pstack/' + relative.as_posix())

for name in ['run_api.py', 'start-hindsight.ps1', 'Hindsight.vbs']:
    add(Path('E:/hindsight') / name, 'hindsight/ops/' + name)
add('E:/hindsight/hindsight.env', 'hindsight/native.env.example', 'env')
add(home / '.hindsight/coding-agent.json', 'hindsight/coding-agent.example.json', 'json')
add('E:/paperclip/docker/docker-compose.yml', 'paperclip/docker-compose.example.yml', 'yaml')
add('E:/paperclip/docker/RECOVERY.md', 'paperclip/RECOVERY.md')
add(home / '.codex/hooks.json', 'integrations/codex/hooks.example.json', 'json')
codex = tomllib.loads((home / '.codex/config.toml').read_text(encoding='utf-8'))
generated('codex-mcp.json', {'mcp_servers': {'hindsight': codex.get('mcp_servers', {}).get('hindsight', {})}},
          'integrations/codex/hindsight-mcp.example.json')
for name in ['backup-hindsight.ps1', 'unpause-pages.py']:
    add(home / '.hindsight' / name, 'hindsight/ops/' + name)

verify = home / 'Documents/Codex/2026-11-01/poteto-mode/.cursor/skills/verify-hermes-stack'
for source in sorted(verify.rglob('*')):
    if source.is_file() and source.suffix in ('.md', '.py'):
        add(source, 'skills/verify-hermes-stack/' + source.relative_to(verify).as_posix())

legacy = inputs / 'legacy-claude-setup'
if not (legacy / '.git').exists():
    subprocess.run(['git', 'clone', '--depth', '1',
                    'https://github.com/kosb123/omniroute-claude-setup.git', str(legacy)], check=True)
for source in sorted(legacy.rglob('*')):
    if '.git' in source.relative_to(legacy).parts:
        continue
    if source.is_file() and source.suffix in ('.md', '.ps1'):
        add(source, 'integrations/claude-code/' + source.relative_to(legacy).as_posix())

add(home / '.omniroute/.env', 'omniroute/.env.example', 'env')
add(home / '.omniroute/omniroute-server.vbs', 'omniroute/ops/omniroute-server.vbs')
db = sqlite3.connect('file:' + (home / '.omniroute/storage.sqlite').as_posix() + '?mode=ro', uri=True)
db.row_factory = sqlite3.Row
combos = []
for row in db.execute('SELECT name,data,sort_order,system_message,tool_filter_regex,context_cache_protection FROM combos ORDER BY sort_order,name'):
    item = dict(row)
    item['data'] = json.loads(item['data'])
    combos.append(item)
generated('combos.json', combos, 'omniroute/combos.example.json')
settings = {}
namespaces = ('settings', 'databaseSettings', 'compression', 'modelCompatOverrides', 'scheduler', 'customModels', 'modelAliases', 'managedModelAliasNames', 'mitmAlias')
for namespace in namespaces:
    settings[namespace] = {}
    for row in db.execute('SELECT key,value FROM key_value WHERE namespace=?', (namespace,)):
        try:
            value = json.loads(row['value'])
        except (ValueError, TypeError):
            value = row['value']
        settings[namespace][row['key']] = value
generated('settings.json', settings, 'omniroute/settings.example.json')
columns = 'id,provider,auth_type,priority,is_active,default_model,max_concurrent,rate_limit_protection,quota_visible,quota_window_thresholds_json,rate_limit_overrides_json'
generated('connections.json', [dict(r) for r in db.execute('SELECT ' + columns + ' FROM provider_connections ORDER BY provider,id')], 'omniroute/connections.example.json')
columns = 'id,type,name,prefix,api_type,base_url,chat_path,models_path,custom_headers_json'
nodes = []
for row in db.execute('SELECT ' + columns + ' FROM provider_nodes ORDER BY id'):
    item = dict(row)
    if item['custom_headers_json']:
        item['custom_headers_json'] = json.loads(item['custom_headers_json'])
    nodes.append(item)
generated('nodes.json', nodes, 'omniroute/provider-nodes.example.json')

secret_values = {}
for index, row in enumerate(db.execute('SELECT access_token,refresh_token,api_key,id_token FROM provider_connections')):
    for name in row.keys():
        if row[name]:
            secret_values[f'api_key_{index}_{name}'] = row[name]
for index, row in enumerate(db.execute("SELECT value FROM key_value WHERE namespace='secrets'")):
    if row['value']:
        secret_values[f'api_key_kv_{index}'] = row['value']
(inputs / 'secret-corpus.json').write_text(json.dumps(secret_values), encoding='utf-8')
db.close()

patch = subprocess.check_output(['git', '-C', str(hermes / 'hermes-agent'), 'diff', '--',
                                  'gateway/run_turn_prepare.py', 'tui_gateway/agent_factory.py'])
(inputs / 'hermes-local.patch').write_bytes(patch)
add(inputs / 'hermes-local.patch', 'hermes/patches/installed-source.patch')
patch = subprocess.check_output(['git', '-C', str(tuner), 'diff', 'trial/argon-hindsight-first..fix/lane-pin-cooldown', '--', 'autotune.py', 'test_preferred.py', 'README.md'])
(inputs / 'lane-pin.patch').write_bytes(patch)
add(inputs / 'lane-pin.patch', 'provenance/pending-lane-pin.patch')

pg0 = Path('E:/hindsight/venv/Lib/site-packages/hindsight_api/pg0.py')
baseline = pg0.with_name('pg0.py.bak-before-datadir-20261102')
diff = ''.join(difflib.unified_diff(baseline.read_text(encoding='utf-8').splitlines(True),
                                  pg0.read_text(encoding='utf-8').splitlines(True),
                                  fromfile='a/hindsight_api/pg0.py', tofile='b/hindsight_api/pg0.py'))
(inputs / 'hindsight-pg0.patch').write_text(diff, encoding='utf-8')
add(inputs / 'hindsight-pg0.patch', 'hindsight/patches/native-pg0-data-dir.patch')

metadata = {'omni_autotune': {}, 'hermes_agent': {}, 'pstack': {},
            'hindsight_coding_agents': {'package': '@vectorize-io/hindsight-coding-agents', 'version': '0.8.4'},
            'hindsight_hermes_plugin': {'version': '1.2.3', 'path': str(hermes / 'plugins/hindsight')},
            'note': 'Git source SHAs describe upstream baselines. Uncommitted running sources are preserved by file hashes.'}
for key, source in [('omni_autotune', tuner), ('hermes_agent', hermes / 'hermes-agent'), ('pstack', plugin)]:
    metadata[key] = {'path': str(source), 'head': subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip(),
                     'branch': subprocess.check_output(['git', '-C', str(source), 'branch', '--show-current'], text=True).strip()}
generated('upstreams.json', metadata, 'provenance/upstreams.json')

manifest = {'entries': entries}
(repo / 'snapshot-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'manifest_files': len(entries), 'generated_inputs': str(inputs), 'destination': str(repo)}))

from snapshot import export
from prepare_snapshot import prepare
from verify_repo import verify

secret_files = [inputs / 'secret-corpus.json', hermes / '.env', hermes / 'config.yaml',
                Path('E:/hindsight/hindsight.env'), home / '.hindsight/hindsight.env', home / '.omniroute/.env']
try:
    export(repo / 'snapshot-manifest.json', repo, secret_files)
    prepare()
    problems = verify(repo)
    if problems:
        raise RuntimeError('Repository verification failed; do not push.')
finally:
    (inputs / 'secret-corpus.json').unlink(missing_ok=True)
