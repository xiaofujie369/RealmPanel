import uvicorn
from backend import storage as s
s.init()
with s.connect() as db:
    port = s.get(db, 'web_port')
if not port:
    raise SystemExit('请先运行安装初始化')
uvicorn.run('backend.app:app', host='0.0.0.0', port=port, workers=1, server_header=False, proxy_headers=False, access_log=False)
