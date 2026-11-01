import secrets
import time
import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from backend import storage as s
from backend.app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(s, 'DATA', tmp_path)
    monkeypatch.setattr(s, 'DB', tmp_path / 'database/realm-panel.db')
    s.init()
    with s.connect() as db:
        s.put(db, 'web_path', '/testpath/')
        s.put(db, 'web_port', 32123)
        db.execute('INSERT INTO users VALUES (1,?,?)', ('tester', PasswordHasher().hash('Testing!password17')))
    return TestClient(app, headers={'X-RMP-Request': '1'})


def login(client):
    result = client.post('/testpath/api/v1/auth/login', json={'username': 'tester', 'password': 'Testing!password17'})
    assert result.status_code == 200
    client.headers['X-CSRF-Token'] = result.json()['csrf']
    return result


def test_auth_csrf_cookie_and_logout(client):
    assert client.get('/api/v1/rules').status_code == 404
    assert client.get('/testpath/api/v1/rules').status_code == 401
    response = login(client)
    cookie = response.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'samesite=strict' in cookie
    assert 'secure' not in cookie
    assert client.get('/testpath/api/v1/auth/me').status_code == 200
    assert client.post('/testpath/api/v1/backups', headers={'X-CSRF-Token': 'wrong'}).status_code == 403
    assert client.post('/testpath/api/v1/backups', headers={'Origin': 'https://attacker.invalid'}).status_code == 403
    assert client.post('/testpath/api/v1/auth/logout').status_code == 200
    assert client.get('/testpath/api/v1/rules').status_code == 401


def test_login_rate_limit(client):
    for _ in range(5):
        assert client.post('/testpath/api/v1/auth/login', json={'username': 'tester', 'password': 'wrong'}).status_code == 401
    assert client.post('/testpath/api/v1/auth/login', json={'username': 'tester', 'password': 'Testing!password17'}).status_code == 429
    with s.connect() as db:
        db.execute('UPDATE login_attempts SET expires=?', (time.time() - 1,))
    login(client)


def test_credential_change_invalidates_session(client):
    login(client)
    body = {'username': 'newtester', 'current_password': 'wrong', 'password': 'New!password987'}
    assert client.put('/testpath/api/v1/credentials', json=body).status_code == 400
    body['current_password'] = 'Testing!password17'
    assert client.put('/testpath/api/v1/credentials', json=body).status_code == 200
    assert client.get('/testpath/api/v1/rules').status_code == 401
    assert client.post('/testpath/api/v1/auth/login', json={'username': 'newtester', 'password': body['password']}).status_code == 200


def test_path_rotation_keeps_one_session(client):
    login(client)
    path = client.post('/testpath/api/v1/reset-path').json()['path']
    assert client.get('/testpath/api/v1/auth/me').status_code == 404
    assert client.get(path + 'api/v1/auth/me').status_code == 200


def test_invalid_import_prevents_apply(client):
    login(client)
    body = {'kind': 'json', 'content': '{"format":"realm-panel","schema_version":1,"rules":[{"name":"bad","listen_port":99999,"remote_host":"localhost","remote_port":80}]}'}
    preview = client.post('/testpath/api/v1/import/preview', json=body).json()
    assert preview['errors']
    assert client.post('/testpath/api/v1/import/apply', json={'token': preview['token']}).status_code == 400


def test_diagnose_checks_only_that_rule(client, monkeypatch):
    from backend import app as app_module, manager
    login(client)
    with s.connect() as db:
        rid = s.save_rule(db, {'name': '线路A', 'group': '默认', 'enabled': True, 'listen_host': '0.0.0.0', 'listen_port': 30001, 'remote_host': 'example.com', 'remote_port': 443, 'protocol': 'tcp', 'through': '', 'remark': ''})
        rule_uuid = s.rules(db)[0]['uuid']
    asked = []
    def core(operation, method='GET'):
        asked.append(operation)
        return {'endpoints': [{'proto': 'tcp', 'family': 4, 'listen': '0.0.0.0:30001', 'remote': '203.0.113.5:443', 'target': '203.0.113.5', 'target_port': 443, 'through': ''}], 'problems': [], 'warnings': [], 'ip_forward': True, 'traffic': {'rx_bytes': 1, 'tx_bytes': 2, 'total_bytes': 3}, 'traffic_available': True}
    monkeypatch.setattr(manager, 'core', core)
    monkeypatch.setattr(app_module, 'probe', lambda *a: (True, 'TCP 连接成功'))
    result = client.get(f'/testpath/api/v1/rules/{rid}/diagnose').json()
    assert asked == [f'rule/{rule_uuid}']  # never the whole-core status
    assert result['name'] == '线路A' and result['traffic']['total_bytes'] == 3
    items = [c['item'] for c in result['checks']]
    assert items == ['规则状态', 'TCP 内核转发', 'TCP 目标 203.0.113.5:443']
    assert 'example.com 解析为 203.0.113.5' in result['checks'][2]['detail']
