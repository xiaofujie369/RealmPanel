import argparse
import getpass
import json
import secrets
import socket
import string
import sys
from argon2 import PasswordHasher
from backend import storage as s
from backend import __version__
from backend.models import generate


def random_password() -> str:
    return ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20)) + '!aA7'


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['init', 'info', 'reset-password', 'reset-path', 'backup', 'restore-full'])
    parser.add_argument('--auto', action='store_true')
    parser.add_argument('--file')
    args = parser.parse_args()
    s.init()
    with s.connect() as db:
        if args.command == 'init':
            if db.execute('SELECT 1 FROM users').fetchone():
                print('已有安装，保留原账号及数据。')
                return
            port_text = '' if args.auto else input('Web 管理端口 [回车随机]：').strip()
            while True:
                port = int(port_text) if port_text else secrets.randbelow(45000) + 20000
                if not 1024 <= port <= 65535:
                    raise ValueError('端口必须在 1024 - 65535 之间')
                try:
                    with socket.socket() as sock:
                        sock.bind(('0.0.0.0', port))
                    break
                except OSError:
                    if port_text:
                        port_text = input('端口已被占用，请输入其他端口：').strip()
            path = '' if args.auto else input('Web 管理路径 [回车随机]：').strip('/')
            if path and (not path.isascii() or not path.isalnum() or not 1 <= len(path) <= 64):
                raise ValueError('管理路径只能包含 1 - 64 位英文字母和数字')
            path = '/' + (path or ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(14))) + '/'
            name = ('' if args.auto else input('管理员账号 [回车随机]：').strip()) or 'rmp_' + secrets.token_hex(4)
            password = ('' if args.auto else getpass.getpass('管理员密码 [回车随机]：')) or random_password()
            if len(password) < 8:
                raise ValueError('管理员密码至少需要 8 位')
            db.execute('INSERT INTO users VALUES (1,?,?)', (name, PasswordHasher().hash(password)))
            s.put(db, 'web_port', port)
            s.put(db, 'web_path', path)
            s.atomic(s.DATA / 'realm/active.json', json.dumps(generate([], s.get(db, 'network'))).encode())
            s.atomic(s.DATA / 'secrets/install-info.txt', f'Web 端口：{port}\nWeb 路径：{path}\n管理员账号：{name}\n管理员密码：{password}\n'.encode())
            print(f'Web 端口：{port}\nWeb 路径：{path}\n管理员账号：{name}\n管理员密码：{password}')
        elif args.command == 'info':
            row = db.execute('SELECT username FROM users LIMIT 1').fetchone()
            print(f'RealmPanel {__version__} / Realm 2.9.3\nWeb 端口：{s.get(db, "web_port")}\nWeb 路径：{s.get(db, "web_path")}\n管理员：{row[0]}\n规则：{len(s.rules(db))}\n安装目录：/opt/realm-panel')
        elif args.command == 'reset-password':
            row = db.execute('SELECT username FROM users LIMIT 1').fetchone()
            name = ('' if args.auto else input(f'新账号 [回车保留 {row[0]}]：')) or row[0]
            password = ('' if args.auto else getpass.getpass('新密码 [回车随机]：')) or random_password()
            if len(password) < 8:
                raise ValueError('密码至少需要 8 位')
            db.execute('UPDATE users SET username=?,password=?', (name, PasswordHasher().hash(password)))
            db.execute('DELETE FROM sessions')
            db.execute('DELETE FROM login_attempts')
            print(f'管理员信息已更新。\n账号：{name}\n密码：{password}')
        elif args.command == 'reset-path':
            path = '/' + ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(14)) + '/'
            s.put(db, 'web_path', path)
            s.put(db, 'whitelist', [])
            db.execute('DELETE FROM sessions')
            print(f'管理路径已重置：{path}\nIP 白名单已清空。')
        elif args.command == 'backup':
            print('已创建加密备份：' + s.backup(db, 'SSH 手动备份'))
        elif args.command == 'restore-full':
            if not args.file:
                raise ValueError('需要 --file 备份 UUID')
            payload = s.read_backup(args.file)
            from backend import manager
            def restore(target):
                for table in ('users', 'settings', 'groups', 'forward_rules'):
                    target.execute(f'DELETE FROM {table}')
                    for row in payload[table]:
                        columns = list(row)
                        allowed = {r[1] for r in target.execute(f'PRAGMA table_info({table})')}
                        if not set(columns) <= allowed:
                            raise ValueError('备份包含无效数据库字段')
                        target.execute(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})', tuple(row.values()))
                target.execute('DELETE FROM sessions')
            manager.apply(restore, '完整恢复前备份')
            print('完整数据库已恢复，重启 Web 后将从数据库重新生成 Realm 配置。')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'操作失败：{exc}', file=sys.stderr)
        sys.exit(1)
