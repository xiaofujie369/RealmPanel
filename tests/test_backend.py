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
    return Rule(name='中文线路', listen_port=30001, remote_host='192.0.2.10', remote_port=30002, **kwargs).model_dump()


def test_plan_contains_all_rules_with_enabled_flag():
    plan = generate([{**rule(), 'uuid': '00000000-0000-0000-0000-000000000001'}, {**rule(enabled=False), 'listen_port': 30005, 'uuid': '00000000-0000-0000-0000-000000000002'}])
    assert plan['engine'] == 'nftables'
    assert [r['enabled'] for r in plan['rules']] == [True, False]
    assert set(plan['rules'][0]) == {'uuid', 'name', 'enabled', 'listen_host', 'listen_port', 'remote_host', 'remote_port', 'protocol', 'through'}


def test_ipv6_and_family_validation():
    r = rule()
    r.update(listen_host='::', remote_host='2001:db8::1')
    assert Rule.model_validate(r).remote_host == '2001:db8::1'
    with pytest.raises(ValueError, match='同族'):
        Rule.model_validate({**r, 'listen_host': '0.0.0.0'})
    with pytest.raises(ValueError, match='回环'):
        Rule.model_validate({**rule(), 'listen_host': '0.0.0.0', 'remote_host': '127.0.0.1'})
    with pytest.raises(ValueError, match='出口源 IP'):
        Rule.model_validate({**rule(), 'listen_host': '0.0.0.0', 'remote_host': '1.1.1.1', 'through': '::1'})


def test_legacy_realm_fields():
    legacy = {**rule(), 'tcp_timeout': 5, 'udp_timeout': 30, 'tcp_keepalive': 15, 'interface': ''}
    assert Rule.model_validate(legacy).model_dump() == rule()
    with pytest.raises(ValueError, match='Interface'):
        Rule.model_validate({**legacy, 'interface': 'eth0'})
    schema1 = {'format': 'realm-panel', 'schema_version': 1, 'realm_version': '2.9.3', 'settings': {'tcp_timeout': 5}, 'rules': [legacy]}
    assert portable.preview(portable.parse(json.dumps(schema1), 'json'), [])['valid'] == 1


def test_migration_from_realm_database(database):
    with s.connect() as db:
        db.execute('INSERT INTO forward_rules(uuid,body,created_at,updated_at) VALUES (?,?,?,?)', ('00000000-0000-0000-0000-000000000009', json.dumps({**rule(), 'interface': 'eth0', 'tcp_timeout': 9}), 0, 0))
        db.execute('DELETE FROM migrations WHERE version=2')
        s.put(db, 'network', {'tcp_timeout': 5})
    s.init()
    with s.connect() as db:
        stored = s.rules(db)[0]
        assert 'interface' not in stored and 'tcp_timeout' not in stored
        assert s.get(db, 'network') is None
        assert generate(s.rules(db))['rules'][0]['listen_port'] == 30001


def test_50_json_csv_roundtrip(database):
    with s.connect() as db:
        for i in range(50):
            r = rule(remark='中文备注，含逗号\n第二行', through='127.0.0.1')
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
        calls.append((operation, json.loads((database / 'forward/rules.json').read_text())))
        if len(calls) == 2:
            raise ValueError('模拟核心启动失败')
        return {'healthy': True}
    monkeypatch.setattr(m, 'core', core)
    rid = m.apply(lambda db: s.save_rule(db, rule()), 'test')
    old = (database / 'forward/rules.json').read_bytes()
    with pytest.raises(ValueError, match='已自动恢复'):
        m.apply(lambda db: db.execute('DELETE FROM forward_rules'), '失败测试')
    assert (database / 'forward/rules.json').read_bytes() == old
    with s.connect() as db:
        assert s.rules(db)[0]['id'] == rid
        assert s.get(db, 'revision') == 1
        assert len(db.execute('SELECT * FROM backups').fetchall()) == 2
    assert [c[0] for c in calls] == ['apply?strict=1', 'apply?strict=1', 'apply']
    assert len(calls[1][1]['rules']) == 0 and len(calls[2][1]['rules']) == 1


def test_encrypted_backup(database):
    with s.connect() as db:
        s.save_rule(db, rule())
        bid = s.backup(db, 'test')
    assert b'192.0.2.10' not in (database / 'backups' / f'{bid}.rmpbak').read_bytes()
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
