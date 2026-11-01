import json
import threading
import httpx
from backend import storage as s
from backend.models import generate, overlap

LOCK = threading.RLock()
PLAN = 'forward/rules.json'


def core(operation: str, method: str = 'GET') -> dict:
    with httpx.Client(transport=httpx.HTTPTransport(uds=str(s.DATA / 'run/supervisor.sock')), timeout=30) as client:
        response = client.request(method, 'http://localhost/' + operation)
        if response.status_code != 200:
            try:
                detail = response.json().get('error') or response.text
            except ValueError:
                detail = response.text
            raise ValueError(detail)
        return response.json()


def validate(rules: list[dict], port: int) -> None:
    for i, rule in enumerate(rules):
        if rule['listen_port'] == port:
            raise ValueError('监听端口与 Web 管理端口冲突')
        for other in rules[:i]:
            if overlap(rule, other):
                raise ValueError(f'监听端口冲突：{rule["listen_host"]}:{rule["listen_port"]}')


def write_plan(rules: list[dict]) -> bytes:
    content = json.dumps(generate(rules), ensure_ascii=False).encode()
    s.atomic(s.DATA / PLAN, content)
    return content


def recover() -> None:
    # SQLite commit is the source of truth after interruption at any apply stage.
    with LOCK, s.connect() as db:
        write_plan(s.rules(db))
        core('apply', 'POST')


def apply(change, reason: str):
    with LOCK:
        with s.connect() as pre:
            s.backup(pre, reason)
            s.prune(pre)
        db = s.connect()
        old = json.dumps(generate(s.rules(db)), ensure_ascii=False).encode()
        try:
            db.execute('BEGIN IMMEDIATE')
            result = change(db)
            validate(s.rules(db), s.get(db, 'web_port'))
            write_plan(s.rules(db))
            core('apply?strict=1', 'POST')
            s.put(db, 'revision', s.get(db, 'revision') + 1)
            db.commit()
            return result
        except Exception as exc:
            db.rollback()
            s.atomic(s.DATA / PLAN, old)
            try:
                core('apply', 'POST')
            except Exception as rollback:
                raise ValueError(f'应用失败且恢复检查未通过，请查看运行日志：{rollback}') from exc
            raise ValueError(f'配置应用失败，已自动恢复：{exc}') from exc
        finally:
            db.close()
