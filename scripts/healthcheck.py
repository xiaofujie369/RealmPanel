import json
import socket
import sqlite3
import time
import urllib.request

for attempt in range(60):
    try:
        with sqlite3.connect('/opt/realm-panel/data/database/realm-panel.db') as db:
            values = {k: json.loads(v) for k, v in db.execute('SELECT key,value FROM settings')}
        with urllib.request.urlopen(f'http://127.0.0.1:{values["web_port"]}{values["web_path"]}', timeout=3) as response:
            assert response.status == 200
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(3)
            sock.connect('/opt/realm-panel/data/run/supervisor.sock')
            sock.sendall(b'GET /status HTTP/1.0\r\nHost: localhost\r\n\r\n')
            response = b''
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                response += chunk
        assert json.loads(response.split(b'\r\n\r\n', 1)[1])['healthy']
        print('✓ Web、Realm 进程、配置监听检查通过')
        break
    except Exception:
        time.sleep(1)
else:
    raise SystemExit('健康检查失败，请运行 rmpctl logs 排查')
