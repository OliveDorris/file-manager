from io import BytesIO
import re
import sqlite3
import zipfile

from fastapi.testclient import TestClient

import app


def configure_temp_app(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(app, "UPLOAD_DIR", tmp_path / "data" / "uploads")
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "data" / "file_manager.sqlite3")
    app.init_db()


def admin_user():
    with app.get_db() as conn:
        return conn.execute("SELECT id, is_admin FROM users WHERE username = ?", ("admin",)).fetchone()


def authenticated_client(user_id: int) -> TestClient:
    client = TestClient(app.app)
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
    return int(document_id)


def test_login_page_loads(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)

    client = TestClient(app.app)
    response = client.get("/login")

    assert response.status_code == 200
    assert "登录" in response.text


def test_delete_document_moves_document_to_recycle_bin(tmp_path, monkeypatch):
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
    assert document_dir.exists()

    with app.get_db() as conn:
        deleted_document = conn.execute(
            "SELECT deleted_at FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone()
        version_count = conn.execute("SELECT COUNT(*) FROM document_versions").fetchone()[0]

    assert deleted_document["deleted_at"] is not None
    assert version_count == 1


def test_category_delete_is_blocked_when_category_has_documents(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()

    with app.get_db() as conn:
        category_id = conn.execute(
            "INSERT INTO categories (name, description, created_at) VALUES (?, '', ?)",
            ("AI技巧", app.now_iso()),
        ).lastrowid
        conn.commit()

    insert_document("Category sample", user["id"], category_id=category_id)
    client = authenticated_client(user["id"])
    response = client.post(
        f"/categories/{category_id}/delete",
        data={"active_category_id": str(category_id), "q": "", "page": "1"},
    )

    assert response.status_code == 200
    assert "文件夹中有文件，请清空后再删除文件夹。" in response.text

    with app.get_db() as conn:
        category_count = conn.execute(
            "SELECT COUNT(*) FROM categories WHERE id = ?",
            (category_id,),
        ).fetchone()[0]

    assert category_count == 1


def test_empty_category_can_be_deleted(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()

    with app.get_db() as conn:
        category_id = conn.execute(
            "INSERT INTO categories (name, description, created_at) VALUES (?, '', ?)",
            ("空文件夹", app.now_iso()),
        ).lastrowid
        conn.commit()

    client = authenticated_client(user["id"])
    response = client.post(
        f"/categories/{category_id}/delete",
        data={"active_category_id": "", "q": "", "page": "1"},
        follow_redirects=False,
    )

    assert response.status_code == 303

    with app.get_db() as conn:
        category_count = conn.execute(
            "SELECT COUNT(*) FROM categories WHERE id = ?",
            (category_id,),
        ).fetchone()[0]

    assert category_count == 0


def test_batch_delete_moves_selected_documents_to_recycle_bin(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()
    first_id = insert_document("First", user["id"], filename="first.txt")
    second_id = insert_document("Second", user["id"], filename="second.txt")
    third_id = insert_document("Third", user["id"], filename="third.txt")

    client = authenticated_client(user["id"])
    response = client.post(
        "/documents/batch-delete",
        data={
            "document_ids": [str(first_id), str(second_id)],
            "active_category_id": "",
            "q": "",
            "page": "1",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert (app.UPLOAD_DIR / str(first_id)).exists()
    assert (app.UPLOAD_DIR / str(second_id)).exists()
    assert (app.UPLOAD_DIR / str(third_id)).exists()

    with app.get_db() as conn:
        rows = conn.execute("SELECT id, deleted_at FROM documents ORDER BY id").fetchall()

    deleted_map = {row["id"]: row["deleted_at"] for row in rows}
    assert deleted_map[first_id] is not None
    assert deleted_map[second_id] is not None
    assert deleted_map[third_id] is None


def test_batch_download_returns_zip_for_selected_documents(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()
    first_id = insert_document("First", user["id"], filename="first.txt", content="first")
    second_id = insert_document("Second", user["id"], filename="second.txt", content="second")

    client = authenticated_client(user["id"])
    response = client.post(
        "/documents/batch-download",
        data={
            "document_ids": [str(first_id), str(second_id)],
            "active_category_id": "",
            "q": "",
            "page": "1",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")

    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        assert sorted(archive.namelist()) == ["first.txt", "second.txt"]
        assert archive.read("first.txt") == b"first"
        assert archive.read("second.txt") == b"second"


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


def test_admin_user_can_manage_account_users(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()
    client = authenticated_client(user["id"])

    account_page = client.get("/account")

    assert account_page.status_code == 200
    assert "用户列表与权限管理" in account_page.text
    assert user["is_admin"] == 1

    create_response = client.post(
        "/account/users",
        data={
            "username": "manager",
            "password": "Password123",
            "confirm_password": "Password123",
            "is_admin": "0",
        },
    )

    assert create_response.status_code == 200
    assert "用户已新增" in create_response.text

    with app.get_db() as conn:
        managed_user = conn.execute(
            "SELECT id, username, is_admin FROM users WHERE username = ?",
            ("manager",),
        ).fetchone()

    assert managed_user["is_admin"] == 0

    permission_response = client.post(
        f"/account/users/{managed_user['id']}/admin",
        data={"is_admin": "1"},
    )

    assert permission_response.status_code == 200
    assert "权限已更新" in permission_response.text

    with app.get_db() as conn:
        managed_user = conn.execute("SELECT is_admin FROM users WHERE username = ?", ("manager",)).fetchone()

    assert managed_user["is_admin"] == 1


def test_admin_can_disable_and_enable_regular_user(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    admin = admin_user()
    managed_user = create_test_user("managed-user")
    client = authenticated_client(admin["id"])

    disable_response = client.post(
        f"/account/users/{managed_user['id']}/active",
        data={"is_active": "0", "user_page": "1"},
    )

    assert disable_response.status_code == 200
    assert "账号已停用" in disable_response.text
    with app.get_db() as conn:
        disabled_user = app.get_user_by_id(conn, managed_user["id"])
    assert disabled_user["is_active"] == 0

    login_response = TestClient(app.app).post(
        "/login",
        data={"username": "managed-user", "password": "Password123"},
    )
    assert login_response.status_code == 403
    assert "账号已停用，请联系管理员" in login_response.text

    disabled_client = authenticated_client(managed_user["id"])
    session_response = disabled_client.get("/dashboard", follow_redirects=False)
    assert session_response.status_code == 303
    assert session_response.headers["location"].startswith("/logout?error=")

    enable_response = client.post(
        f"/account/users/{managed_user['id']}/active",
        data={"is_active": "1", "user_page": "1"},
    )
    assert enable_response.status_code == 200
    assert "账号已启用" in enable_response.text

    active_login = TestClient(app.app).post(
        "/login",
        data={"username": "managed-user", "password": "Password123"},
        follow_redirects=False,
    )
    assert active_login.status_code == 303
    assert active_login.headers["location"] == "/dashboard"


def test_admin_cannot_disable_current_account(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    admin = admin_user()
    client = authenticated_client(admin["id"])

    response = client.post(
        f"/account/users/{admin['id']}/active",
        data={"is_active": "0", "user_page": "1"},
    )

    assert response.status_code == 400
    assert "不能停用当前登录账号" in response.text
    with app.get_db() as conn:
        current_admin = app.get_user_by_id(conn, admin["id"])
    assert current_admin["is_active"] == 1


def test_admin_user_list_paginates_to_ten_users(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()

    with app.get_db() as conn:
        for index in range(12):
            app.create_user(
                conn,
                f"user-{index:02d}",
                app.password_hash("Password123"),
                False,
                app.now_iso(),
            )
        conn.commit()

    client = authenticated_client(user["id"])
    first_page = client.get("/account")
    second_page = client.get("/account?user_page=2")

    assert first_page.status_code == 200
    assert "共 13 个用户，每页最多 10 个" in first_page.text
    assert "第 1 / 2 页" in first_page.text
    assert "user-00" in first_page.text
    assert "user-09" not in first_page.text

    assert second_page.status_code == 200
    assert "第 2 / 2 页" in second_page.text
    assert "user-09" in second_page.text
    assert "user-11" in second_page.text


def test_last_admin_cannot_be_downgraded(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()
    client = authenticated_client(user["id"])

    response = client.post(
        f"/account/users/{user['id']}/admin",
        data={"is_admin": "0"},
    )

    assert response.status_code == 400
    assert "至少需要保留一个管理员" in response.text


def test_user_can_change_own_password(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()
    client = authenticated_client(user["id"])

    response = client.post(
        "/account/password",
        data={
            "current_password": "admin123456",
            "new_password": "NewPassword123",
            "confirm_password": "NewPassword123",
        },
    )

    assert response.status_code == 200
    assert "密码已更新" in response.text

    with app.get_db() as conn:
        db_user = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()

    assert app.verify_password("NewPassword123", db_user["password_hash"])


def test_user_requests_download_and_admin_approval_grants_access(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    owner = admin_user()
    requester = create_test_user("requester")
    document_id = insert_document("Protected document", owner["id"], content="protected")
    requester_client = authenticated_client(requester["id"])

    denied = requester_client.get(f"/documents/{document_id}/download")
    assert denied.status_code == 403

    submitted = requester_client.post(
        f"/documents/{document_id}/access-requests",
        data={"action": "download", "return_to": f"/documents/{document_id}"},
        follow_redirects=False,
    )
    assert submitted.status_code == 303
    assert "success=" in submitted.headers["location"]

    with app.get_db() as conn:
        access_request = conn.execute(
            "SELECT id, status FROM access_requests WHERE requester_id = ? AND document_id = ?",
            (requester["id"], document_id),
        ).fetchone()
    assert access_request["status"] == "pending"

    admin_client = authenticated_client(owner["id"])
    account = admin_client.get("/account")
    assert account.status_code == 200
    assert "Protected document" in account.text
    assert "notification-badge" in account.text

    approved = admin_client.post(
        f"/access-requests/{access_request['id']}/approve",
        follow_redirects=False,
    )
    assert approved.status_code == 303

    download = requester_client.get(f"/documents/{document_id}/download")
    assert download.status_code == 200
    assert download.content == b"protected"


def test_user_cannot_delete_another_users_document(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    owner = create_test_user("owner")
    other_user = create_test_user("other-user")
    document_id = insert_document("Owner only", owner["id"])
    client = authenticated_client(other_user["id"])

    direct_delete = client.post(f"/documents/{document_id}/delete", follow_redirects=False)
    assert direct_delete.status_code == 403

    batch_delete = client.post(
        "/documents/batch-delete",
        data={"document_ids": [str(document_id)], "active_category_id": "", "q": "", "page": "1"},
        follow_redirects=False,
    )
    assert batch_delete.status_code == 303
    assert "error=" in batch_delete.headers["location"]

    with app.get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM documents WHERE id = ?", (document_id,)).fetchone()[0] == 1
    assert (app.UPLOAD_DIR / str(document_id)).exists()


def test_approved_upload_version_request_allows_new_version(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    owner = admin_user()
    requester = create_test_user("version-requester")
    document_id = insert_document("Version protected", owner["id"])
    requester_client = authenticated_client(requester["id"])

    denied = requester_client.post(
        f"/documents/{document_id}/versions",
        data={"notes": "not approved"},
        files={"file": ("v2.txt", b"version two", "text/plain")},
        follow_redirects=False,
    )
    assert denied.status_code == 303

    requester_client.post(
        f"/documents/{document_id}/access-requests",
        data={"action": "upload_version", "return_to": f"/documents/{document_id}"},
        follow_redirects=False,
    )
    with app.get_db() as conn:
        access_request_id = conn.execute(
            """
            SELECT id FROM access_requests
            WHERE requester_id = ? AND document_id = ? AND action = 'upload_version'
            """,
            (requester["id"], document_id),
        ).fetchone()["id"]

    admin_client = authenticated_client(owner["id"])
    admin_client.post(f"/access-requests/{access_request_id}/approve", follow_redirects=False)

    uploaded = requester_client.post(
        f"/documents/{document_id}/versions",
        data={"notes": "approved version"},
        files={"file": ("v2.txt", b"version two", "text/plain")},
        follow_redirects=False,
    )
    assert uploaded.status_code == 303

    with app.get_db() as conn:
        versions = conn.execute(
            "SELECT version_number, uploaded_by FROM document_versions WHERE document_id = ? ORDER BY version_number",
            (document_id,),
        ).fetchall()
    assert [(row["version_number"], row["uploaded_by"]) for row in versions] == [
        (1, owner["id"]),
        (2, requester["id"]),
    ]


def test_rejected_download_request_does_not_grant_access(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    owner = admin_user()
    requester = create_test_user("rejected-requester")
    document_id = insert_document("Rejected document", owner["id"])
    requester_client = authenticated_client(requester["id"])

    requester_client.post(
        f"/documents/{document_id}/access-requests",
        data={"action": "download", "return_to": f"/documents/{document_id}"},
        follow_redirects=False,
    )
    with app.get_db() as conn:
        access_request_id = conn.execute(
            "SELECT id FROM access_requests WHERE requester_id = ? AND document_id = ?",
            (requester["id"], document_id),
        ).fetchone()["id"]

    admin_client = authenticated_client(owner["id"])
    rejected = admin_client.post(
        f"/access-requests/{access_request_id}/reject",
        follow_redirects=False,
    )
    assert rejected.status_code == 303

    with app.get_db() as conn:
        status_value = conn.execute(
            "SELECT status FROM access_requests WHERE id = ?",
            (access_request_id,),
        ).fetchone()["status"]
    assert status_value == "rejected"
    assert requester_client.get(f"/documents/{document_id}/download").status_code == 403


def test_existing_category_table_is_migrated_with_parent_id(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    database_path = data_dir / "file_manager.sqlite3"
    with sqlite3.connect(database_path) as conn:
        conn.execute(
            """
            CREATE TABLE categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO categories (name, description, created_at) VALUES ('旧分类', '', '2026-07-01')"
        )
        conn.commit()

    monkeypatch.setattr(app, "DATA_DIR", data_dir)
    monkeypatch.setattr(app, "UPLOAD_DIR", data_dir / "uploads")
    monkeypatch.setattr(app, "DB_PATH", database_path)
    app.init_db()

    with app.get_db() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(categories)").fetchall()}
        legacy_category = conn.execute(
            "SELECT name, parent_id FROM categories WHERE name = '旧分类'"
        ).fetchone()

    assert "parent_id" in columns
    assert legacy_category["parent_id"] is None


def test_existing_user_table_is_migrated_with_active_status(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    database_path = data_dir / "file_manager.sqlite3"
    with sqlite3.connect(database_path) as conn:
        conn.execute(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO users (username, password_hash, is_admin, created_at)
            VALUES (?, ?, 1, ?)
            """,
            ("admin", app.password_hash("admin123456"), app.now_iso()),
        )
        conn.commit()

    monkeypatch.setattr(app, "DATA_DIR", data_dir)
    monkeypatch.setattr(app, "UPLOAD_DIR", data_dir / "uploads")
    monkeypatch.setattr(app, "DB_PATH", database_path)
    app.init_db()

    with app.get_db() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        migrated_admin = app.get_user_by_username(conn, "admin")

    assert "is_active" in columns
    assert migrated_admin["is_active"] == 1


def test_nested_categories_are_limited_to_three_levels(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()
    client = authenticated_client(user["id"])

    parent_id = None
    created_ids = []
    for level in range(1, 4):
        response = client.post(
            "/categories",
            data={
                "name": f"层级-{level}",
                "parent_id": str(parent_id or ""),
                "active_category_id": "",
                "q": "",
                "page": "1",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        with app.get_db() as conn:
            parent_id = conn.execute(
                "SELECT id FROM categories WHERE name = ?",
                (f"层级-{level}",),
            ).fetchone()["id"]
        created_ids.append(parent_id)

    rejected = client.post(
        "/categories",
        data={
            "name": "层级-4",
            "parent_id": str(created_ids[-1]),
            "active_category_id": str(created_ids[-1]),
            "q": "",
            "page": "1",
        },
    )

    assert rejected.status_code == 200
    assert "最多支持三级文件夹" in rejected.text
    with app.get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM categories WHERE name = '层级-4'").fetchone()[0] == 0


def test_parent_category_filter_includes_descendant_documents(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()
    with app.get_db() as conn:
        parent_id = conn.execute(
            "INSERT INTO categories (name, parent_id, description, created_at) VALUES (?, NULL, '', ?)",
            ("父文件夹", app.now_iso()),
        ).lastrowid
        child_id = conn.execute(
            "INSERT INTO categories (name, parent_id, description, created_at) VALUES (?, ?, '', ?)",
            ("子文件夹", parent_id, app.now_iso()),
        ).lastrowid
        conn.commit()

    insert_document("下级文件", user["id"], category_id=child_id)
    client = authenticated_client(user["id"])
    response = client.get(f"/dashboard?category_id={parent_id}")

    assert response.status_code == 200
    assert "下级文件" in response.text
    assert "--category-depth: 1" in response.text


def test_category_with_child_folder_cannot_be_deleted(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()
    with app.get_db() as conn:
        parent_id = conn.execute(
            "INSERT INTO categories (name, parent_id, description, created_at) VALUES (?, NULL, '', ?)",
            ("待保护父文件夹", app.now_iso()),
        ).lastrowid
        conn.execute(
            "INSERT INTO categories (name, parent_id, description, created_at) VALUES (?, ?, '', ?)",
            ("保留子文件夹", parent_id, app.now_iso()),
        )
        conn.commit()

    client = authenticated_client(user["id"])
    response = client.post(
        f"/categories/{parent_id}/delete",
        data={"active_category_id": str(parent_id), "q": "", "page": "1"},
    )

    assert response.status_code == 200
    assert "文件夹中有文件，请清空后再删除文件夹。" in response.text
    with app.get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM categories WHERE id = ?", (parent_id,)).fetchone()[0] == 1


def test_regular_user_can_select_any_file_and_batch_request_access(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    owner = admin_user()
    requester = create_test_user("batch-requester")
    first_id = insert_document("待申请一", owner["id"], filename="request-1.txt")
    second_id = insert_document("待申请二", owner["id"], filename="request-2.txt")
    client = authenticated_client(requester["id"])

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    checkbox_match = re.search(
        rf'<input\s+[^>]*class="document-select"[^>]*value="{first_id}"[^>]*>',
        dashboard.text,
    )
    assert checkbox_match is not None
    assert "disabled" not in checkbox_match.group(0)
    assert "<th>操作</th>" not in dashboard.text
    assert 'formaction="/documents/batch-access-request"' in dashboard.text
    assert "data-preview-action" in dashboard.text

    response = client.post(
        "/documents/batch-access-request",
        data={
            "document_ids": [str(first_id), str(second_id)],
            "active_category_id": "",
            "q": "",
            "page": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    with app.get_db() as conn:
        requests = conn.execute(
            """
            SELECT document_id, status
            FROM access_requests
            WHERE requester_id = ? AND action = 'download'
            ORDER BY document_id
            """,
            (requester["id"],),
        ).fetchall()
    assert [(row["document_id"], row["status"]) for row in requests] == [
        (first_id, "pending"),
        (second_id, "pending"),
    ]
