"""Pin release and verify GitHub's asset digest when available."""
import hashlib
import json
import os
import argparse
import platform
import tarfile
import urllib.request
from pathlib import Path

target = Path('vendor')
target.mkdir(exist_ok=True)
parser = argparse.ArgumentParser()
parser.add_argument('--arch', choices=['amd64', 'arm64'], default='arm64' if platform.machine() in ('aarch64', 'arm64') else 'amd64')
arch = parser.parse_args().arch
name = f'realm-{"aarch64" if arch == "arm64" else "x86_64"}-unknown-linux-gnu.tar.gz'
request = urllib.request.Request('https://api.github.com/repos/zhboner/realm/releases/tags/v2.9.3', headers={'User-Agent': 'RealmPanel-installer'})
release = json.load(urllib.request.urlopen(request, timeout=30))
asset = next(a for a in release['assets'] if a['name'] == name)
archive = urllib.request.urlopen(asset['browser_download_url'], timeout=90).read()
digest = 'sha256:' + hashlib.sha256(archive).hexdigest()
if asset.get('digest') and digest != asset['digest']:
    raise SystemExit('Realm 下载校验失败')
(target / name).write_bytes(archive)
with tarfile.open(target / name) as tar:
    member = next(m for m in tar.getmembers() if Path(m.name).name == 'realm' and m.isfile())
    (target / 'realm').write_bytes(tar.extractfile(member).read())
os.chmod(target / 'realm', 0o755)
(target / 'SHA256SUM').write_text(digest + '  ' + name + '\n')
print('Realm v2.9.3 下载完成；' + digest)
