# 验收记录

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
