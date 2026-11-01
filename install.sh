#!/usr/bin/env bash
# RealmPanel（nftables 内核转发）预编译镜像安装器；VPS 不编译前后端。
set -Eeuo pipefail
umask 077
RMP_REPO=${RMP_REPO:-xiaofujie369/RealmPanel}
RMP_VERSION=${RMP_VERSION:-latest}
# GitHub 加速前缀，例如 RMP_MIRROR=https://ghfast.top/ ；为空时直连 GitHub。
RMP_MIRROR=${RMP_MIRROR:-}
[[ -z $RMP_MIRROR || $RMP_MIRROR == */ ]] || RMP_MIRROR="$RMP_MIRROR/"
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
  # Debian 13 provides the engine, CLI and Compose v2 in its own repository.
  # Avoid download.docker.com entirely on fresh Debian 13 installations.
  if [[ $ID == debian && ${VERSION_ID%%.*} -ge 13 ]]; then
    if ! command -v docker >/dev/null; then
      retry apt-get -o DPkg::Lock::Timeout=120 install -y docker.io docker-cli
    fi
    if ! docker compose version >/dev/null 2>&1; then
      retry apt-get -o DPkg::Lock::Timeout=120 install -y docker-compose
    fi
    docker --version
    docker compose version
    return
  fi
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
        if ! { curl -fsSL --retry 2 --connect-timeout 15 https://get.docker.com -o "$installer" && sh "$installer"; }; then
          rm -f "$installer"
          [[ $ID == ubuntu ]] || die '无法从 download.docker.com 安装 Docker；请先手动安装 Docker 与 Compose v2 后重试。'
          # 国内网络常连不上 Docker 官方源，改用 Ubuntu 自带软件源（同样是 Compose v2）。
          log 'Docker 官方源不可用，改用 Ubuntu 软件源安装 docker.io 与 docker-compose-v2……'
          retry apt-get -o DPkg::Lock::Timeout=120 install -y docker.io docker-compose-v2
        fi
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
enable_ip_forward() {
  # 直接写 /proc，精简 Debian 可能没有 procps 提供的 sysctl 命令。
  mkdir -p /etc/sysctl.d
  printf 'net.ipv4.ip_forward = 1\n' > /etc/sysctl.d/90-realm-panel.conf
  [[ $(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null) == 1 ]] || echo 1 > /proc/sys/net/ipv4/ip_forward || die '无法开启 IPv4 转发（net.ipv4.ip_forward）。'
}
ensure_forwarding() {
  # nftables DNAT needs nf_tables and the kernel routing packets between interfaces.
  modprobe nf_tables 2>/dev/null || true
  [[ -d /sys/module/nf_tables ]] || die '内核不支持 nftables（nf_tables 不可用）；OpenVZ/LXC 等受限容器无法使用内核转发。'
  enable_ip_forward
  log '✓ 已开启 IPv4 转发并写入 /etc/sysctl.d/90-realm-panel.conf。'
  [[ $(cat /proc/sys/net/ipv6/conf/all/forwarding 2>/dev/null) == 1 ]] || log '提示：未自动开启 IPv6 转发（会影响依赖路由通告的 IPv6 网络），需要 IPv6 转发时请手动设置 net.ipv6.conf.all.forwarding=1。'
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
  [[ -z $RMP_MIRROR ]] || log "使用 GitHub 加速镜像：$RMP_MIRROR（文件仍按 SHA256SUMS 校验）"
  url="$RMP_MIRROR$url"
  curl -fL --retry 3 --connect-timeout 15 "$url/release.json" -o "$WORK/release.json"
  RELEASE_VERSION=$(python3 -c 'import json,sys,re; v=json.load(open(sys.argv[1]))["version"]; assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[.-][A-Za-z0-9.-]+)?",v); print(v)' "$WORK/release.json")
  url="${RMP_MIRROR}https://github.com/$RMP_REPO/releases/download/v$RELEASE_VERSION"
  curl -fL --retry 3 --connect-timeout 15 "$url/SHA256SUMS" -o "$WORK/SHA256SUMS"
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
update_release() {
  # 供 rmpctl update、install.sh --update 及本地安装包升级共用。
  [[ $(id -u) == 0 ]] || die '请使用 root 用户执行更新。'
  [[ -f "$INSTALL_DIR/data/database/realm-panel.db" ]] || die '未检测到已安装的 RealmPanel，请使用全新安装命令。'
  RMP_VERSION=${1:-$RMP_VERSION}
  cd "$INSTALL_DIR"
  detect_system
  log '获取新版本预编译镜像，当前服务继续运行……'
  download_release
  docker load -i "$WORK/realm-panel-images-$RMP_ARCH.tar.gz"
  UPDATE_BACKUP=$(mktemp -d /opt/realm-panel-update.XXXXXX)
  mapfile -t OLD_IMAGES < <(docker compose config --images)
  local index
  for index in "${!OLD_IMAGES[@]}"; do
    docker tag "${OLD_IMAGES[$index]}" "realm-panel-rollback:$index"
  done
  log '创建完整升级备份……'
  docker compose stop realm-panel-web realm-panel-core
  tar -czf "$UPDATE_BACKUP/previous.tar.gz" --exclude=.git -C "$INSTALL_DIR" .
  trap update_rollback ERR
  tar -C "$WORK/source" -cf - . | tar -C "$INSTALL_DIR" -xf -
  mkdir -p data/forward data/traffic
  enable_ip_forward
  printf 'RMP_VERSION=%s\n' "$RELEASE_VERSION" > .env
  docker compose up -d --no-build
  python3 scripts/healthcheck.py
  install -m 755 scripts/rmpctl /usr/local/bin/rmpctl
  trap - ERR
  [[ $WORK == /tmp/realm-panel-release.* ]] && rm -rf -- "$WORK"
  log "✓ 已升级到 $RELEASE_VERSION，无需本机编译。升级备份：$UPDATE_BACKUP/previous.tar.gz"
}
update_rollback() {
  trap - ERR
  log '更新失败，恢复之前版本、数据库和镜像……'
  cd "$INSTALL_DIR"
  docker compose down || true
  tar -xzf "$UPDATE_BACKUP/previous.tar.gz" -C "$INSTALL_DIR"
  local index
  for index in "${!OLD_IMAGES[@]}"; do
    docker tag "realm-panel-rollback:$index" "${OLD_IMAGES[$index]}"
  done
  docker compose up -d --no-build
  python3 scripts/healthcheck.py || { log '恢复健康检查未通过，请运行 rmpctl logs。'; exit 1; }
  log "已恢复旧版本；完整备份：$UPDATE_BACKUP"
  exit 1
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
    log '用法：bash install.sh [--yes|--check|--update]'
    log '环境变量：RMP_VERSION=版本号；RMP_PUBLIC_IP=公网IP；RMP_MIRROR=GitHub 加速前缀；RMP_SKIP_SWAP=1（明确无需 Swap 时）'
    return
  fi
  if [[ ${1:-} == --update ]]; then
    update_release "$RMP_VERSION"
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
    log '已安装 RealmPanel，保留现有数据。升级请运行：rmpctl update（国内网络可用 bash install.sh --update 配合 RMP_MIRROR）'
    return
  fi
  log '[2/9] 安装必要的系统工具'
  install_dependencies
  log '[3/9] 配置 1 GB Swap 交换空间'
  ensure_swap
  log '[4/9] 检查 nftables 内核支持、开启转发，并自动安装 Docker / Compose'
  ensure_forwarding
  ensure_docker
  log '[5/9] 下载并校验 GitHub Release'
  download_release
  log '[6/9] 导入预编译镜像（不在 VPS 编译）'
  docker load -i "$WORK/realm-panel-images-$RMP_ARCH.tar.gz"
  mkdir -p "$INSTALL_DIR"
  tar -C "$WORK/source" -cf - . | tar -C "$INSTALL_DIR" -xf -
  cd "$INSTALL_DIR"
  printf 'RMP_VERSION=%s\n' "$RELEASE_VERSION" > .env
  mkdir -p data/{database,forward,run,backups,logs,secrets,traffic}
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
