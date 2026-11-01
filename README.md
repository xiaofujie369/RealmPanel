# RealmPanel

基于 **nftables 内核转发**的轻量 TCP / UDP 转发管理面板。Web 管理规则，Linux 内核 netfilter（DNAT + SNAT）负责真实转发，数据不经过用户态进程；无需域名或证书，Docker 部署。

0.10.0 起转发核心由 Realm 切换为 nftables。同机实测（4 并发 iperf3，veth 隔离网络）：TCP 吞吐 realm 17.8–18.4 Gbit/s → nftables 25.7–27.5 Gbit/s（约 +45%）；UDP 800 Mbit/s 时 realm 丢包 15–25%，nftables 0%。旧版数据库、备份和导出文件自动兼容，详见下文「从 Realm 版本迁移」。

当前为 **0.10.2 验收版本**。设计原文见 [DESIGN.md](DESIGN.md)，真实验收范围见 [ACCEPTANCE.md](ACCEPTANCE.md)。未完成全矩阵验收前不会标记为 1.0.0。

## 功能

- 随机端口、管理路径、管理员账号及高强度密码；支持安装时自定义。
- TCP / UDP / TCP+UDP，IPv4 / IPv6 / 域名目标，规则启停、编辑、复制、分组及批量操作。
- 目标支持域名：核心每 30 秒重新解析，地址变化自动更新；DNS 暂时失败时沿用上次地址。监听 `::` 时 IPv4 客户端转发到 A 记录、IPv6 客户端转发到 AAAA 记录。
- Through（出口源 IP，SNAT）；留空时自动使用出口网卡地址（masquerade）。
- 每次修改先加密备份，再原子替换 nftables 规则；端口被本机进程占用、域名无法解析或未开启转发时拒绝应用并整体回滚。
- 按规则增量更新：只改动被操作规则的端口，未改动规则的现有连接不受影响；被修改、暂停或删除的规则会清除其连接跟踪，立即断开。
- JSON / CSV 导入预览及冲突处理（跳过、覆盖、寻找新端口），批量文本添加。
- 备份管理、运行日志、操作审计、账号修改、SSH 重置、访问白名单。
- 紧凑的中文表单与表格、响应式移动布局、黑白主题切换；每条规则支持编辑、暂停/恢复、诊断、复制和删除。
- 每条规则的已用流量、上行和下行：按连接跟踪方向统计网络层字节，包含协议开销；不重复计入出口侧相同转发数据。

计数每 5 秒保存至 `data/traffic/traffic.sqlite3`，界面每 15 秒刷新，规则启停、端口编辑、正常重启及从 Realm 版本升级均保留累计；异常断电可能丢失最后一个保存周期。

### 转发原理与对防火墙的影响

- 核心只创建 `table inet realmpanel`：prerouting / output 链做 DNAT（`fib daddr type local` 限定只处理发往本机的流量），postrouting 链对已 DNAT 的连接做 masquerade 或指定 SNAT，另有两个只计数的 filter 链。从不执行 `flush ruleset`，不改动其他表。
- 每个钩子只有一条按「协议 . 端口」查表的规则（nftables verdict map），命中后跳到该端口自己的小链；数据包开销与规则数量无关。
- **按规则增量应用**：新增、编辑、暂停、删除一条规则时，只重写该规则所用端口的链和查表条目，其余规则的内核对象与现有连接完全不动；严格检查也只针对本次新增或修改的规则，其他规则的问题（例如域名暂时解析失败）只在概览中提示，不会阻止保存。
- Docker、ufw 会把 iptables FORWARD 默认策略设为 DROP。此时核心新建 `RMP-FORWARD` 链，并在 `DOCKER-USER`（无则在 `FORWARD`）首行跳转，链内只放行本面板规则对应的 DNAT 连接（`--ctstate DNAT --ctorigdstport 端口`）。
- 核心容器停止（`rmpctl stop` / 卸载）时删除上述表和链，转发随之停止；核心启动时从数据库重建。
- 没有监听进程，因此端口冲突改为读取本机套接字表检查：端口被本机服务（例如 SSH、Web）监听时拒绝新建规则，避免劫持该服务的外部流量。
- 安装器开启 `net.ipv4.ip_forward` 并写入 `/etc/sysctl.d/90-realm-panel.conf`。IPv6 转发不会自动开启（开启后内核默认不再接受路由通告，可能导致依赖 SLAAC 的 IPv6 断网）；需要转发外部 IPv6 流量时请自行设置 `net.ipv6.conf.all.forwarding=1`（并按需设置 `accept_ra=2`）。

