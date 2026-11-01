# 国内网络：加速镜像或本地上传安装 / 升级

适用于 VPS 无法顺畅访问 GitHub 的情况（常见于中国大陆）。镜像已由 GitHub Actions 编译，VPS 不编译、不拉取 Docker Hub 镜像。两种方式都**同时支持全新安装和升级**（包括从 0.9.x Realm 版升级）：

| 方式 | 适合 | 说明 |
|---|---|---|
| A. GitHub 加速镜像 | VPS 能访问某个 GitHub 代理 | 最省事，一条命令 |
| B. 本地下载再上传 | 任何代理都不可用 | 最可靠，文件在本地校验后上传 |

> 0.9.x 自带的 `rmpctl update` 只能直连 GitHub，因此国内的旧版本请用下面的命令升级。升级到 0.10.1 后，`rmpctl update` 同样可以配合 `RMP_MIRROR` 使用。

## A. GitHub 加速镜像

`RMP_MIRROR` 是加在 `https://github.com/...` 前面的代理前缀，下面以 `https://ghfast.top/` 为例，可换成任何同类 GitHub 文件代理。安装器仍按 `SHA256SUMS` 校验，但校验清单也经由该代理下载，请只使用你信任的代理；完全不想经过第三方时用方式 B。

全新安装：

```bash
M=https://ghfast.top/
curl -fsSL ${M}https://github.com/xiaofujie369/RealmPanel/releases/latest/download/install.sh -o /root/rmp-install.sh && RMP_MIRROR=$M bash /root/rmp-install.sh
```

升级已安装的 RealmPanel（0.9.x 或 0.10.x）：

```bash
M=https://ghfast.top/
curl -fsSL ${M}https://github.com/xiaofujie369/RealmPanel/releases/latest/download/install.sh -o /root/rmp-install.sh && RMP_MIRROR=$M bash /root/rmp-install.sh --update
```

已是 0.10.1 及以上时也可以：`RMP_MIRROR=https://ghfast.top/ rmpctl update`。

安装或升级时 Docker 官方源（download.docker.com）若连不上：Ubuntu 自动改用系统软件源的 `docker.io` 与 `docker-compose-v2`；Debian 13 本来就使用系统软件源。

## B. 本地下载，再通过 SSH 上传

本地包安装器保留 SHA256 校验、约 1 GiB Swap、Docker/Compose 环境检查及初始化。它只省去应用安装包的联网下载；系统依赖和缺失的 Docker 仍需通过软件包源安装。

**VPS 上已有 RealmPanel 时，同一条 `install-local.sh … --yes` 命令自动执行升级**：先完整备份，再加载新镜像，规则、账号、管理地址和流量累计全部保留，失败自动回滚。没有安装时执行全新安装。

## Windows PowerShell：下载并上传

先修改 VPS IP、SSH 端口和密钥文件路径。私钥保存在本地，不上传到 VPS。

```powershell
$Vps = "64.186.231.196"       # 改为要安装的新 VPS 公网 IP
$SshPort = 22
$Key = "$env:USERPROFILE\.ssh\id_ed25519"  # 改为你的 SSH 私钥文件
$Version = "0.10.1"

# 根据 VPS 架构选择镜像，而不是根据本地 Windows 架构选择。
$Machine = (ssh -i $Key -p $SshPort "root@$Vps" "uname -m").Trim()
if ($LASTEXITCODE -ne 0) { throw "SSH 连接失败，请检查 IP、端口和密钥路径" }
$Arch = switch ($Machine) {
    "x86_64" { "amd64" }
    "aarch64" { "arm64" }
    "arm64" { "arm64" }
    default { throw "不支持的 VPS 架构：$Machine" }
}

$Bundle = Join-Path $env:USERPROFILE "Downloads\RealmPanel-v$Version-$Arch"
New-Item -ItemType Directory -Force -Path $Bundle | Out-Null
$Base = "https://github.com/xiaofujie369/RealmPanel/releases/download/v$Version"
$Files = @("install.sh", "install-local.sh", "realm-panel.tar.gz", "realm-panel-images-$Arch.tar.gz", "release.json", "SHA256SUMS")

foreach ($Name in $Files) {
    curl.exe -fL --retry 3 --connect-timeout 20 "$Base/$Name" -o (Join-Path $Bundle $Name)
    if ($LASTEXITCODE -ne 0) { throw "下载失败：$Name" }
}

# 校验实际下载的文件；清单还包含另一个架构镜像，不需要下载它。
$Lines = Get-Content (Join-Path $Bundle "SHA256SUMS")
foreach ($Name in ($Files | Where-Object { $_ -ne "SHA256SUMS" })) {
    $Matched = @($Lines | Where-Object { ($_ -split '\s+')[-1] -eq $Name })
    if ($Matched.Count -ne 1) { throw "校验清单不完整：$Name" }
    $Expected = ($Matched[0] -split '\s+')[0]
    $Actual = (Get-FileHash (Join-Path $Bundle $Name) -Algorithm SHA256).Hash
    if ($Actual -ne $Expected) { throw "SHA256 校验失败：$Name" }
    Write-Host "校验通过：$Name"
}

scp -r -i $Key -P $SshPort $Bundle "root@${Vps}:/root/"
if ($LASTEXITCODE -ne 0) { throw "上传失败，请重试 scp" }

$RemoteBundle = "/root/RealmPanel-v$Version-$Arch"
# --check 只检查包与架构；不会安装或修改现有服务。
ssh -i $Key -p $SshPort "root@$Vps" "bash '$RemoteBundle/install-local.sh' '$RemoteBundle' --check"
if ($LASTEXITCODE -ne 0) { throw "VPS 安装包检查失败" }

# 未安装：全随机初始化（端口、路径、管理员账号和密码均由安装器生成）。
# 已安装：自动升级并保留全部数据。
ssh -i $Key -p $SshPort "root@$Vps" "RMP_PUBLIC_IP='$Vps' bash '$RemoteBundle/install-local.sh' '$RemoteBundle' --yes"
if ($LASTEXITCODE -ne 0) { throw "部署失败，请检查安装日志" }
```

