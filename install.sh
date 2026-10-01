#!/usr/bin/env bash
# RealmPanel 预编译镜像安装器；VPS 不编译前后端。
set -Eeuo pipefail
umask 077
RMP_REPO=${RMP_REPO:-xiaofujie369/RealmPanel}
RMP_VERSION=${RMP_VERSION:-latest}
INSTALL_DIR=/opt/realm-panel
log() { printf '%s\n' "$*"; }
die() { log "✗ $*" >&2; exit 1; }
retry() {
  local attempt=1
  until "$@"; do
    (( attempt >= 3 )) && return 1
    log "下载或安装未完成，3 秒后重试（$attempt/3）……"
    sleep 3
    attempt=$((attempt + 1))
  done
}
detect_system() {
  [[ $(uname -s) == Linux ]] || die '仅支持 Linux。'
  case $(uname -m) in
    x86_64) RMP_ARCH=amd64 ;;
    aarch64|arm64) RMP_ARCH=arm64 ;;
    *) die '仅支持 x86_64 / ARM64 架构。' ;;
  esac
  [[ -r /etc/os-release ]] || die '无法识别操作系统。'
  . /etc/os-release
  case $ID in
    ubuntu) [[ ${VERSION_ID%%.*} -ge 22 ]] || die '需要 Ubuntu 22.04 或更新版本。'; RMP_FAMILY=apt ;;
    debian) [[ ${VERSION_ID%%.*} -ge 11 ]] || die '需要 Debian 11 或更新版本。'; RMP_FAMILY=apt ;;
    rocky|almalinux|rhel|centos) [[ ${VERSION_ID%%.*} -ge 8 ]] || die '不支持 CentOS 7，请升级操作系统。'; RMP_FAMILY=rpm ;;
    fedora) RMP_FAMILY=rpm ;;
    alpine) RMP_FAMILY=apk ;;
    *) die "暂不支持 $ID；支持 Ubuntu、Debian、Rocky、AlmaLinux、RHEL、CentOS Stream、Fedora、Alpine。" ;;
  esac
  log "系统：${PRETTY_NAME:-$ID}；架构：$RMP_ARCH"
}
install_dependencies() {
  case $RMP_FAMILY in
    apt)
      export DEBIAN_FRONTEND=noninteractive
      retry apt-get -o DPkg::Lock::Timeout=120 update
      retry apt-get -o DPkg::Lock::Timeout=120 install -y ca-certificates curl python3 tar gzip util-linux coreutils
      ;;
    rpm)
      local package_manager=dnf
      local packages=(ca-certificates python3 tar gzip util-linux)
      command -v dnf >/dev/null || package_manager=yum
      command -v curl >/dev/null || packages+=(curl)
      command -v sha256sum >/dev/null && command -v stat >/dev/null || packages+=(coreutils)
      retry "$package_manager" install -y "${packages[@]}"
      ;;
    apk) retry apk add --no-cache bash ca-certificates curl python3 tar gzip util-linux coreutils ;;
  esac
}
ensure_swap() {
  [[ ${RMP_SKIP_SWAP:-0} == 1 ]] && { log '已按 RMP_SKIP_SWAP=1 跳过 Swap 配置。'; return; }
  local current free_kb swap_dir=/var/lib/realm-panel swap_file=/var/lib/realm-panel/swapfile
  current=$(awk '/^SwapTotal:/ {print $2}' /proc/meminfo)
  if (( current >= 1048000 )); then
    log '✓ 已有至少 1 GB Swap，保留原有配置。'
    return
  fi
  mkdir -p "$swap_dir"
  chmod 700 "$swap_dir"
  [[ ! -L "$swap_file" ]] || die 'Swap 路径是符号链接，拒绝覆盖。'
  free_kb=$(df -Pk "$swap_dir" | awk 'END {print $4}')
  (( free_kb >= 2097152 )) || die '创建 1 GB Swap 至少需要 2 GB 空闲磁盘。'
  if [[ ! -f "$swap_file" ]]; then
    if [[ $(stat -f -c %T "$swap_dir") == btrfs ]]; then
      command -v btrfs >/dev/null || die 'Btrfs 文件系统需要先安装 btrfs-progs，或设置 RMP_SKIP_SWAP=1。'
      btrfs filesystem mkswapfile --size 1g "$swap_file"
    else
      dd if=/dev/zero of="$swap_file" bs=1M count=1024 status=none
      chmod 600 "$swap_file"
      mkswap "$swap_file"
    fi
  fi
  [[ $(stat -c %s "$swap_file") == 1073741824 ]] || die '已有 RealmPanel Swap 大小异常，拒绝覆盖。'
  chmod 600 "$swap_file"
  if ! swapon --show=NAME --noheadings | grep -Fxq "$swap_file"; then
    swapon "$swap_file" || die '当前虚拟化或文件系统禁止启用 Swap。请联系 VPS 提供商；确认内存充足后可用 RMP_SKIP_SWAP=1 跳过。'
  fi
  grep -Fq "$swap_file " /etc/fstab || printf '%s none swap sw 0 0\n' "$swap_file" >> /etc/fstab
  log '✓ 已启用 1 GB Swap 并配置开机启用。Swap 是磁盘交换空间，不是物理内存。'
}
install_docker_packages() {
  if ! command -v docker >/dev/null; then
    case $ID in
      alpine) retry apk add --no-cache docker docker-cli-compose ;;
      rocky|almalinux|rhel)
        retry dnf install -y dnf-plugins-core
        curl -fsSL --retry 3 https://download.docker.com/linux/centos/docker-ce.repo -o /etc/yum.repos.d/docker-ce.repo
        retry dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
        ;;
      *)
        local installer
        installer=$(mktemp /tmp/realm-panel-docker.XXXXXX)
        curl -fsSL --retry 3 https://get.docker.com -o "$installer"
        sh "$installer"
        rm -f "$installer"
        ;;
    esac
  fi
  if ! docker compose version >/dev/null 2>&1; then
    case $RMP_FAMILY in
      apt) retry apt-get -o DPkg::Lock::Timeout=120 install -y docker-compose-plugin || retry apt-get install -y docker-compose-v2 ;;
      rpm) retry dnf install -y docker-compose-plugin ;;
      apk) retry apk add --no-cache docker-cli-compose ;;
    esac
  fi
  docker compose version
}
ensure_docker() {
  install_docker_packages
  if command -v systemctl >/dev/null && [[ -d /run/systemd/system ]]; then
    systemctl enable --now docker
  elif command -v rc-update >/dev/null; then
    rc-update add docker default
    rc-service docker start
  elif ! docker info >/dev/null 2>&1; then
    die '未检测到 systemd 或 OpenRC，请先启动 Docker 服务。'
  fi
  docker info >/dev/null || die 'Docker 服务不可用。'
  docker compose version
  log '✓ Docker 和 Compose 已就绪。'
}
download_release() {
  WORK=$(mktemp -d /tmp/realm-panel-release.XXXXXX)
  local url
  if [[ $RMP_VERSION == latest ]]; then
    url="https://github.com/$RMP_REPO/releases/latest/download"
  else
    RMP_VERSION=${RMP_VERSION#v}
    [[ $RMP_VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.-]+)?$ ]] || die '无效版本号。'
    url="https://github.com/$RMP_REPO/releases/download/v$RMP_VERSION"
  fi
  curl -fL --retry 3 --connect-timeout 15 "$url/release.json" -o "$WORK/release.json"
  RELEASE_VERSION=$(python3 -c 'import json,sys,re; v=json.load(open(sys.argv[1]))["version"]; assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[.-][A-Za-z0-9.-]+)?",v); print(v)' "$WORK/release.json")
  url="https://github.com/$RMP_REPO/releases/download/v$RELEASE_VERSION"
  curl -fL --retry 3 "$url/SHA256SUMS" -o "$WORK/SHA256SUMS"
  local file
  for file in realm-panel.tar.gz "realm-panel-images-$RMP_ARCH.tar.gz"; do
    curl -fL --retry 3 --connect-timeout 15 "$url/$file" -o "$WORK/$file"
    (cd "$WORK"; awk -v file="$file" '$2 == file {print}' SHA256SUMS | sha256sum -c -) || die "文件校验失败：$file"
  done
  mkdir "$WORK/source"
  tar -xzf "$WORK/realm-panel.tar.gz" -C "$WORK/source" --strip-components=1
  [[ $(cat "$WORK/source/VERSION") == "$RELEASE_VERSION" ]] || die '源码和镜像版本不一致。'
  log "✓ 已校验 RealmPanel $RELEASE_VERSION ($RMP_ARCH) 预编译镜像。"
}
show_install_info() {
  local public_ip=${RMP_PUBLIC_IP:-}
  if [[ -z $public_ip ]]; then
    public_ip=$(curl -4 -fsS --connect-timeout 3 --max-time 5 https://api.ipify.org || true)
    [[ $public_ip =~ ^[0-9.]+$ ]] || public_ip=$(hostname -I 2>/dev/null | awk '{print $1}')
  fi
  python3 scripts/show-address.py "${public_ip:-服务器IP}"
  [[ ! -f /root/realm-panel-install-info.txt ]] || cat /root/realm-panel-install-info.txt
  log '安装信息：/root/realm-panel-install-info.txt（权限 600）'
  log '常用命令：rmpctl status | info | restart | backup | reset-password | update'
}
main() {
  if [[ ${1:-} == --help ]]; then
    log '用法：bash install.sh [--yes|--check]'
    log '环境变量：RMP_VERSION=版本号；RMP_PUBLIC_IP=公网IP；RMP_SKIP_SWAP=1（明确无需 Swap 时）'
    return
  fi
  trap 'log "✗ 安装失败（第 $LINENO 行）。请检查上方错误，已有数据不会删除。" >&2' ERR
  log '========================================================'
  log ' RealmPanel 中文安装器 · GitHub 预编译镜像 · 无需本机编译'
  log '========================================================'
  log '[1/9] 检查系统与架构'
  detect_system
  [[ ${1:-} == --check ]] && return
  [[ $(id -u) == 0 ]] || die '请使用 root 用户执行安装。'
  if [[ -f "$INSTALL_DIR/data/database/realm-panel.db" ]]; then
    log '已安装 RealmPanel，保留现有数据。升级请运行：rmpctl update'
    return
  fi
  log '[2/9] 安装必要的系统工具'
  install_dependencies
  log '[3/9] 配置 1 GB Swap 交换空间'
  ensure_swap
  log '[4/9] 检查并自动安装 Docker / Compose'
  ensure_docker
  log '[5/9] 下载并校验 GitHub Release'
  download_release
  log '[6/9] 导入预编译镜像（不在 VPS 编译）'
  docker load -i "$WORK/realm-panel-images-$RMP_ARCH.tar.gz"
  mkdir -p "$INSTALL_DIR"
  tar -C "$WORK/source" -cf - . | tar -C "$INSTALL_DIR" -xf -
  cd "$INSTALL_DIR"
  printf 'RMP_VERSION=%s\n' "$RELEASE_VERSION" > .env
  mkdir -p data/{database,realm,run,backups,logs,secrets}
  chmod 700 data data/secrets
  log '[7/9] 初始化数据库与管理员账号'
  if [[ ${1:-} == --yes || ! -e /dev/tty ]]; then
    docker compose run --rm --no-deps -T realm-panel-web python -m backend.cli init --auto
  else
    docker compose run --rm --no-deps realm-panel-web python -m backend.cli init </dev/tty
  fi
  log '[8/9] 启动服务并设置开机自动恢复'
  install -m 755 scripts/rmpctl /usr/local/bin/rmpctl
  docker compose up -d --no-build
  log '[9/9] 执行健康检查'
  python3 scripts/healthcheck.py
  if [[ -f data/secrets/install-info.txt ]]; then
    install -m 600 data/secrets/install-info.txt /root/realm-panel-install-info.txt
    rm data/secrets/install-info.txt
  fi
  log '========================================================'
  log '✓ RealmPanel 安装完成'
  show_install_info
  log '========================================================'
  [[ $WORK == /tmp/realm-panel-release.* ]] && rm -rf -- "$WORK"
}
if [[ -z ${BASH_SOURCE[0]:-} || ${BASH_SOURCE[0]} == "$0" ]]; then
  main "$@"
fi
