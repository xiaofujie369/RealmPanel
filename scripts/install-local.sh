#!/usr/bin/env bash
# Install a public release uploaded through SSH; no application download on VPS.
set -Eeuo pipefail
umask 077
BUNDLE=$(cd -- "${1:-.}" && pwd -P)
MODE=${2:---yes}
[[ $MODE == --yes || $MODE == --check ]] || { echo '用法：bash install-local.sh 安装包目录 [--yes|--check]' >&2; exit 1; }
verify_file() {
  local file=$1 line
  [[ -f "$BUNDLE/$file" && -f "$BUNDLE/SHA256SUMS" ]] || { echo "缺少安装文件：$file" >&2; exit 1; }
  line=$(awk -v file="$file" 'NF == 2 && $2 == file {print}' "$BUNDLE/SHA256SUMS")
  [[ $(printf '%s\n' "$line" | awk 'NF {n++} END {print n+0}') == 1 ]] || { echo "校验清单缺少或重复：$file" >&2; exit 1; }
  (cd "$BUNDLE" && printf '%s\n' "$line" | sha256sum -c -)
}
verify_file install.sh
source "$BUNDLE/install.sh"
# Reuse the verified release installer, replacing only its download operation.
download_release() {
  for file in release.json realm-panel.tar.gz "realm-panel-images-$RMP_ARCH.tar.gz"; do
    verify_file "$file"
  done
  RELEASE_VERSION=$(python3 -c 'import json,sys,re; v=json.load(open(sys.argv[1]))["version"]; assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[.-][A-Za-z0-9.-]+)?",v); print(v)' "$BUNDLE/release.json")
  WORK=$(mktemp -d /tmp/realm-panel-release.XXXXXX)
  cp -- "$BUNDLE/realm-panel-images-$RMP_ARCH.tar.gz" "$WORK/"
  mkdir "$WORK/source"
  tar -xzf "$BUNDLE/realm-panel.tar.gz" -C "$WORK/source" --strip-components=1
  [[ $(cat "$WORK/source/VERSION") == "$RELEASE_VERSION" ]] || die '源码和镜像版本不一致。'
  log "✓ 本地安装包校验通过：RealmPanel $RELEASE_VERSION ($RMP_ARCH)，VPS 无需连接 GitHub。"
}
if [[ $MODE == --check ]]; then
  detect_system
  download_release
  [[ $WORK == /tmp/realm-panel-release.* ]] && rm -rf -- "$WORK"
  exit 0
fi
main --yes
