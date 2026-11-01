#!/usr/bin/env bash
# rmpctl update 入口；更新逻辑位于与本脚本同版本的 install.sh。
set -Eeuo pipefail
umask 077
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)/install.sh"
update_release "${1:-latest}"
