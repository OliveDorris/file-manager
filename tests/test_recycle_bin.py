from datetime import datetime, timedelta, timezone

import app
from conftest import (
    admin_user,
    authenticated_client,
    configure_temp_app,
    create_test_user,
    insert_document,
    mark_document_deleted,
)


def test_soft_deleted_document_hidden_from_list_and_direct_access(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()
    document_id = insert_document("待删除文件", user["id"])
    client = authenticated_client(user["id"])

    response = client.post(f"/documents/{document_id}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert (app.UPLOAD_DIR / str(document_id)).exists()

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert "待删除文件" not in dashboard.text

    detail = client.get(f"/documents/{document_id}")
    assert detail.status_code == 404

    download = client.get(f"/documents/{document_id}/download")
    assert download.status_code == 404

    preview = client.get(f"/documents/{document_id}/preview")
    assert preview.status_code == 404


def test_restore_document_makes_it_visible_again(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()
    document_id = insert_document("恢复目标", user["id"], content="restored")
    client = authenticated_client(user["id"])
    client.post(f"/documents/{document_id}/delete", follow_redirects=False)

    recycle_bin = client.get("/recycle-bin")
    assert recycle_bin.status_code == 200
    assert "恢复目标" in recycle_bin.text

    restored = client.post(f"/documents/{document_id}/restore", follow_redirects=False)
    assert restored.status_code == 303

    dashboard = client.get("/dashboard")
    assert "恢复目标" in dashboard.text
    download = client.get(f"/documents/{document_id}/download")
    assert download.status_code == 200
    assert download.content == b"restored"

    with app.get_db() as conn:
        row = conn.execute("SELECT deleted_at FROM documents WHERE id = ?", (document_id,)).fetchone()
    assert row["deleted_at"] is None

    with app.get_db() as conn:
        audit = conn.execute(
            "SELECT action FROM audit_logs WHERE action = 'restore_document'"
        ).fetchone()
    assert audit is not None


def test_purge_document_removes_rows_and_files(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()
    document_id = insert_document("彻底删除目标", user["id"])
    client = authenticated_client(user["id"])
    client.post(f"/documents/{document_id}/delete", follow_redirects=False)

    purged = client.post(f"/documents/{document_id}/purge", follow_redirects=False)
    assert purged.status_code == 303

    assert not (app.UPLOAD_DIR / str(document_id)).exists()
    with app.get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM documents WHERE id = ?", (document_id,)).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM document_versions WHERE document_id = ?",
            (document_id,),
        ).fetchone()[0] == 0
        audit = conn.execute(
            "SELECT action FROM audit_logs WHERE action = 'purge_document'"
        ).fetchone()
    assert audit is not None


def test_recycle_bin_scopes_and_permission_boundaries(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    admin = admin_user()
    owner = create_test_user("recycle-owner")
    other = create_test_user("recycle-other")
    admin_document_id = insert_document("管理员删除的文件", admin["id"])
    owner_document_id = insert_document("普通用户删除的文件", owner["id"])

    admin_client = authenticated_client(admin["id"])
    admin_client.post(f"/documents/{admin_document_id}/delete", follow_redirects=False)
    owner_client = authenticated_client(owner["id"])
    owner_client.post(f"/documents/{owner_document_id}/delete", follow_redirects=False)

    owner_bin = owner_client.get("/recycle-bin")
    assert "普通用户删除的文件" in owner_bin.text
    assert "管理员删除的文件" not in owner_bin.text

    admin_bin = admin_client.get("/recycle-bin")
    assert "普通用户删除的文件" in admin_bin.text
    assert "管理员删除的文件" in admin_bin.text

    other_client = authenticated_client(other["id"])
    assert other_client.post(
        f"/documents/{owner_document_id}/restore", follow_redirects=False
    ).status_code == 403
    assert other_client.post(
        f"/documents/{owner_document_id}/purge", follow_redirects=False
    ).status_code == 403
    assert other_client.post(
        f"/documents/{owner_document_id}/delete", follow_redirects=False
    ).status_code == 404

    with app.get_db() as conn:
        row = conn.execute("SELECT deleted_at FROM documents WHERE id = ?", (owner_document_id,)).fetchone()
    assert row["deleted_at"] is not None
    assert (app.UPLOAD_DIR / str(owner_document_id)).exists()


def test_clear_recycle_bin_scopes_by_user(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    admin = admin_user()
    owner = create_test_user("clear-owner")
    admin_document_id = insert_document("管理员的回收文件", admin["id"])
    owner_document_id = insert_document("普通用户的回收文件", owner["id"])
    mark_document_deleted(admin_document_id)
    mark_document_deleted(owner_document_id)

    owner_client = authenticated_client(owner["id"])
    cleared = owner_client.post("/recycle-bin/clear", follow_redirects=False)
    assert cleared.status_code == 303

    with app.get_db() as conn:
        remaining = {
            row["id"]
            for row in conn.execute("SELECT id FROM documents WHERE deleted_at IS NOT NULL").fetchall()
        }
    assert remaining == {admin_document_id}
    assert not (app.UPLOAD_DIR / str(owner_document_id)).exists()
    assert (app.UPLOAD_DIR / str(admin_document_id)).exists()

    admin_client = authenticated_client(admin["id"])
    admin_client.post("/recycle-bin/clear", follow_redirects=False)
    with app.get_db() as conn:
        remaining_count = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE deleted_at IS NOT NULL"
        ).fetchone()[0]
    assert remaining_count == 0
    assert not (app.UPLOAD_DIR / str(admin_document_id)).exists()


def test_recycle_bin_retention_auto_purges_expired_documents(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    monkeypatch.setenv("RECYCLE_RETENTION_DAYS", "7")
    user = admin_user()
    expired_id = insert_document("十天前删除", user["id"])
    fresh_id = insert_document("刚刚删除", user["id"])
    old_timestamp = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(timespec="seconds")
    mark_document_deleted(expired_id, old_timestamp)
    mark_document_deleted(fresh_id)

    client = authenticated_client(user["id"])
    response = client.get("/recycle-bin")

    assert response.status_code == 200
    assert "刚刚删除" in response.text
    assert "十天前删除" not in response.text
    assert not (app.UPLOAD_DIR / str(expired_id)).exists()
    assert (app.UPLOAD_DIR / str(fresh_id)).exists()
    with app.get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM documents WHERE id = ?", (expired_id,)).fetchone()[0] == 0


def test_soft_deleted_document_does_not_block_category_delete(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()
    with app.get_db() as conn:
        category_id = conn.execute(
            "INSERT INTO categories (name, description, created_at) VALUES (?, '', ?)",
            ("回收分类", app.now_iso()),
        ).lastrowid
        conn.commit()

    document_id = insert_document("分类中的文件", user["id"], category_id=category_id)
    client = authenticated_client(user["id"])
    client.post(f"/documents/{document_id}/delete", follow_redirects=False)

    response = client.post(
        f"/categories/{category_id}/delete",
        data={"active_category_id": "", "q": "", "page": "1"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with app.get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM categories WHERE id = ?", (category_id,)).fetchone()[0] == 0
