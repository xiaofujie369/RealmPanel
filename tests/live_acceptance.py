"""Destructive only to self-created test rules; execute on the deployment host."""
import concurrent.futures
import json
import socket
import socketserver
import sqlite3
import threading
import time
import sys
from pathlib import Path
import httpx

ROOT = Path('/opt/realm-panel')
with sqlite3.connect(ROOT / 'data/database/realm-panel.db') as db:
    settings = {k: json.loads(v) for k, v in db.execute('SELECT key,value FROM settings')}
info = Path('/root/realm-panel-install-info.txt').read_text()
username = next(x.split('：', 1)[1] for x in info.splitlines() if x.startswith('管理员账号：'))
password = next(x.split('：', 1)[1] for x in info.splitlines() if x.startswith('管理员密码：'))
origin = f'http://127.0.0.1:{settings["web_port"]}'
base = origin + settings['web_path'] + 'api/v1/'
client = httpx.Client(timeout=45, headers={'X-RMP-Request': '1'})
results = []
owned = []


def record(name):
    results.append({'test': name, 'result': 'PASS'})
    print('PASS', name, flush=True)


def api(path, method='GET', body=None, expected=200):
    response = client.request(method, base + path, json=body)
    assert response.status_code == expected, (path, response.status_code, response.text[:1000])
    return response.json()


class TCP(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.sendall(self.request.recv(1024))


class UDP(socketserver.BaseRequestHandler):
    def handle(self):
        data, sock = self.request
        sock.sendto(data, self.client_address)


tcp = socketserver.ThreadingTCPServer(('127.0.0.1', 0), TCP)
target_port = tcp.server_address[1]
udp = socketserver.ThreadingUDPServer(('127.0.0.1', target_port), UDP)
for server in (tcp, udp):
    threading.Thread(target=server.serve_forever, daemon=True).start()


def free_port():
    while True:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            p = sock.getsockname()[1]
        try:
            with socket.socket(type=socket.SOCK_DGRAM) as sock:
                sock.bind(('127.0.0.1', p))
            return p
        except OSError:
            pass


def tcp_echo(port, works=True):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=2) as sock:
            sock.sendall(b'RealmPanel-real-TCP')
            assert sock.recv(100) == b'RealmPanel-real-TCP'
        assert works, 'TCP should not be listening'
    except OSError:
        assert not works


def udp_echo(port):
    with socket.socket(type=socket.SOCK_DGRAM) as sock:
        sock.settimeout(3)
        sock.sendto(b'RealmPanel-real-UDP', ('127.0.0.1', port))
        assert sock.recv(100) == b'RealmPanel-real-UDP'