### 与 Realm 的行为差异

- nftables 不做 IPv4 / IPv6 互转：目标地址必须与监听地址同族，监听 `::` 可同时服务两种。
- 外部流量不能转发到 `127.0.0.1` 等回环地址；转发到本机服务请填写本机网卡 IP，或监听地址也使用回环地址。
- 不再支持 Interface（出口网卡由系统路由决定）、TCP 连接超时、UDP 超时和 TCP Keepalive（由内核连接跟踪管理）。
- 从 `127.0.0.1` 发起、目标为其他主机的连接无法经回环地址转发（内核会丢弃源地址为回环的外发包）；本机测试请连接本机网卡 IP。

### 从 Realm 版本迁移

在 0.9.x 上执行 `rmpctl update` 即可。升级时数据库自动迁移：规则保留，Realm 专用的 Interface、超时及 Keepalive 参数与全局网络设置被移除；旧 `realmpanel_meter` 计数表被删除，累计流量保留。旧版加密备份可直接在 Web 或 `rmpctl restore-full` 中恢复。若有规则同时满足「目标为 IPv6、监听为 IPv4」等跨族组合，升级后它会在概览中显示为问题，请改为同族地址。

## 系统要求

Linux **x86_64 / ARM64**，内核支持 nftables（nf_tables，Linux 5.2+ 的 inet NAT；主流发行版默认具备）。OpenVZ / 部分 LXC 等不允许操作内核防火墙的虚拟化无法使用。建议至少 512 MB 内存、3 GB 可用磁盘。安装仅需下载 GitHub Releases 预编译镜像，不需要 VPS 访问 npm/PyPI 或编译代码。

安装器支持 Ubuntu 22.04+、Debian 11+、Rocky Linux / AlmaLinux / RHEL / CentOS Stream 8+、Fedora、Alpine。使用 apt、dnf/yum 或 apk 安装依赖，适配 systemd / OpenRC。不同发行版的实际验证层次详见验收记录；发行版派生环境不冒充官方 Docker 支持。

## 安装

VPS 访问 GitHub 困难（如中国大陆）时，使用 [GitHub 加速镜像或本地上传的安装 / 升级命令](LOCAL_INSTALL.md)。

在 root SSH 终端执行一键安装（安装器使用 GitHub Releases 预编译镜像）：

```bash
curl -fsSL https://github.com/xiaofujie369/RealmPanel/releases/latest/download/install.sh | bash
```

全随机无人值守安装：

```bash
curl -fsSL https://github.com/xiaofujie369/RealmPanel/releases/latest/download/install.sh | RMP_PUBLIC_IP=服务器公网IP bash -s -- --yes
```

安装于 `/opt/realm-panel`，登录信息保存在 `/root/realm-panel-install-info.txt`（权限 600）。密码进入数据库前使用 Argon2id 哈希。安装信息文件是按设计要求保存的初始凭据，请妥善保管；后续修改密码不会更新该旧文件。

安装会先确保至少约 1 GB Swap；已有足够 Swap 时保留原配置，否则建立 `/var/lib/realm-panel/swapfile` 并写入开机配置。Swap 是磁盘交换空间，不是物理内存，也不是 Cloudflare WARP。禁止 Swap 的 OpenVZ/LXC 环境会明确报错，可在确认内存足够后显式设置 `RMP_SKIP_SWAP=1`。Btrfs 需安装 `btrfs-progs`。

安装器自动检查并安装 Docker/Compose，现有 Docker 环境会复用；所有镜像从 GitHub Release 加载，**不在 VPS 上执行 docker build、npm 或 pip**。下载包含 SHA256 校验和重试。Alpine 极简镜像若没有 bash/curl，先执行 `apk add bash curl`。

固定版本安装：

```bash
curl -fsSL https://github.com/xiaofujie369/RealmPanel/releases/latest/download/install.sh | RMP_VERSION=0.10.1 bash -s -- --yes
```

重复运行安装器不会重置已有数据；已安装环境使用 `rmpctl update`。

## 日常使用

