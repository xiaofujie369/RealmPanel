#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
cd /opt/realm-panel
source ./install.sh
RMP_VERSION=${1:-latest}
detect_system
log '下载新版本预编译镜像，当前服务继续运行……'
download_release
docker load -i "$WORK/realm-panel-images-$RMP_ARCH.tar.gz"
backup_dir=$(mktemp -d /opt/realm-panel-update.XXXXXX)
mapfile -t old_images < <(docker compose config --images)
for index in "${!old_images[@]}"; do
  docker tag "${old_images[$index]}" "realm-panel-rollback:$index"
done
log '创建完整升级备份……'
docker compose stop realm-panel-web realm-panel-core
tar -czf "$backup_dir/previous.tar.gz" --exclude=.git -C /opt/realm-panel .
rollback() {
  trap - ERR
  log '更新失败，恢复之前版本、数据库和镜像……'
  docker compose down || true
  tar -xzf "$backup_dir/previous.tar.gz" -C /opt/realm-panel
  for index in "${!old_images[@]}"; do
    docker tag "realm-panel-rollback:$index" "${old_images[$index]}"
  done
  docker compose up -d --no-build
  python3 scripts/healthcheck.py || { log '恢复健康检查未通过，请运行 rmpctl logs。'; exit 1; }
  log "已恢复旧版本；完整备份：$backup_dir"
  exit 1
}
trap rollback ERR
tar -C "$WORK/source" -cf - . | tar -C /opt/realm-panel -xf -
printf 'RMP_VERSION=%s\n' "$RELEASE_VERSION" > .env
docker compose up -d --no-build
python3 scripts/healthcheck.py
install -m 755 scripts/rmpctl /usr/local/bin/rmpctl
trap - ERR
[[ $WORK == /tmp/realm-panel-release.* ]] && rm -rf -- "$WORK"
log "✓ 已升级到 $RELEASE_VERSION，无需本机编译。升级备份：$backup_dir/previous.tar.gz"
