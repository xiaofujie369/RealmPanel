# RealmPanel

基于 Realm v2.9.3 的 Docker 化 TCP / UDP 转发管理面板。

目标是做一套适合个人及多 VPS 批量部署的轻量级生产环境管理工具。

核心原则：

- Realm 专门负责 TCP / UDP 转发
- Web Panel 专门负责配置管理
- Docker 部署
- 不依赖域名
- 不依赖 HTTPS 证书
- 支持随机 Web 管理路径
- 支持随机 Web 管理端口
- 支持随机管理员账号密码
- 安装过程必须全中文提示
- 支持 TCP
- 支持 UDP
- 支持 TCP + UDP
- Web 页面完成所有日常管理
- 支持 Web 导入 / 导出
- 支持多 VPS 配置迁移
- 所有配置修改必须支持安全应用和失败回滚
- UI 风格接近 Apple / macOS，而不是传统 Bootstrap 管理后台

---

# 1. 项目名称

项目名称：

```text
RealmPanel
```

CLI：

```text
rmpctl
```

默认安装目录：

```text
/opt/realm-panel
```

Realm 默认版本：

```text
v2.9.3
```

Realm 下载地址：

```text
https://github.com/zhboner/realm/releases/download/v2.9.3/realm-x86_64-unknown-linux-gnu.tar.gz
```

第一版暂时主要支持：

```text
Linux x86_64
```

安装脚本必须检测系统架构。

---

# 2. 技术架构

建议：

## Backend

```text
Python 3.12
FastAPI
SQLAlchemy
SQLite
Pydantic
Argon2
```

## Frontend

```text
Vue 3
TypeScript
Vite
Pinia
```

UI：

```text
TailwindCSS
自定义 Apple/macOS 风格组件
```

不要直接套：

```text
Element Plus 默认主题
Ant Design 默认主题
Bootstrap 默认主题
```

允许使用其底层组件，但是 UI 必须重新设计。

---

# 3. Docker 架构

采用两个容器：

```text
realm-panel-web
realm-panel-core
```

架构：

```text
Browser
   │
   ▼
RealmPanel Web
   │
   ├── SQLite
   │
   ├── Config Manager
   │
   └── Realm Manager
            │
            ▼
      Realm v2.9.3
            │
            ▼
       TCP / UDP
```

Realm Core 必须：

```yaml
network_mode: host
```

原因：

增加新的转发端口时：

```text
2088
2089
2090
30000
50000
```

不需要修改 Docker Compose `ports:`。

---

# 4. Docker Compose

建议：

```yaml
services:

  realm-panel-web:
    build:
      context: .
      dockerfile: docker/Dockerfile.web
    container_name: realm-panel-web
    restart: unless-stopped
    network_mode: host
    volumes:
      - ./data:/app/data
      - ./config:/app/config
      - /var/run/docker.sock:/var/run/docker.sock
    environment:
      - TZ=Asia/Shanghai

  realm-panel-core:
    build:
      context: .
      dockerfile: docker/Dockerfile.realm
    container_name: realm-panel-core
    restart: unless-stopped
    network_mode: host
    volumes:
      - ./data/realm:/etc/realm
      - ./data/logs:/var/log/realm
```

实际实现时应尽量减少 Web 容器直接拥有 Docker Socket 的权限。

优先考虑：

```text
独立 Realm Supervisor / 本地 Unix Socket
```

如果第一版必须使用 Docker Socket，则：

- 所有允许执行的操作必须写死
- 禁止 Web API 任意执行 shell
- 禁止接受用户传入任意 Docker 参数

---

# 5. 安装方式

必须实现：

```bash
curl -fsSL https://example.com/install.sh | bash
```

安装脚本风格参考宝塔国际版。

但是必须：

```text
全程中文说明
关键步骤中文注释
错误中文解释
安装结果中文显示
```

---

# 6. 安装器启动界面

示例：

```text
========================================================
                 RealmPanel 安装程序
========================================================

项目名称：RealmPanel
Realm 版本：v2.9.3
安装目录：/opt/realm-panel

RealmPanel 是一个基于 Realm 的 TCP / UDP
端口转发 Web 管理面板。

支持：

✓ TCP 转发
✓ UDP 转发
✓ TCP + UDP
✓ Docker 部署
✓ Web 管理
✓ 配置导入导出
✓ 自动备份
✓ 配置回滚

========================================================
```