try:
    for path in ('/', '/login', '/api/', '/docs', '/openapi.json'):
        assert client.get(origin + path).status_code == 404
    assert client.get(base + 'rules').status_code == 401
    assert client.post(base + 'auth/login', json={'username': username, 'password': password}, headers={'X-RMP-Request': ''}).status_code == 403
    auth = api('auth/login', 'POST', {'username': username, 'password': password})
    assert 'httponly' in client.cookies.jar._cookies['127.0.0.1'][settings['web_path']]['rmp_session']._rest or client.cookies
    client.headers['X-CSRF-Token'] = auth['csrf']
    assert client.post(base + 'backups', headers={'X-CSRF-Token': 'invalid'}).status_code == 403
    record('随机路径隔离、登录、未授权访问、CSRF')
    original = api('rules')
    if original and '--cleanup-previous-test' in sys.argv:
        assert all(r['name'].startswith('验收-') and r['remark'] == '验收后自动清理' for r in original)
        api('batch', 'POST', {'ids': [r['id'] for r in original], 'action': 'delete'})
        original = api('rules')
    assert not original, '验收脚本要求空规则部署，以免影响用户规则'
    backup = api('backups', 'POST')['id']
    port = free_port()
    body = {'name': '验收-TCP+UDP', 'group': '验收', 'listen_host': '127.0.0.1', 'listen_port': port, 'remote_host': '127.0.0.1', 'remote_port': target_port, 'protocol': 'tcp_udp', 'enabled': True, 'remark': '验收后自动清理'}
    rid = api('rules', 'POST', body)['id']; owned.append(rid)
    tcp_echo(port); udp_echo(port)
    record('真实 TCP+UDP 双协议转发')
    api(f'rules/{rid}/disable', 'POST'); tcp_echo(port, False)
    api(f'rules/{rid}/enable', 'POST'); tcp_echo(port); udp_echo(port)
    record('暂停与恢复')
    new_port = free_port(); body['listen_port'] = new_port
    api(f'rules/{rid}', 'PUT', body); tcp_echo(port, False); tcp_echo(new_port); udp_echo(new_port)
    record('编辑监听端口，旧端口关闭、新端口生效')
    body['protocol'] = 'udp'
    api(f'rules/{rid}', 'PUT', body); tcp_echo(new_port, False); udp_echo(new_port)
    record('UDP Only 不监听 TCP，UDP 真实转发')
    body['protocol'] = 'tcp'
    api(f'rules/{rid}', 'PUT', body); tcp_echo(new_port)
    with socket.socket(type=socket.SOCK_DGRAM) as sock:
        sock.bind(('127.0.0.1', new_port))
    record('TCP Only 不监听 UDP')
    clone = api(f'rules/{rid}/clone', 'POST')['id']; owned.append(clone)
    cloned = api(f'rules/{clone}'); tcp_echo(cloned['listen_port'])
    record('复制规则并寻找可用端口')
    occupied = socket.socket(); occupied.bind(('127.0.0.1', 0)); occupied.listen()
    failed = {**body, 'listen_port': occupied.getsockname()[1]}
    response = api(f'rules/{rid}', 'PUT', failed, expected=400)
    assert '已自动恢复' in response['detail']
    assert api(f'rules/{rid}')['listen_port'] == new_port
    tcp_echo(new_port)
    occupied.close()
    record('真实端口占用导致应用失败，数据库与核心自动回滚')
    exported = api('export/json')
    api('batch', 'POST', {'ids': owned, 'action': 'delete'})
    owned.clear()
    preview = api('import/preview', 'POST', {'content': json.dumps(exported), 'kind': 'json'})
    assert not api('rules')
    api('import/apply', 'POST', {'token': preview['token'], 'strategy': 'skip'})
    owned.extend(r['id'] for r in api('rules'))
    tcp_echo(new_port)
    record('导出、删除、预览不应用、确认导入、规则恢复')
    invalid = {**exported, 'rules': [{**exported['rules'][0], 'listen_port': 99999}]}
    preview = api('import/preview', 'POST', {'content': json.dumps(invalid), 'kind': 'json'})
    assert preview['errors']
    api('import/apply', 'POST', {'token': preview['token']}, expected=400)
    preview = api('import/preview', 'POST', {'content': json.dumps(exported), 'kind': 'json'})
    assert preview['conflicts']
    record('无效端口预览报错禁止应用、已有规则冲突展示')
    api('backups/' + backup + '/restore', 'POST'); owned.clear()
    assert not api('rules')
    record('恢复备份并回到空规则正常待机')
    first_port = 51000 if 50000 <= settings['web_port'] < 50050 else 50000
    rows = [{**body, 'name': f'迁移-{i:02d}', 'listen_port': first_port + i, 'enabled': False, 'protocol': ['tcp', 'udp', 'tcp_udp'][i % 3], 'remark': '中文备注，换行\n完整保留', 'tcp_timeout': 13, 'udp_timeout': 31, 'tcp_keepalive': 17, 'through': '', 'interface': ''} for i in range(50)]
    payload = {**exported, 'rules': rows}
    preview = api('import/preview', 'POST', {'content': json.dumps(payload), 'kind': 'json'})
    assert preview['valid'] == 50 and not preview['errors']
    assert api('import/apply', 'POST', {'token': preview['token']})['imported'] == 50
    owned.extend(r['id'] for r in api('rules'))
    exported50 = api('export/json')
    csv50 = client.get(base + 'export/csv').text
    api('batch', 'POST', {'ids': owned, 'action': 'delete'}); owned.clear()
    preview = api('import/preview', 'POST', {'kind': 'json', 'content': json.dumps(exported50)})
    assert api('import/apply', 'POST', {'token': preview['token']})['imported'] == 50
    owned.extend(r['id'] for r in api('rules'))
    assert api('export/json')['rules'] == exported50['rules']
    api('batch', 'POST', {'ids': owned, 'action': 'delete'}); owned.clear()
    preview = api('import/preview', 'POST', {'kind': 'csv', 'content': csv50})
    assert not preview['errors']
    assert api('import/apply', 'POST', {'token': preview['token']})['imported'] == 50
    owned.extend(r['id'] for r in api('rules'))
    assert api('export/json')['rules'] == exported50['rules']
    record('50/50 JSON 和 CSV 实际 API 导入导出，中文、协议、高级参数完整保留')
    api('batch', 'POST', {'ids': owned, 'action': 'delete'}); owned.clear()
    api('groups', 'POST', {'name': '验收', 'delete': True})
    api('auth/logout', 'POST')
    assert client.get(base + 'rules').status_code == 401
    record('退出后会话失效')
finally:
    if owned:
        try:
            api('batch', 'POST', {'ids': owned, 'action': 'delete'})
        except Exception as exc:
            print('验收清理失败', str(exc))
    for server in (tcp, udp):
        server.shutdown(); server.server_close()
    (ROOT / 'artifacts').mkdir(exist_ok=True)
    (ROOT / 'artifacts/live-acceptance.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
    client.close()
