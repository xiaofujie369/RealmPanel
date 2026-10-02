# 本地下载，再通过 SSH 上传部署

适用于 VPS 无法顺畅访问 GitHub，但本地电脑可以下载的情况。当前正式版本为 **v0.9.5**；镜像已由 GitHub Actions 编译，VPS 不编译、不拉取 Docker Hub 镜像。

本地包安装器保留 SHA256 校验、约 1 GiB Swap、Docker/Compose 环境检查及初始化。它只省去应用安装包的联网下载；系统依赖和缺失的 Docker 仍需通过软件包源安装。这不是完全断网的系统安装方案。已有 RealmPanel 数据时安装器不会重置数据。

## 在线一键安装

root 终端执行：

```bash
curl -fsSL https://github.com/xiaofujie369/RealmPanel/releases/latest/download/install.sh | bash
```

## Windows PowerShell：下载并上传

先修改 VPS IP、SSH 端口和密钥文件路径。私钥保存在本地，不上传到 VPS。

```powershell
$Vps = "64.186.231.196"       # 改为要安装的新 VPS 公网 IP
$SshPort = 22
$Key = "$env:USERPROFILE\.ssh\id_ed25519"  # 改为你的 SSH 私钥文件
$Version = "0.9.5"

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

# 全随机初始化：端口、路径、管理员账号和密码均由安装器生成。
ssh -i $Key -p $SshPort "root@$Vps" "RMP_PUBLIC_IP='$Vps' bash '$RemoteBundle/install-local.sh' '$RemoteBundle' --yes"
if ($LASTEXITCODE -ne 0) { throw "部署失败，请检查安装日志" }
```

全新 VPS 若没有 Python 3，先跳过上面的 `--check` 命令，直接运行最后的安装命令：安装器会先安装 Python 等系统依赖，再检查包。Alpine 极简环境需先通过 `apk add bash coreutils` 提供 Bash 与 SHA256 工具。

如果本地终端下载也失败，可以在能访问 GitHub 的浏览器打开 [v0.9.5 Release](https://github.com/xiaofujie369/RealmPanel/releases/tag/v0.9.5)，下载同样的六个文件到 `$Bundle`，再从校验步骤继续。无需下载另一个架构镜像。

## 查看安装结果

```powershell
ssh -i $Key -p $SshPort "root@$Vps" "rmpctl status"
ssh -i $Key -p $SshPort "root@$Vps" "cat /root/realm-panel-install-info.txt"
```

第二条命令显示完整管理地址、管理员账号和初始密码，请妥善保管。若无法打开面板，在 VPS 提供商安全组和系统防火墙中放行安装器显示的管理 TCP 端口；转发监听端口按所用 TCP/UDP 协议放行。

已有部署可在线升级：`rmpctl update`。本地包安装命令用于全新部署，不会强制覆盖已有数据库。

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
