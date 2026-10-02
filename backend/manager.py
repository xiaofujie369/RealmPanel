import json
import threading
import httpx
from backend import storage as s
from backend.models import generate, overlap

LOCK = threading.RLock()


def core(operation: str, method: str = 'GET') -> dict:
    with httpx.Client(transport=httpx.HTTPTransport(uds=str(s.DATA / 'run/supervisor.sock')), timeout=20) as client:
        response = client.request(method, 'http://localhost/' + operation)
        if response.status_code != 200:
            raise ValueError(response.text)
        return response.json()


def validate(rules: list[dict], port: int) -> None:
    for i, rule in enumerate(rules):
        if rule['listen_port'] == port:
            raise ValueError('监听端口与 Web 管理端口冲突')
        for other in rules[:i]:
            if overlap(rule, other):
                raise ValueError(f'监听端口冲突：{rule["listen_host"]}:{rule["listen_port"]}')


def recover() -> None:
    # SQLite commit is the source of truth after interruption at any apply stage.
    with LOCK, s.connect() as db:
        content = json.dumps(generate(s.rules(db), s.get(db, 'network'))).encode()
        s.atomic(s.DATA / 'realm/active.json', content)
        s.atomic(s.DATA / 'realm/meter-rules.json', json.dumps(s.rules(db)).encode())
        core('restart', 'POST')


def apply(change, reason: str):
    with LOCK:
        with s.connect() as pre:
            s.backup(pre, reason)
            s.prune(pre)
        db = s.connect()
        old = json.dumps(generate(s.rules(db), s.get(db, 'network'))).encode()
        try:
            db.execute('BEGIN IMMEDIATE')
            result = change(db)
            validate(s.rules(db), s.get(db, 'web_port'))
            content = json.dumps(generate(s.rules(db), s.get(db, 'network'))).encode()
            s.atomic(s.DATA / 'realm/active.json', content)
            s.atomic(s.DATA / 'realm/meter-rules.json', json.dumps(s.rules(db)).encode())
            core('restart', 'POST')
            s.put(db, 'revision', s.get(db, 'revision') + 1)
            db.commit()
            return result
        except Exception as exc:
            db.rollback()
            s.atomic(s.DATA / 'realm/active.json', old)
            s.atomic(s.DATA / 'realm/meter-rules.json', json.dumps(s.rules(db)).encode())
            try:
                core('restart', 'POST')
            except Exception as rollback:
                raise ValueError(f'应用失败且恢复检查未通过，请查看运行日志：{rollback}') from exc
            raise ValueError(f'配置应用失败，已自动恢复：{exc}') from exc
        finally:
            db.close()
