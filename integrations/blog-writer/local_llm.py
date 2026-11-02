"""Authenticated requests to the shared local llama.cpp endpoint."""
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request

DEFAULT_URL = 'http://127.0.0.1:18434/v1/chat/completions'


def make_request(url, body):
    headers = {'Content-Type': 'application/json'}
    credential = os.environ.get('BLOG_LLM_API_KEY', '').strip()
    target = urllib.parse.urlsplit(url)
    if not credential and target.hostname in {'127.0.0.1', 'localhost', '::1'} and target.port == 18434:
        local_appdata = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local')
        path = Path(os.environ.get('BLOG_LLM_API_KEY_FILE') or local_appdata / 'Hermes/runtimes/llamacpp/.api_key')
        credential = path.read_text(encoding='utf-8').strip()
        if not credential:
            raise ValueError('The local llama.cpp API key file is empty')
    if credential:
        headers['Authorization'] = 'Bearer ' + credential
    return urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers=headers)
