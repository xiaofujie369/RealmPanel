"""SQLite migrations, durable atomic writes and encrypted backups."""
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from cryptography.fernet import Fernet

DATA = Path(os.environ.get('RMP_DATA', '/app/data'))
DB = DATA / 'database/realm-panel.db'


def atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    with open(temp, 'wb') as f:
        os.chmod(temp, 0o600)
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def init() -> None:
    for d in ('database', 'realm', 'backups', 'logs', 'secrets', 'run'):
        (DATA / d).mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS migrations(version INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS groups(name TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS forward_rules(id INTEGER PRIMARY KEY AUTOINCREMENT,uuid TEXT UNIQUE NOT NULL,body TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,csrf TEXT NOT NULL,expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS login_attempts(ip TEXT PRIMARY KEY,count INTEGER NOT NULL,expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS operation_logs(id INTEGER PRIMARY KEY,time REAL,admin TEXT,source_ip TEXT,action TEXT,resource TEXT,result TEXT);
        CREATE TABLE IF NOT EXISTS backups(id TEXT PRIMARY KEY,time REAL,reason TEXT,rules INTEGER,size INTEGER);
        INSERT OR IGNORE INTO migrations VALUES(1);
        INSERT OR IGNORE INTO groups VALUES('默认');
        ''')
        db.execute('PRAGMA journal_mode=WAL')
        for key, value in {'network': {'tcp_timeout': 5, 'udp_timeout': 30, 'tcp_keepalive': 15}, 'retention': 30, 'whitelist': [], 'revision': 0}.items():
            db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', (key, json.dumps(value)))
    key = DATA / 'secrets/backup.key'
    if not key.exists():
        atomic(key, Fernet.generate_key())


def get(db: sqlite3.Connection, key: str):
    row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else None


def put(db: sqlite3.Connection, key: str, value) -> None:
    db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value, ensure_ascii=False)))


def rules(db: sqlite3.Connection) -> list[dict]:
    return [{**json.loads(r['body']), 'id': r['id'], 'uuid': r['uuid'], 'created_at': r['created_at'], 'updated_at': r['updated_at']} for r in db.execute('SELECT * FROM forward_rules ORDER BY id')]


def save_rule(db: sqlite3.Connection, body: dict, rid: int | None = None) -> int:
    db.execute('INSERT OR IGNORE INTO groups VALUES (?)', (body['group'],))
    if rid is None:
        return db.execute('INSERT INTO forward_rules(uuid,body,created_at,updated_at) VALUES (?,?,?,?)', (str(uuid.uuid4()), json.dumps(body, ensure_ascii=False), time.time(), time.time())).lastrowid
    if not db.execute('UPDATE forward_rules SET body=?,updated_at=? WHERE id=?', (json.dumps(body, ensure_ascii=False), time.time(), rid)).rowcount:
        raise ValueError('规则不存在')
    return rid


def snapshot(db: sqlite3.Connection) -> dict:
    return {table: [dict(r) for r in db.execute(f'SELECT * FROM {table}')] for table in ('users', 'settings', 'groups', 'forward_rules')}


def backup(db: sqlite3.Connection, reason: str) -> str:
    bid = str(uuid.uuid4())
    payload = Fernet((DATA / 'secrets/backup.key').read_bytes()).encrypt(json.dumps(snapshot(db), ensure_ascii=False).encode())
    atomic(DATA / 'backups' / f'{bid}.rmpbak', payload)
    db.execute('INSERT INTO backups VALUES (?,?,?,?,?)', (bid, time.time(), reason, len(rules(db)), len(payload)))
    return bid


def prune(db: sqlite3.Connection) -> None:
    for row in db.execute('SELECT id FROM backups ORDER BY time DESC LIMIT -1 OFFSET ?', (get(db, 'retention'),)).fetchall():
        (DATA / 'backups' / f'{row[0]}.rmpbak').unlink(missing_ok=True)
        db.execute('DELETE FROM backups WHERE id=?', (row[0],))


def read_backup(bid: str) -> dict:
    bid = str(uuid.UUID(bid))
    return json.loads(Fernet((DATA / 'secrets/backup.key').read_bytes()).decrypt((DATA / 'backups' / f'{bid}.rmpbak').read_bytes()))
