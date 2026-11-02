import json
import importlib.util
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'snapshot.py'
VERIFY = SCRIPT.with_name('verify_repo.py')


class SnapshotTest(unittest.TestCase):
    def test_relocation_removes_old_prefix_and_repeats_without_duplication(self):
        spec = importlib.util.spec_from_file_location('prepare_snapshot', SCRIPT.with_name('prepare_snapshot.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module.ROOT = root
            module.REPLACEMENTS = {'guide.md': [('C:/old/root/skills/verify', 'skills/verify'), ('Heading', 'Heading\nRun from root')]}
            (root / 'provenance').mkdir()
            (root / 'provenance/sources.json').write_text(json.dumps([{'target': 'guide.md'}]), encoding='utf-8')
            (root / 'guide.md').write_text('Heading\nC:/old/root/skills/verify/run.py', encoding='utf-8')
            module.prepare()
            module.prepare()
            self.assertEqual((root / 'guide.md').read_text(encoding='utf-8'), 'Heading\nRun from root\nskills/verify/run.py')

    def test_rejects_output_overlapping_any_source(self):
        sys.path.insert(0, str(SCRIPT.parent))
        from snapshot import export
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second = root / 'first.env', root / 'second.env'
            original = 'API_KEY=' + 'fake-original-value' + '\n'
            first.write_text(original, encoding='utf-8')
            second.write_text(original, encoding='utf-8')
            manifest = root / 'manifest.json'
            for entries in ([{'source': str(first), 'target': 'first.env', 'kind': 'env'}],
                            [{'source': str(first), 'target': 'second.env', 'kind': 'env'},
                             {'source': str(second), 'target': 'output.env', 'kind': 'env'}]):
                manifest.write_text(json.dumps(entries), encoding='utf-8')
                with self.assertRaises(ValueError):
                    export(manifest, root, [])
                self.assertEqual(first.read_text(encoding='utf-8'), original)
                self.assertEqual(second.read_text(encoding='utf-8'), original)

    def test_encryption_keys_and_salts_are_secrets_but_token_budgets_are_not(self):
        sys.path.insert(0, str(SCRIPT.parent))
        from snapshot import is_secret_key, scrub_env
        self.assertTrue(is_secret_key('STORAGE_ENCRYPTION_KEY'))
        self.assertTrue(is_secret_key('MACHINE_ID_SALT'))
        self.assertFalse(is_secret_key('token_budget'))
        text = 'STORAGE_ENCRYPTION_KEY=' + 'synthetic-value' + '\nMACHINE_ID_SALT=' + 'synthetic-salt' + '\nREQUIRE_API_KEY=true\n'
        result = scrub_env(text, ())
        self.assertEqual(result, 'STORAGE_ENCRYPTION_KEY=${LOCAL_SECRET}\nMACHINE_ID_SALT=${LOCAL_SECRET}\nREQUIRE_API_KEY=true\n')

    def test_prepare_repeats_and_rejects_missing_expected_content(self):
        spec = importlib.util.spec_from_file_location('prepare_snapshot', SCRIPT.with_name('prepare_snapshot.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module.ROOT = root
            module.REPLACEMENTS = {'a.txt': [('old', 'new')], 'b.txt': [('before', 'after')]}
            (root / 'provenance').mkdir()
            (root / 'a.txt').write_text('old', encoding='utf-8')
            (root / 'b.txt').write_text('unexpected', encoding='utf-8')
            provenance = root / 'provenance' / 'sources.json'
            provenance.write_text(json.dumps([{'target': 'a.txt'}, {'target': 'b.txt'}]), encoding='utf-8')
            with self.assertRaises(ValueError):
                module.prepare()
            self.assertEqual((root / 'a.txt').read_text(encoding='utf-8'), 'old')
            (root / 'b.txt').write_text('before', encoding='utf-8')
            module.prepare()
            first = provenance.read_bytes()
            module.prepare()
            self.assertEqual(provenance.read_bytes(), first)
            records = json.loads(first)
            self.assertEqual(records[0]['snapshot_sha256'], hashlib.sha256(b'new').hexdigest())
            self.assertTrue(records[0]['adjustments'])

    def test_export_and_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source'
            output = root / 'output'
            source.mkdir()
            secret = 'synthetic' + '-credential-12345'
            files = {
                'local.env': 'API_KEY=' + secret + '\nMODEL=qwen\n',
                'config.json': json.dumps({'nested': {'api' + '_key': secret, 'model': 'qwen', 'max_tokens': 500}, 'url': 'https://' + 'user:pass' + '@example.org/path?' + 'token=' + secret}),
                'config.yaml': 'model: qwen\nmax_tokens: 42\nnested:\n  client_secret: ' + secret + '\n',
                'run.py': 'value = "' + secret + '"\n',
            }
            for name, value in files.items():
                (source / name).write_text(value, encoding='utf-8')
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'entries': [{'source': str(source / name), 'target': name if name != 'local.env' else 'example.env', 'kind': kind} for name, kind in [('local.env', 'env'), ('config.json', 'json'), ('config.yaml', 'yaml'), ('run.py', 'copy')]]}), encoding='utf-8')
            result = subprocess.run([sys.executable, str(SCRIPT), '--manifest', str(manifest), '--output', str(output), '--secret-file', str(source / 'local.env')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for path in output.rglob('*'):
                if path.is_file():
                    self.assertNotIn(secret, path.read_text(encoding='utf-8'))
            config = json.loads((output / 'config.json').read_text(encoding='utf-8'))
            self.assertEqual(config['nested']['model'], 'qwen')
            self.assertEqual(config['nested']['max_tokens'], 500)
            self.assertNotIn('user:pass', config['url'])
            self.assertIn('${LOCAL_SECRET}', config['url'])
            self.assertIn('max_tokens: 42', (output / 'config.yaml').read_text(encoding='utf-8'))
            self.assertEqual((source / 'run.py').read_text(encoding='utf-8'), files['run.py'])
            result = subprocess.run([sys.executable, str(VERIFY), str(output)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout)
            (output / 'bad.db').write_text('x')
            result = subprocess.run([sys.executable, str(VERIFY), str(output)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)

    def test_source_and_configuration_preserve_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret = 'synthetic-known-' + 'secret-54321'
            source = root / 'source'
            source.mkdir()
            python_source = ("api_key = values.get('API_KEY')\n"
                             "headers = {'Authorization': 'Bearer ' + key}\n"
                             "schema = {'api_key': 'str'}\n"
                             "def foo(api_key: str):\n    return api_key\n"
                             f"known = '{secret}'\n")
            inputs = {
                'run.py': (python_source, 'copy'),
                'settings.json': (json.dumps({'token': 'hidden', 'secret': 'hidden',
                    'max_tokens': 123, 'model_max_tokens': 456, 'auth': True,
                    'DATABASE_URL': 'postgresql://' + 'user:pass@localhost/db',
                    'url': 'postgres://' + 'user:pass@localhost/db'}), 'json'),
                'local.env': (f'BOT_TOKEN="{secret} with spaces"\nPGPASSWORD=hidden\nMAX_TOKENS=400\n', 'env'),
            }
            entries = []
            for name, (content, kind) in inputs.items():
                path = source / name
                path.write_text(content, encoding='utf-8')
                entries.append({'source': str(path), 'target': '.env.example' if kind == 'env' else name, 'kind': kind})
            corpus = source / 'secrets.env'
            corpus.write_text(f'API_KEY={secret}\n', encoding='utf-8')
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps(entries), encoding='utf-8')
            output = root / 'output'
            result = subprocess.run([sys.executable, str(SCRIPT), '--manifest', str(manifest),
                                     '--output', str(output), '--secret-file', str(corpus)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            copied = (output / 'run.py').read_text(encoding='utf-8')
            self.assertEqual(copied, python_source.replace(secret, '${LOCAL_SECRET}'))
            compile(copied, 'run.py', 'exec')
            settings = json.loads((output / 'settings.json').read_text(encoding='utf-8'))
            self.assertEqual(settings['token'], '${LOCAL_SECRET}')
            self.assertEqual(settings['secret'], '${LOCAL_SECRET}')
            self.assertEqual(settings['DATABASE_URL'], '${LOCAL_SECRET}')
            self.assertEqual(settings['url'], 'postgres://${LOCAL_SECRET}@localhost/db')
            self.assertEqual(settings['max_tokens'], 123)
            self.assertEqual(settings['model_max_tokens'], 456)
            self.assertIs(settings['auth'], True)
            self.assertEqual((output / '.env.example').read_text(encoding='utf-8'),
                             'BOT_TOKEN=${LOCAL_SECRET}\nPGPASSWORD=${LOCAL_SECRET}\nMAX_TOKENS=400\n')
            for name, (content, _) in inputs.items():
                self.assertEqual((source / name).read_text(encoding='utf-8'), content)
            result = subprocess.run([sys.executable, str(VERIFY), str(output)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout)
            (output / '.env.example').write_text('TELEGRAM_BOT_TOKEN="secret with spaces"\n', encoding='utf-8')
            result = subprocess.run([sys.executable, str(VERIFY), str(output)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)

    def test_verifier_flags_static_assignment_not_dynamic_expression(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'run.py'
            source.write_text("api_key = values.get('API_KEY')\nheaders = {'Authorization': 'Bearer ' + key}\n", encoding='utf-8')
            result = subprocess.run([sys.executable, str(VERIFY), str(root)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout)
            source.write_text("api_key = 'hardcoded-value'\n", encoding='utf-8')
            result = subprocess.run([sys.executable, str(VERIFY), str(root)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)

    def test_rejects_escape_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'input.txt'
            source.write_text('safe', encoding='utf-8')
            manifest = root / 'manifest.json'
            for target in ('../escape', 'safe/../escape', 'C:/escape', '/escape', 'safe/%2e%2e/../../../escape'):
                manifest.write_text(json.dumps([{'source': str(source), 'target': target, 'kind': 'copy'}]), encoding='utf-8')
                result = subprocess.run([sys.executable, str(SCRIPT), '--manifest', str(manifest), '--output', str(root / 'output')], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0, target)
                self.assertFalse((root / 'escape').exists())
            self.assertEqual(source.read_text(encoding='utf-8'), 'safe')


if __name__ == '__main__':
    unittest.main()
