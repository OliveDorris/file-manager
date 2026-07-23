from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException as StarletteHTTPException

from repositories.audit_log_repository import (
    AUDIT_LOG_PAGE_SIZE,
    count_audit_logs,
    initialize_audit_log_schema,
    insert_audit_log,
    list_audit_actions,
    list_audit_logs,
)
from repositories.category_repository import (
    count_child_categories,
    count_documents_in_category,
    delete_category as delete_category_record,
    get_category,
)
from repositories.access_request_repository import (
    ACCESS_REQUEST_PAGE_SIZE,
    count_access_requests,
    count_pending_access_requests,
    initialize_access_request_schema,
    list_access_requests,
    list_pending_access_requests,
)
from repositories.document_repository import (
    DOCUMENT_PAGE_SIZE,
    count_documents,
    get_current_version,
    get_document_detail,
    get_version,
    list_categories,
    list_current_versions_for_documents,
    list_deleted_documents,
    list_document_versions,
    list_documents_by_ids,
    list_documents,
    restore_document,
    soft_delete_documents_by_ids,
)
from repositories.share_link_repository import (
    initialize_share_link_schema,
    list_share_links_for_document,
)
from repositories.user_repository import (
    USER_PAGE_SIZE,
    count_active_admin_users,
    count_users,
    create_user,
    get_user_by_id,
    get_user_by_username,
    list_users,
    update_user_active_status,
    update_user_admin_status,
    update_user_password,
)
from services.category_service import (
    build_category_tree,
    create_category_with_parent,
    flatten_category_tree,
    validate_category_can_delete,
)
from services.audit_service import configure_audit_file_handler
from services.backup_status_service import build_backup_overview
from services.document_service import (
    build_batch_download_zip,
    parse_selected_document_ids,
)
from services.preview_service import build_preview_context
from services.permission_service import (
    ACTION_DOWNLOAD,
    ACTION_UPLOAD_VERSION,
    access_action_label,
    access_status_label,
    build_document_access_flags,
    build_document_access_flags_one,
    is_grant_expired,
    parse_validity_days,
    review_access_request,
    revoke_access_request_grant,
    submit_access_request,
    submit_download_access_requests,
)
from services.recycle_bin_service import (
    can_recycle_document,
    purge_documents_completely,
    purge_retention_expired_documents,
    recycle_retention_days,
)
from services.search_service import (
    build_search_contexts,
    configure_search,
    find_matching_document_ids,
    index_document,
    remove_document_index,
)
from services.share_service import (
    create_document_share_link,
    get_active_share_link,
    revoke_document_share_link,
    share_link_status_label,
    verify_share_password,
)
from services.user_service import (
    validate_admin_status_change,
    validate_password_pair,
    validate_user_active_status_change,
    validate_username,
)


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = Path(os.getenv("DATABASE_PATH", DATA_DIR / "file_manager.sqlite3"))
SECRET_KEY = os.getenv("SECRET_KEY", "change-this-secret-key-in-production")
SESSION_COOKIE = "file_manager_session"
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "100"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("file_manager")
audit_logger = logging.getLogger("file_manager.audit")

app = FastAPI(title="File Manager System")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _static_version() -> str:
    """Cache-busting token for static assets, based on styles.css mtime."""
    try:
        return str(int((BASE_DIR / "static" / "styles.css").stat().st_mtime))
    except OSError:
        logger.exception("无法读取 styles.css 修改时间，静态资源版本号使用固定值")
        return "1"


templates.env.globals["static_version"] = _static_version()
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


DEFAULT_ERROR_MESSAGES = {
    status.HTTP_400_BAD_REQUEST: "请求无效，请检查后重试。",
    status.HTTP_403_FORBIDDEN: "没有权限执行此操作。",
    status.HTTP_404_NOT_FOUND: "请求的页面或资源不存在。",
    status.HTTP_405_METHOD_NOT_ALLOWED: "请求方式不支持。",
    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE: "文件大小超出限制。",
    status.HTTP_422_UNPROCESSABLE_ENTITY: "请求参数无效，请检查后重试。",
    status.HTTP_500_INTERNAL_SERVER_ERROR: "服务器内部错误，请稍后重试。",
}


def wants_json_response(request: Request) -> bool:
    if request.url.path.startswith("/api"):
        return True
    accept = request.headers.get("accept", "")
    return "application/json" in accept and "text/html" not in accept


def render_error_page(request: Request, status_code: int, message: str = "") -> Response:
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "status_code": status_code,
            "message": message or DEFAULT_ERROR_MESSAGES.get(status_code, "请求处理失败，请稍后重试。"),
        },
        status_code=status_code,
    )


