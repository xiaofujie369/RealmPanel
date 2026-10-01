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
