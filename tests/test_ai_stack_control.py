import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('ai_control', Path(__file__).parents[1] / '.cursor/skills/verify-ai-stack/scripts/control_stack.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class ControlTests(unittest.TestCase):
    def test_local_idle_requires_both_counters_and_no_active_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key = root / 'runtimes/llamacpp/.api_key'
            key.parent.mkdir(parents=True)
            key.write_text('${LOCAL_SECRET}')
            for metrics, expected in ((b'llamacpp:requests_processing 0\n', False),
                    (b'llamacpp:requests_processing 1\nllamacpp:requests_deferred 0\n', False),
                    (b'llamacpp:requests_processing 0\nllamacpp:requests_deferred 0\n', True)):
                with self.subTest(metrics=metrics), patch.object(control, 'HOME', root), \
                     patch('urllib.request.urlopen', return_value=io.BytesIO(metrics)):
                    self.assertEqual(control.local_idle(root), expected)

    def test_drive_refuses_a_run_without_passing_doctor(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(control, 'process_inventory') as probe:
            self.assertFalse(control.drive_ready(Path(tmp), {'results': {}}))
            probe.assert_not_called()

    def test_drive_refuses_changed_process_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous = {'listeners': {'18434': [{'pid': 10, 'created': 1}]}, 'model_processes': [{'pid': 10}]}
            (Path(tmp) / 'host-identity.json').write_text(json.dumps(previous))
            current = {**previous, 'listeners': {'18434': [{'pid': 10, 'created': 2}]}}
            with patch.object(control, 'process_inventory', return_value=current):
                self.assertFalse(control.drive_ready(Path(tmp), {'results': {'doctor': {'verdict': 'VERIFIED'}}}))

    def test_passing_implemented_journeys_do_not_claim_manual_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = {'run_id': 'test', 'results': {name: {'verdict': 'VERIFIED'} for name in ('doctor',) + control.JOURNEYS}}
            with contextlib.redirect_stdout(io.StringIO()):
                code = control.report(Path(tmp), manifest)
            report = json.loads((Path(tmp) / 'coverage.json').read_text())
            self.assertEqual(code, 2)
            self.assertEqual(report['environment_verdict'], 'INCONCLUSIVE')
            self.assertEqual(report['not_run'], [])
            self.assertIn('Hermes Desktop model switch and resume UI', report['manual_or_unimplemented'])

    def test_unexecuted_and_failed_journeys_remain_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = {'run_id': 'test', 'results': {'doctor': {'verdict': 'NOT VERIFIED'}}}
            with contextlib.redirect_stdout(io.StringIO()):
                code = control.report(Path(tmp), manifest)
            report = json.loads((Path(tmp) / 'coverage.json').read_text())
            self.assertEqual(code, 1)
            self.assertIn('plugin-memory', report['not_run'])
            self.assertEqual(report['environment_verdict'], 'NOT VERIFIED')

    def test_saved_evidence_redacts_known_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            value = ''.join(('private', '-fixture-', 'value'))
            with patch.object(control, 'HOME', Path(tmp)), patch.object(control.legacy, 'secrets', return_value={'TEST_API_KEY': value}):
                control.evidence(Path(tmp), 'proof.json', {'response': value})
            saved = (Path(tmp) / 'proof.json').read_text()
            self.assertNotIn(value, saved)
            self.assertIn('${LOCAL_SECRET}', saved)

    def test_readable_but_stale_dashboard_does_not_pass(self):
        def response(_method, url, **_kwargs):
            status = 400 if 'invalid' in url else 404 if 'not-a-route' in url else 200
            return status, {'last_collect_at': '2000-01-01T00:00:00Z', 'last_collect_error': None}, {}
        with tempfile.TemporaryDirectory() as tmp:
            manifest = {'run_id': 'test', 'results': {}}
            html = io.BytesIO('<h1>OmniRoute 대시보드</h1>'.encode())
            html.status = 200
            with patch('urllib.request.urlopen', return_value=html), patch.object(control.legacy, 'request', side_effect=response), \
                 contextlib.redirect_stdout(io.StringIO()):
                control.dashboard(Path(tmp), manifest)
            self.assertEqual(manifest['results']['dashboard']['verdict'], 'NOT VERIFIED')
            self.assertTrue(any(c['name'].startswith('collector_fresh') and not c['ok'] for c in manifest['results']['dashboard']['checks']))


if __name__ == '__main__':
    unittest.main()
