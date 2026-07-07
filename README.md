# 文件管理系统

这是一个轻量级网页文件管理系统，支持登录、文件上传、文件下载、分类管理、上传新版本并保留历史版本。

## 技术选型

- 后端：FastAPI
- 页面：Jinja2 模板
- 数据库：SQLite，免费，无需单独购买云数据库
- 文件存储：服务器本地磁盘，默认保存在 `data/uploads`
- 部署：Docker / Docker Compose，适合腾讯云轻量应用服务器或 CVM

## 功能

- 网页登录
- 上传文档并选择分类
- 按分类和关键词筛选文件
- 下载当前版本
- 上传新文件作为新版本
- 下载任意历史版本
- 修改文件标题和分类
- 默认分类初始化：合同、制度、项目资料、财务、其他

## 本地运行

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app:app --reload --host 0.0.0.0 --port 8000 --env-file .env
```

浏览器打开：

```text
http://127.0.0.1:8000
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

## 腾讯云部署建议

1. 购买腾讯云轻量应用服务器或 CVM，系统选择 Ubuntu LTS。
2. 在服务器安全组或防火墙放行 `8000` 端口。
3. 安装 Docker 和 Docker Compose。
4. 上传本项目到服务器，例如 `/opt/file-manager-system`。
5. 在服务器上复制 `.env.example` 为 `.env`，修改管理员密码和 `SECRET_KEY`。
6. 执行 `docker compose up -d --build`。
7. 浏览器访问 `http://服务器公网 IP:8000`。

生产环境如果绑定域名，建议在前面加 Nginx 或腾讯云负载均衡，并开启 HTTPS。开启 HTTPS 后把 `.env` 里的 `COOKIE_SECURE` 改成 `true`。

## 备份

定期备份：

```bash
tar -czf file-manager-data-backup.tar.gz data
```

恢复时停止服务，解压回项目目录，再启动服务。

## 目录结构

```text
file-manager-system/
  app.py
  templates/
  static/
  data/
  Dockerfile
  docker-compose.yml
  requirements.txt
  .env.example
```
