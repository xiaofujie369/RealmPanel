import csv
import io
import json
from datetime import datetime, timezone
from backend.models import Rule, Network, overlap
from backend import storage as s


def export(db, ids: list[int] | None = None, group: str | None = None) -> dict:
    rows = [r for r in s.rules(db) if (ids is None or r['id'] in ids) and (group is None or r['group'] == group)]
    return {'format': 'realm-panel', 'schema_version': 1, 'realm_version': '2.9.3', 'exported_at': datetime.now(timezone.utc).isoformat(), 'settings': s.get(db, 'network'), 'groups': [{'name': r[0]} for r in db.execute('SELECT name FROM groups ORDER BY name')], 'rules': [{k: r[k] for k in Rule.model_fields} for r in rows]}


def csv_export(payload: dict) -> str:
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=list(Rule.model_fields))
    writer.writeheader()
    for rule in payload['rules']:
        row = dict(rule)
        row['enabled'] = str(row['enabled']).lower()
        # Spreadsheet formula injection protection, reversed by this importer.
        for key in ('name', 'group', 'remark'):
            if row[key].startswith(('=', '+', '-', '@', '\t', '\r', "'")):
                row[key] = "'" + row[key]
        writer.writerow(row)
    return '\ufeff' + output.getvalue()


def parse(content: str, kind: str) -> dict:
    if len(content.encode()) > 2_000_000:
        raise ValueError('文件不得超过 2 MB')
    if kind == 'json':
        data = json.loads(content.lstrip('\ufeff'))
        if not isinstance(data, dict) or data.get('format') != 'realm-panel' or data.get('schema_version') != 1 or not isinstance(data.get('rules'), list):
            raise ValueError('不支持的格式或 schema_version')
        Network.model_validate(data.get('settings', {}))
    elif kind == 'csv':
        rows = list(csv.DictReader(io.StringIO(content.lstrip('\ufeff'))))
        for row in rows:
            for key in ('name', 'group', 'remark'):
                if row.get(key, '').startswith("'"):
                    row[key] = row[key][1:]
            if row.get('enabled') in ('true', 'false'):
                row['enabled'] = row['enabled'] == 'true'
            for key in Network.model_fields:
                if row.get(key) == '':
                    row[key] = None
        data = {'rules': rows}
    elif kind == 'text':
        rows = []
        for line in content.splitlines():
            if not line.strip():
                continue
            parts = line.split('|')
            if len(parts) != 5:
                raise ValueError('批量格式：监听端口|目标地址|目标端口|协议|名称')
            rows.append(dict(zip(('listen_port', 'remote_host', 'remote_port', 'protocol', 'name'), parts)))
        data = {'rules': rows}
    else:
        raise ValueError('不支持的文件格式')
    if len(data['rules']) > 2000:
        raise ValueError('每次最多导入 2000 条规则')
    return data


def preview(data: dict, existing: list[dict]) -> dict:
    valid, errors, conflicts = [], [], []
    for i, raw in enumerate(data['rules'], 1):
        try:
            rule = Rule.model_validate(raw).model_dump()
            if any(overlap(rule, r) for r in valid):
                raise ValueError('导入文件内部监听端口重复')
            matches = [r['id'] for r in existing if overlap(rule, r)]
            if matches:
                conflicts.append({'row': i, 'ids': matches, 'port': rule['listen_port']})
            valid.append(rule)
        except Exception as exc:
            errors.append({'row': i, 'error': str(exc)})
    return {'total': len(data['rules']), 'valid': len(valid), 'new': len(valid) - len(conflicts), 'conflicts': conflicts, 'errors': errors, 'rules': valid}
