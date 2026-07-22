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
- 管理员启用或停用其他用户；禁止停用当前登录账号，停用账号不能登录，已有会话也会在下一次请求时失效。
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
- 删除文档移入回收站（软删除），不立即清理上传文件。
- 批量删除选中文档同样移入回收站。
- 回收站页展示已删除文件：普通用户看自己删除的，管理员看全部；支持单条恢复、单条彻底删除（清理版本记录和上传文件）和清空回收站（普通用户清空自己的，管理员清空全部）。
- 可通过 `RECYCLE_RETENTION_DAYS` 配置回收站自动清理天数，访问回收站时自动彻底删除超期文件，默认 0 表示不自动清理。
- 文件所有者或管理员可创建分享链接，支持可选访问密码、有效期（1 天/7 天/30 天/永久）和仅预览/允许下载两种权限；创建人可撤销自己创建的链接，管理员可撤销全部链接。
- 公开访问路由 `/share/{token}` 无需登录；链接不存在、已撤销、已过期或文档已删除时返回 404；有密码的链接先验证密码；通过分享链接的预览和下载会以 anonymous 用户写审计日志。
- 文件列表分页，每页最多 10 条。
- 全文搜索：关键词同时匹配文档标题和文本类文件内容（FTS5 虚表索引，不可用时降级为仅文件名搜索）；结果区分标题命中和内容命中，内容命中显示上下文片段；沿用现有列表可见性并排除回收站。
- 列表页显示当前版本上传时间作为更新时间。
- 当前版本基础在线预览：PDF、图片和文本类文件可预览；Office 等复杂格式提示下载查看。
- 用户信息页的修改密码和新增用户使用弹窗表单，结果在页面顶部 alert 提示。
- 修改密码弹窗带进入动画。
- 分类三点按钮使用不会被相邻分类遮挡的小型下拉菜单，选择删除后再显示确认提示。
- 普通用户默认只能下载、预览、覆盖或删除自己上传的文件。
- 普通用户可对他人文件提交“下载和预览”或“覆盖新版本”申请；相同待审批申请不会重复创建。
- 管理员右上角铃铛显示待审批数量，并可在用户信息页接受或拒绝申请。
- 管理员审批接受申请时可选有效期（7 天、30 天或永久），过期后权限自动失效，用户可重新申请。
- 管理员和文件所有者可撤销已授予的权限，撤销后立即失效。
- 审批历史页展示申请/审批/撤销记录：管理员可看全部并按申请人、状态筛选分页；普通用户可看自己提交的申请和自己文件的授权记录。
- 审批通过后，申请用户在有效期内拥有对应文件和对应操作的权限；删除他人文件不开放申请。
- 登录、创建/删除分类、删除文档、批量删除文档、恢复和彻底删除回收站文件、修改密码、新增用户、修改用户权限、提交文件操作申请、审批和撤销授权、创建和撤销分享链接、分享链接访问会写入审计日志；审计日志同时写入 `audit_logs` 表和轮转日志文件。
- 管理员可在 `/admin/audit-logs` 查看审计日志，支持按用户、操作类型和日期范围筛选，每页 20 条。
- 全局统一错误处理：网页路由的 404、403、422、500 等错误渲染统一错误页，API 语义的请求返回 JSON；未捕获异常记录日志后返回 500 错误页。
- SQLite 自动初始化数据库表和默认分类。
- 提供 SQLite 一致性数据库备份脚本，支持完整性检查和保留最近指定数量的备份。
- 提供上传文件增量备份脚本，按日期目录保留快照（未变化文件与上一份快照硬链接），支持保留最近指定数量和恢复指定快照。
- 数据库备份和上传文件备份的执行结果写入备份状态文件；管理员用户信息页展示最近备份状态和数据目录磁盘剩余空间。

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
  - `scripts/`：数据库备份等独立运维脚本。

数据库：

- SQLite。
- 生产数据库路径：`/home/file-manager/data/file_manager.sqlite3`。
- 数据库表包括：`users`、`categories`、`documents`、`document_versions`、`access_requests`、`audit_logs`、`share_links`，以及全文搜索用的 FTS5 虚表 `document_fts`。
- `users` 表包含 `is_admin` 和 `is_active` 字段，分别标记管理员权限和账号启用状态；旧数据库启动时会自动补充 `is_active`，已有用户默认启用。
- `categories` 表包含 `parent_id` 字段；旧数据库启动时自动迁移，旧分类保留为一级分类。
- `documents` 表包含 `deleted_at` 字段，为空表示未删除；旧数据库启动时自动迁移，存量文档不受影响。
- `access_requests` 表记录申请人、文件、操作类型、审批状态、审批人、有效期和申请/审批时间；旧数据库启动时自动补充 `expires_at`，存量记录视为永久。
- `audit_logs` 表记录审计日志的用户、IP、操作、详情和时间，旧数据库启动时自动建表。
- `share_links` 表记录分享链接的文档、token、密码哈希、有效期、下载权限、创建人、创建和撤销时间，启动时自动建表。
- `document_fts` 虚表索引文档标题和文本类文件内容（扩展名可通过 `SEARCH_INDEX_EXTENSIONS` 配置，单个文件索引上限默认 2 MB，可通过 `SEARCH_INDEX_MAX_BYTES` 配置）；启动时自动建表并对存量文档幂等补齐索引；FTS5 不可用时自动降级为仅文件名搜索。

