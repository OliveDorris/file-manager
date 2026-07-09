from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from repositories.document_repository import (
    DOCUMENT_PAGE_SIZE,
    count_documents,
    get_current_version,
    get_document_detail,
    get_version,
    list_categories,
    list_document_versions,
    list_documents,
)
from repositories.user_repository import (
    count_admin_users,
    create_user,
    get_user_by_id,
    get_user_by_username,
    list_users,
    update_user_admin_status,
    update_user_password,
)
from services.preview_service import build_preview_context
from services.user_service import (
    validate_admin_status_change,
    validate_password_pair,
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
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


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
    audit_logger.info(
        "user=%s time=%s ip=%s operation=%s target=%s",
        user.get("username", "unknown"),
        now_iso(),
        client_ip(request),
        operation,
        target,
    )


def parse_optional_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def is_admin_user(user: dict[str, Any]) -> bool:
    return bool(user.get("is_admin"))


def require_admin_user(user: dict[str, Any]) -> None:
    if not is_admin_user(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin permission is required")


def dashboard_url(category_id: int | None, q: str, page: int) -> str:
    params: list[tuple[str, str]] = []
    if category_id:
        params.append(("category_id", str(category_id)))
    if q.strip():
        params.append(("q", q.strip()))
    if page > 1:
        params.append(("page", str(page)))
    query = urlencode(params)
    return f"/dashboard?{query}" if query else "/dashboard"


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


def logout_response() -> RedirectResponse:
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE)
    return response


def get_current_user(request: Request) -> dict[str, Any]:
    cookie_value = request.cookies.get(SESSION_COOKIE)
    if not cookie_value:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    user_id = unsign_value(cookie_value)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    with get_db() as conn:
        user = row_to_dict(
            conn.execute(
                "SELECT id, username, is_admin, created_at FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        )
    if not user:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
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
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
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

        user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "is_admin" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")

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
        conn.commit()


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/")
def home() -> RedirectResponse:
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/login")
def login_page(request: Request) -> Response:
    return templates.TemplateResponse("login.html", {"request": request, "error": ""})


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)) -> Response:
    with get_db() as conn:
        user = conn.execute(
            "SELECT id, username, password_hash FROM users WHERE username = ?",
            (username.strip(),),
        ).fetchone()
    if not user or not verify_password(password, user["password_hash"]):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "用户名或密码不正确"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    log_audit_action(request, {"username": user["username"]}, "login", "success")
    return login_response(user["id"])


@app.get("/logout")
def logout() -> RedirectResponse:
    return logout_response()


def account_template(
    request: Request,
    user: dict[str, Any],
    success: str = "",
    error: str = "",
    response_status: int = status.HTTP_200_OK,
) -> Response:
    managed_users = []
    if is_admin_user(user):
        with get_db() as conn:
            managed_users = list_users(conn)
    return templates.TemplateResponse(
        "account.html",
        {
            "request": request,
            "user": user,
            "managed_users": managed_users,
            "success": success,
            "error": error,
        },
        status_code=response_status,
    )


@app.get("/account")
def account_page(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    return account_template(request, user)


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
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    require_admin_user(user)
    new_is_admin = is_admin == "1"

    with get_db() as conn:
        target_user = get_user_by_id(conn, target_user_id)
        if not target_user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        try:
            validate_admin_status_change(target_user, new_is_admin, count_admin_users(conn))
        except ValueError as exc:
            return account_template(request, user, error=str(exc), response_status=status.HTTP_400_BAD_REQUEST)
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
    return account_template(request, response_user, success="权限已更新")


@app.get("/dashboard")
def dashboard(
    request: Request,
    category_id: int | None = None,
    q: str = "",
    page: int = 1,
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    cleaned_query = q.strip()
    requested_page = max(page, 1)

    with get_db() as conn:
        total_count = count_documents(conn, category_id, cleaned_query)
        total_pages = max(1, (total_count + DOCUMENT_PAGE_SIZE - 1) // DOCUMENT_PAGE_SIZE)
        current_page = min(requested_page, total_pages)
        documents = list_documents(conn, category_id, cleaned_query, current_page)
        categories = list_categories(conn)

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
            "categories": categories,
            "active_category_id": category_id,
            "q": cleaned_query,
            "pagination": pagination,
            "max_upload_mb": MAX_UPLOAD_MB,
        },
    )


@app.post("/categories")
def create_category(
    name: str = Form(...),
    description: str = Form(""),
    user: dict[str, Any] = Depends(get_current_user),
) -> RedirectResponse:
    del user
    cleaned_name = name.strip()
    if not cleaned_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Category name is required")
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO categories (name, description, created_at) VALUES (?, ?, ?)",
            (cleaned_name, description.strip(), now_iso()),
        )
        conn.commit()
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)


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

    return RedirectResponse(url=f"/documents/{document_id}", status_code=status.HTTP_303_SEE_OTHER)


def get_document_or_404(conn: sqlite3.Connection, document_id: int) -> sqlite3.Row:
    document = get_document_detail(conn, document_id)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document


@app.get("/documents/{document_id}")
def document_detail(
    request: Request,
    document_id: int,
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    with get_db() as conn:
        document = get_document_or_404(conn, document_id)
        versions = list_document_versions(conn, document_id)
        categories = list_categories(conn)

    return templates.TemplateResponse(
        "document_detail.html",
        {
            "request": request,
            "user": user,
            "document": document,
            "versions": versions,
            "categories": categories,
            "max_upload_mb": MAX_UPLOAD_MB,
        },
    )


@app.get("/documents/{document_id}/preview")
def document_preview(
    request: Request,
    document_id: int,
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    with get_db() as conn:
        document = get_document_or_404(conn, document_id)
        version = get_current_version(conn, document_id)

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
        },
    )


@app.get("/documents/{document_id}/preview/file")
def preview_current_version_file(
    document_id: int,
    user: dict[str, Any] = Depends(get_current_user),
) -> FileResponse:
    del user
    with get_db() as conn:
        get_document_or_404(conn, document_id)
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
    del user
    cleaned_title = title.strip()
    if not cleaned_title:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Title is required")
    with get_db() as conn:
        get_document_or_404(conn, document_id)
        conn.execute(
            "UPDATE documents SET title = ?, category_id = ?, updated_at = ? WHERE id = ?",
            (cleaned_title, parse_optional_int(category_id), now_iso(), document_id),
        )
        conn.commit()
    return RedirectResponse(url=f"/documents/{document_id}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/documents/{document_id}/delete")
def delete_document(
    request: Request,
    document_id: int,
    user: dict[str, Any] = Depends(get_current_user),
) -> RedirectResponse:
    with get_db() as conn:
        document = get_document_or_404(conn, document_id)
        document_title = document["title"]
        conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        conn.commit()

    document_dir = UPLOAD_DIR / str(document_id)
    if document_dir.exists():
        shutil.rmtree(document_dir)

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
        get_document_or_404(conn, document_id)
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
    del user
    with get_db() as conn:
        document = get_document_or_404(conn, document_id)
        if not document["current_version_id"]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document has no version")
        return version_file_response(conn, document_id, document["current_version_id"])


@app.get("/documents/{document_id}/versions/{version_id}/download")
def download_version(
    document_id: int,
    version_id: int,
    user: dict[str, Any] = Depends(get_current_user),
) -> FileResponse:
    del user
    with get_db() as conn:
        return version_file_response(conn, document_id, version_id)
