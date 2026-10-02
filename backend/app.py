"""Single-worker API; mutations are serialized with safe apply."""
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import socket
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
import psutil
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from backend import storage as s, manager as m, portable
from backend import __version__
from backend.models import Rule, Network, overlap

PH = PasswordHasher()
LOGGER = logging.getLogger('realmpanel')
CACHE: dict = {'healthy': False, 'running': False}
PREVIEWS: dict = {}


def monitor() -> None:
    global CACHE
    while True:
        try:
            CACHE = m.core('status')
        except Exception:
            CACHE = {'healthy': False, 'running': False, 'error': '无法连接 Realm Supervisor'}
        time.sleep(10)


@asynccontextmanager
async def lifespan(app):
    s.init()
    for attempt in range(20):
        try:
            m.recover()
            break
        except Exception:
            if attempt == 19:
                LOGGER.exception('启动恢复未通过')
            time.sleep(1)
    threading.Thread(target=monitor, daemon=True).start()
    yield


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


def audit(request: Request, action: str, resource: str = '', result: str = '成功') -> None:
    with s.connect() as db:
        user = db.execute('SELECT username FROM users LIMIT 1').fetchone()
        db.execute('INSERT INTO operation_logs(time,admin,source_ip,action,resource,result) VALUES (?,?,?,?,?,?)', (time.time(), user[0] if user else '', request.client.host, action, resource[:300], result[:500]))
        db.execute('DELETE FROM operation_logs WHERE id < (SELECT COALESCE(MAX(id),0)-10000 FROM operation_logs)')


@app.middleware('http')
async def guard(request: Request, call_next):
    with s.connect() as db:
        prefix = s.get(db, 'web_path')
        whitelist = s.get(db, 'whitelist') or []
    if not prefix or not request.url.path.startswith(prefix):
        return Response('Not Found', 404)
    if whitelist and not any(ipaddress.ip_address(request.client.host) in ipaddress.ip_network(n) for n in whitelist):
        return Response('Not Found', 404)
    route = request.url.path[len(prefix):]
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        if request.headers.get('origin') and request.headers['origin'] != f'{request.url.scheme}://{request.url.netloc}':
            return JSONResponse({'code': 'CSRF', 'detail': '来源校验失败'}, 403)
        if request.headers.get('x-rmp-request') != '1':
            return JSONResponse({'code': 'CSRF', 'detail': '缺少同源请求标记'}, 403)
        if int(request.headers.get('content-length', '0')) > 3_000_000:
            return JSONResponse({'detail': '请求过大'}, 413)
    if route.startswith('api/') and route != 'api/v1/auth/login':
        token = hashlib.sha256(request.cookies.get('rmp_session', '').encode()).hexdigest()
        with s.connect() as db:
            session = db.execute('SELECT * FROM sessions WHERE token=? AND expires>?', (token, time.time())).fetchone()
        if not session:
            return JSONResponse({'code': 'UNAUTHORIZED', 'detail': '请先登录'}, 401)
        if request.method not in ('GET', 'HEAD') and not hmac.compare_digest(request.headers.get('x-csrf-token', ''), session['csrf']):
            return JSONResponse({'code': 'CSRF', 'detail': '会话校验失败'}, 403)
        request.state.session = dict(session)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') and route.startswith('api/') and route != 'api/v1/auth/login':
        audit(request, route, result='成功' if response.status_code < 400 else f'失败 HTTP {response.status_code}')
    return response


@app.exception_handler(ValueError)
async def invalid(request, exc):
    return JSONResponse({'code': 'INVALID_OPERATION', 'detail': str(exc)}, 400)


@app.exception_handler(RequestValidationError)
async def validation(request, exc):
    return JSONResponse({'code': 'VALIDATION', 'detail': '; '.join(f'{".".join(map(str, e["loc"]))}: {e["msg"]}' for e in exc.errors())}, 422)