1. 打开安装器显示的完整管理地址，输入管理员账号密码。
2. 在「转发规则」创建规则，设置监听和目标地址、端口及协议。
3. 保存后自动应用；不需要重新执行 Compose 或编辑 TOML。
4. 从「导入 / 导出」下载配置，上传到其他服务器后先预览再确认应用。

新规则默认 TCP+UDP。保存即原子生效，不重启任何进程；只有被修改、暂停或删除的规则会断开现有连接。

## CLI

```bash
rmpctl status
rmpctl start
rmpctl stop
rmpctl restart
rmpctl logs
rmpctl info
rmpctl reset-password
rmpctl reset-path
rmpctl backup
rmpctl restore-full --file 备份UUID
rmpctl update
rmpctl update 0.10.1
rmpctl uninstall
```

`info` 不显示密码。`reset-path` 同时清空白名单并退出全部会话，便于误锁时恢复访问。Web 备份恢复只恢复规则、分组和网络参数，保留当前登录与管理地址；SSH `restore-full` 还原备份中的账号与 Web 设置。

更新先下载预编译镜像，再保存完整安装目录和数据、保留旧镜像；迁移或健康检查失败会恢复旧代码、数据和镜像。升级备份保留在 `/opt/realm-panel-update.*`，包含秘密数据，仅由 root 访问。默认更新源为本仓库最新 GitHub Release。

卸载会提供「保留数据」「完全删除」「取消」三个选项。仅操作 `/opt/realm-panel` 和 `/usr/local/bin/rmpctl`，不会卸载系统 Docker、Swap 或其他业务容器。

## JSON 格式

扩展名 `.rmp.json`，不导出账号、哈希、Session、管理路径或秘密信息：

```json
{
  "format": "realm-panel",
  "schema_version": 2,
  "engine": "nftables",
  "groups": [{"name": "美国"}],
  "rules": [{
    "name": "美国01", "group": "美国", "enabled": true,
    "listen_host": "0.0.0.0", "listen_port": 2088,
    "remote_host": "example.com", "remote_port": 443,
    "protocol": "tcp_udp", "through": "",
    "remark": "美国落地"
  }]
}
```

仍可导入 Realm 版本导出的 `schema_version: 1` 文件：其 `settings`、`tcp_timeout`、`udp_timeout`、`tcp_keepalive` 会被忽略；`interface` 非空的规则会报错，清空后即可导入。不同监听地址在通配地址覆盖时也被识别为冲突；默认不允许同端口重复，即使协议不同。

## CSV 与批量文本

CSV 为 UTF-8 BOM，包含完整规则参数，支持中文、逗号和换行：

```csv
name,group,enabled,listen_host,listen_port,remote_host,remote_port,protocol,through,remark
美国01,美国,true,0.0.0.0,2088,example.com,443,tcp_udp,,美国落地
```

以公式字符开头的名称、分组及备注会增加前导单引号，防止电子表格公式注入；重新导入时自动还原。旧版 CSV 中的超时列会被忽略。

批量文本：

```text
2088|example.com|443|tcp_udp|美国01
2089|1.1.1.1|443|tcp|备用线路
```

最大单次导入 2 MB / 2000 条规则。预览 15 分钟有效，绑定当前会话和配置版本；预览后配置变化必须重新解析。存在无效条目时禁止整个导入。多个操作不会并发改写内核规则。

## 数据及备份

```text
/opt/realm-panel/
├── backend/ frontend/ docker/ scripts/ tests/
├── docker-compose.yml
└── data/
    ├── database/realm-panel.db
    ├── forward/rules.json
    ├── traffic/traffic.sqlite3
    ├── run/supervisor.sock
    ├── backups/*.rmpbak
    ├── logs/
    └── secrets/backup.key
```

**请不要直接编辑 `forward/rules.json` 或 `table inet realmpanel`。请通过 Web 后台修改规则。** 数据库是唯一事实来源，启动、下次应用及每 30 秒的后台检查都会从中重建内核规则。

`.rmpbak` 是 Fernet 认证加密的逻辑数据库快照（账号、Web 设置、分组、规则），密钥保存在 `data/secrets/backup.key`。下载备份时不会下载密钥，异地恢复前需单独安全保管密钥；密钥遗失则无法解密备份。备份不迁移登录会话。自动保留最近 30 份，可设为 10/20/30/50/100。

