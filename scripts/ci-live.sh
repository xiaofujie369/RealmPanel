#!/usr/bin/env bash
set -Eeuo pipefail
# GitHub-hosted runner only: a disposable install for real socket tests.
[[ ${GITHUB_ACTIONS:-} == true ]] || { echo '此脚本仅在 GitHub Actions 临时 runner 中执行。'; exit 1; }
mkdir -p /opt/realm-panel
tar --exclude=.git --exclude=dist --exclude=node_modules --exclude=frontend/node_modules -cf - . | tar -C /opt/realm-panel -xf -
cd /opt/realm-panel
export RMP_VERSION=$(cat VERSION)
mkdir -p data/{database,realm,run,backups,logs,secrets}
docker compose run --rm --no-deps -T realm-panel-web python -m backend.cli init --auto
install -m 600 data/secrets/install-info.txt /root/realm-panel-install-info.txt
rm data/secrets/install-info.txt
cleanup() { docker compose down || true; }
trap cleanup EXIT
docker compose up -d --no-build
python3 scripts/healthcheck.py
docker run --rm --network host -v /opt/realm-panel:/opt/realm-panel -v /root/realm-panel-install-info.txt:/root/realm-panel-install-info.txt:ro "realm-panel-web:$RMP_VERSION" python /opt/realm-panel/tests/live_acceptance.py
