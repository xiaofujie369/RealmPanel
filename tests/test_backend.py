import json
import sqlite3
import pytest
from backend import storage as s, manager as m, portable
from backend.models import Rule, generate, overlap


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(s, 'DATA', tmp_path)
    monkeypatch.setattr(s, 'DB', tmp_path / 'database/realm-panel.db')
    s.init()
    with s.connect() as db:
        s.put(db, 'web_port', 39999)
    return tmp_path


def rule(**kwargs):
    return Rule(name='中文线路', listen_port=30001, remote_host='127.0.0.1', remote_port=30002, **kwargs).model_dump()


@pytest.mark.parametrize('protocol,no_tcp,use_udp', [('tcp', False, False), ('udp', True, True), ('tcp_udp', False, True)])
def test_protocol(protocol, no_tcp, use_udp):
    config = generate([rule(protocol=protocol)], {})
    assert config['endpoints'][0]['network']['no_tcp'] is no_tcp
    assert config['endpoints'][0]['network']['use_udp'] is use_udp


def test_disabled():
    assert generate([rule(enabled=False)], {})['endpoints'] == []


def test_ipv6():
    r = rule()
    r.update(listen_host='::1', remote_host='2001:db8::1')
    config = generate([r], {})
    assert config['endpoints'][0]['listen'] == '[::1]:30001'
    assert config['endpoints'][0]['remote'] == '[2001:db8::1]:30002'


def test_50_json_csv_roundtrip(database):
    with s.connect() as db:
        for i in range(50):
            r = rule(remark='中文备注，含逗号\n第二行', tcp_timeout=12, through='127.0.0.1', interface='lo')
            r['listen_port'] += i
            r['protocol'] = ['tcp', 'udp', 'tcp_udp'][i % 3]
            r['group'] = '日本' if i % 2 else '美国'
            s.save_rule(db, r)
        payload = portable.export(db)
        for fmt, content in [('json', json.dumps(payload)), ('csv', portable.csv_export(payload))]:
            parsed = portable.parse(content, fmt)
            result = portable.preview(parsed, [])
            assert result['valid'] == 50
            assert result['errors'] == []
            assert result['rules'] == payload['rules']
        assert not any(secret in json.dumps(payload) for secret in ('password', 'web_path', 'sessions'))


def test_invalid_and_conflict(database):
    r = rule()
    r['listen_port'] = 99999
    assert portable.preview({'rules': [r]}, [])['errors']
    existing = {**rule(), 'id': 1}
    assert portable.preview({'rules': [rule()]}, [existing])['conflicts']
    assert overlap(rule(), {**rule(), 'listen_host': '127.0.0.1'})
    with pytest.raises(ValueError):
        portable.parse('{"format":"wrong","rules":[]}', 'json')


def test_safe_apply_rollback(database, monkeypatch):
    calls = []
    def core(operation, method):
        calls.append(json.loads((database / 'realm/active.json').read_text()))
        if len(calls) == 2:
            raise ValueError('模拟核心启动失败')
        return {'healthy': True}
    monkeypatch.setattr(m, 'core', core)
    rid = m.apply(lambda db: s.save_rule(db, rule()), 'test')
    old = (database / 'realm/active.json').read_bytes()
    with pytest.raises(ValueError, match='已自动恢复'):
        m.apply(lambda db: db.execute('DELETE FROM forward_rules'), '失败测试')
    assert (database / 'realm/active.json').read_bytes() == old
    with s.connect() as db:
        assert s.rules(db)[0]['id'] == rid
        assert s.get(db, 'revision') == 1
        assert len(db.execute('SELECT * FROM backups').fetchall()) == 2
    assert len(calls) == 3


def test_encrypted_backup(database):
    with s.connect() as db:
        s.save_rule(db, rule())
        bid = s.backup(db, 'test')
    assert b'127.0.0.1' not in (database / 'backups' / f'{bid}.rmpbak').read_bytes()
    payload = s.read_backup(bid)
    assert len(payload['forward_rules']) == 1


def test_portable_formula_safety(database):
    with s.connect() as db:
        r = rule()
        r.update(name='=SUM(1,2)', remark="'literal")
        s.save_rule(db, r)
        text = portable.csv_export(portable.export(db))
    assert "'=SUM" in text
    assert portable.preview(portable.parse(text, 'csv'), [])['rules'][0] == r


def test_password_reset(database, monkeypatch, capsys):
    from backend.cli import main
    from argon2 import PasswordHasher
    monkeypatch.setattr('sys.argv', ['cli', 'init', '--auto'])
    main()
    capsys.readouterr()
    with s.connect() as db:
        old = db.execute('SELECT password FROM users').fetchone()[0]
        db.execute('INSERT INTO sessions VALUES (?,?,?)', ('token', 'csrf', 9999999999))
    monkeypatch.setattr('sys.argv', ['cli', 'reset-password', '--auto'])
    main()
    output = capsys.readouterr().out
    password = output.split('密码：')[1].strip()
    with s.connect() as db:
        new = db.execute('SELECT password FROM users').fetchone()[0]
        assert old != new
        assert PasswordHasher().verify(new, password)
        assert not db.execute('SELECT * FROM sessions').fetchall()
