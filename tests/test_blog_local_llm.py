import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('local_llm', Path(__file__).parents[1] / 'integrations/blog-writer/local_llm.py')
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class LocalRequestTests(unittest.TestCase):
    def test_local_request_uses_key_file_and_preserves_image_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'key'
            path.write_text('${LOCAL_SECRET}\n')
            with patch.dict(os.environ, {'BLOG_LLM_API_KEY': '', 'BLOG_LLM_API_KEY_FILE': str(path)}):
                body = {'messages': [{'content': [{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,AA=='}}]}]}
                request = client.make_request(client.DEFAULT_URL, body)
                self.assertEqual(request.get_header('Authorization'), 'Bearer ${LOCAL_SECRET}')
                self.assertEqual(json.loads(request.data), body)

    def test_local_key_is_not_sent_to_a_remote_url(self):
        with patch.dict(os.environ, {'BLOG_LLM_API_KEY': '', 'BLOG_LLM_API_KEY_FILE': 'must-not-read'}):
            request = client.make_request('https://example.com/v1/chat/completions', {})
            self.assertIsNone(request.get_header('Authorization'))

    def test_explicit_credential_can_serve_an_overridden_endpoint(self):
        with patch.dict(os.environ, {'BLOG_LLM_API_KEY': '${LOCAL_SECRET}'}):
            request = client.make_request('https://example.com/v1/chat/completions', {})
            self.assertEqual(request.get_header('Authorization'), 'Bearer ${LOCAL_SECRET}')


if __name__ == '__main__':
    unittest.main()
