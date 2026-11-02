import unittest
from unittest import mock
import tempfile
from pathlib import Path
import verify_stack as v


class VerifierTests(unittest.TestCase):
    def test_candidate_changes_only_isolated_cloud_alias(self):
        import yaml
        source = {'providers': {'omni': {'base_url': 'example'}},
                  'model_aliases': {'omni': {'model': 'original', 'provider': 'omni'},
                                    'local': {'model': 'local-model'}}}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'config.yaml').write_text('ignored', encoding='utf-8')
            captured = []
            def capture(config, **kwargs):
                captured.append(config)
                raise RuntimeError('stop before execution')
            with mock.patch.object(v, 'HERMES_HOME', root), \
                 mock.patch.object(v, 'scratch_dir', return_value=root), \
                 mock.patch.object(v, 'create_profile', return_value=root), \
                 mock.patch.object(v, 'runtime_ready', return_value=True), \
                 mock.patch.object(yaml, 'safe_load', return_value=source), \
                 mock.patch.object(yaml, 'safe_dump', side_effect=capture):
                with self.assertRaisesRegex(RuntimeError, 'stop before execution'):
                    v.hermes_tool(root, {'run_id': 'run'}, False, 'candidate')
            self.assertEqual(captured[0]['model_aliases']['omni']['model'], 'candidate')
            self.assertEqual(captured[0]['model']['default'], 'candidate')
            self.assertEqual(source['model_aliases']['omni']['model'], 'original')
            self.assertEqual(source['model_aliases']['local']['model'], 'local-model')

    def test_model_option_rejected_for_other_commands(self):
        with mock.patch('sys.argv', ['verify_stack.py', 'launch', '--model', 'candidate']):
            with self.assertRaises(SystemExit) as result, mock.patch('sys.stderr'):
                v.main()
        self.assertEqual(result.exception.code, 2)

    def test_cleanup_of_never_served_profile_requires_fresh_status(self):
        item = {'profile': 'owned', 'answer': {'error': "profile 'owned' is not served"}}
        self.assertFalse(v.profile_released(item))
        item['status'] = {'served_profiles': ['owned'], 'answered_at': 1}
        self.assertFalse(v.profile_released(item))
        item['status'] = {'served_profiles': ['default'], 'answered_at': 1}
        self.assertTrue(v.profile_released(item))
        item['answer'] = {'error': 'control socket unavailable'}
        self.assertFalse(v.profile_released(item))

    def test_cleanup_requires_confirmed_profile_release(self):
        self.assertFalse(v.profile_released({'profile': 'owned', 'answer': None}))
        self.assertFalse(v.profile_released({'profile': 'owned', 'answer': {'unserved': 'other', 'served_profiles': []}}))
        self.assertFalse(v.profile_released({'profile': 'owned', 'answer': {'unserved': 'owned', 'served_profiles': ['owned']}}))
        self.assertTrue(v.profile_released({'profile': 'owned', 'answer': {'unserved': 'owned', 'served_profiles': ['default']}}))

    def test_plugin_requires_matched_actual_tool_and_terminal(self):
        events = [{'type': 'tool_use', 'name': 'hindsight_retain', 'tool_call_id': 'x', 'input': {'content': 'marker value'}},
                  {'type': 'tool_result', 'name': 'hindsight_retain', 'tool_call_id': 'x', 'output': '{"result":"Memory queued for storage."}'},
                  {'type': 'result', 'exit_code': 0, 'session_id': 'one', 'text': 'done'}]
        self.assertEqual(v.plugin_tool(events, 'hindsight_retain', 'marker', 'value'), 'one')
        self.assertIsNone(v.plugin_tool(events[1:], 'hindsight_retain', 'marker', 'value'))
        events[1]['tool_call_id'] = 'other'
        self.assertIsNone(v.plugin_tool(events, 'hindsight_retain', 'marker', 'value'))
        events[1]['tool_call_id'] = 'x'
        events[1]['output'] = '{"error":"failed"}'
        self.assertIsNone(v.plugin_tool(events, 'hindsight_retain', 'marker', 'value'))

    def test_recall_requires_value_from_tool_and_terminal_without_leaking_it_in_query(self):
        events = [{'type': 'tool_use', 'name': 'hindsight_recall', 'tool_call_id': 'y', 'input': {'query': 'marker'}},
                  {'type': 'tool_result', 'name': 'hindsight_recall', 'tool_call_id': 'y', 'output': '{"result":"marker value"}'},
                  {'type': 'result', 'exit_code': 0, 'session_id': 'two', 'text': 'value'}]
        self.assertEqual(v.plugin_tool(events, 'hindsight_recall', 'marker', 'value', True), 'two')
        events[0]['input']['query'] = 'marker value'
        self.assertIsNone(v.plugin_tool(events, 'hindsight_recall', 'marker', 'value', True))
        events[0]['input']['query'] = 'marker'
        events[1]['output'] = '{"result":"no result"}'
        self.assertIsNone(v.plugin_tool(events, 'hindsight_recall', 'marker', 'value', True))
        events[1]['output'] = '{"result":"marker value"}'
        events[2]['text'] = 'no result'
        self.assertIsNone(v.plugin_tool(events, 'hindsight_recall', 'marker', 'value', True))

    def test_runtime_preflight_rejects_pending_stale_or_unreadable_state(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(v, 'HERMES_HOME', Path(temp)):
            python = Path(temp) / 'installs' / 'x' / 'environments' / 'x' / 'venv' / 'Scripts' / 'python.exe'
            python.parent.mkdir(parents=True)
            python.touch()
            import json, hashlib
            key = hashlib.sha256(str((Path(temp) / 'hermes-agent').resolve()).encode()).hexdigest()[:16]
            facts = Path(temp) / 'installs' / key / 'facts.json'
            facts.parent.mkdir(parents=True)
            facts.write_text(json.dumps({'packages': {'venv': {'environment': str(python.parent.parent)}}}), encoding='utf-8')
            with mock.patch.object(v.subprocess, 'run') as run:
                for output, expected in [('{"current":true,"pending":false}', True),
                                         ('{"current":true,"pending":true}', False),
                                         ('{"current":false,"pending":false}', False),
                                         ('invalid', False)]:
                    run.return_value.stdout = output
                    self.assertEqual(v.runtime_ready(Path(temp) / 'profile'), expected)
                run.return_value.stdout = '{"current":false,"pending":true}'
                self.assertTrue(v.runtime_ready(Path(temp) / 'profile', frozen=True))

    def test_recall_requires_result_text_and_document(self):
        self.assertFalse(v.recalled({'query': 'marker value', 'results': []}, 'marker', 'value', 'doc'))
        self.assertFalse(v.recalled({'results': [{'text': 'marker value', 'document_id': 'other'}]}, 'marker', 'value', 'doc'))
        self.assertTrue(v.recalled({'results': [{'text': 'marker value', 'document_id': 'doc'}]}, 'marker', 'value', 'doc'))

    def test_tool_requires_actual_matching_call_and_result(self):
        events = [{'type': 'tool_use', 'name': 'read_file', 'tool_call_id': '1', 'input': {'path': 'C:\\work\\note'}},
                  {'type': 'tool_result', 'name': 'read_file', 'tool_call_id': '1', 'output': 'secret'},
                  {'type': 'result', 'exit_code': 0, 'text': 'secret'}]
        self.assertTrue(v.tool_ok(events, 'secret', 'C:\\work\\note'))
        self.assertFalse(v.tool_ok(events[1:], 'secret', 'C:\\work\\note'))
        self.assertFalse(v.tool_ok(events[:-1], 'secret', 'C:\\work\\note'))
        events[1]['tool_call_id'] = 'other'
        self.assertFalse(v.tool_ok(events, 'secret', 'C:\\work\\note'))
        del events[0]['tool_call_id']
        del events[1]['tool_call_id']
        self.assertFalse(v.tool_ok(events, 'secret', 'C:\\work\\note'))

    def test_forced_tool_rejects_text_and_wrong_name(self):
        self.assertFalse(v.combo_tool_ok({'choices': [{'message': {'content': 'search_observations'}}]}))
        self.assertFalse(v.combo_tool_ok({'choices': [{'message': {'tool_calls': [{'function': {'name': 'wrong', 'arguments': '{"query":"x"}'}}]}}]}))
        self.assertTrue(v.combo_tool_ok({'choices': [{'message': {'tool_calls': [{'function': {'name': 'search_observations', 'arguments': '{"query":"x"}'}}]}}]}))
        body = {'choices': [{'finish_reason': 'length', 'message': {'tool_calls': [{'function': {'name': 'search_observations', 'arguments': '{"query":"nonce"}'}}]}}]}
        self.assertFalse(v.combo_tool_ok(body, 'nonce'))
        body['choices'][0]['finish_reason'] = 'tool_calls'
        self.assertFalse(v.combo_tool_ok(body, 'other'))
        self.assertTrue(v.combo_tool_ok(body, 'nonce'))

    def test_run_traversal_rejected(self):
        with self.assertRaises(ValueError):
            v.run_dir('../outside')
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(v, 'RUNS', Path(temp)):
            outside = Path(temp).parent / 'outside'
            with self.assertRaises(ValueError):
                v.run_dir(str(outside))
        with self.assertRaises(ValueError):
            v.scratch_dir('../outside')
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(v, 'SCRATCH', Path(temp)):
            self.assertEqual(v.scratch_dir('19a62b48-2fe6-4cee-b0eb-72ca24bceeac').parent, Path(temp))

    def test_unknown_route_is_never_primary_proof(self):
        self.assertEqual(v.route_verdict([{'structured': True, 'required_tool_call': True,
                                           'served': None, 'primary': None, 'fallback': 'unknown'}]), 'INCONCLUSIVE')

    def test_run_lock_rejects_concurrent_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            with v.run_lock(Path(temp)):
                with self.assertRaises((OSError, RuntimeError)):
                    with v.run_lock(Path(temp)):
                        pass
            with v.run_lock(Path(temp)):
                pass


if __name__ == '__main__':
    unittest.main()
