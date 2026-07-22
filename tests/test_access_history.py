from datetime import datetime, timedelta, timezone

import app
from conftest import (
    admin_user,
    authenticated_client,
    configure_temp_app,
    create_test_user,
    insert_document,
)


def submit_download_request(client, document_id: int) -> int:
    client.post(
        f"/documents/{document_id}/access-requests",
        data={"action": "download", "return_to": f"/documents/{document_id}"},
        follow_redirects=False,
    )
    with app.get_db() as conn:
        return conn.execute(
            """
            SELECT id FROM access_requests
            WHERE document_id = ? AND action = 'download'
            ORDER BY id DESC
            """,
            (document_id,),
        ).fetchone()["id"]


def approve_request(client, request_id: int, validity: str = "") -> None:
    response = client.post(
        f"/access-requests/{request_id}/approve",
        data={"validity": validity},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_approve_with_validity_grants_temporary_access(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    owner = admin_user()
    requester = create_test_user("validity-requester")
    document_id = insert_document("限时文件", owner["id"], content="limited")
    requester_client = authenticated_client(requester["id"])

    request_id = submit_download_request(requester_client, document_id)
    admin_client = authenticated_client(owner["id"])
    approve_request(admin_client, request_id, validity="7")

    with app.get_db() as conn:
        row = conn.execute(
            "SELECT status, expires_at FROM access_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
    assert row["status"] == "approved"
    assert row["expires_at"] is not None

    download = requester_client.get(f"/documents/{document_id}/download")
    assert download.status_code == 200
    assert download.content == b"limited"


def test_expired_grant_loses_access_and_can_reapply(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    owner = admin_user()
    requester = create_test_user("expired-requester")
    document_id = insert_document("过期文件", owner["id"])
    requester_client = authenticated_client(requester["id"])

    request_id = submit_download_request(requester_client, document_id)
    admin_client = authenticated_client(owner["id"])
    approve_request(admin_client, request_id, validity="7")
    assert requester_client.get(f"/documents/{document_id}/download").status_code == 200

    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    with app.get_db() as conn:
        conn.execute("UPDATE access_requests SET expires_at = ? WHERE id = ?", (past, request_id))
        conn.commit()

    assert requester_client.get(f"/documents/{document_id}/download").status_code == 403

    reapplied = requester_client.post(
        f"/documents/{document_id}/access-requests",
        data={"action": "download", "return_to": f"/documents/{document_id}"},
        follow_redirects=False,
    )
    assert reapplied.status_code == 303
    assert "success=" in reapplied.headers["location"]
    with app.get_db() as conn:
        pending_count = conn.execute(
            """
            SELECT COUNT(*) FROM access_requests
            WHERE requester_id = ? AND document_id = ? AND status = 'pending'
            """,
            (requester["id"], document_id),
        ).fetchone()[0]
    assert pending_count == 1


def test_permanent_approval_has_no_expiration(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    owner = admin_user()
    requester = create_test_user("permanent-requester")
    document_id = insert_document("永久授权文件", owner["id"])
    requester_client = authenticated_client(requester["id"])

    request_id = submit_download_request(requester_client, document_id)
    admin_client = authenticated_client(owner["id"])
    approve_request(admin_client, request_id)

    with app.get_db() as conn:
        row = conn.execute(
            "SELECT expires_at FROM access_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
    assert row["expires_at"] is None
    assert requester_client.get(f"/documents/{document_id}/download").status_code == 200


def test_revoke_grant_takes_effect_immediately(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    owner = admin_user()
    requester = create_test_user("revoke-requester")
    document_id = insert_document("撤销目标", owner["id"])
    requester_client = authenticated_client(requester["id"])

    request_id = submit_download_request(requester_client, document_id)
    admin_client = authenticated_client(owner["id"])
    approve_request(admin_client, request_id)
    assert requester_client.get(f"/documents/{document_id}/download").status_code == 200

    revoked = admin_client.post(f"/access-requests/{request_id}/revoke", follow_redirects=False)
    assert revoked.status_code == 303

    with app.get_db() as conn:
        row = conn.execute(
            "SELECT status FROM access_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
    assert row["status"] == "revoked"
    assert requester_client.get(f"/documents/{document_id}/download").status_code == 403

    with app.get_db() as conn:
        audit = conn.execute(
            "SELECT action FROM audit_logs WHERE action = 'revoke_access_request'"
        ).fetchone()
    assert audit is not None


def test_document_owner_can_revoke_but_other_user_cannot(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    admin = admin_user()
    owner = create_test_user("grant-owner")
    outsider = create_test_user("grant-outsider")
    requester = create_test_user("grant-requester")
    document_id = insert_document("所有者的文件", owner["id"])
    requester_client = authenticated_client(requester["id"])

    request_id = submit_download_request(requester_client, document_id)
    admin_client = authenticated_client(admin["id"])
    approve_request(admin_client, request_id)

    outsider_client = authenticated_client(outsider["id"])
    assert outsider_client.post(
        f"/access-requests/{request_id}/revoke", follow_redirects=False
    ).status_code == 403

    owner_client = authenticated_client(owner["id"])
    revoked = owner_client.post(f"/access-requests/{request_id}/revoke", follow_redirects=False)
    assert revoked.status_code == 303

    with app.get_db() as conn:
        row = conn.execute(
            "SELECT status FROM access_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
    assert row["status"] == "revoked"


def test_access_history_page_for_admin_and_regular_users(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    admin = admin_user()
    owner = create_test_user("history-owner")
    requester = create_test_user("history-requester")
    document_id = insert_document("历史文件", owner["id"])
    requester_client = authenticated_client(requester["id"])

    request_id = submit_download_request(requester_client, document_id)
    admin_client = authenticated_client(admin["id"])
    approve_request(admin_client, request_id, validity="30")

    admin_page = admin_client.get("/access-requests")
    assert admin_page.status_code == 200
    assert "审批历史" in admin_page.text
    assert "history-requester" in admin_page.text
    assert "历史文件" in admin_page.text
    assert "已通过" in admin_page.text

    filtered = admin_client.get("/access-requests?user=history-requester&status=approved")
    assert "历史文件" in filtered.text
    empty = admin_client.get("/access-requests?user=nobody")
    assert "历史文件" not in empty.text

    requester_page = requester_client.get("/access-requests")
    assert requester_page.status_code == 200
    assert "历史文件" in requester_page.text
    assert "已通过" in requester_page.text

    owner_page = authenticated_client(owner["id"]).get("/access-requests")
    assert owner_page.status_code == 200
    assert "历史文件" in owner_page.text

    outsider = create_test_user("history-outsider")
    outsider_page = authenticated_client(outsider["id"]).get("/access-requests")
    assert outsider_page.status_code == 200
    assert "历史文件" not in outsider_page.text
