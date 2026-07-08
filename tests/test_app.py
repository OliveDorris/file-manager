from fastapi.testclient import TestClient

import app


def test_login_page_loads(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(app, "UPLOAD_DIR", tmp_path / "data" / "uploads")
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "data" / "file_manager.sqlite3")
    app.init_db()

    client = TestClient(app.app)
    response = client.get("/login")

    assert response.status_code == 200
    assert "登录" in response.text


def test_delete_document_removes_database_rows_and_files(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(app, "UPLOAD_DIR", tmp_path / "data" / "uploads")
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "data" / "file_manager.sqlite3")
    app.init_db()

    document_dir = app.UPLOAD_DIR / "1"
    document_dir.mkdir(parents=True)
    (document_dir / "sample.txt").write_text("sample", encoding="utf-8")

    with app.get_db() as conn:
        user = conn.execute("SELECT id FROM users WHERE username = ?", ("admin",)).fetchone()
        cursor = conn.execute(
            """
            INSERT INTO documents (title, category_id, owner_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("Sample", None, user["id"], app.now_iso(), app.now_iso()),
        )
        document_id = cursor.lastrowid
        version_cursor = conn.execute(
            """
            INSERT INTO document_versions (
                document_id, version_number, original_filename, stored_filename,
                content_type, size_bytes, notes, uploaded_by, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (document_id, 1, "sample.txt", "sample.txt", "text/plain", 6, "", user["id"], app.now_iso()),
        )
        conn.execute(
            "UPDATE documents SET current_version_id = ? WHERE id = ?",
            (version_cursor.lastrowid, document_id),
        )
        conn.commit()

    client = TestClient(app.app)
    client.cookies.set(app.SESSION_COOKIE, app.sign_value(str(user["id"])))
    response = client.post(f"/documents/{document_id}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert not document_dir.exists()

    with app.get_db() as conn:
        document_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        version_count = conn.execute("SELECT COUNT(*) FROM document_versions").fetchone()[0]

    assert document_count == 0
    assert version_count == 0