---

# 7. 安装环境检查

显示：

```text
[1/9] 正在检查系统环境……

系统：
Debian 13

架构：
x86_64

内核：
6.12.x

公网 IP：
xxx.xxx.xxx.xxx

Docker：
未安装

Docker Compose：
未安装

内存：
1024 MB

磁盘：
18 GB

检查完成。
```

如果 Docker 没安装：

```text
检测到当前服务器尚未安装 Docker。

RealmPanel 将自动安装 Docker 和 Docker Compose。

是否继续？
[Y/n]:
```

默认直接回车：

```text
Yes
```

---

# 8. Web 管理端口

安装过程：

```text
请输入 Web 管理端口

直接按回车：
自动生成随机端口

自定义：
请输入 1024 - 65535 之间未被占用的端口

Web 管理端口 []:
```

用户回车。

系统随机产生：

```text
34728
```

然后必须进行端口占用检测。

例如：

```text
随机端口：34728

正在检查端口……

✓ 34728 未被占用
```

如果用户指定：

```text
443
```

而 443 已被 Caddy 使用：

```text
错误：

端口 443 已被占用。

占用进程：
caddy

PID：
1832

请重新输入其他端口。
```

---

# 9. 随机 Web 路径

提示：

```text
请输入 Web 管理路径

例如：

/admin123/

直接按回车：
系统自动生成随机管理路径。

Web 管理路径 []:
```

回车生成：

```text
/K8mP2xQa7N/
```

随机长度：

```text
10 - 16
```

字符：

```text
A-Z
a-z
0-9
```

必须：

```text
以 / 开头
以 / 结束
```

例如：

```text
/K8mP2xQa7N/
```

访问：

```text
http://IP:34728/
```

返回：

```text
404 Not Found
```

访问：

```text
http://IP:34728/login
```

也返回：

```text
404
```

只有：

```text
http://IP:34728/K8mP2xQa7N/
```

才能进入。

所有 API 同样必须挂在该随机路径下。

例如：

```text
/K8mP2xQa7N/api/
```

不要暴露统一：

```text
/api/
```

---

# 10. 管理员用户名

安装提示：

```text
设置 RealmPanel 管理员账号。

直接按回车：
系统自动生成随机账号。

管理员账号 []:
```

如果用户回车：

生成：

```text
rmp_Kd9xQ28
```

规则：

```text
rmp_ + 7~10 位随机字符
```

如果指定：

```text
admin
```

则使用：

```text
admin
```

---

# 11. 管理员密码

提示：

```text
设置 RealmPanel 管理员密码。

直接按回车：
系统自动生成高强度随机密码。

管理员密码 []:
```

用户回车：

生成：

```text
N7!qPx92Lm#K4sAe
```

随机密码：

```text
至少 16 位
```

包括：

```text
大小写字母
数字
特殊字符
```

用户自定义密码时：

最低要求：

```text
8 位
```

推荐：

```text
12 位以上
```

密码必须使用：

```text
Argon2id
```

保存。

禁止：

```text
明文密码
SHA1
MD5
单纯 SHA256
```

---

# 12. 安装过程

必须显示明确步骤：

```text
[1/9] 检查系统环境
[2/9] 安装 Docker
[3/9] 下载 Realm v2.9.3
[4/9] 创建 RealmPanel 目录
[5/9] 创建 Web 配置
[6/9] 初始化数据库
[7/9] 构建 Docker 镜像
[8/9] 启动服务
[9/9] 执行健康检查
```

每项：

```text
✓ 完成
```

或者：

```text
✗ 失败
```

失败必须说明原因。

不要只输出：

```text
exit code 1
```

---

# 13. 安装成功界面

最终显示：

```text
========================================================
              RealmPanel 安装完成
========================================================

Web 管理地址：

http://1.2.3.4:34728/K8mP2xQa7N/

管理员账号：

rmp_Kd9xQ28

管理员密码：

N7!qPx92Lm#K4sAe

Realm 版本：

v2.9.3

RealmPanel 安装目录：

/opt/realm-panel

配置目录：

/opt/realm-panel/data

========================================================

请妥善保存以上登录信息。

常用命令：

查看状态：
rmpctl status

重启：
rmpctl restart

查看日志：
rmpctl logs

重置管理员：
rmpctl reset-password

备份：
rmpctl backup

升级：
rmpctl update

卸载：
rmpctl uninstall

========================================================
```

