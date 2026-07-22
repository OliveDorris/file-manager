from fastapi.testclient import TestClient

import app
import services.search_service as search_service


def configure_temp_app(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(app, "UPLOAD_DIR", tmp_path / "data" / "uploads")
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "data" / "file_manager.sqlite3")
    app.init_db()


def admin_user():
    with app.get_db() as conn:
        return conn.execute("SELECT id, is_admin FROM users WHERE username = ?", ("admin",)).fetchone()


def authenticated_client(user_id: int, **client_kwargs) -> TestClient:
    client = TestClient(app.app, **client_kwargs)
    client.cookies.set(app.SESSION_COOKIE, app.sign_value(str(user_id)))
    return client


def create_test_user(username: str, is_admin: bool = False):
    with app.get_db() as conn:
        user_id = app.create_user(
            conn,
            username,
            app.password_hash("Password123"),
            is_admin,
            app.now_iso(),
        )
        conn.commit()
        return conn.execute(
            "SELECT id, username, is_admin FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()


def insert_document(
    title: str,
    user_id: int,
    category_id: int | None = None,
    filename: str = "sample.txt",
    content: str = "sample",
) -> int:
    created_at = app.now_iso()
    with app.get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO documents (title, category_id, owner_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (title, category_id, user_id, created_at, created_at),
        )
        document_id = cursor.lastrowid
        document_dir = app.UPLOAD_DIR / str(document_id)
        document_dir.mkdir(parents=True)
        stored_filename = filename
        (document_dir / stored_filename).write_text(content, encoding="utf-8")
        version_cursor = conn.execute(
            """
            INSERT INTO document_versions (
                document_id, version_number, original_filename, stored_filename,
                content_type, size_bytes, notes, uploaded_by, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (document_id, 1, filename, stored_filename, "text/plain", len(content), "", user_id, created_at),
        )
        conn.execute(
            "UPDATE documents SET current_version_id = ? WHERE id = ?",
            (version_cursor.lastrowid, document_id),
        )
        conn.commit()
        if search_service.FTS_ENABLED:
            search_service.index_document(conn, app.UPLOAD_DIR, int(document_id))
            conn.commit()
    return int(document_id)


def mark_document_deleted(document_id: int, deleted_at: str | None = None) -> None:
    with app.get_db() as conn:
        conn.execute(
            "UPDATE documents SET deleted_at = ? WHERE id = ?",
            (deleted_at or app.now_iso(), document_id),
        )
        conn.commit()
