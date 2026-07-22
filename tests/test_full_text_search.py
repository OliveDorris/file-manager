import app
import services.search_service as search_service
from conftest import (
    admin_user,
    authenticated_client,
    configure_temp_app,
    create_test_user,
    insert_document,
)


def test_content_match_search_shows_snippet(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()
    insert_document("普通标题", user["id"], content="body contains uniquekeyalpha here")
    client = authenticated_client(user["id"])

    response = client.get("/dashboard?q=uniquekeyalpha")

    assert response.status_code == 200
    assert "共 1 条" in response.text
    assert "普通标题" in response.text
    assert "内容命中" in response.text
    assert "uniquekeyalpha" in response.text


def test_title_match_search_shows_title_badge(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()
    insert_document("Titlexyz 报告", user["id"], content="nothing special")
    client = authenticated_client(user["id"])

    response = client.get("/dashboard?q=Titlexyz")

    assert response.status_code == 200
    assert "共 1 条" in response.text
    assert "标题命中" in response.text


def test_search_does_not_bypass_permission_rules(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    owner = admin_user()
    regular = create_test_user("search-regular")
    document_id = insert_document("受保护文件", owner["id"], content="protectedkeybeta inside")
    regular_client = authenticated_client(regular["id"])

    response = regular_client.get("/dashboard?q=protectedkeybeta")

    assert response.status_code == 200
    assert "受保护文件" in response.text
    assert regular_client.get(f"/documents/{document_id}/download").status_code == 403


def test_soft_deleted_document_not_searchable(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()
    document_id = insert_document("将被删除", user["id"], content="deletedkeygamma content")
    client = authenticated_client(user["id"])

    before = client.get("/dashboard?q=deletedkeygamma")
    assert "共 1 条" in before.text

    client.post(f"/documents/{document_id}/delete", follow_redirects=False)
    after = client.get("/dashboard?q=deletedkeygamma")
    assert "共 0 条" in after.text

    client.post(f"/documents/{document_id}/restore", follow_redirects=False)
    restored = client.get("/dashboard?q=deletedkeygamma")
    assert "共 1 条" in restored.text


def test_title_update_refreshes_search_index(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()
    document_id = insert_document("oldtitleqq", user["id"])
    client = authenticated_client(user["id"])

    renamed = client.post(
        f"/documents/{document_id}/metadata",
        data={"title": "newtitleqq", "category_id": ""},
        follow_redirects=False,
    )
    assert renamed.status_code == 303

    old_search = client.get("/dashboard?q=oldtitleqq")
    assert "共 0 条" in old_search.text
    new_search = client.get("/dashboard?q=newtitleqq")
    assert "共 1 条" in new_search.text


def test_new_version_content_updates_search_index(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()
    document_id = insert_document("版本索引文件", user["id"], content="firstkeydelta")
    client = authenticated_client(user["id"])

    uploaded = client.post(
        f"/documents/{document_id}/versions",
        data={"notes": ""},
        files={"file": ("v2.txt", b"secondkeydelta", "text/plain")},
        follow_redirects=False,
    )
    assert uploaded.status_code == 303

    old_search = client.get("/dashboard?q=firstkeydelta")
    assert "共 0 条" in old_search.text
    new_search = client.get("/dashboard?q=secondkeydelta")
    assert "共 1 条" in new_search.text


def test_search_snippet_is_html_escaped(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()
    insert_document("转义测试", user["id"], content="xx <script>alert(1)</script> yy")
    client = authenticated_client(user["id"])

    response = client.get("/dashboard?q=alert")

    assert response.status_code == 200
    assert "共 1 条" in response.text
    assert "&lt;script&gt;" in response.text
    assert "<script>alert(1)</script>" not in response.text


def test_search_falls_back_to_filename_match_when_fts_disabled(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()
    insert_document("fallbacktitle", user["id"])
    client = authenticated_client(user["id"])

    monkeypatch.setattr(search_service, "FTS_ENABLED", False)
    response = client.get("/dashboard?q=fallbacktitle")

    assert response.status_code == 200
    assert "共 1 条" in response.text
    assert "fallbacktitle" in response.text