同时：

```text
/root/realm-panel-install-info.txt
```

保存安装信息。

文件权限：

```text
600
```

---

# 14. Web UI

风格参考：

```text
macOS
iOS Settings
Apple Developer
```

设计原则：

- 大量留白
- 圆角
- 轻阴影
- 简洁
- 高信息密度
- 不花哨
- 不做廉价毛玻璃
- 不做传统服务器面板样式

支持：

```text
亮色
暗色
跟随系统
```

---

# 15. 左侧导航

```text
概览

转发规则

批量操作

导入 / 导出

备份

运行日志

操作日志

系统状态

设置
```

---

# 16. Dashboard

首页展示：

```text
Realm Core

● 正常运行

Realm：
v2.9.3

运行时间：
3 天 12 小时

转发规则：
18

启用：
16

暂停：
2

监听端口：
18

CPU：
0.3%

内存：
19 MB
```

可以额外展示：

```text
最近操作
最近错误
系统负载
服务器 IP
Docker 状态
```

---

# 17. 转发规则数据结构

数据库字段：

```text
id
uuid
name
group_id
enabled

listen_host
listen_port

remote_host
remote_port

protocol

through
interface

tcp_timeout
udp_timeout
tcp_keepalive

remark

created_at
updated_at
```

protocol 只允许：

```text
tcp
udp
tcp_udp
```

---

# 18. 创建转发

页面：

```text
新建转发
```

基础配置：

```text
名称
美国线路01

监听地址
0.0.0.0

监听端口
2088

目标地址
38.65.93.71

目标端口
443
```

协议：

```text
● TCP + UDP
○ TCP
○ UDP
```

默认：

```text
TCP + UDP
```

状态：

```text
✓ 创建后立即启用
```

备注：

```text
美国线路测试
```

---

# 19. 高级参数

折叠：

```text
高级设置
```

包含：

```text
Through

Interface

TCP Timeout

UDP Timeout

TCP Keepalive
```

高级参数为空时：

使用 RealmPanel 全局默认值。

---

# 20. UDP

必须保留 Realm 的 UDP 转发功能。

UI 必须支持：

```text
TCP

UDP

TCP + UDP
```

不能删除 UDP。

如果 Realm 配置要求：

```text
no_tcp
use_udp
```

则由 Config Generator 自动转换。

用户不需要理解：

```text
no_tcp = false
use_udp = true
```

用户只操作：

```text
TCP
UDP
TCP + UDP
```

---

# 21. 转发列表

表格：

```text
状态
名称
分组
本地监听
远程目标
协议
备注
操作
```

示例：

```text
● 美国01
默认
0.0.0.0:2088
38.65.93.71:443
TCP+UDP
美国落地
```

操作：

```text
编辑

复制

暂停

启动

删除
```

---

# 22. 暂停规则

暂停：

```text
enabled = false
```

暂停后的规则：

不要写入 Realm Active Config。

数据库必须保留。

重新启动：

```text
enabled = true
```

即可恢复。

---

# 23. 删除规则

删除前弹窗：

```text
确认删除？

美国01

0.0.0.0:2088
→
38.65.93.71:443
```

按钮：

```text
取消

删除
```

执行后：

立即 Safe Apply。

---

# 24. 复制规则

点击：

```text
复制
```

产生：

```text
美国01-copy
```

目标保持：

```text
38.65.93.71:443
```

自动建议下一个可用监听端口。

例如：

```text
2088
```

已经使用。

自动：

```text
2089
```

---

# 25. 分组

支持：

```text
默认

美国

日本

香港

测试

备用
```

用户可以：

```text
创建分组
改名
删除分组
```

删除分组不能删除规则。

规则自动移动：

```text
默认
```

---

# 26. 批量操作

支持勾选多条规则：

```text
批量启动
批量暂停
批量删除
批量移动分组
批量导出
```

---

# 27. Safe Apply

这是整个项目最重要的功能之一。

任何：