@app.exception_handler(Exception)
async def unexpected(request, exc):
    LOGGER.exception('request_failed', exc_info=exc)
    return JSONResponse({'code': 'INTERNAL', 'detail': '内部错误，请查看服务日志'}, 500)


P = '/{path}/api/v1'


class Login(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=1024)


@app.post(P + '/auth/login')
def login(body: Login, request: Request):
    with m.LOCK, s.connect() as db:
        ip = request.client.host
        now = time.time()
        row = db.execute('SELECT * FROM login_attempts WHERE ip=?', (ip,)).fetchone()
        if row and row['expires'] > now and row['count'] >= 5:
            raise HTTPException(429, '登录失败过多，请在 15 分钟后重试')
        user = db.execute('SELECT * FROM users LIMIT 1').fetchone()
        valid = False
        if user:
            try:
                valid = PH.verify(user['password'], body.password) and hmac.compare_digest(user['username'].encode(), body.username.encode())
            except VerificationError:
                pass
        if not valid:
            count = row['count'] + 1 if row and row['expires'] > now else 1
            db.execute('INSERT OR REPLACE INTO login_attempts VALUES (?,?,?)', (ip, count, now + 900))
            db.commit()
            audit(request, '登录', result='失败')
            raise HTTPException(401, '账号或密码错误')
        db.execute('DELETE FROM login_attempts WHERE ip=?', (ip,))
        db.execute('DELETE FROM sessions WHERE expires<?', (now,))
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        db.execute('INSERT INTO sessions VALUES (?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), csrf, now + 43200))
        response = JSONResponse({'username': user['username'], 'csrf': csrf})
        response.set_cookie('rmp_session', token, httponly=True, secure=request.url.scheme == 'https', samesite='strict', path=s.get(db, 'web_path'), max_age=43200)
    audit(request, '登录')
    return response


@app.get(P + '/auth/me')
def me(request: Request):
    with s.connect() as db:
        return {'username': db.execute('SELECT username FROM users LIMIT 1').fetchone()[0], 'csrf': request.state.session['csrf']}


@app.post(P + '/auth/logout')
def logout(request: Request):
    with s.connect() as db:
        db.execute('DELETE FROM sessions WHERE token=?', (request.state.session['token'],))
        response = JSONResponse({'ok': True})
        response.delete_cookie('rmp_session', path=s.get(db, 'web_path'))
        return response


@app.get(P + '/status')
def status():
    with s.connect() as db:
        rules = s.rules(db)
        return {**CACHE, 'panel_version': __version__, 'realm_version': '2.9.3', 'rules': len(rules), 'enabled': sum(r['enabled'] for r in rules), 'host_memory_mb': round(psutil.virtual_memory().used / 1048576), 'load': os.getloadavg(), 'revision': s.get(db, 'revision'), 'web_port': s.get(db, 'web_port')}


@app.get(P + '/rules')
def list_rules():
    with s.connect() as db:
        return s.rules(db)


@app.get(P + '/traffic')
def traffic():
    return m.core('traffic')


@app.post(P + '/rules')
def create_rule(body: Rule):
    return {'id': m.apply(lambda db: s.save_rule(db, body.model_dump()), '添加规则')}


@app.get(P + '/rules/{rid}')
def get_rule(rid: int):
    with s.connect() as db:
        return find_rule(db, rid)


def find_rule(db, rid: int) -> dict:
    for r in s.rules(db):
        if r['id'] == rid:
            return r
    raise ValueError('规则不存在')


@app.put(P + '/rules/{rid}')
def update_rule(rid: int, body: Rule):
    m.apply(lambda db: s.save_rule(db, body.model_dump(), rid), '编辑规则')
    return {'ok': True}


@app.delete(P + '/rules/{rid}')
def delete_rule(rid: int):
    def change(db):
        find_rule(db, rid)
        db.execute('DELETE FROM forward_rules WHERE id=?', (rid,))
    m.apply(change, '删除规则')
    return {'ok': True}


def available(rule: dict, existing: list[dict]) -> bool:
    if any(overlap(rule, r) for r in existing):
        return False
    family = socket.AF_INET6 if ':' in rule['listen_host'] else socket.AF_INET
    try:
        for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
            with socket.socket(family, kind) as sock:
                sock.bind((rule['listen_host'], rule['listen_port']))
        return True
    except OSError:
        return False


def next_port(rule: dict, existing: list[dict], web_port: int) -> None:
    for port in range(max(1024, rule['listen_port'] + 1), 65536):
        rule['listen_port'] = port
        if port != web_port and available(rule, existing):
            return
    raise ValueError('未找到可用端口')


@app.post(P + '/rules/{rid}/{action}')
def rule_action(rid: int, action: Literal['enable', 'disable', 'clone']):
    def change(db):
        raw = find_rule(db, rid)
        r = {k: raw[k] for k in Rule.model_fields}
        if action == 'clone':
            r['name'] = r['name'][:95] + '-copy'
            next_port(r, s.rules(db), s.get(db, 'web_port'))
            return s.save_rule(db, r)
        r['enabled'] = action == 'enable'
        return s.save_rule(db, r, rid)
    return {'id': m.apply(change, action)}


class Batch(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=2000)
    action: Literal['enable', 'disable', 'delete', 'group']
    group: str = Field(default='默认', min_length=1, max_length=100)


@app.post(P + '/batch')
def batch(body: Batch):
    def change(db):
        for rid in set(body.ids):
            raw = find_rule(db, rid)
            if body.action == 'delete':
                db.execute('DELETE FROM forward_rules WHERE id=?', (rid,))
            else:
                r = {k: raw[k] for k in Rule.model_fields}
                r.update({'group': body.group} if body.action == 'group' else {'enabled': body.action == 'enable'})
                s.save_rule(db, r, rid)
    m.apply(change, '批量' + body.action)
    return {'ok': True}


@app.get(P + '/groups')
def groups():
    with s.connect() as db:
        return [r[0] for r in db.execute('SELECT name FROM groups ORDER BY name')]


class Group(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    old: str | None = None
    delete: bool = False


@app.post(P + '/groups')
def group_change(body: Group):
    def change(db):
        if body.old == '默认' or (body.delete and body.name == '默认'):
            raise ValueError('默认分组不能修改或删除')
        old = body.old or body.name
        new = '默认' if body.delete else body.name
        db.execute('INSERT OR IGNORE INTO groups VALUES (?)', (new,))
        if body.old or body.delete:
            for raw in s.rules(db):
                if raw['group'] == old:
                    r = {k: raw[k] for k in Rule.model_fields}
                    r['group'] = new
                    s.save_rule(db, r, raw['id'])
            if old != new:
                db.execute('DELETE FROM groups WHERE name=?', (old,))
    m.apply(change, '修改分组')
    return {'ok': True}


class Import(BaseModel):
    content: str
    kind: Literal['json', 'csv', 'text']


@app.post(P + '/import/preview')
def import_preview(body: Import, request: Request):
    data = portable.parse(body.content, body.kind)
    with m.LOCK, s.connect() as db:
        result = portable.preview(data, s.rules(db))
        token = secrets.token_urlsafe(24)
        for k, v in list(PREVIEWS.items()):
            if v['expires'] < time.time():
                del PREVIEWS[k]
        if len(PREVIEWS) >= 32:
            PREVIEWS.pop(next(iter(PREVIEWS)))
        PREVIEWS[token] = {'data': data, 'result': result, 'revision': s.get(db, 'revision'), 'session': request.state.session['token'], 'expires': time.time() + 900}
        return {**result, 'token': token}


class ImportApply(BaseModel):
    token: str
    strategy: Literal['skip', 'overwrite', 'new_port'] = 'skip'


@app.post(P + '/import/apply')
def import_apply(body: ImportApply, request: Request):
    with m.LOCK:
        item = PREVIEWS.get(body.token)
        if not item or item['expires'] < time.time() or item['session'] != request.state.session['token']:
            raise ValueError('预览已过期，请重新解析')
        if item['result']['errors']:
            raise ValueError('存在无效规则，禁止导入')
        def change(db):
            if s.get(db, 'revision') != item['revision']:
                raise ValueError('配置已变化，请重新预览')
            count = 0
            for raw in item['result']['rules']:
                r = dict(raw)
                existing = s.rules(db)
                matches = [x for x in existing if overlap(r, x)]
                rid = None
                if matches:
                    if body.strategy == 'skip':
                        continue
                    if body.strategy == 'new_port':
                        next_port(r, existing, s.get(db, 'web_port'))
                    else:
                        if len(matches) != 1:
                            raise ValueError('一条导入规则与多个现有监听地址冲突，不能覆盖')
                        rid = matches[0]['id']
                s.save_rule(db, r, rid)
                count += 1
            for group in item['data'].get('groups', []):
                name = Group(name=group['name']).name
                db.execute('INSERT OR IGNORE INTO groups VALUES (?)', (name,))
            if 'settings' in item['data']:
                s.put(db, 'network', Network.model_validate(item['data']['settings']).model_dump())
            return count
        count = m.apply(change, '导入前备份')
        PREVIEWS.pop(body.token, None)
        return {'imported': count}


@app.get(P + '/export/{kind}')
def export(kind: Literal['json', 'csv'], request: Request, ids: str | None = None, group: str | None = None):
    with s.connect() as db:
        payload = portable.export(db, [int(x) for x in ids.split(',')] if ids else None, group)
    audit(request, '导出', kind)
    content = json.dumps(payload, ensure_ascii=False, indent=2) if kind == 'json' else portable.csv_export(payload)
    return Response(content, media_type='application/json' if kind == 'json' else 'text/csv', headers={'Content-Disposition': f'attachment; filename="realm-panel.rmp.{kind}"'})


@app.get(P + '/backups')
def backups():
    with s.connect() as db:
        return [dict(r) for r in db.execute('SELECT * FROM backups ORDER BY time DESC')]


@app.post(P + '/backups')
def backup():
    with m.LOCK, s.connect() as db:
        bid = s.backup(db, '手动备份')
        s.prune(db)
        return {'id': bid}


@app.post(P + '/backups/{bid}/restore')
def restore(bid: str):
    payload = s.read_backup(bid)
    def change(db):
        # Restore forwarding settings; retain current login and web access to avoid lockout.
        db.execute('DELETE FROM forward_rules')
        db.execute('DELETE FROM groups')
        for row in payload['groups']:
            db.execute('INSERT INTO groups VALUES (?)', (row['name'],))
        for row in payload['forward_rules']:
            Rule.model_validate(json.loads(row['body']))
            db.execute('INSERT INTO forward_rules VALUES (?,?,?,?,?)', tuple(row[k] for k in ('id', 'uuid', 'body', 'created_at', 'updated_at')))
        network = next(json.loads(r['value']) for r in payload['settings'] if r['key'] == 'network')
        s.put(db, 'network', Network.model_validate(network).model_dump())
    m.apply(change, '恢复前备份')
    return {'ok': True}


@app.get(P + '/backups/{bid}/download')
def backup_download(bid: str):
    import uuid
    path = s.DATA / 'backups' / f'{uuid.UUID(bid)}.rmpbak'
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, filename=path.name)


@app.delete(P + '/backups/{bid}')
def delete_backup(bid: str):
    import uuid
    bid = str(uuid.UUID(bid))
    with m.LOCK, s.connect() as db:
        (s.DATA / 'backups' / f'{bid}.rmpbak').unlink(missing_ok=True)
        db.execute('DELETE FROM backups WHERE id=?', (bid,))
    return {'ok': True}


@app.get(P + '/logs/{kind}')
def logs(kind: Literal['realm', 'operations'], lines: int = 100):
    lines = min(max(lines, 1), 1000)
    if kind == 'realm':
        return m.core('logs')['lines'][-lines:]
    with s.connect() as db:
        return [dict(r) for r in db.execute('SELECT * FROM operation_logs ORDER BY id DESC LIMIT ?', (lines,))]


@app.get(P + '/settings')
def settings(request: Request):
    with s.connect() as db:
        return {**{k: s.get(db, k) for k in ('network', 'retention', 'whitelist', 'web_port', 'web_path')}, 'client_ip': request.client.host}


class Settings(BaseModel):
    network: Network
    retention: Literal[10, 20, 30, 50, 100]
    whitelist: list[str] = Field(max_length=100)
    confirm_lockout: bool = False


@app.put(P + '/settings')
def update_settings(body: Settings, request: Request):
    nets = [str(ipaddress.ip_network(n.strip(), strict=False)) for n in body.whitelist if n.strip()]
    if nets and not any(ipaddress.ip_address(request.client.host) in ipaddress.ip_network(n) for n in nets) and not body.confirm_lockout:
        raise ValueError('白名单不包含当前客户端 IP；请确认允许将自己排除')
    def change(db):
        s.put(db, 'network', body.network.model_dump())
        s.put(db, 'retention', body.retention)
        s.put(db, 'whitelist', nets)
    m.apply(change, '修改设置')
    return {'ok': True}


class Credentials(BaseModel):
    current_password: str = Field(max_length=1024)
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=1024)


