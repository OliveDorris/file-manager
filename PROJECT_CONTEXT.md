# 项目背景

## 项目概述

文件管理系统是一个轻量级 Web 文档管理应用，用于在网页端完成登录、文件分类、文档上传、文档下载、文档修改、版本记录、删除、分页列表和基础在线预览。

项目最初目标是使用轻量级框架开发，并部署到腾讯云。数据库要求免费，因此当前使用 SQLite 本地文件数据库。

## 当前仓库

GitHub 仓库：
`OliveDorris/file-manager`

默认分支：
`main`

## 当前功能

已实现功能：

- 网页登录。
- 用户信息页面。
- 修改当前用户密码。
- 管理员新增用户。
- 管理员查看用户列表，用户列表每页最多 10 条。
- 管理员设置用户是否为管理员。
- 文件分类。
- 通过分类旁的加号和弹窗新增文件夹，支持最多三级树形分类。
- 分类树支持展开、收起和逐级缩进；选择父文件夹时会同时显示下级文件夹中的文档。
- 删除空分类；分类中有文件或子文件夹时禁止删除，并在页面顶部提示。
- 上传文档。
- 下载当前版本文档。
- 批量下载选中文档当前版本，打包为 zip。
- 普通用户可以勾选任意文件，并可为多个未授权文件批量提交预览/下载申请。
- 文件列表底部使用细线图标提供申请、预览、下载和删除操作；禁用操作悬停时显示原因，预览只允许单选。
- 上传新文件作为新版本。
- 查看版本记录。
- 下载历史版本。
- 修改文件标题和分类。
- 删除文档及其全部历史版本，同时清理对应上传文件目录。
- 批量删除选中文档及其全部历史版本，同时清理对应上传文件目录。
- 文件列表分页，每页最多 10 条。
- 列表页显示当前版本上传时间作为更新时间。
- 当前版本基础在线预览：PDF、图片和文本类文件可预览；Office 等复杂格式提示下载查看。
- 用户信息页的修改密码和新增用户使用弹窗表单，结果在页面顶部 alert 提示。
- 修改密码弹窗带进入动画。
- 分类三点按钮使用不会被相邻分类遮挡的小型下拉菜单，选择删除后再显示确认提示。
- 普通用户默认只能下载、预览、覆盖或删除自己上传的文件。
- 普通用户可对他人文件提交“下载和预览”或“覆盖新版本”申请；相同待审批申请不会重复创建。
- 管理员右上角铃铛显示待审批数量，并可在用户信息页接受或拒绝申请。
- 审批通过后，申请用户持续拥有对应文件和对应操作的权限；删除他人文件不开放申请。
- 登录、创建/删除分类、删除文档、批量删除文档、修改密码、新增用户、修改用户权限、提交文件操作申请和审批操作会写入基础审计日志。
- SQLite 自动初始化数据库表和默认分类。

默认初始化分类：

- 合同
- 制度
- 项目资料
- 财务
- 其他

## 技术架构

前端：

- Jinja2 服务端渲染模板。
- 原生 HTML 表单。
- 自定义 CSS，文件位于 `static/styles.css`。

后端：

- Python 3.12。
- FastAPI。
- Uvicorn。
- 当前入口文件为 `app.py`，生产启动方式仍为 `uvicorn app:app`。
- 新增轻量分层目录：
  - `repositories/`：数据库查询和持久化逻辑。
  - `services/`：业务判断、文件预览、文件权限和审批逻辑。

数据库：

- SQLite。
- 生产数据库路径：`/home/file-manager/data/file_manager.sqlite3`。
- 数据库表包括：`users`、`categories`、`documents`、`document_versions`、`access_requests`。
- `users` 表包含 `is_admin` 字段，使用 `1/0` 标记管理员或普通用户。
- `categories` 表包含 `parent_id` 字段；旧数据库启动时自动迁移，旧分类保留为一级分类。
- `access_requests` 表记录申请人、文件、操作类型、审批状态、审批人和申请/审批时间。

权限模型：

- 管理员可操作全部文件并审批普通用户申请。
- 普通用户可直接操作自己上传的文件。
- 普通用户下载、预览或覆盖他人文件时必须先申请对应权限。
- 下载权限同时用于当前版本、历史版本、批量下载和在线预览。
- 覆盖新版本权限不包含修改文件标题、分类或删除文件的权限。

文件存储：

- 本地磁盘。
- 上传目录：`/home/file-manager/data/uploads`。
- 每个文档按 document id 建目录，每个版本保留独立文件。

## 部署背景

服务器：
腾讯云 Ubuntu。