```text
增加
编辑
删除
暂停
启动
批量操作
导入
恢复备份
```

都必须执行：

```text
Safe Apply
```

流程：

```text
修改数据库

↓

生成临时 Realm 配置

↓

配置合法性检查

↓

生成备份

↓

替换正式配置

↓

重新加载 Realm

↓

检查 Realm 是否正常运行

↓

检查监听端口

↓

成功
```

---

# 28. Realm 热应用

不能假设 Realm 有真正意义上的无中断动态配置 API。

RealmPanel 第一版采用：

```text
Fast Restart / Safe Apply
```

前端名称可以显示：

```text
即时应用
```

执行：

```text
写配置
→
安全重启 Realm Core
→
健康检查
```

正常情况下用户无需手动重启。

新增：

```text
立即应用
```

编辑：

```text
立即应用
```

暂停：

```text
立即应用
```

删除：

```text
立即应用
```

---

# 29. 自动回滚

如果新配置启动失败：

必须：

```text
停止新配置

恢复旧配置

启动旧 Realm

检查恢复状态
```

然后显示：

```text
配置应用失败。

系统已自动恢复到修改前状态。

错误原因：

端口 2088 已被其他程序占用。
```

数据库也应该恢复事务。

不能出现：

```text
数据库已经修改
Realm 配置却没修改
```

必须保证一致性。

---

# 30. Web 导出

这是正式必做功能。

页面：

```text
导入 / 导出
```

导出：

```text
配置导出
```

支持：

```text
JSON

CSV
```

---

# 31. JSON 导出格式

格式名称：

```text
RealmPanel Portable Format
```

扩展名：

```text
.rmp.json
```

Example：

```json
{
  "format": "realm-panel",
  "schema_version": 1,
  "realm_version": "2.9.3",
  "exported_at": "2026-10-01T23:00:00+08:00",
  "settings": {
    "tcp_timeout": 5,
    "udp_timeout": 30,
    "tcp_keepalive": 15
  },
  "groups": [
    {
      "name": "美国"
    },
    {
      "name": "日本"
    }
  ],
  "rules": [
    {
      "name": "美国01",
      "group": "美国",
      "enabled": true,
      "listen_host": "0.0.0.0",
      "listen_port": 2088,
      "remote_host": "38.65.93.71",
      "remote_port": 443,
      "protocol": "tcp_udp",
      "through": "",
      "interface": "",
      "remark": "美国落地"
    }
  ]
}
```

---

# 32. JSON Schema 规则

必须校验：

```text
format == realm-panel

schema_version == 支持的版本

rules 必须是 array
```

每个规则：

```text
listen_port:
1 - 65535

remote_port:
1 - 65535

protocol:
tcp
udp
tcp_udp

enabled:
boolean
```

remote_host 支持：

```text
IPv4
IPv6
域名
```

---

# 33. CSV 导出格式

CSV 必须适合用户手动修改。

格式：

```csv
name,group,enabled,listen_host,listen_port,remote_host,remote_port,protocol,through,interface,remark
美国01,美国,true,0.0.0.0,2088,38.65.93.71,443,tcp_udp,,,美国落地
日本01,日本,true,0.0.0.0,2089,1.2.3.4,443,tcp,,,日本落地
```

protocol：

```text
tcp
udp
tcp_udp
```

enabled：

```text
true
false
```

CSV：

```text
UTF-8
```

建议：

```text
UTF-8 BOM
```

以便 Windows Excel 中文正常显示。

---

# 34. 导入

Web：

```text
导入配置
```

支持：

```text
.rmp.json

.json

.csv
```

上传以后：

绝对不能直接应用。

首先：

```text
解析
```

然后：

```text
校验
```

再：

```text
预览
```

---

# 35. 导入预览

例如：

```text
导入预览

文件：
realm-us.rmp.json

检测规则：
26

新增：
21

更新：
3

冲突：
1

无效：
1
```

无效规则必须明确指出：

```text
第 17 条

监听端口：
99999

错误：
监听端口必须为 1 - 65535
```

---

# 36. 导入冲突

默认检测：

```text
listen_host + listen_port
```

如果：

```text
0.0.0.0:2088
```

已经存在。

显示：

```text
发现端口冲突。
```

用户选择：