全新 VPS 若没有 Python 3，先跳过上面的 `--check` 命令，直接运行最后的安装命令：安装器会先安装 Python 等系统依赖，再检查包。Alpine 极简环境需先通过 `apk add bash coreutils` 提供 Bash 与 SHA256 工具。

如果本地终端下载也失败，可以在能访问 GitHub 的浏览器打开 [v0.10.1 Release](https://github.com/xiaofujie369/RealmPanel/releases/tag/v0.10.1)，下载同样的六个文件到 `$Bundle`，再从校验步骤继续。无需下载另一个架构镜像。

## 查看安装结果

```powershell
ssh -i $Key -p $SshPort "root@$Vps" "rmpctl status"
ssh -i $Key -p $SshPort "root@$Vps" "cat /root/realm-panel-install-info.txt"
```

第二条命令显示完整管理地址、管理员账号和初始密码，请妥善保管。若无法打开面板，在 VPS 提供商安全组和系统防火墙中放行安装器显示的管理 TCP 端口；转发监听端口按所用 TCP/UDP 协议放行。

### Linux / macOS 本地电脑

```bash
VPS=1.2.3.4; V=0.10.1; ARCH=amd64   # ARM VPS 用 arm64
mkdir -p RealmPanel-v$V-$ARCH && cd RealmPanel-v$V-$ARCH
for f in install.sh install-local.sh realm-panel.tar.gz realm-panel-images-$ARCH.tar.gz release.json SHA256SUMS; do
  curl -fL --retry 3 -O "https://github.com/xiaofujie369/RealmPanel/releases/download/v$V/$f" || exit 1
done
grep -v "images-$([ $ARCH = amd64 ] && echo arm64 || echo amd64)" SHA256SUMS | sha256sum -c - && cd .. \
  && scp -r RealmPanel-v$V-$ARCH root@$VPS:/root/ \
  && ssh root@$VPS "bash /root/RealmPanel-v$V-$ARCH/install-local.sh /root/RealmPanel-v$V-$ARCH --yes"
```

已上传到 VPS 时，在 VPS 上直接执行（未安装则安装，已安装则升级）：

```bash
bash /root/RealmPanel-v0.10.1-amd64/install-local.sh /root/RealmPanel-v0.10.1-amd64 --yes
```

能直连 GitHub 的 VPS 用 `rmpctl update` 在线升级即可。

## Debian 13：Docker 官方源连接被重置

若旧安装包在下载 `download.docker.com/linux/debian/gpg` 时出现 `curl: (35) Recv failure: Connection reset by peer`，可以通过 Debian 13 自身的软件源安装引擎和 Compose v2：

```bash
apt-get update
apt-get install -y docker.io docker-cli docker-compose
systemctl enable --now docker
docker --version
docker compose version
```

然后重新执行原来的 `install-local.sh` 命令即可，无需重新下载已上传的 RealmPanel 镜像。新版安装器在 Debian 13 上自动使用这条安装路径。此命令专用于 Debian 13；Debian 12 的 `docker-compose` 软件包是 v1，不能直接沿用。
