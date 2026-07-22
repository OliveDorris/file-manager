from fastapi.testclient import TestClient

import app
from conftest import admin_user, authenticated_client, configure_temp_app, create_test_user


def test_missing_document_renders_unified_error_page(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()
    client = authenticated_client(user["id"])

    response = client.get("/documents/99999")

    assert response.status_code == 404
    assert "Error" in response.text
    assert "404" in response.text
    assert "返回文件列表" in response.text


def test_unknown_path_renders_unified_error_page(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()
    client = authenticated_client(user["id"])

    response = client.get("/no-such-page")

    assert response.status_code == 404
    assert "404" in response.text
    assert "返回文件列表" in response.text


def test_forbidden_request_renders_unified_error_page(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    regular_user = create_test_user("error-regular")
    client = authenticated_client(regular_user["id"])

    response = client.get("/admin/audit-logs")

    assert response.status_code == 403
    assert "403" in response.text
    assert "返回文件列表" in response.text


def test_unauthenticated_request_still_redirects_to_login(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)

    client = TestClient(app.app)
    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_unhandled_error_renders_unified_error_page(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()

    def raise_runtime_error(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(app, "count_documents", raise_runtime_error)
    client = authenticated_client(user["id"], raise_server_exceptions=False)

    response = client.get("/dashboard")

    assert response.status_code == 500
    assert "500" in response.text
    assert "服务器内部错误" in response.text


def test_json_request_gets_json_error(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()
    client = authenticated_client(user["id"])

    response = client.get("/documents/99999", headers={"accept": "application/json"})

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "Document not found"}


def test_validation_error_renders_unified_error_page(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()
    client = authenticated_client(user["id"])

    response = client.post("/account/users/not-a-number/admin", data={"is_admin": "1"})

    assert response.status_code == 422
    assert "请求参数无效" in response.text
