from fastapi.testclient import TestClient

import app


def configure_temp_app(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(app, "UPLOAD_DIR", tmp_path / "data" / "uploads")
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "data" / "file_manager.sqlite3")
    app.init_db()


def admin_user():
    with app.get_db() as conn:
        return conn.execute("SELECT id FROM users WHERE username = ?", ("admin",)).fetchone()


def authenticated_client(user_id: int) -> TestClient:
    client = TestClient(app.app)
    client.cookies.set(app.SESSION_COOKIE, app.sign_value(str(user_id)))
    return client


def test_login_page_loads(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)

    client = TestClient(app.app)
    response = client.get("/login")

    assert response.status_code == 200
    assert "登录" in response.text


def test_delete_document_removes_database_rows_and_files(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)

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

    client = authenticated_client(user["id"])
    response = client.post(f"/documents/{document_id}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert not document_dir.exists()

    with app.get_db() as conn:
        document_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        version_count = conn.execute("SELECT COUNT(*) FROM document_versions").fetchone()[0]

    assert document_count == 0
    assert version_count == 0


def test_dashboard_paginates_documents_to_ten_per_page(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()

    with app.get_db() as conn:
        for index in range(12):
            created_at = f"2026-07-08T00:{index:02d}:00+00:00"
            cursor = conn.execute(
                """
                INSERT INTO documents (title, category_id, owner_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (f"Doc {index}", None, user["id"], created_at, created_at),
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
                (
                    document_id,
                    1,
                    f"doc-{index}.txt",
                    f"doc-{index}.txt",
                    "text/plain",
                    10,
                    "",
                    user["id"],
                    created_at,
                ),
            )
            conn.execute(
                "UPDATE documents SET current_version_id = ? WHERE id = ?",
                (version_cursor.lastrowid, document_id),
            )
        conn.commit()

    client = authenticated_client(user["id"])
    first_page = client.get("/dashboard")
    second_page = client.get("/dashboard?page=2")

    assert first_page.status_code == 200
    assert "共 12 条，每页最多 10 条" in first_page.text
    assert "第 1 / 2 页" in first_page.text
    assert "Doc 11" in first_page.text
    assert "Doc 0" not in first_page.text

    assert second_page.status_code == 200
    assert "第 2 / 2 页" in second_page.text
    assert "Doc 1" in second_page.text
    assert "Doc 0" in second_page.text


def test_text_preview_loads_current_version_inline(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()

    with app.get_db() as conn:
        created_at = app.now_iso()
        cursor = conn.execute(
            """
            INSERT INTO documents (title, category_id, owner_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("Preview sample", None, user["id"], created_at, created_at),
        )
        document_id = cursor.lastrowid
        document_dir = app.UPLOAD_DIR / str(document_id)
        document_dir.mkdir(parents=True)
        (document_dir / "sample.txt").write_text("hello preview", encoding="utf-8")
        version_cursor = conn.execute(
            """
            INSERT INTO document_versions (
                document_id, version_number, original_filename, stored_filename,
                content_type, size_bytes, notes, uploaded_by, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (document_id, 1, "sample.txt", "sample.txt", "text/plain", 13, "", user["id"], created_at),
        )
        conn.execute(
            "UPDATE documents SET current_version_id = ? WHERE id = ?",
            (version_cursor.lastrowid, document_id),
        )
        conn.commit()

    client = authenticated_client(user["id"])
    preview = client.get(f"/documents/{document_id}/preview")
    inline_file = client.get(f"/documents/{document_id}/preview/file")

    assert preview.status_code == 200
    assert "hello preview" in preview.text
    assert "版本管理" in preview.text
    assert inline_file.status_code == 200
    assert inline_file.headers["content-disposition"].startswith("inline")