@app.exception_handler(HTTPException)
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> Response:
    if exc.headers and "Location" in exc.headers:
        return Response(status_code=exc.status_code, headers=exc.headers)
    if wants_json_response(request):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
    message = exc.detail if isinstance(exc.detail, str) else ""
    return render_error_page(request, exc.status_code, message)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError) -> Response:
    logger.warning("Request validation failed: path=%s errors=%s", request.url.path, exc.errors())
    if wants_json_response(request):
        return JSONResponse(
            {"detail": jsonable_encoder(exc.errors())},
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    return render_error_page(request, status.HTTP_422_UNPROCESSABLE_ENTITY)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> Response:
    logger.exception("Unhandled error: path=%s", request.url.path)
    if wants_json_response(request):
        return JSONResponse(
            {"detail": "Internal server error"},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return render_error_page(request, status.HTTP_500_INTERNAL_SERVER_ERROR)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def log_audit_action(request: Request, user: dict[str, Any], operation: str, target: str) -> None:
    username = user.get("username", "unknown")
    ip = client_ip(request)
    timestamp = now_iso()
    audit_logger.info(
        "user=%s time=%s ip=%s operation=%s target=%s",
        username,
        timestamp,
        ip,
        operation,
        target,
    )
    try:
        with get_db() as conn:
            insert_audit_log(conn, str(username), ip, operation, target, timestamp)
            conn.commit()
    except Exception:
        logger.exception(
            "审计日志写入数据库失败：user=%s operation=%s target=%s",
            username,
            operation,
            target,
        )


def sync_document_index(document_id: int) -> None:
    try:
        with get_db() as conn:
            index_document(conn, UPLOAD_DIR, document_id)
            conn.commit()
    except Exception:
        logger.exception("全文索引更新失败：document_id=%s", document_id)


def remove_document_from_index(document_id: int) -> None:
    try:
        with get_db() as conn:
            remove_document_index(conn, document_id)
            conn.commit()
    except Exception:
        logger.exception("全文索引移除失败：document_id=%s", document_id)


def parse_optional_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def parse_page(value: object, default: int = 1) -> int:
    try:
        return max(int(str(value)), 1)
    except (TypeError, ValueError):
        return default


def is_admin_user(user: dict[str, Any]) -> bool:
    return bool(user.get("is_admin"))


def require_admin_user(user: dict[str, Any]) -> None:
    if not is_admin_user(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin permission is required")


def dashboard_url(
    category_id: int | None,
    q: str,
    page: int,
    success: str = "",
    error: str = "",
) -> str:
    params: list[tuple[str, str]] = []
    if category_id:
        params.append(("category_id", str(category_id)))
    if q.strip():
        params.append(("q", q.strip()))
    if page > 1:
        params.append(("page", str(page)))
    if success:
        params.append(("success", success))
    if error:
        params.append(("error", error))
    query = urlencode(params)
    return f"/dashboard?{query}" if query else "/dashboard"


def account_url(user_page: int) -> str:
    if user_page > 1:
        return f"/account?user_page={user_page}"
    return "/account"


def local_url_with_message(return_to: str, success: str = "", error: str = "") -> str:
    parsed = urlsplit(return_to)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/") or parsed.path.startswith("//"):
        parsed = urlsplit("/dashboard")
    params = [(key, value) for key, value in parse_qsl(parsed.query) if key not in {"success", "error"}]
    if success:
        params.append(("success", success))
    if error:
        params.append(("error", error))
    return urlunsplit(("", "", parsed.path, urlencode(params), parsed.fragment))


def dashboard_url_from_form(form: Any, success: str = "", error: str = "") -> str:
    return dashboard_url(
        parse_optional_int(str(form.get("active_category_id") or "")),
        str(form.get("q") or ""),
        parse_page(form.get("page")),
        success=success,
        error=error,
    )


def password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 240_000)
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, salt, expected_digest = stored_hash.split("$", 2)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    candidate = password_hash(password, salt).split("$", 2)[2]
    return hmac.compare_digest(candidate, expected_digest)


def sign_value(value: str) -> str:
    signature = hmac.new(SECRET_KEY.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{value}.{signature}"


def unsign_value(signed_value: str) -> str | None:
    if "." not in signed_value:
        return None
    value, signature = signed_value.rsplit(".", 1)
    expected = hmac.new(SECRET_KEY.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    return value


def login_response(user_id: int) -> RedirectResponse:
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE,
        sign_value(str(user_id)),
        httponly=True,
        secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
        samesite="lax",
        max_age=60 * 60 * 12,
    )
    return response


def logout_response(error: str = "") -> RedirectResponse:
    target = f"/login?{urlencode({'error': error})}" if error else "/login"
    response = RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE)
    return response


def get_current_user(request: Request) -> dict[str, Any]:
    cookie_value = request.cookies.get(SESSION_COOKIE)
    if not cookie_value:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    user_id = unsign_value(cookie_value)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    try:
        parsed_user_id = int(user_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"}) from None
    with get_db() as conn:
        user = row_to_dict(get_user_by_id(conn, parsed_user_id))
    if not user:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    if not user["is_active"]:
        log_audit_action(request, user, "session_denied", "account_disabled")
        location = f"/logout?{urlencode({'error': '账号已停用，请联系管理员'})}"
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": location})
    return user


def sanitize_filename(filename: str) -> str:
    cleaned = Path(filename).name.replace("\x00", "")
    safe = "".join(ch for ch in cleaned if ch.isalnum() or ch in "._- ()[]")
    return safe.strip() or "upload.bin"


def save_upload(upload: UploadFile, document_id: int, version_number: int) -> tuple[str, int]:
    safe_name = sanitize_filename(upload.filename or "upload.bin")
    version_dir = UPLOAD_DIR / str(document_id)
    version_dir.mkdir(parents=True, exist_ok=True)
    stored_filename = f"v{version_number}_{uuid.uuid4().hex}_{safe_name}"
    destination = version_dir / stored_filename

    size = 0
    with destination.open("wb") as output:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                output.close()
                destination.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"File is larger than {MAX_UPLOAD_MB} MB",
                )
            output.write(chunk)
    upload.file.close()
    return stored_filename, size


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    configure_audit_file_handler(DATA_DIR)
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                parent_id INTEGER,
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(parent_id) REFERENCES categories(id) ON DELETE RESTRICT
            );

            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                category_id INTEGER,
                owner_id INTEGER NOT NULL,
                current_version_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(category_id) REFERENCES categories(id) ON DELETE SET NULL,
                FOREIGN KEY(owner_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(current_version_id) REFERENCES document_versions(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS document_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                version_number INTEGER NOT NULL,
                original_filename TEXT NOT NULL,
                stored_filename TEXT NOT NULL,
                content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
                size_bytes INTEGER NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                uploaded_by INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE,
                FOREIGN KEY(uploaded_by) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE(document_id, version_number)
            );
            """
        )
        initialize_access_request_schema(conn)
        initialize_audit_log_schema(conn)
        initialize_share_link_schema(conn)

        user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "is_admin" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
        if "is_active" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")

        category_columns = {row["name"] for row in conn.execute("PRAGMA table_info(categories)").fetchall()}
        if "parent_id" not in category_columns:
            conn.execute(
                "ALTER TABLE categories ADD COLUMN parent_id INTEGER REFERENCES categories(id) ON DELETE RESTRICT"
            )

        document_columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)").fetchall()}
        if "deleted_at" not in document_columns:
            conn.execute("ALTER TABLE documents ADD COLUMN deleted_at TEXT")

        admin_username = os.getenv("ADMIN_USERNAME", "admin")
        admin_password = os.getenv("ADMIN_PASSWORD", "admin123456")
        existing_admin = get_user_by_username(conn, admin_username)
        if not existing_admin:
            create_user(
                conn,
                admin_username,
                password_hash(admin_password),
                True,
                now_iso(),
            )
        elif not existing_admin["is_admin"]:
            update_user_admin_status(conn, existing_admin["id"], True)

        for name in ("合同", "制度", "项目资料", "财务", "其他"):
            conn.execute(
                "INSERT OR IGNORE INTO categories (name, description, created_at) VALUES (?, '', ?)",
                (name, now_iso()),
            )
        configure_search(conn, UPLOAD_DIR)
        conn.commit()


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/")
def home() -> RedirectResponse:
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/login")
def login_page(request: Request, error: str = "") -> Response:
    return templates.TemplateResponse("login.html", {"request": request, "error": error})


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)) -> Response:
    cleaned_username = username.strip()
    with get_db() as conn:
        user = get_user_by_username(conn, cleaned_username)
    if not user or not verify_password(password, user["password_hash"]):
        audit_username = cleaned_username.replace("\r", "").replace("\n", "")[:50] or "unknown"
        log_audit_action(
            request,
            {"username": audit_username},
            "login",
            "failed_invalid_credentials",
        )
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "用户名或密码不正确"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    if not user["is_active"]:
        log_audit_action(request, dict(user), "login", "denied_account_disabled")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "账号已停用，请联系管理员"},
            status_code=status.HTTP_403_FORBIDDEN,
        )
    log_audit_action(request, {"username": user["username"]}, "login", "success")
    return login_response(user["id"])


@app.get("/logout")
def logout(error: str = "") -> RedirectResponse:
    return logout_response(error)


def backup_status_path() -> Path:
    return Path(os.getenv("BACKUP_STATUS_FILE", str(DATA_DIR / "backup_status.json")))


def account_template(
    request: Request,
    user: dict[str, Any],
    success: str = "",
    error: str = "",
    response_status: int = status.HTTP_200_OK,
    user_page: int = 1,
) -> Response:
    managed_users = []
    pending_access_requests = []
    pending_request_count = 0
    user_pagination: dict[str, Any] = {}
    backup_overview: dict[str, Any] | None = None
    if is_admin_user(user):
        with get_db() as conn:
            total_count = count_users(conn)
            total_pages = max(1, (total_count + USER_PAGE_SIZE - 1) // USER_PAGE_SIZE)
            current_page = min(max(user_page, 1), total_pages)
            managed_users = list_users(conn, current_page)
            pending_access_requests = list_pending_access_requests(conn)
            pending_request_count = count_pending_access_requests(conn)
        backup_overview = build_backup_overview(backup_status_path(), DATA_DIR)
        user_pagination = {
            "page": current_page,
            "page_size": USER_PAGE_SIZE,
            "total_count": total_count,
            "total_pages": total_pages,
            "previous_url": account_url(current_page - 1) if current_page > 1 else "",
            "next_url": account_url(current_page + 1) if current_page < total_pages else "",
        }
    return templates.TemplateResponse(
        "account.html",
        {
            "request": request,
            "user": user,
            "managed_users": managed_users,
            "pending_access_requests": pending_access_requests,
            "pending_request_count": pending_request_count,
            "access_action_label": access_action_label,
            "user_pagination": user_pagination,
            "backup_overview": backup_overview,
            "success": success,
            "error": error,
        },
        status_code=response_status,
    )


@app.get("/account")
def account_page(
    request: Request,
    user_page: int = 1,
    success: str = "",
    error: str = "",
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    return account_template(request, user, success=success, error=error, user_page=user_page)


@app.post("/account/password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    try:
        validated_password = validate_password_pair(new_password, confirm_password)
    except ValueError as exc:
        return account_template(request, user, error=str(exc), response_status=status.HTTP_400_BAD_REQUEST)

    with get_db() as conn:
        db_user = get_user_by_id(conn, user["id"])
        if not db_user or not verify_password(current_password, db_user["password_hash"]):
            return account_template(
                request,
                user,
                error="当前密码不正确",
                response_status=status.HTTP_400_BAD_REQUEST,
            )
        update_user_password(conn, user["id"], password_hash(validated_password))
        conn.commit()

    log_audit_action(request, user, "change_password", f"user_id={user['id']}")
    return account_template(request, user, success="密码已更新")


@app.post("/account/users")
def create_managed_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    is_admin: str = Form("0"),
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    require_admin_user(user)
    try:
        cleaned_username = validate_username(username)
        validated_password = validate_password_pair(password, confirm_password)
    except ValueError as exc:
        return account_template(request, user, error=str(exc), response_status=status.HTTP_400_BAD_REQUEST)

    new_is_admin = is_admin == "1"
    with get_db() as conn:
        if get_user_by_username(conn, cleaned_username):
            return account_template(
                request,
                user,
                error="用户名已存在",
                response_status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            new_user_id = create_user(
                conn,
                cleaned_username,
                password_hash(validated_password),
                new_is_admin,
                now_iso(),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            logger.warning("Failed to create user: username=%s error=%s", cleaned_username, exc)
            return account_template(
                request,
                user,
                error="用户名已存在",
                response_status=status.HTTP_400_BAD_REQUEST,
            )

    log_audit_action(
        request,
        user,
        "create_user",
        f"user_id={new_user_id}; username={cleaned_username}; is_admin={int(new_is_admin)}",
    )
    return account_template(request, user, success="用户已新增")


@app.post("/account/users/{target_user_id}/admin")
def update_managed_user_admin(
    request: Request,
    target_user_id: int,
    is_admin: str = Form("0"),
    user_page: int = Form(1),
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    require_admin_user(user)
    new_is_admin = is_admin == "1"

    with get_db() as conn:
        target_user = get_user_by_id(conn, target_user_id)
        if not target_user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        try:
            validate_admin_status_change(target_user, new_is_admin, count_active_admin_users(conn))
        except ValueError as exc:
            return account_template(
                request,
                user,
                error=str(exc),
                response_status=status.HTTP_400_BAD_REQUEST,
                user_page=user_page,
            )
        update_user_admin_status(conn, target_user_id, new_is_admin)
        conn.commit()

    log_audit_action(
        request,
        user,
        "update_user_permission",
        f"user_id={target_user_id}; is_admin={int(new_is_admin)}",
    )
    response_user = dict(user)
    if target_user_id == user["id"]:
        response_user["is_admin"] = int(new_is_admin)
    return account_template(request, response_user, success="权限已更新", user_page=user_page)


@app.post("/account/users/{target_user_id}/active")
def update_managed_user_active(
    request: Request,
    target_user_id: int,
    is_active: str = Form("0"),
    user_page: int = Form(1),
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    require_admin_user(user)
    new_is_active = is_active == "1"

    with get_db() as conn:
        target_user = get_user_by_id(conn, target_user_id)
        if not target_user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        try:
            validate_user_active_status_change(
                target_user,
                user["id"],
                new_is_active,
                count_active_admin_users(conn),
            )
        except ValueError as exc:
            return account_template(
                request,
                user,
                error=str(exc),
                response_status=status.HTTP_400_BAD_REQUEST,
                user_page=user_page,
            )
        update_user_active_status(conn, target_user_id, new_is_active)
        conn.commit()

    log_audit_action(
        request,
        user,
        "update_user_status",
        f"user_id={target_user_id}; is_active={int(new_is_active)}",
    )
    message = "账号已启用" if new_is_active else "账号已停用"
    return account_template(request, user, success=message, user_page=user_page)


def audit_logs_url(
    username: str,
    action: str,
    start_date: str,
    end_date: str,
    page: int,
) -> str:
    params: list[tuple[str, str]] = []
    if username.strip():
        params.append(("user", username.strip()))
    if action.strip():
        params.append(("action", action.strip()))
    if start_date.strip():
        params.append(("start", start_date.strip()))
    if end_date.strip():
        params.append(("end", end_date.strip()))
    if page > 1:
        params.append(("page", str(page)))
    query = urlencode(params)
    return f"/admin/audit-logs?{query}" if query else "/admin/audit-logs"


@app.get("/admin/audit-logs")
def audit_logs_page(
    request: Request,
    username: str = Query("", alias="user"),
    action: str = "",
    start: str = "",
    end: str = "",
    page: int = 1,
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    require_admin_user(user)

    with get_db() as conn:
        total_count = count_audit_logs(conn, username, action, start, end)
        total_pages = max(1, (total_count + AUDIT_LOG_PAGE_SIZE - 1) // AUDIT_LOG_PAGE_SIZE)
        current_page = min(max(page, 1), total_pages)
        audit_logs = list_audit_logs(conn, current_page, username, action, start, end)
        action_options = list_audit_actions(conn)
        pending_request_count = count_pending_access_requests(conn)

    filters = {"user": username, "action": action, "start": start, "end": end}
    pagination = {
        "page": current_page,
        "page_size": AUDIT_LOG_PAGE_SIZE,
        "total_count": total_count,
        "total_pages": total_pages,
        "previous_url": audit_logs_url(username, action, start, end, current_page - 1)
        if current_page > 1
        else "",
        "next_url": audit_logs_url(username, action, start, end, current_page + 1)
        if current_page < total_pages
        else "",
    }

    return templates.TemplateResponse(
        "audit_logs.html",
        {
            "request": request,
            "user": user,
            "audit_logs": audit_logs,
            "action_options": action_options,
            "filters": filters,
            "pagination": pagination,
            "pending_request_count": pending_request_count,
        },
    )


@app.get("/dashboard")
def dashboard(
    request: Request,
    category_id: int | None = None,
    q: str = "",
    page: int = 1,
    success: str = "",
    error: str = "",
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    cleaned_query = q.strip()
    requested_page = max(page, 1)

    with get_db() as conn:
        matched_ids = find_matching_document_ids(conn, cleaned_query)
        total_count = count_documents(conn, category_id, cleaned_query, matched_ids)
        total_pages = max(1, (total_count + DOCUMENT_PAGE_SIZE - 1) // DOCUMENT_PAGE_SIZE)
        current_page = min(requested_page, total_pages)
        documents = build_document_access_flags(
            conn,
            user,
            list_documents(conn, category_id, cleaned_query, current_page, matched_ids=matched_ids),
        )
        search_contexts = (
            build_search_contexts(conn, [document["id"] for document in documents], cleaned_query)
            if matched_ids is not None
            else {}
        )
        categories = list_categories(conn)
        category_tree = build_category_tree(categories, category_id)
        category_options = flatten_category_tree(category_tree)
        pending_request_count = count_pending_access_requests(conn) if is_admin_user(user) else 0

    pagination = {
        "page": current_page,
        "page_size": DOCUMENT_PAGE_SIZE,
        "total_count": total_count,
        "total_pages": total_pages,
        "previous_url": dashboard_url(category_id, cleaned_query, current_page - 1) if current_page > 1 else "",
        "next_url": dashboard_url(category_id, cleaned_query, current_page + 1)
        if current_page < total_pages
        else "",
    }

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "documents": documents,
            "search_contexts": search_contexts,
            "category_tree": category_tree,
            "category_options": category_options,
            "pending_request_count": pending_request_count,
            "action_download": ACTION_DOWNLOAD,
            "active_category_id": category_id,
            "q": cleaned_query,
            "pagination": pagination,
            "max_upload_mb": MAX_UPLOAD_MB,
            "success": success,
            "error": error,
        },
    )


@app.post("/categories")
def create_category(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    parent_id: str = Form(""),
    active_category_id: str = Form(""),
    q: str = Form(""),
    page: int = Form(1),
    user: dict[str, Any] = Depends(get_current_user),
) -> RedirectResponse:
    return_category_id = parse_optional_int(active_category_id)
    return_page = parse_page(page)
    try:
        parsed_parent_id = parse_optional_int(parent_id)
    except ValueError:
        return RedirectResponse(
            url=dashboard_url(return_category_id, q, return_page, error="父文件夹无效"),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    with get_db() as conn:
        try:
            category_id = create_category_with_parent(
                conn,
                name,
                description,
                parsed_parent_id,
                now_iso(),
            )
        except ValueError as exc:
            logger.warning(
                "Failed to create category: user=%s parent_id=%s error=%s",
                user["username"],
                parsed_parent_id,
                exc,
            )
            return RedirectResponse(
                url=dashboard_url(return_category_id, q, return_page, error=str(exc)),
                status_code=status.HTTP_303_SEE_OTHER,
            )
        conn.commit()

    log_audit_action(
        request,
        user,
        "create_category",
        f"category_id={category_id}; parent_id={parsed_parent_id or ''}; name={name.strip()}",
    )
    return RedirectResponse(
        url=dashboard_url(category_id, q, 1, success="文件夹已创建"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/categories/{category_id}/delete")
def delete_category(
    request: Request,
    category_id: int,
    active_category_id: str = Form(""),
    q: str = Form(""),
    page: int = Form(1),
    user: dict[str, Any] = Depends(get_current_user),
) -> RedirectResponse:
    return_category_id = parse_optional_int(active_category_id)
    return_page = parse_page(page)

    with get_db() as conn:
        category = get_category(conn, category_id)
        if not category:
            return RedirectResponse(
                url=dashboard_url(return_category_id, q, return_page, error="文件夹不存在"),
                status_code=status.HTTP_303_SEE_OTHER,
            )
        try:
            validate_category_can_delete(
                count_documents_in_category(conn, category_id),
                count_child_categories(conn, category_id),
            )
        except ValueError as exc:
            return RedirectResponse(
                url=dashboard_url(return_category_id, q, return_page, error=str(exc)),
                status_code=status.HTTP_303_SEE_OTHER,
            )
        delete_category_record(conn, category_id)
        conn.commit()

    next_category_id = None if return_category_id == category_id else return_category_id
    log_audit_action(request, user, "delete_category", f"category_id={category_id}; name={category['name']}")
    return RedirectResponse(
        url=dashboard_url(next_category_id, q, return_page, success="文件夹已删除"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/documents/upload")
def upload_document(
    title: str = Form(""),
    category_id: str = Form(""),
    notes: str = Form(""),
    file: UploadFile = File(...),
    user: dict[str, Any] = Depends(get_current_user),
) -> RedirectResponse:
    original_filename = sanitize_filename(file.filename or "upload.bin")
    document_title = title.strip() or Path(original_filename).stem or original_filename
    parsed_category_id = parse_optional_int(category_id)
    created_at = now_iso()

    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO documents (title, category_id, owner_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (document_title, parsed_category_id, user["id"], created_at, created_at),
        )
        document_id = cursor.lastrowid

        stored_filename, size = save_upload(file, document_id, 1)
        version_cursor = conn.execute(
            """
            INSERT INTO document_versions (
                document_id, version_number, original_filename, stored_filename,
                content_type, size_bytes, notes, uploaded_by, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                1,
                original_filename,
                stored_filename,
                file.content_type or "application/octet-stream",
                size,
                notes.strip(),
                user["id"],
                created_at,
            ),
        )
        conn.execute(
            "UPDATE documents SET current_version_id = ? WHERE id = ?",
            (version_cursor.lastrowid, document_id),
        )
        conn.commit()

    sync_document_index(document_id)
    return RedirectResponse(url=f"/documents/{document_id}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/documents/batch-download")
async def batch_download_documents(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    form = await request.form()
    try:
        document_ids = parse_selected_document_ids(form.getlist("document_ids"))
    except ValueError as exc:
        return RedirectResponse(
            url=dashboard_url_from_form(form, error=str(exc)),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    with get_db() as conn:
        documents = build_document_access_flags(
            conn,
            user,
            list_current_versions_for_documents(conn, document_ids),
        )

    if len(documents) != len(document_ids) or any(not document["can_download"] for document in documents):
        return RedirectResponse(
            url=dashboard_url_from_form(form, error="选中文件中包含无权下载的文件，请先提交申请。"),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    try:
        zip_path, download_name = build_batch_download_zip(documents, UPLOAD_DIR, DATA_DIR / "tmp")
    except ValueError as exc:
        return RedirectResponse(
            url=dashboard_url_from_form(form, error=str(exc)),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except FileNotFoundError as exc:
        logger.warning("Stored file is missing for batch download: file=%s", exc)
        return RedirectResponse(
            url=dashboard_url_from_form(form, error="选中文件中有文件不存在，请检查后再试。"),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    encoded_name = quote(download_name)
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=download_name,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"},
        background=BackgroundTask(lambda path: Path(path).unlink(missing_ok=True), str(zip_path)),
    )


@app.post("/documents/batch-access-request")
async def batch_request_document_access(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> RedirectResponse:
    form = await request.form()
    try:
        document_ids = parse_selected_document_ids(form.getlist("document_ids"))
    except ValueError as exc:
        return RedirectResponse(
            url=dashboard_url_from_form(form, error=str(exc)),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    with get_db() as conn:
        documents = list_documents_by_ids(conn, document_ids)
        if len(documents) != len(document_ids):
            return RedirectResponse(
                url=dashboard_url_from_form(form, error="选中的文件无效，请刷新页面后重试。"),
                status_code=status.HTTP_303_SEE_OTHER,
            )
        result = submit_download_access_requests(conn, user, documents, now_iso())
        conn.commit()

    created_ids = result["created_request_ids"]
    if created_ids:
        log_audit_action(
            request,
            user,
            "batch_request_document_access",
            (
                f"request_ids={','.join(str(request_id) for request_id in created_ids)}; "
                f"document_ids={','.join(str(document_id) for document_id in document_ids)}"
            ),
        )
        message = f"已提交 {result['created_count']} 个预览/下载申请，请等待管理员处理"
    elif result["pending_count"]:
        message = "选中文件的申请已提交，请等待管理员处理"
    else:
        message = "选中文件已拥有预览/下载权限"

    return RedirectResponse(
        url=dashboard_url_from_form(form, success=message),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/documents/batch-delete")
async def batch_delete_documents(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> RedirectResponse:
    form = await request.form()
    try:
        document_ids = parse_selected_document_ids(form.getlist("document_ids"))
    except ValueError as exc:
        return RedirectResponse(
            url=dashboard_url_from_form(form, error=str(exc)),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    with get_db() as conn:
        documents = build_document_access_flags(
            conn,
            user,
            list_documents_by_ids(conn, document_ids),
        )
        if not documents:
            return RedirectResponse(
                url=dashboard_url_from_form(form, error="请选择有效文件"),
                status_code=status.HTTP_303_SEE_OTHER,
            )
        if len(documents) != len(document_ids) or any(not document["can_manage"] for document in documents):
            return RedirectResponse(
                url=dashboard_url_from_form(form, error="只能删除自己上传的文件。"),
                status_code=status.HTTP_303_SEE_OTHER,
            )
        deleted_count = soft_delete_documents_by_ids(
            conn,
            [document["id"] for document in documents],
            now_iso(),
        )
        conn.commit()

    for document in documents:
        remove_document_from_index(document["id"])
    log_audit_action(
        request,
        user,
        "batch_delete_documents",
        f"document_ids={','.join(str(document['id']) for document in documents)}; count={deleted_count}",
    )
    return RedirectResponse(
        url=dashboard_url_from_form(form, success=f"已删除 {deleted_count} 个文件"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


def get_document_or_404(conn: sqlite3.Connection, document_id: int) -> sqlite3.Row:
    document = get_document_detail(conn, document_id)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document


@app.post("/documents/{document_id}/access-requests")
def request_document_access(
    request: Request,
    document_id: int,
    action: str = Form(...),
    return_to: str = Form("/dashboard"),
    user: dict[str, Any] = Depends(get_current_user),
) -> RedirectResponse:
    with get_db() as conn:
        document = get_document_or_404(conn, document_id)
        try:
            request_id, message = submit_access_request(conn, user, document, action, now_iso())
        except ValueError as exc:
            return RedirectResponse(
                url=local_url_with_message(return_to, error=str(exc)),
                status_code=status.HTTP_303_SEE_OTHER,
            )
        conn.commit()

    if request_id is not None:
        log_audit_action(
            request,
            user,
            "request_document_access",
            f"request_id={request_id}; document_id={document_id}; action={action}",
        )
    return RedirectResponse(
        url=local_url_with_message(return_to, success=message),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/access-requests/{request_id}/revoke")
def revoke_access_request_route(
    request: Request,
    request_id: int,
    return_to: str = Form("/access-requests"),
    user: dict[str, Any] = Depends(get_current_user),
) -> RedirectResponse:
    with get_db() as conn:
        try:
            access_request = revoke_access_request_grant(conn, request_id, user, now_iso())
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            return RedirectResponse(
                url=local_url_with_message(return_to, error=str(exc)),
                status_code=status.HTTP_303_SEE_OTHER,
            )
        conn.commit()

    log_audit_action(
        request,
        user,
        "revoke_access_request",
        (
            f"request_id={request_id}; requester_id={access_request['requester_id']}; "
            f"document_id={access_request['document_id']}; action={access_request['action']}"
        ),
    )
    return RedirectResponse(
        url=local_url_with_message(return_to, success="权限已撤销"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/access-requests/{request_id}/{decision}")
def decide_access_request(
    request: Request,
    request_id: int,
    decision: str,
    validity: str = Form(""),
    user: dict[str, Any] = Depends(get_current_user),
) -> RedirectResponse:
    require_admin_user(user)
    decision_map = {"approve": "approved", "reject": "rejected"}
    normalized_decision = decision_map.get(decision)
    if not normalized_decision:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review action not found")

    expires_at = None
    if normalized_decision == "approved":
        try:
            validity_days = parse_validity_days(validity)
        except ValueError as exc:
            return RedirectResponse(
                url=local_url_with_message("/account#access-requests", error=str(exc)),
                status_code=status.HTTP_303_SEE_OTHER,
            )
        if validity_days:
            expires_at = (datetime.now(timezone.utc) + timedelta(days=validity_days)).isoformat(timespec="seconds")

    with get_db() as conn:
        try:
            access_request = review_access_request(
                conn,
                request_id,
                int(user["id"]),
                normalized_decision,
                now_iso(),
                expires_at,
            )
        except ValueError as exc:
            return RedirectResponse(
                url=local_url_with_message("/account#access-requests", error=str(exc)),
                status_code=status.HTTP_303_SEE_OTHER,
            )
        conn.commit()

    operation = "approve_access_request" if normalized_decision == "approved" else "reject_access_request"
    log_audit_action(
        request,
        user,
        operation,
        (
            f"request_id={request_id}; requester_id={access_request['requester_id']}; "
            f"document_id={access_request['document_id']}; action={access_request['action']}; "
            f"expires_at={expires_at or 'permanent'}"
        ),
    )
    result_text = "已接受" if normalized_decision == "approved" else "已拒绝"
    return RedirectResponse(
        url=local_url_with_message("/account#access-requests", success=f"申请{result_text}"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


def access_requests_url(username: str, status_filter: str, page: int) -> str:
    params: list[tuple[str, str]] = []
    if username.strip():
        params.append(("user", username.strip()))
    if status_filter.strip():
        params.append(("status", status_filter.strip()))
    if page > 1:
        params.append(("page", str(page)))
    query = urlencode(params)
    return f"/access-requests?{query}" if query else "/access-requests"


@app.get("/access-requests")
def access_requests_page(
    request: Request,
    username: str = Query("", alias="user"),
    status_filter: str = Query("", alias="status"),
    page: int = 1,
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    admin = is_admin_user(user)
    effective_username = username if admin else ""
    scoped_user_id = None if admin else int(user["id"])

    with get_db() as conn:
        total_count = count_access_requests(conn, effective_username, status_filter, scoped_user_id)
        total_pages = max(1, (total_count + ACCESS_REQUEST_PAGE_SIZE - 1) // ACCESS_REQUEST_PAGE_SIZE)
        current_page = min(max(page, 1), total_pages)
        access_requests = list_access_requests(conn, current_page, effective_username, status_filter, scoped_user_id)
        pending_request_count = count_pending_access_requests(conn) if admin else 0

    filters = {"user": effective_username, "status": status_filter}
    pagination = {
        "page": current_page,
        "page_size": ACCESS_REQUEST_PAGE_SIZE,
        "total_count": total_count,
        "total_pages": total_pages,
        "previous_url": access_requests_url(effective_username, status_filter, current_page - 1)
        if current_page > 1
        else "",
        "next_url": access_requests_url(effective_username, status_filter, current_page + 1)
        if current_page < total_pages
        else "",
    }

    return templates.TemplateResponse(
        "access_requests.html",
        {
            "request": request,
            "user": user,
            "access_requests": access_requests,
            "filters": filters,
            "pagination": pagination,
            "pending_request_count": pending_request_count,
            "access_action_label": access_action_label,
            "access_status_label": access_status_label,
            "is_grant_expired": is_grant_expired,
            "now": now_iso(),
        },
    )


@app.get("/recycle-bin")
def recycle_bin_page(
    request: Request,
    success: str = "",
    error: str = "",
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    admin = is_admin_user(user)
    retention_days = recycle_retention_days()

    with get_db() as conn:
        if retention_days > 0:
            purged_expired = purge_retention_expired_documents(conn, UPLOAD_DIR, retention_days)
            if purged_expired:
                logger.info("回收站自动清理超期文件：count=%s retention_days=%s", purged_expired, retention_days)
        documents = list_deleted_documents(conn, None if admin else int(user["id"]))
        pending_request_count = count_pending_access_requests(conn) if admin else 0

    return templates.TemplateResponse(
        "recycle_bin.html",
        {
            "request": request,
            "user": user,
            "documents": documents,
            "pending_request_count": pending_request_count,
            "success": success,
            "error": error,
        },
    )


@app.post("/documents/{document_id}/restore")
def restore_document_route(
    request: Request,
    document_id: int,
    user: dict[str, Any] = Depends(get_current_user),
) -> RedirectResponse:
    with get_db() as conn:
        document = get_document_detail(conn, document_id, include_deleted=True)
        if not document or not document["deleted_at"]:
            return RedirectResponse(
                url=local_url_with_message("/recycle-bin", error="文件不在回收站"),
                status_code=status.HTTP_303_SEE_OTHER,
            )
        if not can_recycle_document(user, document):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="只能恢复自己删除的文件",
            )
        restore_document(conn, document_id)
        conn.commit()

    sync_document_index(document_id)
    log_audit_action(request, user, "restore_document", f"document_id={document_id}; title={document['title']}")
    return RedirectResponse(
        url=local_url_with_message("/recycle-bin", success="文件已恢复"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/documents/{document_id}/purge")
def purge_document_route(
    request: Request,
    document_id: int,
    user: dict[str, Any] = Depends(get_current_user),
) -> RedirectResponse:
    with get_db() as conn:
        document = get_document_detail(conn, document_id, include_deleted=True)
        if not document or not document["deleted_at"]:
            return RedirectResponse(
                url=local_url_with_message("/recycle-bin", error="文件不在回收站"),
                status_code=status.HTTP_303_SEE_OTHER,
            )
        if not can_recycle_document(user, document):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="只能彻底删除自己删除的文件",
            )
        purge_documents_completely(conn, UPLOAD_DIR, [document_id])

    log_audit_action(request, user, "purge_document", f"document_id={document_id}; title={document['title']}")
    return RedirectResponse(
        url=local_url_with_message("/recycle-bin", success="文件已彻底删除"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/recycle-bin/clear")
def clear_recycle_bin(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> RedirectResponse:
    admin = is_admin_user(user)
    with get_db() as conn:
        documents = list_deleted_documents(conn, None if admin else int(user["id"]))
        document_ids = [int(document["id"]) for document in documents]
        purged_count = purge_documents_completely(conn, UPLOAD_DIR, document_ids)

    log_audit_action(
        request,
        user,
        "clear_recycle_bin",
        f"count={purged_count}; scope={'all' if admin else 'own'}",
    )
    return RedirectResponse(
        url=local_url_with_message("/recycle-bin", success=f"已彻底删除 {purged_count} 个文件"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/documents/{document_id}")
def document_detail(
    request: Request,
    document_id: int,
    success: str = "",
    error: str = "",
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    with get_db() as conn:
        document = build_document_access_flags_one(conn, user, get_document_or_404(conn, document_id))
        versions = list_document_versions(conn, document_id)
        categories = list_categories(conn)
        category_options = flatten_category_tree(build_category_tree(categories, document["category_id"]))
        pending_request_count = count_pending_access_requests(conn) if is_admin_user(user) else 0
        share_links = list_share_links_for_document(conn, document_id) if document["can_manage"] else []

    return templates.TemplateResponse(
        "document_detail.html",
        {
            "request": request,
            "user": user,
            "document": document,
            "versions": versions,
            "categories": category_options,
            "pending_request_count": pending_request_count,
            "share_links": share_links,
            "share_link_status_label": share_link_status_label,
            "now": now_iso(),
            "action_download": ACTION_DOWNLOAD,
            "action_upload_version": ACTION_UPLOAD_VERSION,
            "max_upload_mb": MAX_UPLOAD_MB,
            "success": success,
            "error": error,
        },
    )


@app.get("/documents/{document_id}/preview")
def document_preview(
    request: Request,
    document_id: int,
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    with get_db() as conn:
        document = build_document_access_flags_one(conn, user, get_document_or_404(conn, document_id))
        if not document["can_download"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="请先申请文件下载权限")
        version = get_current_version(conn, document_id)
        pending_request_count = count_pending_access_requests(conn) if is_admin_user(user) else 0

    if not version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document has no version")

    try:
        preview = build_preview_context(document_id, version, UPLOAD_DIR)
    except FileNotFoundError as exc:
        logger.warning("Stored file is missing for preview: document_id=%s file=%s", document_id, exc)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stored file is missing") from exc

    return templates.TemplateResponse(
        "document_preview.html",
        {
            "request": request,
            "user": user,
            "document": document,
            "version": version,
            "preview": preview,
            "pending_request_count": pending_request_count,
        },
    )


@app.get("/documents/{document_id}/preview/file")
def preview_current_version_file(
    document_id: int,
    user: dict[str, Any] = Depends(get_current_user),
) -> FileResponse:
    with get_db() as conn:
        document = build_document_access_flags_one(conn, user, get_document_or_404(conn, document_id))
        if not document["can_download"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="请先申请文件下载权限")
        version = get_current_version(conn, document_id)
    if not version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document has no version")
    return version_row_file_response(document_id, version, "inline")


@app.post("/documents/{document_id}/metadata")
def update_document_metadata(
    document_id: int,
    title: str = Form(...),
    category_id: str = Form(""),
    user: dict[str, Any] = Depends(get_current_user),
) -> RedirectResponse:
    cleaned_title = title.strip()
    if not cleaned_title:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Title is required")
    with get_db() as conn:
        document = build_document_access_flags_one(conn, user, get_document_or_404(conn, document_id))
        if not document["can_manage"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只能修改自己上传的文件")
        conn.execute(
            "UPDATE documents SET title = ?, category_id = ?, updated_at = ? WHERE id = ?",
            (cleaned_title, parse_optional_int(category_id), now_iso(), document_id),
        )
        conn.commit()
    sync_document_index(document_id)
    return RedirectResponse(url=f"/documents/{document_id}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/documents/{document_id}/delete")
def delete_document(
    request: Request,
    document_id: int,
    user: dict[str, Any] = Depends(get_current_user),
) -> RedirectResponse:
    with get_db() as conn:
        document = build_document_access_flags_one(conn, user, get_document_or_404(conn, document_id))
        if not document["can_manage"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只能删除自己上传的文件")
        document_title = document["title"]
        deleted_count = soft_delete_documents_by_ids(conn, [document_id], now_iso())
        conn.commit()

    remove_document_from_index(document_id)
    log_audit_action(request, user, "delete_document", f"document_id={document_id}; title={document_title}")
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/documents/{document_id}/versions")
def upload_new_version(
    document_id: int,
    notes: str = Form(""),
    file: UploadFile = File(...),
    user: dict[str, Any] = Depends(get_current_user),
) -> RedirectResponse:
    created_at = now_iso()
    original_filename = sanitize_filename(file.filename or "upload.bin")

    with get_db() as conn:
        document = build_document_access_flags_one(conn, user, get_document_or_404(conn, document_id))
        if not document["can_upload_version"]:
            return RedirectResponse(
                url=local_url_with_message(
                    f"/documents/{document_id}",
                    error="无权覆盖这个文件的新版本，请先提交申请。",
                ),
                status_code=status.HTTP_303_SEE_OTHER,
            )
        latest = conn.execute(
            "SELECT COALESCE(MAX(version_number), 0) AS latest_version FROM document_versions WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        version_number = int(latest["latest_version"]) + 1
        stored_filename, size = save_upload(file, document_id, version_number)
        cursor = conn.execute(
            """
            INSERT INTO document_versions (
                document_id, version_number, original_filename, stored_filename,
                content_type, size_bytes, notes, uploaded_by, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                version_number,
                original_filename,
                stored_filename,
                file.content_type or "application/octet-stream",
                size,
                notes.strip(),
                user["id"],
                created_at,
            ),
        )
        conn.execute(
            "UPDATE documents SET current_version_id = ?, updated_at = ? WHERE id = ?",
            (cursor.lastrowid, created_at, document_id),
        )
        conn.commit()

    sync_document_index(document_id)
    return RedirectResponse(url=f"/documents/{document_id}", status_code=status.HTTP_303_SEE_OTHER)


def version_row_file_response(document_id: int, version: sqlite3.Row, disposition: str) -> FileResponse:
    file_path = UPLOAD_DIR / str(document_id) / version["stored_filename"]
    if not file_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stored file is missing")

    download_name = version["original_filename"]
    encoded_name = quote(download_name)
    headers = {"Content-Disposition": f"{disposition}; filename*=UTF-8''{encoded_name}"}
    return FileResponse(
        file_path,
        media_type=version["content_type"] or "application/octet-stream",
        filename=download_name,
        headers=headers,
    )


def version_file_response(conn: sqlite3.Connection, document_id: int, version_id: int) -> FileResponse:
    get_document_or_404(conn, document_id)
    version = get_version(conn, document_id, version_id)
    if not version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")
    return version_row_file_response(document_id, version, "attachment")


@app.get("/documents/{document_id}/download")
def download_current_version(
    document_id: int,
    user: dict[str, Any] = Depends(get_current_user),
) -> FileResponse:
    with get_db() as conn:
        document = build_document_access_flags_one(conn, user, get_document_or_404(conn, document_id))
        if not document["can_download"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="请先申请文件下载权限")
        if not document["current_version_id"]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document has no version")
        return version_file_response(conn, document_id, document["current_version_id"])


@app.get("/documents/{document_id}/versions/{version_id}/download")
def download_version(
    document_id: int,
    version_id: int,
    user: dict[str, Any] = Depends(get_current_user),
) -> FileResponse:
    with get_db() as conn:
        document = build_document_access_flags_one(conn, user, get_document_or_404(conn, document_id))
        if not document["can_download"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="请先申请文件下载权限")
        return version_file_response(conn, document_id, version_id)


@app.post("/documents/{document_id}/shares")
def create_document_share(
    request: Request,
    document_id: int,
    password: str = Form(""),
    validity: str = Form(""),
    allow_download: str = Form("0"),
    user: dict[str, Any] = Depends(get_current_user),
) -> RedirectResponse:
    allow = allow_download == "1"
    with get_db() as conn:
        document = get_document_or_404(conn, document_id)
        try:
            token = create_document_share_link(conn, user, document, password, validity, allow, now_iso())
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            return RedirectResponse(
                url=local_url_with_message(f"/documents/{document_id}", error=str(exc)),
                status_code=status.HTTP_303_SEE_OTHER,
            )
        conn.commit()

    log_audit_action(
        request,
        user,
        "create_share_link",
        (
            f"document_id={document_id}; allow_download={int(allow)}; "
            f"validity={validity.strip() or 'permanent'}; has_password={int(bool(password))}"
        ),
    )
    return RedirectResponse(
        url=local_url_with_message(f"/documents/{document_id}", success=f"分享链接已创建：/share/{token}"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/shares/{share_id}/revoke")
def revoke_share(
    request: Request,
    share_id: int,
    return_to: str = Form("/dashboard"),
    user: dict[str, Any] = Depends(get_current_user),
) -> RedirectResponse:
    with get_db() as conn:
        try:
            share = revoke_document_share_link(conn, share_id, user, now_iso())
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            return RedirectResponse(
                url=local_url_with_message(return_to, error=str(exc)),
                status_code=status.HTTP_303_SEE_OTHER,
            )
        conn.commit()

    log_audit_action(request, user, "revoke_share_link", f"share_id={share_id}; document_id={share['document_id']}")
    return RedirectResponse(
        url=local_url_with_message(return_to, success="分享链接已撤销"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


SHARE_COOKIE_PREFIX = "share_access_"


def resolve_public_share(conn: sqlite3.Connection, token: str) -> sqlite3.Row:
    share = get_active_share_link(conn, token, now_iso())
    if not share:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分享链接不存在或已失效")
    return share


def share_access_granted(request: Request, share: sqlite3.Row) -> bool:
    if not share["password_hash"]:
        return True
    cookie_value = request.cookies.get(f"{SHARE_COOKIE_PREFIX}{share['token']}")
    return bool(cookie_value) and unsign_value(cookie_value) == share["token"]


def log_share_access(request: Request, share: sqlite3.Row, detail: str) -> None:
    log_audit_action(
        request,
        {"username": "anonymous"},
        "share_access",
        f"document_id={share['document_id']}; share_id={share['id']}; {detail}",
    )


@app.get("/share/{token}")
def public_share_view(request: Request, token: str) -> Response:
    with get_db() as conn:
        share = resolve_public_share(conn, token)
        if not share_access_granted(request, share):
            return templates.TemplateResponse(
                "share_password.html",
                {"request": request, "token": token, "error": ""},
            )
        version = get_current_version(conn, share["document_id"])

    if not version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分享的文件没有可预览的版本")
    try:
        preview = build_preview_context(share["document_id"], version, UPLOAD_DIR)
    except FileNotFoundError as exc:
        logger.warning("Stored file is missing for share: share_id=%s file=%s", share["id"], exc)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stored file is missing") from exc

    log_share_access(request, share, "view")
    return templates.TemplateResponse(
        "share_view.html",
        {
            "request": request,
            "share": share,
            "token": token,
            "version": version,
            "preview": preview,
        },
    )


@app.post("/share/{token}")
def public_share_password(request: Request, token: str, password: str = Form("")) -> Response:
    with get_db() as conn:
        share = resolve_public_share(conn, token)

    if not share["password_hash"]:
        return RedirectResponse(url=f"/share/{token}", status_code=status.HTTP_303_SEE_OTHER)
    if not verify_share_password(share["password_hash"], password):
        log_share_access(request, share, "failed_password")
        return templates.TemplateResponse(
            "share_password.html",
            {"request": request, "token": token, "error": "密码不正确"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    response = RedirectResponse(url=f"/share/{token}", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        f"{SHARE_COOKIE_PREFIX}{token}",
        sign_value(token),
        httponly=True,
        secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
        samesite="lax",
        max_age=60 * 60,
    )
    return response


def shared_version_response(request: Request, token: str, disposition: str) -> FileResponse:
    with get_db() as conn:
        share = resolve_public_share(conn, token)
        if not share_access_granted(request, share):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分享链接不存在或已失效")
        if disposition == "attachment" and not share["allow_download"]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="该分享链接不允许下载")
        version = get_current_version(conn, share["document_id"])

    if not version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分享的文件没有可下载的版本")
    if disposition == "attachment":
        log_audit_action(
            request,
            {"username": "anonymous"},
            "share_download",
            f"document_id={share['document_id']}; share_id={share['id']}",
        )
    return version_row_file_response(share["document_id"], version, disposition)


@app.get("/share/{token}/file")
def public_share_file(request: Request, token: str) -> FileResponse:
    return shared_version_response(request, token, "inline")


@app.get("/share/{token}/download")
def public_share_download(request: Request, token: str) -> FileResponse:
    return shared_version_response(request, token, "attachment")
