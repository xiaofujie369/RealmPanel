"""Fixed-operation Unix socket supervisor for the nftables engine. No Docker socket or shell."""
import collections
import json
import os
import signal
import socketserver
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from forwarder import ApplyError, Engine, run
from traffic import Meter

ROOT = Path('/app/data')
PLAN = ROOT / 'forward/rules.json'
LOCK = threading.RLock()
LINES: collections.deque[str] = collections.deque(maxlen=1000)
STARTED = time.time()
ENGINE: Engine | None = None


def log(message: str) -> None:
    line = f'{datetime.now().strftime("%Y-%m-%d %H:%M:%S")} {message}'
    LINES.append(line)
    print(line, flush=True)


def rules() -> list[dict]:
    return json.loads(PLAN.read_text())['rules'] if PLAN.exists() else []


def version() -> str:
    try:
        return run('nft', '--version').stdout.split()[1].lstrip('v')
    except (OSError, RuntimeError, IndexError):
        return ''


NFT_VERSION = version()


def status() -> dict:
    return {**ENGINE.status(), 'nft_version': NFT_VERSION, 'uptime': int(time.time() - STARTED), 'rules_file': PLAN.exists()}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:
        with LOCK:
            if self.path == '/status':
                self.reply(200, status())
            elif self.path == '/logs':
                self.reply(200, {'lines': list(LINES)})
            elif self.path == '/traffic':
                self.reply(200, ENGINE.meter.snapshot())
            else:
                self.reply(404, {})

    def do_POST(self) -> None:
        url = urlparse(self.path)
        with LOCK:
            if url.path != '/apply':
                self.reply(404, {})
                return
            strict = parse_qs(url.query).get('strict') == ['1']
            try:
                ENGINE.apply(rules(), strict=strict)
                state = status()
                self.reply(200, state)
            except ApplyError as exc:
                log('拒绝应用：' + str(exc))
                self.reply(409, {**status(), 'error': str(exc)})
            except Exception as exc:
                ENGINE.error = str(exc)
                log('应用失败：' + str(exc))
                self.reply(500, {**status(), 'error': str(exc), 'logs': list(LINES)[-15:]})

    def reply(self, code: int, body: dict) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main() -> None:
    global ENGINE
    ENGINE = Engine(Meter(ROOT / 'traffic'), log=log)
    ROOT.joinpath('run').mkdir(parents=True, exist_ok=True)
    path = ROOT / 'run/supervisor.sock'
    path.unlink(missing_ok=True)
    server = socketserver.UnixStreamServer(str(path), Handler)
    os.chmod(path, 0o660)
    log(f'nftables 转发核心启动（nft {NFT_VERSION}）')
    # 0.9.x left its Realm-era counter table behind; totals already live in SQLite.
    if run('nft', 'delete', 'table', 'inet', 'realmpanel_meter', check=False).returncode == 0:
        log('已移除旧版 realmpanel_meter 计数表')

    def terminate(*_) -> None:
        with LOCK:
            try:
                ENGINE.teardown()
            except Exception as exc:
                log('清理失败：' + str(exc))
        os._exit(0)
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)

    def monitor() -> None:
        # Persist counters; every 30 s re-resolve domains, re-check port owners and
        # repair our objects if something outside (e.g. a Docker restart) removed them.
        tick = 0
        while True:
            with LOCK:
                try:
                    if tick % 6 == 0 and PLAN.exists():
                        ENGINE.apply(rules())
                    ENGINE.meter.snapshot()
                except Exception as exc:
                    ENGINE.error = str(exc)
                    log('后台检查失败：' + str(exc))
            tick += 1
            time.sleep(5)
    threading.Thread(target=monitor, daemon=True).start()
    server.serve_forever()


if __name__ == '__main__':
    main()
