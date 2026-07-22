from fastapi.testclient import TestClient

import app
from conftest import admin_user, authenticated_client, configure_temp_app, create_test_user
from repositories.audit_log_repository import insert_audit_log


def seed_audit_log(username: str, action: str, detail: str, created_at: str) -> None:
    with app.get_db() as conn:
        insert_audit_log(conn, username, "127.0.0.1", action, detail, created_at)
        conn.commit()


def test_important_operation_writes_audit_log_row(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)

    client = TestClient(app.app)
    response = client.post(
        "/login",
        data={"username": "admin", "password": "admin123456"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with app.get_db() as conn:
        row = conn.execute(
            "SELECT username, ip, action, detail, created_at FROM audit_logs WHERE action = 'login'"
        ).fetchone()

    assert row is not None
    assert row["username"] == "admin"
    assert row["ip"]
    assert row["detail"] == "success"
    assert row["created_at"]


def test_audit_log_file_is_created_with_rotating_handler(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)

    client = TestClient(app.app)
    client.post("/login", data={"username": "admin", "password": "admin123456"}, follow_redirects=False)

    log_path = tmp_path / "data" / "logs" / "audit.log"
    assert log_path.is_file()
    content = log_path.read_text(encoding="utf-8")
    assert "user=admin" in content
    assert "operation=login" in content


def test_admin_can_view_audit_logs_page(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()
    seed_audit_log("admin", "login", "seed-login-row", "2026-07-15T01:00:00+00:00")

    client = authenticated_client(user["id"])
    response = client.get("/admin/audit-logs")

    assert response.status_code == 200
    assert "审计日志" in response.text
    assert "seed-login-row" in response.text
    assert "127.0.0.1" in response.text


def test_regular_user_cannot_view_audit_logs_page(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    regular_user = create_test_user("audit-regular")

    client = authenticated_client(regular_user["id"])
    response = client.get("/admin/audit-logs")

    assert response.status_code == 403


def test_audit_logs_page_filters_by_user_and_action(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()
    seed_audit_log("alice", "login", "alice-login-detail", "2026-07-15T01:00:00+00:00")
    seed_audit_log("bob", "delete_document", "bob-delete-detail", "2026-07-16T01:00:00+00:00")

    client = authenticated_client(user["id"])

    by_user = client.get("/admin/audit-logs?user=alice")
    assert by_user.status_code == 200
    assert "alice-login-detail" in by_user.text
    assert "bob-delete-detail" not in by_user.text

    by_action = client.get("/admin/audit-logs?action=delete_document")
    assert by_action.status_code == 200
    assert "bob-delete-detail" in by_action.text
    assert "alice-login-detail" not in by_action.text


def test_audit_logs_page_filters_by_date_range(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()
    seed_audit_log("alice", "login", "early-row", "2026-07-01T01:00:00+00:00")
    seed_audit_log("alice", "login", "late-row", "2026-07-20T01:00:00+00:00")

    client = authenticated_client(user["id"])
    response = client.get("/admin/audit-logs?start=2026-07-10&end=2026-07-31")

    assert response.status_code == 200
    assert "late-row" in response.text
    assert "early-row" not in response.text


def test_audit_logs_page_paginates_twenty_per_page(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()
    for index in range(25):
        seed_audit_log("admin", "login", f"row-{index:02d}", f"2026-07-15T01:{index:02d}:00+00:00")

    client = authenticated_client(user["id"])
    first_page = client.get("/admin/audit-logs")
    second_page = client.get("/admin/audit-logs?page=2")

    assert first_page.status_code == 200
    assert "共 25 条，每页最多 20 条" in first_page.text
    assert "第 1 / 2 页" in first_page.text
    assert "row-24" in first_page.text
    assert "row-04" not in first_page.text

    assert second_page.status_code == 200
    assert "第 2 / 2 页" in second_page.text
    assert "row-04" in second_page.text
    assert "row-24" not in second_page.text