权限模型：

- 管理员可操作全部文件并审批普通用户申请。
- 普通用户可直接操作自己上传的文件。
- 普通用户下载、预览或覆盖他人文件时必须先申请对应权限。
- 管理员审批时可选有效期（7 天、30 天或永久），`access_requests.expires_at` 为空表示永久；存量已批准记录视为永久。
- 管理员和文件所有者可撤销已批准的权限，撤销后立即失效；过期权限自动失效，用户可重新申请。
- 下载权限同时用于当前版本、历史版本、批量下载和在线预览。
- 覆盖新版本权限不包含修改文件标题、分类或删除文件的权限。
- 删除文件移入回收站；恢复和彻底删除仅限文件所有者或管理员。

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
DATABASE_BACKUP_DIR=/home/file-manager/backups
DATABASE_BACKUP_KEEP=7
UPLOADS_BACKUP_DIR=/home/file-manager/backups
UPLOADS_BACKUP_KEEP=7
BACKUP_STATUS_FILE=/home/file-manager/data/backup_status.json
AUDIT_LOG_MAX_BYTES=5242880
AUDIT_LOG_BACKUP_COUNT=5
RECYCLE_RETENTION_DAYS=0
SEARCH_INDEX_MAX_BYTES=2097152
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
3. 审计日志已覆盖登录、删除、用户与权限变更、文件操作申请和审批；日志同时写入 `audit_logs` 表和 RotatingFileHandler 轮转文件（`data/logs/audit.log`），管理员可在页面筛选查看。
4. 统一错误处理已抽象为全局异常处理器：网页错误渲染 `templates/error.html`，API 语义请求返回 JSON。
5. 当前系统已支持管理员/普通用户两级权限、文件级操作审批、授权有效期和撤销，尚未实现自定义角色、部门或菜单级权限体系。
6. 当前删除为软删除并进入回收站，支持恢复和彻底删除；删除审批（删除需管理员批准）能力尚未实现。
7. 当前预览功能是轻量实现，PDF、图片、文本支持在线预览；Office 文档在线预览需要额外转换组件，暂未引入。
8. 当前部署是 HTTP + IP + 9000 端口。正式对外使用时建议增加域名、Nginx 反向代理和 HTTPS。
9. 分享链接允许未登录访问文件，属高风险能力：当前已支持密码、有效期、撤销和审计，生产环境建议结合 HTTPS 使用，并根据需要评估是否限制创建权限或增加访问次数限制。
10. 分享链接的密码错误次数当前只写审计日志，未做速率限制；如暴露公网，建议后续增加限速。
11. 备份已覆盖 SQLite 数据库和 `data/uploads` 上传文件；上传文件备份为按日期目录的增量快照，恢复时会覆盖目标目录，执行前需确认。
12. 数据库备份和上传文件备份需要通过 cron 或其他调度方式定期执行；执行结果写入 `backup_status.json`，管理员用户信息页可查看最近备份状态和磁盘剩余空间。

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

手工备份数据库并保留最近 7 份：

```bash
cd /home/file-manager
/home/file-manager/.venv/bin/python scripts/backup_database.py \
  --database /home/file-manager/data/file_manager.sqlite3 \
  --output-dir /home/file-manager/backups \
  --keep 7
```

手工增量备份上传文件并保留最近 7 份：

```bash
cd /home/file-manager
/home/file-manager/.venv/bin/python scripts/backup_data.py \
  --uploads-dir /home/file-manager/data/uploads \
  --output-dir /home/file-manager/backups \
  --keep 7
```

恢复指定上传文件快照（会覆盖目标目录现有内容）：

```bash
cd /home/file-manager
/home/file-manager/.venv/bin/python scripts/backup_data.py \
  --restore uploads-20260715T010000Z \
  --output-dir /home/file-manager/backups \
  --target-dir /home/file-manager/data/uploads
```

注意：两类备份的执行结果都会写入备份状态文件（默认 `BACKUP_STATUS_FILE` 或 `data/backup_status.json`），管理员可在用户信息页查看。

## 后续开发方向

优先级较高：

- 继续把旧的数据库写入逻辑逐步迁移到 repository / service。
- 完善删除/归档策略，增加删除审批能力。
- 优化部署文档，使 README 与当前 systemd 部署方式保持一致。

开发原则：

- 保持当前核心功能稳定。
- 不删除已有功能。
- 不进行大的框架改变，除非用户明确要求。
- 修改前先读 `AGENTS.md` 和本文件。