```text
跳过

覆盖现有规则

自动寻找新端口
```

不要默默覆盖。

---

# 37. 导入执行

点击：

```text
确认导入并应用
```

流程：

```text
创建导入前备份

↓

数据库事务

↓

写入新配置

↓

Safe Apply

↓

Realm Health Check

↓

成功
```

失败：

```text
整个导入回滚
```

不要只回滚失败的那一条。

---

# 38. 导出内容范围

普通：

```text
配置导出
```

只导出：

```text
转发规则

分组

Realm 网络参数
```

绝对不要包含：

```text
管理员密码 Hash

Session

Web Path

API Secret
```

---

# 39. Full Backup

单独提供：

```text
完整备份
```

完整备份主要供原服务器恢复。

格式：

```text
.rmpbak
```

包含：

```text
SQLite DB

配置

Web Settings

Realm Settings
```

如果包含敏感信息：

必须支持：

```text
加密备份
```

---

# 40. 自动备份

每次 Safe Apply 前：

自动创建备份。

目录：

```text
/opt/realm-panel/data/backups
```

保留：

```text
最近 30 份
```

设置页面允许：

```text
10
20
30
50
100
```

---

# 41. Web 备份管理

显示：

```text
备份时间

规则数量

产生原因

文件大小
```

产生原因：

```text
修改规则

导入

批量删除

手动备份

版本升级
```

操作：

```text
恢复

下载

删除
```

---

# 42. 恢复备份

恢复之前：

RealmPanel 必须先给当前状态创建：

```text
Pre-Restore Backup
```

然后才恢复旧版本。

如果旧配置无法启动：

再次恢复刚才的：

```text
Pre-Restore Backup
```

---

# 43. 批量添加

支持 Web 文本输入。

格式：

```text
LISTEN_PORT|REMOTE_HOST|REMOTE_PORT|PROTOCOL|NAME
```

例如：

```text
2088|38.65.93.71|443|tcp_udp|美国01
2089|38.65.93.72|443|tcp_udp|美国02
2090|1.2.3.4|443|tcp|日本01
```

点击：

```text
解析
```

先预览。

---

# 44. 端口检测

新增规则时检查：

```text
listen_host
listen_port
```

是否冲突。

检测：

```text
RealmPanel DB

Realm 当前监听

系统 ss

Docker

其他服务
```

显示：

```text
✓ 2088 可以使用
```

或者：

```text
✗ 2088 已被占用

进程：
xray

PID：
1834
```

---

# 45. 远端测试

提供：

```text
测试目标
```

TCP：

执行真实连接测试。

显示：

```text
目标：
38.65.93.71:443

结果：
连接成功

延迟：
37 ms
```

UDP：

不要显示：

```text
UDP连接成功
```

因为 UDP 无连接。

显示：

```text
UDP 数据发送测试完成。

注意：
该结果不能保证远端应用一定正确响应 UDP。
```

---

# 46. Realm 配置生成器

RealmPanel DB 是：

```text
Single Source of Truth
```

不要让用户直接修改 Realm TOML 后又反向同步数据库。

Realm TOML：

只能由 RealmPanel 自动生成。

目录：

```text
/opt/realm-panel/data/realm/
```

建议：

```text
global.toml
rules/
```

例如：

```text
rules/
000001.toml
000002.toml
000003.toml
```

---

# 47. 手动配置保护

如果用户 SSH 手工修改：

```text
realm/*.toml
```

下一次 Safe Apply：

RealmPanel 应覆盖生成。

README 必须明确说明：

```text
请不要直接编辑 RealmPanel 生成的 Realm 配置文件。

如需修改规则，请通过 Web 管理后台操作。
```

---

# 48. 操作日志

必须记录：

```text
登录

登录失败

退出

添加规则

编辑规则

删除规则

暂停规则

恢复规则

批量操作

导入

导出

备份

恢复

修改账号

修改密码

修改 Web 设置
```

字段：

```text
time

admin

source_ip

action

resource

result
```

不要记录管理员明文密码。

---

# 49. Realm 日志

页面：

```text
运行日志
```

支持：

```text
最近 100 行
最近 500 行
最近 1000 行
```

支持：

```text
自动刷新
```

默认：

```text
关闭
```

