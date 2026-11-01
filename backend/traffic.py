"""Persistent per-rule byte counters backed by named nft counters in our own table."""
import json
import sqlite3
import subprocess
import threading
import time
import uuid
from pathlib import Path

TABLE = 'realmpanel'


class Meter:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(root / 'traffic.sqlite3', check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY,total INTEGER NOT NULL,last INTEGER NOT NULL)')
        self.db.commit()
        self.error = None
        self.ready = False

    def nft(self, *args):
        result = subprocess.run(['nft', *args], text=True, capture_output=True, timeout=5)
        if result.returncode:
            raise RuntimeError(result.stderr.strip()[:300])
        return result.stdout

    def read(self) -> dict[str, int]:
        objects = json.loads(self.nft('-j', 'list', 'counters', 'table', 'inet', TABLE))['nftables']
        return {item['counter']['name']: int(item['counter']['bytes']) for item in objects if 'counter' in item and item['counter']['name'].startswith('r_')}

    def accumulate(self, counters: dict[str, int]) -> None:
        with self.lock:
            for name, value in counters.items():
                row = self.db.execute('SELECT total,last FROM counters WHERE name=?', (name,)).fetchone()
                total, last = row or (0, 0)
                if row and value == last:
                    continue
                # A kernel counter lower than last seen was recreated (reboot, reinstall).
                total += value - last if value >= last else value
                self.db.execute('INSERT OR REPLACE INTO counters VALUES (?,?,?)', (name, total, value))
            self.db.commit()

    def created(self, names: list[str]) -> None:
        with self.lock:
            for name in names:
                self.db.execute('INSERT OR IGNORE INTO counters VALUES (?,0,0)', (name,))
                self.db.execute('UPDATE counters SET last=0 WHERE name=?', (name,))
            self.db.commit()
            self.ready = True

    def snapshot(self) -> dict:
        try:
            self.accumulate(self.read())
            self.error = None
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
            self.error = str(exc)
        rules = {}
        with self.lock:
            rows = self.db.execute('SELECT name,total FROM counters').fetchall()
        for name, total in rows:
            if not name.startswith('r_'):
                continue
            key, direction = name[2:].rsplit('_', 1)
            rules.setdefault(str(uuid.UUID(key)), {'rx_bytes': 0, 'tx_bytes': 0})[direction + '_bytes'] = total
        for rule in rules.values():
            rule['total_bytes'] = rule['rx_bytes'] + rule['tx_bytes']
        return {'available': self.ready and not self.error, 'error': self.error, 'updated_at': int(time.time()), 'rules': rules}
