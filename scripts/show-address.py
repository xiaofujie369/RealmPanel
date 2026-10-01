import json
import sqlite3
import sys
with sqlite3.connect('/opt/realm-panel/data/database/realm-panel.db') as db:
    settings = {k: json.loads(v) for k, v in db.execute('SELECT key,value FROM settings')}
print(f'Web 管理地址：http://{sys.argv[1]}:{settings["web_port"]}{settings["web_path"]}')
