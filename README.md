# 文件管理系统

这是一个轻量级网页文件管理系统，支持登录、用户与权限管理、树形分类、文件上传下载、批量操作、在线预览、版本记录和文件操作审批。

## 技术选型

- 后端：FastAPI
- 页面：Jinja2 模板
- 数据库：SQLite，免费，无需单独购买云数据库
- 文件存储：服务器本地磁盘，默认保存在 `data/uploads`
- 部署：腾讯云 Ubuntu 上使用 Python 虚拟环境 + systemd；仓库仍保留可选的 Docker 配置

## 功能

- 网页登录
- 管理员新增用户、设置管理员权限、启用或停用账号
- 上传文档并选择分类
- 按分类和关键词筛选文件
- 批量申请、下载和删除文件
- 上传新文件作为新版本
- 下载任意历史版本
- 修改文件标题和分类
- PDF、图片和文本文件基础在线预览
- 普通用户操作他人文件前提交申请，由管理员审批
- SQLite 数据库一致性备份和备份保留策略
- 默认分类初始化：合同、制度、项目资料、财务、其他

## 本地运行

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app:app --reload --host 0.0.0.0 --port 9000 --env-file .env
```

浏览器打开：

```text
http://127.0.0.1:9000
```

默认账号来自 `.env`：

```text
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-me-now
```

第一次上线前请务必修改 `ADMIN_PASSWORD` 和 `SECRET_KEY`。

## Docker 运行

```bash
copy .env.example .env
docker compose up -d --build
```

访问：

```text
http://服务器公网 IP:8000
```

数据会保存在项目目录的 `data` 文件夹中，包括 SQLite 数据库和上传文件。迁移服务器时备份整个 `data` 目录即可。

## 腾讯云部署

生产服务器的唯一项目路径是 `/home/file-manager`，服务名是 `file-manager`，端口是 `9000`。

更新代码并重启：

```bash
cd /home/file-manager
git pull
/home/file-manager/.venv/bin/pip install -r requirements.txt
sudo systemctl restart file-manager
sudo systemctl status file-manager
```

腾讯云安全组和 Ubuntu 防火墙都需要放行 `9000/tcp`。浏览器访问 `http://服务器公网IP:9000/login`。

生产环境如果绑定域名，建议在前面加 Nginx 或腾讯云负载均衡，并开启 HTTPS。开启 HTTPS 后把 `.env` 里的 `COOKIE_SECURE` 改成 `true`。

## 备份

以下命令使用 SQLite 原生备份 API 创建一致性数据库副本，并保留最近 7 份：

```bash
cd /home/file-manager
/home/file-manager/.venv/bin/python scripts/backup_database.py \
  --database /home/file-manager/data/file_manager.sqlite3 \
  --output-dir /home/file-manager/backups \
  --keep 7
```

脚本会自动执行 SQLite 完整性检查。它只备份数据库，不包含 `/home/file-manager/data/uploads` 中的上传文件；因此恢复数据库备份不会恢复已经丢失的文件内容。

每天凌晨 2 点自动执行，可通过 `crontab -e` 加入：

```cron
0 2 * * * cd /home/file-manager && /home/file-manager/.venv/bin/python scripts/backup_database.py >> /home/file-manager/database-backup.log 2>&1
```

## 目录结构

```text
file-manager/
  app.py
  repositories/
  services/
  templates/
  static/
  scripts/
  tests/
  data/
  Dockerfile
  docker-compose.yml
  requirements.txt
  .env.example
```
