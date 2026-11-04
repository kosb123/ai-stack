import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('stack_control', Path(__file__).with_name('control_stack.py'))
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class EnvironmentTests(unittest.TestCase):
    def test_stopped_dashboard_is_unreachable_and_keeps_evidence(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            path = Path(tmp)
            (path / 'host-identity.json').write_text(json.dumps({'listeners': {'20140': []}}))
            manifest = {'results': {}}
            with patch.object(control.legacy, 'request') as request:
                control.dashboard(path, manifest)
            self.assertEqual(manifest['results']['dashboard']['coverage'], 'verified-unreachable')
            request.assert_not_called()
            control.preserve_attempt(path, 'doctor')
            self.assertEqual(len(list(path.glob('attempts/*/host-identity.json'))), 1)

    def test_same_model_from_another_provider_is_not_primary_proof(self):
        headers = {'x-omniroute-model': 'model', 'x-omniroute-provider': 'other'}
        self.assertFalse(control.served_primary('first/model', headers))
        headers['x-omniroute-provider'] = 'antigravity'
        self.assertFalse(control.served_primary('agy/model', headers))
        self.assertTrue(control.served_primary('antigravity/model', headers))

    def test_a_foreign_local_endpoint_is_not_driven(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            home = Path(tmp)
            (home / 'config.yaml').write_text('providers:\n  llamacpp:\n    base_url: http://foreign.invalid/v1\n')
            manifest = {'results': {}}
            with patch.object(control, 'HOME', home), patch.object(control.legacy, 'hermes_tool') as drive:
                control.hermes_tool(home, manifest, True)
            drive.assert_not_called()
            self.assertEqual(manifest['results']['local-tool']['verdict'], 'NOT VERIFIED')

    def test_hindsight_tool_contract_and_token_budget_are_sent(self):
        sent = []
        def request(method, url, payload=None, headers=None, timeout=20):
            sent.append(dict(payload))
            return 200, {}, {}
        def inherited(path, manifest):
            for body in ({'model': 'hindsight-refresh', 'tools': [{}]},
                         {'model': 'hermes-fast-agent', 'response_format': {}}):
                control.legacy.request('POST', control.legacy.OMNI + '/v1/chat/completions', body)
            manifest['results']['combos'] = {'checks': [{'requested': 'hindsight-refresh', 'primary': 'agy/model',
                'structured': True, 'required_tool_call': True}]}
            (path / 'hindsight-refresh.json').write_text(json.dumps({
                key: {'x-omniroute-provider': 'antigravity', 'x-omniroute-model': 'model'}
                for key in ('structured_headers', 'tool_headers')}))
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()), \
             patch.object(control.legacy, 'request', side_effect=request), patch.object(control.legacy, 'combos', side_effect=inherited):
            manifest = {'results': {}}
            control.combos(Path(tmp), manifest)
        self.assertEqual(sent[0]['tool_choice'], 'required')
        self.assertEqual(sent[0]['max_tokens'], 1024)
        self.assertEqual(sent[1]['max_tokens'], 4096)
        self.assertEqual(sent[0]['reasoning_effort'], 'low')

    def test_all_stops_before_another_cli_after_failure(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            manifest = {'results': {}}
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(patch.object(control.legacy, 'load_run', return_value=(Path(tmp), manifest)))
            stack.enter_context(patch.object(control.legacy, 'run_lock', side_effect=lambda _: contextlib.nullcontext()))
            stack.enter_context(patch.object(sys, 'argv', ['control_stack.py', 'all', '--run', 'owned']))
            for name in ('doctor', 'settings', 'operations', 'autotune', 'services', 'dashboard'):
                def passed(path, state, name=name):
                    state['results'][name] = {'verdict': 'VERIFIED'}
                stack.enter_context(patch.object(control, name, side_effect=passed))
            stack.enter_context(patch.object(control, 'drive_ready', return_value=True))
            def failed(path, state, local):
                state['results']['local-tool'] = {'verdict': 'NOT VERIFIED'}
            stack.enter_context(patch.object(control, 'hermes_tool', side_effect=failed))
            later = stack.enter_context(patch.object(control, 'blog_direct'))
            self.assertEqual(control.main(), 1)
            later.assert_not_called()
            self.assertTrue(manifest['cli_cleanup_required'])

    def test_owner_change_records_need_for_another_doctor(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            manifest = {'results': {}, 'doctor_required': False}
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(patch.object(control.legacy, 'load_run', return_value=(Path(tmp), manifest)))
            stack.enter_context(patch.object(control.legacy, 'run_lock', side_effect=lambda _: contextlib.nullcontext()))
            stack.enter_context(patch.object(sys, 'argv', ['control_stack.py', 'hermes-tool', '--run', 'owned']))
            stack.enter_context(patch.object(control, 'drive_ready', return_value=False))
            drive = stack.enter_context(patch.object(control, 'hermes_tool'))
            self.assertEqual(control.main(), 1)
            drive.assert_not_called()
            self.assertTrue(manifest['doctor_required'])

    def test_cleanup_cannot_pass_after_removing_nested_evidence(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            path = Path(tmp)
            (path / 'attempts').mkdir()
            proof = path / 'attempts/proof.json'
            proof.write_text('{}')
            manifest = {'results': {}}
            def bad_cleanup(path, state):
                proof.unlink()
                state['results']['cleanup'] = {'verdict': 'VERIFIED'}
                control.legacy.save(path, state)
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(patch.object(control.legacy, 'load_run', return_value=(path, manifest)))
            stack.enter_context(patch.object(control.legacy, 'run_lock', side_effect=lambda _: contextlib.nullcontext()))
            stack.enter_context(patch.object(sys, 'argv', ['control_stack.py', 'cleanup', '--run', 'owned']))
            stack.enter_context(patch.object(control.legacy, 'cleanup', side_effect=bad_cleanup))
            self.assertEqual(control.main(), 1)
            self.assertEqual(manifest['results']['cleanup']['verdict'], 'NOT VERIFIED')

    def test_plugin_polling_deadline_is_inconclusive_not_permanent_loss(self):
        def pending(path, manifest):
            manifest['results']['plugin-memory'] = {'verdict': 'NOT VERIFIED', 'checks': [
                {'retain_tool_and_result': True}, {'stage': 'persistence', 'error': 'persisted linked document not established'}],
                'installation_fingerprints': {'before': {'source': 'same'}, 'after': {'source': 'same'}}}
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()), \
             patch.object(control.legacy, 'plugin_memory', side_effect=pending):
            manifest = {'results': {}}
            control.plugin_memory(Path(tmp), manifest)
            self.assertEqual(manifest['results']['plugin-memory']['verdict'], 'INCONCLUSIVE')

    def test_plugin_timeout_does_not_hide_a_changed_installation(self):
        def changed(path, manifest):
            manifest['results']['plugin-memory'] = {'verdict': 'NOT VERIFIED', 'checks': [
                {'stage': 'persistence', 'error': 'persisted linked document not established'},
                {'installation_state_unchanged': False}],
                'installation_fingerprints': {'before': {'source': 'old'}, 'after': {'source': 'changed'}}}
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()), \
             patch.object(control.legacy, 'plugin_memory', side_effect=changed):
            manifest = {'results': {}}
            control.plugin_memory(Path(tmp), manifest)
            self.assertEqual(manifest['results']['plugin-memory']['verdict'], 'NOT VERIFIED')

    def test_plugin_timeout_without_installation_proof_stays_failed(self):
        def missing(path, manifest):
            manifest['results']['plugin-memory'] = {'verdict': 'NOT VERIFIED', 'checks': [
                {'stage': 'persistence', 'error': 'persisted linked document not established'}]}
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()), \
             patch.object(control.legacy, 'plugin_memory', side_effect=missing):
            manifest = {'results': {}}
            control.plugin_memory(Path(tmp), manifest)
            self.assertEqual(manifest['results']['plugin-memory']['verdict'], 'NOT VERIFIED')

    def test_owned_cleanup_retries_one_transient_permission_error(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(control.legacy, 'cleanup', side_effect=[PermissionError(), None]) as owned, \
             patch.object(control.time, 'sleep'):
            control.cleanup(Path(tmp), {'results': {}})
            self.assertEqual(owned.call_count, 2)
            self.assertTrue((Path(tmp) / 'cleanup-retry.json').is_file())


if __name__ == '__main__':
    unittest.main()
