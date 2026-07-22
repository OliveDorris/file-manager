from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import app
from conftest import (
    admin_user,
    authenticated_client,
    configure_temp_app,
    create_test_user,
    insert_document,
)


def create_share(client, document_id: int, password: str = "", validity: str = "", allow_download: str = "0") -> str:
    response = client.post(
        f"/documents/{document_id}/shares",
        data={"password": password, "validity": validity, "allow_download": allow_download},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with app.get_db() as conn:
        return conn.execute("SELECT token FROM share_links ORDER BY id DESC LIMIT 1").fetchone()["token"]


def get_share_row(token: str):
    with app.get_db() as conn:
        return conn.execute("SELECT * FROM share_links WHERE token = ?", (token,)).fetchone()


def test_owner_creates_share_and_anonymous_can_view(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    owner = create_test_user("share-owner")
    document_id = insert_document("分享文件", owner["id"], content="shared-content-xyz")
    owner_client = authenticated_client(owner["id"])

    detail = owner_client.get(f"/documents/{document_id}")
    assert "分享链接" in detail.text

    token = create_share(owner_client, document_id)

    public_client = TestClient(app.app)
    view = public_client.get(f"/share/{token}")
    assert view.status_code == 200
    assert "分享文件" in view.text
    assert "shared-content-xyz" in view.text

    with app.get_db() as conn:
        create_audit = conn.execute(
            "SELECT username FROM audit_logs WHERE action = 'create_share_link'"
        ).fetchone()
        view_audit = conn.execute(
            "SELECT username, ip FROM audit_logs WHERE action = 'share_access'"
        ).fetchone()
    assert create_audit["username"] == "share-owner"
    assert view_audit["username"] == "anonymous"
    assert view_audit["ip"]


def test_cannot_share_another_users_document(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    owner = create_test_user("share-doc-owner")
    other = create_test_user("share-doc-other")
    document_id = insert_document("他人文件", owner["id"])
    other_client = authenticated_client(other["id"])

    response = other_client.post(
        f"/documents/{document_id}/shares",
        data={"password": "", "validity": "", "allow_download": "0"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    with app.get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM share_links").fetchone()[0] == 0


def test_preview_only_share_has_no_download(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    owner = create_test_user("preview-share-owner")
    document_id = insert_document("仅预览文件", owner["id"], content="preview-only")
    owner_client = authenticated_client(owner["id"])
    token = create_share(owner_client, document_id, allow_download="0")

    public_client = TestClient(app.app)
    view = public_client.get(f"/share/{token}")
    assert view.status_code == 200
    assert f"/share/{token}/download" not in view.text

    download = public_client.get(f"/share/{token}/download")
    assert download.status_code == 404

    inline = public_client.get(f"/share/{token}/file")
    assert inline.status_code == 200


def test_downloadable_share_allows_download_and_writes_audit(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    owner = create_test_user("download-share-owner")
    document_id = insert_document("可下载分享", owner["id"], content="download-me")
    owner_client = authenticated_client(owner["id"])
    token = create_share(owner_client, document_id, allow_download="1")

    public_client = TestClient(app.app)
    view = public_client.get(f"/share/{token}")
    assert f"/share/{token}/download" in view.text

    download = public_client.get(f"/share/{token}/download")
    assert download.status_code == 200
    assert download.content == b"download-me"

    with app.get_db() as conn:
        audit = conn.execute(
            "SELECT username FROM audit_logs WHERE action = 'share_download'"
        ).fetchone()
    assert audit is not None
    assert audit["username"] == "anonymous"


def test_password_protected_share(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    owner = create_test_user("password-share-owner")
    document_id = insert_document("密码分享", owner["id"], content="secret-content")
    owner_client = authenticated_client(owner["id"])
    token = create_share(owner_client, document_id, password="secret123")

    public_client = TestClient(app.app)
    view = public_client.get(f"/share/{token}")
    assert view.status_code == 200
    assert "分享访问验证" in view.text
    assert "secret-content" not in view.text

    wrong = public_client.post(f"/share/{token}", data={"password": "bad-password"})
    assert wrong.status_code == 401
    assert "密码不正确" in wrong.text

    with app.get_db() as conn:
        failed_audit = conn.execute(
            "SELECT username FROM audit_logs WHERE action = 'share_access' AND detail LIKE '%failed_password%'"
        ).fetchone()
    assert failed_audit is not None
    assert failed_audit["username"] == "anonymous"

    granted = public_client.post(f"/share/{token}", data={"password": "secret123"}, follow_redirects=False)
    assert granted.status_code == 303

    unlocked = public_client.get(f"/share/{token}")
    assert unlocked.status_code == 200
    assert "secret-content" in unlocked.text


def test_revoked_share_link_stops_working(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    owner = create_test_user("revoke-share-owner")
    other = create_test_user("revoke-share-other")
    document_id = insert_document("撤销分享", owner["id"])
    owner_client = authenticated_client(owner["id"])
    token = create_share(owner_client, document_id)
    share_id = get_share_row(token)["id"]

    other_client = authenticated_client(other["id"])
    assert other_client.post(f"/shares/{share_id}/revoke", follow_redirects=False).status_code == 403

    revoked = owner_client.post(
        f"/shares/{share_id}/revoke",
        data={"return_to": f"/documents/{document_id}"},
        follow_redirects=False,
    )
    assert revoked.status_code == 303

    public_client = TestClient(app.app)
    assert public_client.get(f"/share/{token}").status_code == 404

    with app.get_db() as conn:
        audit = conn.execute(
            "SELECT username FROM audit_logs WHERE action = 'revoke_share_link'"
        ).fetchone()
    assert audit is not None


def test_expired_share_link_returns_404(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    owner = create_test_user("expired-share-owner")
    document_id = insert_document("过期分享", owner["id"])
    owner_client = authenticated_client(owner["id"])
    token = create_share(owner_client, document_id, validity="1")

    past = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
    with app.get_db() as conn:
        conn.execute("UPDATE share_links SET expires_at = ? WHERE token = ?", (past, token))
        conn.commit()

    public_client = TestClient(app.app)
    assert public_client.get(f"/share/{token}").status_code == 404


def test_share_of_deleted_document_returns_404(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    owner = create_test_user("deleted-share-owner")
    document_id = insert_document("删除后分享失效", owner["id"])
    owner_client = authenticated_client(owner["id"])
    token = create_share(owner_client, document_id)

    owner_client.post(f"/documents/{document_id}/delete", follow_redirects=False)

    public_client = TestClient(app.app)
    assert public_client.get(f"/share/{token}").status_code == 404


def test_unknown_share_token_returns_404(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)

    public_client = TestClient(app.app)
    response = public_client.get("/share/no-such-token")

    assert response.status_code == 404
    assert "分享链接不存在或已失效" in response.text