@app.put(P + '/credentials')
def credentials(body: Credentials):
    with m.LOCK, s.connect() as db:
        row = db.execute('SELECT * FROM users LIMIT 1').fetchone()
        try:
            PH.verify(row['password'], body.current_password)
        except VerificationError:
            raise ValueError('当前密码不正确')
        db.execute('UPDATE users SET username=?,password=? WHERE id=?', (body.username, PH.hash(body.password), row['id']))
        db.execute('DELETE FROM sessions')
    return {'ok': True}


@app.post(P + '/reset-path')
def reset_path(request: Request):
    with m.LOCK, s.connect() as db:
        old = s.get(db, 'web_path')
        new = '/' + ''.join(secrets.choice('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(14)) + '/'
        s.put(db, 'web_path', new)
        response = JSONResponse({'path': new})
        response.delete_cookie('rmp_session', path=old)
        response.set_cookie('rmp_session', request.cookies['rmp_session'], path=new, httponly=True, secure=request.url.scheme == 'https', samesite='strict', max_age=43200)
        return response


@app.post(P + '/check-port')
def check_port(body: Rule):
    with s.connect() as db:
        if body.listen_port == s.get(db, 'web_port'):
            return {'available': False, 'detail': 'Web 管理端口'}
        ok = available(body.model_dump(), s.rules(db))
    return {'available': ok, 'detail': '端口可以使用' if ok else '端口已被规则或其他进程占用'}


@app.post(P + '/test-target')
def test_target(body: Rule):
    started = time.monotonic()
    try:
        if body.protocol == 'udp':
            addr = socket.getaddrinfo(body.remote_host, body.remote_port, type=socket.SOCK_DGRAM)[0]
            with socket.socket(addr[0], socket.SOCK_DGRAM) as sock:
                sock.sendto(b'RealmPanel UDP test', addr[4])
            return {'detail': 'UDP 数据发送测试完成，不能保证远端应用正确响应 UDP'}
        with socket.create_connection((body.remote_host, body.remote_port), timeout=5):
            return {'detail': f'TCP 连接成功，延迟 {(time.monotonic() - started) * 1000:.1f} ms'}
    except OSError as exc:
        raise ValueError(f'目标测试失败：{exc}')


@app.get('/{path}/{asset:path}')
def frontend(path: str, asset: str):
    root = Path('/app/frontend/dist').resolve()
    file = (root / (asset or 'index.html')).resolve()
    if not file.is_relative_to(root) or not file.is_file() or asset.startswith('api/'):
        raise HTTPException(404)
    return FileResponse(file)
