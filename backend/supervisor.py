"""Fixed-operation Unix socket supervisor. No Docker socket or shell execution."""
import collections
import json
import os
import signal
import socket
import socketserver
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import psutil

ROOT = Path('/app/data')
CONFIG = ROOT / 'realm/active.json'
PROC: subprocess.Popen | None = None
LOCK = threading.RLock()
LINES: collections.deque[str] = collections.deque(maxlen=1000)


def reader(proc: subprocess.Popen) -> None:
    for line in proc.stdout:
        LINES.append(line.rstrip())
        print(line, end='', flush=True)


def stop() -> None:
    global PROC
    if PROC and PROC.poll() is None:
        PROC.terminate()
        try:
            PROC.wait(timeout=5)
        except subprocess.TimeoutExpired:
            PROC.kill()
            PROC.wait()


def start() -> None:
    global PROC
    stop()
    if not json.loads(CONFIG.read_text())['endpoints']:
        PROC = None
        return
    PROC = subprocess.Popen(['/usr/local/bin/realm', '-c', str(CONFIG)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    threading.Thread(target=reader, args=(PROC,), daemon=True).start()


def status() -> dict:
    alive = PROC is not None and PROC.poll() is None
    result = {'running': alive, 'healthy': False, 'realm_version': '2.9.3', 'pid': PROC.pid if alive else None, 'listeners': [], 'missing': [], 'memory_mb': 0, 'uptime': 0, 'cpu_percent': 0}
    if not alive:
        if CONFIG.exists() and not json.loads(CONFIG.read_text())['endpoints']:
            result.update(healthy=True, idle=True)
        return result
    try:
        proc = psutil.Process(PROC.pid)
        conns = proc.net_connections(kind='inet')
        listeners = {(c.laddr.ip, c.laddr.port, 'tcp' if c.type == socket.SOCK_STREAM else 'udp') for c in conns if c.laddr and (c.type == socket.SOCK_DGRAM or c.status == psutil.CONN_LISTEN)}
        result['listeners'] = sorted(listeners)
        conf = json.loads(CONFIG.read_text())
        for endpoint in conf['endpoints']:
            host, port = endpoint['listen'].rsplit(':', 1)
            for proto in ('tcp', 'udp'):
                expected = not endpoint['network']['no_tcp'] if proto == 'tcp' else endpoint['network']['use_udp']
                if expected and (host.strip('[]'), int(port), proto) not in listeners:
                    result['missing'].append(f'{proto} {endpoint["listen"]}')
        result.update(healthy=not result['missing'], memory_mb=round(proc.memory_info().rss / 1048576, 2), uptime=int(time.time() - proc.create_time()), cpu_percent=proc.cpu_percent(interval=0.05))
    except (psutil.Error, OSError, ValueError) as exc:
        result['error'] = str(exc)
    return result


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:
        with LOCK:
            if self.path == '/status':
                self.reply(200, status())
            elif self.path == '/logs':
                self.reply(200, {'lines': list(LINES)})
            else:
                self.reply(404, {})

    def do_POST(self) -> None:
        with LOCK:
            if self.path != '/restart':
                self.reply(404, {})
                return
            try:
                start()
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    time.sleep(0.2)
                    state = status()
                    if state['healthy']:
                        time.sleep(0.3)
                        state = status()
                        if state['healthy']:
                            self.reply(200, state)
                            return
                self.reply(409, {**state, 'error': 'Realm 启动或监听检查失败', 'logs': list(LINES)[-15:]})
            except Exception as exc:
                self.reply(500, {'error': str(exc)})

    def reply(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main() -> None:
    ROOT.joinpath('run').mkdir(parents=True, exist_ok=True)
    path = ROOT / 'run/supervisor.sock'
    path.unlink(missing_ok=True)
    server = socketserver.UnixStreamServer(str(path), Handler)
    os.chmod(path, 0o660)
    def terminate(*_) -> None:
        stop()
        os._exit(0)
    signal.signal(signal.SIGTERM, terminate)
    if CONFIG.exists():
        start()
    def monitor() -> None:
        while True:
            time.sleep(10)
            with LOCK:
                if CONFIG.exists() and (PROC is None or PROC.poll() is not None):
                    start()
    threading.Thread(target=monitor, daemon=True).start()
    server.serve_forever()


if __name__ == '__main__':
    main()
