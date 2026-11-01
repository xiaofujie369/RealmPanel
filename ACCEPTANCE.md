# 验收记录

## 0.10.0 nftables 内核转发

验收日期：2026-11-01，环境：Ubuntu 24.04.5 LTS、内核 6.8、x86_64、2 vCPU / 2 GB，主机已运行其他 Docker 与网络业务（iptables-nft，FORWARD 默认 DROP）。

- 性能对比（独立网络命名空间、veth、iperf3 4 并发，各两轮）：TCP realm 17.84 / 18.44 Gbit/s，nftables 25.72 / 27.51 Gbit/s；UDP 800 Mbit/s 时 realm 丢包 15.03% / 25.20%，nftables 0.00% / 0.00%。
- 25 项后端单元测试通过（新增转发规划、nft 脚本范围、本机端口占用解析、DOCKER-USER 放行、连接跟踪清理、Realm 数据迁移）。
- 引擎在三个网络命名空间的真实内核中通过 18 项检查：Docker 式 FORWARD DROP 下的 IPv4 TCP/UDP、指定 SNAT 源地址、TCP Only、`::` 监听下域名按协议族分别转发到 A / AAAA、IPv6 UDP、本机发起连接、计数器、暂停与恢复保留累计、`0.0.0.0` 与 `[::]` 监听进程的端口保护、只新增自身表、停止时完整清理。
- 本机 Docker 构建两镜像（前端 vue-tsc strict 检查通过）并全新部署到 `/opt/realm-panel`，健康检查通过；`tests/live_acceptance.py` 16 项真实 TCP/UDP 与 API 验收全部通过。
- 外部客户端（独立命名空间经 veth 接入主机）→ 主机 DNAT → `example.com:80` 域名目标返回 HTTP 200，流量计数正确；`DOCKER-USER` 中只有一条跳转到 `RMP-FORWARD`，链内只放行该规则端口。
- `rmpctl stop` 后 `table inet realmpanel`、`RMP-FORWARD` 及跳转全部移除，主机原有表与 Docker 规则不变；`rmpctl start` 后自动重建。主机其他容器服务在整个过程中保持可用。

尚未执行：GitHub Actions 双架构构建与公开 Release、从 0.9.5 实例的在线升级（`rmpctl update`）、整机重启验证。

## 0.9.5 简洁界面与单规则流量

- [GitHub Actions 36961069942](https://github.com/xiaofujie369/RealmPanel/actions/runs/36961069942) 全部通过：19 项后端测试、前端 strict 构建、两架构镜像、真实 TCP/UDP 及流量计数、全新安装与升级。
- VPS 更新到 0.9.5，现有两条规则保留；真实连接产生各自的上行、下行累计，核心容器重启后两条规则的累计均保留。
- 独立 nftables 测试表验证真实 TCP/UDP 收发计数、暂停期间不计数、恢复后继续累计、采集器重启及内核计数器重建后累计保留；测试表已删除。
- 规则每行包含编辑、暂停/恢复、诊断、复制、删除；诊断验证本地双协议监听、TCP 目标连接及 UDP 发送测试。
- 浏览器验证简洁表格的真实流量显示、亮色/暗色切换和 390 × 844 手机编辑表单；验证期间未修改现有规则。
- 流量是监听侧网络层字节，包含协议开销，从功能启用时开始；异常断电最多可能丢失最后一个保存周期，受限内核明确显示不可用。

验收日期：2026-10-02（Asia/Shanghai）。第一版真实部署验收已完成；0.9.2 预编译发布流程全部通过。

## 已通过

- 后端 16 项自动测试：认证、Cookie/CSRF、限速、密码重置及修改、路径切换、配置生成、导入导出、冲突、加密备份、安全应用回滚。
- TypeScript strict 检查及 Vite 生产构建。
- 浏览器登录、概览、桌面及手机布局、亮色/暗色模式。
- 真实 TCP+UDP、TCP Only、UDP Only 回显转发。
- 启停、编辑端口、复制、导出后删除再导入、备份恢复。
- 故意占用监听端口后应用失败，数据库、配置和原转发同步回滚。
- 50/50 JSON 与 CSV 规则实际 API 往返，中文、协议、高级参数完整保留。
- 用户授权后实际重启整台 VPS，通过 boot_id 变化确认；Docker、面板、持久化 TCP/UDP 规则和真实转发均自动恢复。测试规则已清理。
- 首版空闲时两容器合计约 80 MB 内存，低于 150 MB 目标。
- GitHub Actions 原生 amd64 / arm64 镜像构建及真实 TCP/UDP 验收通过；正式发布 [v0.9.2](https://github.com/xiaofujie369/RealmPanel/releases/tag/v0.9.2)。
- Ubuntu 22.04 / 24.04 全新安装公开 Release 镜像、健康检查、真实转发及预编译升级通过。
- Ubuntu 22.04 / 24.04、Debian 12 / 13、Rocky 9、AlmaLinux 9、Fedora 43、Alpine 3.23 容器内依赖及 Docker/Compose 软件包安装通过；此项不等同于所有系统完整虚拟机部署。
- 独立 CI 环境实际创建 1 GiB Swap，验证权限、开机配置和重复执行不重复添加。
- 提供的 VPS 从 0.9.0 使用预编译镜像升级至 0.9.2，健康检查及全部真实 TCP/UDP 验收通过；浏览器顶栏黑白主题切换通过，测试规则已清理。

完整 CI 记录：[Actions 36924127539](https://github.com/xiaofujie369/RealmPanel/actions/runs/36924127539)。

自动化原始报告保存在部署实例的 `/opt/realm-panel/artifacts/`，不包含登录密码。

## 实际环境

- 提供的 VPS：Linux x86_64、Ubuntu 26.04 LTS、约 2 GB 内存。
- Docker 和 Compose 已存在；服务器还有其他业务，本项目使用独立容器和目录。
- Realm 固定 v2.9.3，上游源码提交 `2ab8e400cea3edf3f1103a0f0cc80c2665081c11`。

## 不得冒充完成的验收

- Debian 等其他发行版完整虚拟机上的全新安装及重启矩阵（Ubuntu 22.04 / 24.04 已验证全新安装）。
- 两台独立 VPS 之间 50 条规则的迁移。
- 在 Windows Excel 中人工编辑 CSV 后重新导入。
- 实际新版本升级失败回滚及卸载（不可在交付实例上破坏性执行）。

以上未完成项目保留为验收限制，不宣称生产 v1.0.0 全矩阵通过。