正确项目路径：
`/home/file-manager`

注意：这是唯一正确路径。如果后续对话中出现 `/home/ubuntu/file-managerV2`、`/home/ubuntu/file-manager`、`/home/file-managerV2` 等路径，应主动纠正为 `/home/file-manager`。

当前部署方式：
Python 虚拟环境 + systemd。

当前服务名：
`file-manager`

当前端口：
`9000`

当前访问地址格式：
`http://服务器公网IP:9000/login`

当前 systemd 服务关键配置应类似：

```ini
WorkingDirectory=/home/file-manager
EnvironmentFile=/home/file-manager/.env
ExecStart=/home/file-manager/.venv/bin/uvicorn app:app --host 0.0.0.0 --port 9000
Restart=always
RestartSec=3
```

服务器防火墙和腾讯云安全组都需要放行 `9000/tcp`。

## Docker 状态

仓库中保留 Dockerfile 和 docker-compose.yml。

实际腾讯云部署时曾遇到 Docker Hub 镜像拉取超时，因此当前生产运行方式改为 Python + systemd。后续如网络条件恢复，可以重新考虑 Docker，但不应在未确认前强行切回 Docker。

## 环境变量

生产环境使用：
`/home/file-manager/.env`

关键变量：

```text
ADMIN_USERNAME=admin
ADMIN_PASSWORD=生产密码，不应提交到 GitHub
SECRET_KEY=生产密钥，不应提交到 GitHub
DATABASE_PATH=/home/file-manager/data/file_manager.sqlite3
DATA_DIR=/home/file-manager/data
MAX_UPLOAD_MB=100
COOKIE_SECURE=false
```

注意：

- `.env` 不应提交到仓库。
- 密码和 SECRET_KEY 如果包含 `#`、`$` 等特殊字符，建议在 `.env` 中用引号包起来。
- 修改 `.env` 中的 `ADMIN_PASSWORD` 不会自动更新已存在数据库中的用户密码。数据库初始化后，重置密码需要更新 SQLite 中的 `users.password_hash`。
- 修改 `SECRET_KEY` 会使旧登录 Cookie 失效，用户需要重新登录。

## 当前已知问题和技术债

1. `app.py` 仍保留部分历史路由逻辑和数据库写入逻辑；新增列表分页和预览逻辑已开始使用 `repositories/`、`services/` 轻量分层。
2. 后续新增功能应继续使用 service / repository 分层，逐步迁移旧逻辑，禁止一次性大范围重构。
3. 审计日志已覆盖登录、删除、用户与权限变更、文件操作申请和审批；仍需考虑日志轮转、检索和持久化策略。
4. 统一错误处理尚未完全抽象。
5. 当前系统已支持管理员/普通用户两级权限和文件级操作审批，尚未实现自定义角色、部门或菜单级权限体系。
6. 当前系统删除是硬删除。后续如需要更安全的数据治理，应增加软删除、恢复和删除审批能力。
7. 当前预览功能是轻量实现，PDF、图片、文本支持在线预览；Office 文档在线预览需要额外转换组件，暂未引入。
8. 当前部署是 HTTP + IP + 9000 端口。正式对外使用时建议增加域名、Nginx 反向代理和 HTTPS。
9. 当前 SQLite 和上传文件都在服务器本地磁盘，需要定期备份 `/home/file-manager/data`。

## 常用运维命令

查看服务状态：

```bash
sudo systemctl status file-manager
```

重启服务：

```bash
sudo systemctl restart file-manager
```

查看日志：

```bash
sudo journalctl -u file-manager -f
```

查看端口监听：

```bash
sudo ss -lntp | grep 9000
```

本机测试登录页：

```bash
curl http://127.0.0.1:9000/login
```

更新代码后重启：

```bash
cd /home/file-manager
git pull
sudo systemctl restart file-manager
```

备份数据：

```bash
cd /home/file-manager
tar -czf file-manager-data-backup.tar.gz data
```

## 后续开发方向

优先级较高：

- 继续把旧的数据库写入逻辑逐步迁移到 repository / service。
- 完善审计日志。
- 增加统一错误处理。
- 根据实际使用反馈完善文件级权限申请，例如权限撤销、有效期和审批历史查询。
- 完善删除/归档策略，增加软删除、恢复和删除审批能力。
- 增加数据备份和恢复脚本。
- 优化部署文档，使 README 与当前 systemd 部署方式保持一致。

开发原则：

- 保持当前核心功能稳定。
- 不删除已有功能。
- 不进行大的框架改变，除非用户明确要求。
- 修改前先读 `AGENTS.md` 和本文件。
