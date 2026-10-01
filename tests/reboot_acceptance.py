"""Run prepare, reboot host separately, then verify. Uses only loopback rules."""
import json
import socket
import sqlite3
import struct
import sys
from pathlib import Path
import httpx

root = Path('/opt/realm-panel')
statefile = root / 'artifacts/reboot-state.json'
with sqlite3.connect(root / 'data/database/realm-panel.db') as db:
    settings = {k: json.loads(v) for k, v in db.execute('SELECT key,value FROM settings')}
info = Path('/root/realm-panel-install-info.txt').read_text()
username = next(x.split('：', 1)[1] for x in info.splitlines() if x.startswith('管理员账号：'))
password = next(x.split('：', 1)[1] for x in info.splitlines() if x.startswith('管理员密码：'))
client = httpx.Client(base_url=f'http://127.0.0.1:{settings["web_port"]}{settings["web_path"]}api/v1/', headers={'X-RMP-Request': '1'}, timeout=45)


def api(path, method='GET', body=None):
    response = client.request(method, path, json=body)
    assert response.status_code == 200, response.text
    return response.json()


auth = api('auth/login', 'POST', {'username': username, 'password': password})
client.headers['X-CSRF-Token'] = auth['csrf']


def verify(state):
    with socket.create_connection(('127.0.0.1', state['tcp_port']), timeout=3) as sock:
        assert sock.recv(200).startswith(b'SSH-')
    query = struct.pack('!HHHHHH', 0x524d, 0x0100, 1, 0, 0, 0) + b'\x09localhost\x00' + struct.pack('!HH', 1, 1)
    with socket.socket(type=socket.SOCK_DGRAM) as sock:
        sock.settimeout(5)
        sock.sendto(query, ('127.0.0.1', state['udp_port']))
        response = sock.recv(4096)
        assert response[:2] == query[:2] and response[2] & 0x80


if sys.argv[1] == 'prepare':
    state = {'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(), 'ids': []}
    for protocol, remote_port, remote_host in [('tcp', 22, '127.0.0.1'), ('udp', 53, '127.0.0.53')]:
        with socket.socket(type=socket.SOCK_STREAM if protocol == 'tcp' else socket.SOCK_DGRAM) as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        rule = {'name': '重启验收-' + protocol, 'listen_host': '127.0.0.1', 'listen_port': port, 'remote_host': remote_host, 'remote_port': remote_port, 'protocol': protocol, 'remark': '整机重启验收后自动清理'}
        state['ids'].append(api('rules', 'POST', rule)['id'])
        state[protocol + '_port'] = port
    verify(state)
    statefile.write_text(json.dumps(state))
    print('PASS 重启前 TCP / UDP 规则真实转发，已记录 boot_id 与规则 ID')
elif sys.argv[1] == 'verify':
    state = json.loads(statefile.read_text())
    assert Path('/proc/sys/kernel/random/boot_id').read_text().strip() != state['boot_id'], '尚未发生整机重启'
    rules = api('rules')
    assert all(any(r['id'] == rid and r['enabled'] for r in rules) for rid in state['ids'])
    verify(state)
    api('batch', 'POST', {'ids': state['ids'], 'action': 'delete'})
    (root / 'artifacts/reboot-acceptance.json').write_text(json.dumps({'result': 'PASS', 'boot_id_changed': True, 'persisted_rules': 2, 'tcp_forwarding': True, 'udp_forwarding': True, 'test_rules_cleaned': True}))
    print('PASS 整机重启后面板自动启动、规则持久化、TCP / UDP 自动恢复；验收规则已清理')