避免一直请求服务器。

---

# 50. 管理员设置

设置页面：

```text
修改管理员用户名

修改密码
```

修改密码要求：

```text
当前密码

新密码

确认新密码
```

---

# 51. SSH 重置管理员

必须实现：

```bash
rmpctl reset-password
```

显示：

```text
========================================================
          RealmPanel 管理员重置
========================================================

当前管理员：
admin

新的管理员账号

直接按回车：
保持当前账号

新账号 []:
```

密码：

```text
新的管理员密码

直接按回车：
自动生成随机密码

新密码 []:
```

完成：

```text
RealmPanel 管理员信息已更新。

账号：
admin

密码：
xxxxx
```

---

# 52. Web Path 修改

设置页面支持：

```text
重新生成管理路径
```

必须二次确认：

```text
修改后当前旧地址将立即失效。

是否继续？
```

生成新路径后：

当前 Session 可以继续一次跳转。

之后旧路径：

```text
404
```

---

# 53. 登录安全

默认：

```text
同一个 IP
5 次登录失败
```

进入：

```text
15 分钟限制
```

同时记录：

```text
IP
时间
失败次数
```

---

# 54. Session

Cookie：

```text
HttpOnly
SameSite=Strict
```

如果当前是 HTTP：

不能错误强制：

```text
Secure
```

否则浏览器无法登录。

如果以后开启 HTTPS：

自动：

```text
Secure=true
```

---

# 55. CSRF

所有：

```text
POST
PUT
PATCH
DELETE
```

必须进行 CSRF 防护。

或者使用安全的：

```text
same-origin token
```

机制。

---

# 56. Web 服务指纹

尽量移除：

```text
Server: uvicorn
Server: nginx
X-Powered-By
```

访问错误路径：

统一：

```text
404
```

不要返回：

```text
RealmPanel API
```

---

# 57. Web IP 白名单

设置：

```text
管理面板访问限制
```

默认：

```text
关闭
```

启用后：

```text
允许 IP/CIDR
```

例如：

```text
64.186.231.196

1.2.3.0/24
```

修改白名单前：

必须检测当前客户端 IP。

如果用户即将把自己排除：

弹出明显警告。

---

# 58. Health Check

每隔一定时间检测：

```text
Web

Realm Core

Realm 进程

Docker

配置文件

监听端口
```

Dashboard：

```text
● 正常
```

或者：

```text
● 异常
```

---

# 59. Realm 崩溃

如果 Realm 容器退出：

Docker：

```text
restart: unless-stopped
```

自动重启。

如果持续失败：

Web：

```text
Realm Core 启动失败

查看日志

恢复上一次配置
```

---

# 60. CLI

必须实现：

```text
rmpctl
```

命令：

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

rmpctl update

rmpctl uninstall
```

---

# 61. rmpctl info

输出：

```text
RealmPanel

状态：
Running

Web：
http://1.2.3.4:34728/K8mP2xQa7N/

管理员：
admin

Realm：
v2.9.3

规则：
18

安装目录：
/opt/realm-panel
```

不要显示密码。

---

# 62. 更新功能

Web：

```text
系统设置
→
版本
```

显示：

```text
RealmPanel：
v1.0.0

Realm Core：
v2.9.3
```

必须区分两个版本。

---

# 63. 更新流程

```text
创建完整备份

↓

下载新版本

↓

数据库迁移

↓

更新 Web

↓

更新 Realm Core（如果需要）

↓

启动

↓

Health Check
```

失败：

```text
自动恢复旧版本
```

---

# 64. 卸载

执行：

```bash
rmpctl uninstall
```

中文：

```text
警告：

此操作将卸载 RealmPanel。

请选择：

1. 仅卸载程序，保留数据

2. 完全删除 RealmPanel 和所有数据

0. 取消
```

必须避免：

```text
rm -rf /
```

一类危险路径。

删除前：

检查目录路径必须等于：

```text
/opt/realm-panel
```

或其明确子目录。

---

# 65. 数据目录

结构：

```text
/opt/realm-panel

├── docker-compose.yml

├── data

│   ├── database

│   │   └── realm-panel.db

│   ├── realm

│   │   ├── global.toml

│   │   └── rules

│   ├── backups

│   ├── logs