运行日志由 Docker 轮转（每容器 3 × 5 MB），Web 显示 Supervisor 最近 1000 行。审计日志在 SQLite 中保留最近 10000 条。系统状态每 10 秒检查，界面概览每 15 秒刷新；运行日志自动刷新默认关闭。

## 安全与架构

- 两容器均为 host 网络，添加端口无需修改 Compose。
- Web 没有 Docker Socket；Supervisor 只接受固定的状态、日志、流量和应用操作，不执行 Shell。
- 根文件系统只读、移除 Linux capabilities、禁止提升权限。核心仅保留管理自身 nftables 表、`RMP-FORWARD` 链与连接跟踪所需的 NET_ADMIN / NET_RAW（NET_RAW 仅 iptables-legacy 主机需要）。Web 容器没有任何网络管理权限。
- 所有 API 都位于随机管理路径下，未知路径统一 404，无公开 OpenAPI / Swagger。
- Session 12 小时，HttpOnly + SameSite=Strict；HTTP 不强制 Secure，直接 HTTPS 请求时自动 Secure。
- 修改操作验证 CSRF Token、同源请求标记和 Origin。五次失败锁定来源 IP 15 分钟。
- 账号密码修改后撤销全部 Session。默认不信任代理转发头，IP 白名单按真实直连地址计算。
- HTTP 不加密传输；需要加密管理链路时可通过 SSH 隧道访问。随机路径不是加密措施。
- 仅一个管理账号，单 Uvicorn worker。不要擅自增加多 worker，否则进程内应用锁不能保证一致性。

## 开发与测试

```bash
python -m pip install -r backend/requirements.txt
python -m pytest tests/test_backend.py tests/test_auth.py tests/test_traffic.py tests/test_forwarder.py -q
cd frontend && npm ci && npm run build
```

`tests/live_acceptance.py` 在全新空规则安装上执行真实 TCP/UDP 与 API 验收，会创建并清理测试规则。不要对有业务规则的面板运行该脚本。完整 Linux Docker 构建和安装以验收记录为准。

开发环境手动构建（不用于低配 VPS 安装）：

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml build
```

GitHub Actions 会执行后端测试、TypeScript 检查、前端构建、npm 审计、多个发行版的安装工具检查，并在 amd64 / arm64 两种原生 runner 上构建镜像、执行真实 TCP/UDP 验收。推送与 VERSION 一致的 `v*` 标签后，流水线创建包含两个架构镜像、源码、安装器及 SHA256SUMS 的 Release。只有全部构建和实测成功后才公开 Release。

## FAQ

**访问根路径返回 404？** 必须使用安装器显示的完整随机路径，包含末尾 `/`。

**忘记地址或密码？** 使用 `rmpctl info` 或 `rmpctl reset-password`。白名单误锁用 `rmpctl reset-path`。

**端口已占用？** 本机已有进程监听该端口时，nftables 规则会被拒绝（否则会劫持该服务的外部流量）。规则生效后才有进程占用该端口时，后台检查会暂停这条转发并在概览显示原因。

**规则已生效但外部无法访问？** 检查云厂商安全组是否放行监听端口；概览的「系统状态」会显示 `ip_forward`、`forward_filter`（FORWARD 放行链位置）和问题列表。使用 firewalld 等自带 nftables 转发拦截的防火墙时，需要在其中放行转发。

**为什么切换到 nftables？** nftables 在内核中完成地址转换，数据包不复制到用户态，吞吐更高、CPU 更省、UDP 不丢包。代价是失去 Realm 的协议互转、出口网卡绑定及连接超时等用户态特性。

**UDP 测试成功是否证明远端可用？** 发送完成不能证明远端应用正确响应。真实 UDP 转发验收使用回显服务验证请求及返回数据。

**如何切换黑白主题？** 点击顶栏的太阳/月亮按钮，或在「设置 → 外观」选择亮色、暗色、跟随系统。偏好保存在当前浏览器。

**为什么还不是 1.0？** 双 VPS、Excel 手工往返及完整虚拟机操作系统矩阵仍需要对应环境。未经执行的项目不会写成通过。

## 许可

RealmPanel 使用 MIT License。0.10.0 起不再包含 Realm 二进制；转发由 Linux 内核 nftables 完成，核心镜像使用 Debian 的 nftables、iptables 与 conntrack-tools 软件包。
