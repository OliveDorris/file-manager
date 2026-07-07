from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = Path(os.getenv("DATABASE_PATH", DATA_DIR / "file_manager.sqlite3"))
SECRET_KEY = os.getenv("SECRET_KEY", "change-this-secret-key-in-production")
SESSION_COOKIE = "file_manager_session"
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "100"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024


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


def parse_optional_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


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
        user = row_to_dict(conn.execute("SELECT id, username, created_at FROM users WHERE id = ?", (user_id,)).fetchone())
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

        admin_username = os.getenv("ADMIN_USERNAME", "admin")
        admin_password = os.getenv("ADMIN_PASSWORD", "admin123456")
        existing_admin = conn.execute("SELECT id FROM users WHERE username = ?", (admin_username,)).fetchone()
        if not existing_admin:
            conn.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (admin_username, password_hash(admin_password), now_iso()),
            )

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
        user = conn.execute("SELECT id, password_hash FROM users WHERE username = ?", (username.strip(),)).fetchone()
    if not user or not verify_password(password, user["password_hash"]):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "用户名或密码不正确"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    return login_response(user["id"])


@app.get("/logout")
def logout() -> RedirectResponse:
    return logout_response()


@app.get("/dashboard")
def dashboard(
    request: Request,
    category_id: int | None = None,
    q: str = "",
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    filters = []
    params: list[Any] = []
    if category_id:
        filters.append("d.category_id = ?")
        params.append(category_id)
    if q.strip():
        filters.append("(d.title LIKE ? OR v.original_filename LIKE ?)")
        like = f"%{q.strip()}%"
        params.extend([like, like])
    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

    with get_db() as conn:
        documents = conn.execute(
            f"""
            SELECT
                d.id, d.title, d.created_at, d.updated_at,
                c.name AS category_name,
                v.original_filename, v.size_bytes, v.version_number
            FROM documents d
            LEFT JOIN categories c ON c.id = d.category_id
            LEFT JOIN document_versions v ON v.id = d.current_version_id
            {where_clause}
            ORDER BY d.updated_at DESC
            """,
            params,
        ).fetchall()
        categories = conn.execute("SELECT id, name FROM categories ORDER BY name").fetchall()

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "documents": documents,
            "categories": categories,
            "active_category_id": category_id,
            "q": q,
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
    document = conn.execute(
        """
        SELECT d.*, c.name AS category_name, u.username AS owner_name
        FROM documents d
        LEFT JOIN categories c ON c.id = d.category_id
        LEFT JOIN users u ON u.id = d.owner_id
        WHERE d.id = ?
        """,
        (document_id,),
    ).fetchone()
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
        versions = conn.execute(
            """
            SELECT v.*, u.username AS uploaded_by_name
            FROM document_versions v
            LEFT JOIN users u ON u.id = v.uploaded_by
            WHERE v.document_id = ?
            ORDER BY v.version_number DESC
            """,
            (document_id,),
        ).fetchall()
        categories = conn.execute("SELECT id, name FROM categories ORDER BY name").fetchall()

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


def version_file_response(conn: sqlite3.Connection, document_id: int, version_id: int) -> FileResponse:
    get_document_or_404(conn, document_id)
    version = conn.execute(
        "SELECT * FROM document_versions WHERE id = ? AND document_id = ?",
        (version_id, document_id),
    ).fetchone()
    if not version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")
    file_path = UPLOAD_DIR / str(document_id) / version["stored_filename"]
    if not file_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stored file is missing")

    download_name = version["original_filename"]
    encoded_name = quote(download_name)
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"}
    return FileResponse(
        file_path,
        media_type=version["content_type"] or "application/octet-stream",
        filename=download_name,
        headers=headers,
    )


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