│   └── secrets

├── scripts

├── VERSION

└── README.md
```

---

# 66. 数据库表

至少：

```text
users

settings

forward_rules

groups

operation_logs

backups

sessions
```

---

# 67. forward_rules

推荐：

```sql
id

uuid

name

group_id

enabled

listen_host

listen_port

remote_host

remote_port

protocol

through_addr

interface_name

tcp_timeout

udp_timeout

tcp_keepalive

remark

created_at

updated_at
```

约束：

```text
listen_host + listen_port
```

默认不能重复。

---

# 68. REST API

所有 API：

```text
/{WEB_PATH}/api/v1/
```

例如：

```text
GET /rules

POST /rules

GET /rules/{id}

PUT /rules/{id}

DELETE /rules/{id}

POST /rules/{id}/enable

POST /rules/{id}/disable

POST /rules/{id}/clone
```

---

# 69. Import API

```text
POST /import/preview

POST /import/apply
```

必须分成：

```text
Preview
Apply
```

严禁上传文件之后立即执行导入。

---

# 70. Export API

```text
GET /export/json

GET /export/csv
```

支持参数：

```text
all

group

selected rules
```

---

# 71. Backup API

```text
GET /backups

POST /backups

POST /backups/{id}/restore

GET /backups/{id}/download

DELETE /backups/{id}
```

---

# 72. 安全原则

绝对禁止：

Web 请求：

```text
command=任意 shell
```

不要实现：

```text
/api/run-command
```

不要：

```python
subprocess.run(user_input, shell=True)
```

必须：

```text
所有系统操作调用固定函数
```

例如：

```text
restart_realm()

get_realm_status()

test_port()

generate_config()
```

---

# 73. 前端体验

任何操作都必须有状态：

```text
正在保存……

正在应用……

应用成功

应用失败
```

不能点击以后没有反馈。

---

# 74. Toast

成功：

```text
转发规则已创建并生效
```

暂停：

```text
转发规则已暂停
```

删除：

```text
转发规则已删除
```

失败：

```text
配置应用失败，已自动恢复
```

---

# 75. Loading

Safe Apply 时：

不要冻结整个页面。

右上角显示：

```text
正在应用配置…
```

完成：

```text
配置已生效
```

---

# 76. 手机页面

必须 Responsive。

至少支持：

```text
iPhone

Android

iPad

桌面 Chrome
```

手机上：

左侧菜单变成：

```text
Drawer
```

---

# 77. 第一版不要开发

V1 暂时不要加入：

```text
会员系统

收费系统

多租户

Telegram Bot

Cloudflare API

Prometheus

复杂用户流量计费

中央多服务器控制

SSO
```

避免项目失控。

---

# 78. V1 必须完成功能

完成标准：

```text
Docker 一键安装

中文安装器

随机 Web 端口

随机 Web 路径

随机管理员账号

随机管理员密码

自定义账号密码

Realm v2.9.3

TCP

UDP

TCP + UDP

Web 登录

修改用户名

修改密码

SSH 重置密码

Dashboard

添加规则

编辑规则

复制规则

暂停规则

恢复规则

删除规则

分组

批量操作

批量添加

即时应用

Safe Apply

自动回滚

端口检测

目标 TCP 测试

JSON 导出

JSON 导入

CSV 导出

CSV 导入

导入预览

导入冲突检测

自动备份

手动备份

备份恢复

操作日志

Realm 日志

IP 白名单

登录限速

暗色模式

移动端

rmpctl

更新

卸载
```

---

# 79. 导入导出验收

这是本项目重点。

必须实际测试：

## Test 1

服务器 A：

创建：

```text
50 条 Realm Rule
```

导出：

```text
JSON
```

服务器 B：

导入。

要求：

```text
50 / 50 全部恢复
```

---

## Test 2

服务器 A：

导出：

```text
CSV
```

Windows：

用：

```text
Excel
```

修改：

```text
remote_host

remote_port

remark
```

保存。

服务器 B：

重新导入。

要求：

```text
中文不乱码

字段不丢失

协议正确

