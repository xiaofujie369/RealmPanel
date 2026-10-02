"""Listener-side L3 counters. Only our own nft table is ever modified."""
import ipaddress
import json
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path

TABLE = 'realmpanel_meter'


class Meter:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(root / 'traffic.sqlite3', check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY,total INTEGER NOT NULL,last INTEGER NOT NULL)')
        self.signature = None
        self.error = None
        self.configuration_error = None

    def nft(self, *args, script=None):
        result = subprocess.run(['nft', *args], input=script, text=True, capture_output=True, timeout=5)
        if result.returncode:
            raise RuntimeError(result.stderr.strip()[:300])
        return result.stdout

    def read(self):
        objects = json.loads(self.nft('-j', 'list', 'table', 'inet', TABLE))['nftables']
        return {item['counter']['name']: int(item['counter']['bytes']) for item in objects if 'counter' in item}

    def accumulate(self, counters):
        for name, value in counters.items():
            row = self.db.execute('SELECT total,last FROM counters WHERE name=?', (name,)).fetchone()
            total, last = row or (0, 0)
            if row and value == last:
                continue
            total += value - last if value >= last else value
            self.db.execute('INSERT OR REPLACE INTO counters VALUES (?,?,?)', (name, total, value))
        self.db.commit()

    def configure(self, rules):
        signature = [(r['uuid'], r['enabled'], r['listen_host'], r['listen_port'], r['protocol']) for r in rules]
        if signature == self.signature and not (self.error or self.configuration_error):
            return
        try:
            # A failed list is not permission to delete any existing table.
            listing = json.loads(self.nft('-j', 'list', 'tables'))['nftables']
            exists = any(i.get('table', {}).get('name') == TABLE and i['table']['family'] == 'inet' for i in listing)
            existing = self.read() if exists else {}
            self.accumulate(existing)
            commands = [] if exists else [f'add table inet {TABLE}', f'add chain inet {TABLE} incoming {{ type filter hook input priority 10; policy accept; }}', f'add chain inet {TABLE} outgoing {{ type filter hook output priority 10; policy accept; }}']
            if exists:
                commands += [f'flush chain inet {TABLE} incoming', f'flush chain inet {TABLE} outgoing']
            created = []
            for rule in rules:
                key = uuid.UUID(rule['uuid']).hex
                host = str(ipaddress.ip_address(rule['listen_host']))
                port = int(rule['listen_port'])
                if not 1 <= port <= 65535:
                    raise ValueError('Invalid listener port')
                for direction in ('rx', 'tx'):
                    name = f'r_{key}_{direction}'
                    if name not in existing:
                        commands.append(f'add counter inet {TABLE} {name}')
                        created.append(name)
                    if not rule['enabled']:
                        continue
                    chain, address, field = ('incoming', 'daddr', 'dport') if direction == 'rx' else ('outgoing', 'saddr', 'sport')
                    match = '' if host in ('0.0.0.0', '::') else f'{"ip6" if ":" in host else "ip"} {address} {host} '
                    if host == '0.0.0.0':
                        match = 'meta nfproto ipv4 '
                    for protocol in ('tcp', 'udp') if rule['protocol'] == 'tcp_udp' else (rule['protocol'],):
                        if protocol not in ('tcp', 'udp'):
                            raise ValueError('Invalid protocol')
                        commands.append(f'add rule inet {TABLE} {chain} {match}{protocol} {field} {port} counter name {name}')
            self.nft('-f', '-', script='\n'.join(commands) + '\n')
            for name in created:
                self.db.execute('INSERT OR IGNORE INTO counters VALUES (?,0,0)', (name,))
                self.db.execute('UPDATE counters SET last=0 WHERE name=?', (name,))
            self.db.commit()
            self.signature = signature
            self.error = None
            self.configuration_error = None
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
            self.configuration_error = str(exc)

    def snapshot(self):
        try:
            self.accumulate(self.read())
            self.error = None
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
            self.error = str(exc)
        rules = {}
        for name, total in self.db.execute('SELECT name,total FROM counters'):
            if not name.startswith('r_'):
                continue
            key, direction = name[2:].rsplit('_', 1)
            rules.setdefault(str(uuid.UUID(key)), {'rx_bytes': 0, 'tx_bytes': 0})[direction + '_bytes'] = total
        for rule in rules.values():
            rule['total_bytes'] = rule['rx_bytes'] + rule['tx_bytes']
        return {'available': self.signature is not None and not (self.error or self.configuration_error), 'error': self.configuration_error or self.error, 'updated_at': int(time.time()), 'rules': rules}
