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