端口正确
```

---

## Test 3

导入：

```text
listen_port = 99999
```

必须：

```text
Preview 报错
```

禁止 Apply。

---

## Test 4

导入存在：

```text
0.0.0.0:2088
```

与已有规则冲突。

必须显示：

```text
冲突
```

不能偷偷覆盖。

---

## Test 5

导入后 Realm 启动失败。

必须：

```text
自动恢复导入前配置
```

---

# 80. Safe Apply 验收

修改：

```text
US01

2088 → 2089
```

保存以后：

无需 SSH。

Web 自动：

```text
生成配置

应用

Realm 恢复运行

2089 开始监听

2088 停止监听
```

---

# 81. UDP 验收

建立：

```text
UDP Only
```

规则。

确认：

```text
TCP 未监听/未转发

UDP 正常转发
```

建立：

```text
TCP + UDP
```

确认：

两个协议均工作。

---

# 82. 安装器验收

必须至少测试：

```text
Ubuntu 22.04

Ubuntu 24.04

Debian 12

Debian 13
```

---

# 83. 资源要求

空闲状态：

目标：

```text
RAM < 150 MB
```

越低越好。

禁止为了 Web UI 引入巨型运行时。

---

# 84. README

README 必须包含中文。

主要包括：

```text
项目介绍

功能

截图

系统要求

安装命令

卸载

升级

CLI

导入导出格式

JSON 示例

CSV 示例

目录说明

安全说明

FAQ
```

---

# 85. 开发原则

Codex 必须遵守：

```text
不要只做 Demo

不要大量 TODO

不要 Mock

不要伪实现

不要在正常流程留下 NotImplemented

不要让用户手工修改数据库

不要要求用户手工编辑 Realm TOML

不要要求增加规则后重新 docker compose

不要让配置失败导致全部转发永久掉线
```

项目必须做到：

```text
git clone
+
install.sh
+
浏览器登录
```

即可使用。

---

# 86. 代码质量

要求：

```text
Backend 类型注解

Pydantic validation

数据库 Migration

统一错误处理

API 错误码

结构化日志

Frontend TypeScript strict

组件化

没有硬编码管理员信息

没有硬编码 Web Path

没有明文 Secret
```

---

# 87. 最终交付物

Codex 最终必须交付：

```text
README.md

DESIGN.md

CHANGELOG.md

LICENSE

install.sh

docker-compose.yml

backend/

frontend/

docker/

scripts/

tests/
```

以及：

```text
完整可运行项目
```

不是单纯代码片段。

---

# 88. 自动测试

至少：

```text
Auth Test

Rules CRUD Test

Import JSON Test

Import CSV Test

Export JSON Test

Export CSV Test

Conflict Test

Safe Apply Test

Rollback Test

Password Reset Test
```

---

# 89. 最终验收

在全新 VPS：

只运行安装命令。

然后：

```text
1. 安装成功

2. 获得随机 Web 地址

3. 登录

4. 创建 TCP+UDP Rule

5. 自动生效

6. 暂停

7. 自动停止

8. 恢复

9. 自动恢复

10. 导出 JSON

11. 删除 Rule

12. 导入 JSON

13. Rule 恢复

14. Realm 正常工作

15. 重启 VPS

16. Docker 自动启动

17. RealmPanel 自动启动

18. 转发规则自动恢复
```

所有测试通过以后才能认为：

```text
RealmPanel v1.0.0
```

正式完成。

---

# Codex 执行要求

请根据本 DESIGN.md：

```text
直接创建完整生产级项目。
```

不要只提供设计建议。

不要等待逐步确认。

按照以下顺序完成：

```text
Phase 1
项目骨架

Phase 2
Backend

Phase 3
Realm Config Manager

Phase 4
Safe Apply / Rollback

Phase 5
Frontend

Phase 6
Import / Export

Phase 7
Installer

Phase 8
rmpctl

Phase 9
Tests

Phase 10
README
```

如果开发过程中发现 Realm v2.9.3 某些参数与设计文档不一致：

必须：

```text
优先检查 Realm v2.9.3 官方配置和源码
```

然后按照真实参数实现。

不要为了迎合 DESIGN.md 编造不存在的 Realm 参数。

最终需要确保：

```text
Realm v2.9.3 真正能够启动
TCP 真正能够转发
UDP 真正能够转发
导出真正可以再次导入
Safe Apply 真正能够回滚
```

这是正式运行项目，不是演示项目。