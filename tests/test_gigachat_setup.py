import json
import ssl
from unittest.mock import patch
from fastapi.testclient import TestClient
from api import config, llm
from api.main import app


def test_save_key_and_not_return_it(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    client = TestClient(app)
    response = client.post('/api/llm/gigachat', json={'key': 'example-secret', 'scope': 'GIGACHAT_API_PERS', 'model': 'GigaChat'})
    assert response.status_code == 200
    assert 'example-secret' not in response.text
    assert client.get('/api/llm/config').json()['configured'] is True
    assert json.loads((tmp_path / 'gigachat.json').read_text())['key'] == 'example-secret'
    assert client.post('/api/llm/gigachat', json={'key': 'x', 'scope': 'wrong'}).status_code == 422


def test_gigachat_exchanges_key_and_uses_token(monkeypatch):
    monkeypatch.setattr(config, 'llm_configured', lambda: True)
    monkeypatch.setattr(config, 'llm_settings', lambda: {'provider': 'gigachat', 'api_key': 'secret', 'model': 'GigaChat', 'scope': 'GIGACHAT_API_PERS', 'base_url': 'https://api.giga.chat/v1'})
    calls = []
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'access_token': 'temporary'} if len(calls) == 1 else {'choices': [{'message': {'content': 'Ответ'}}]}
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kwargs): calls.append((url, kwargs)); return Response()
    with patch('api.llm.httpx.Client', Client):
        result = llm.ask_llm({'total_apps': 1})
    assert result['opinion'] == 'Ответ'
    assert calls[0][1]['headers']['Authorization'] == 'Basic secret'
    assert calls[1][1]['headers']['Authorization'] == 'Bearer temporary'


def test_gigachat_uses_bundled_ca_without_local_certificate(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(config, 'llm_configured', lambda: True)
    monkeypatch.setattr(config, 'llm_settings', lambda: {'provider': 'gigachat', 'api_key': 'test', 'model': 'GigaChat', 'scope': 'GIGACHAT_API_PERS', 'base_url': 'https://api.giga.chat/v1'})
    options = {}
    class Client:
        def __init__(self, **kwargs): options.update(kwargs)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, *args, **kwargs): raise RuntimeError('connection intentionally stopped')
    with patch('api.llm.httpx.Client', Client):
        llm.ask_llm({})
    context = options['verify']
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert any(cert.get('subject') and any(('commonName', 'Russian Trusted Root CA') in part for part in cert['subject']) for cert in context.get_ca_certs())
