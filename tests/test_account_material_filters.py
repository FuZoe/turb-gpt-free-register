from core import db
from webui.app import create_app


def _client():
    client = create_app(auth_code="test-auth").test_client()
    client.environ_base["HTTP_X_AUTH_CODE"] = "test-auth"
    return client


def test_account_material_filters_support_each_state_and_combinations(monkeypatch):
    rows = [
        {"id": 1, "email": "both@example.test", "registration_password": "PASS", "totp_secret": "TOTP", "codex_status": "success"},
        {"id": 2, "email": "password@example.test", "registration_password": "PASS", "totp_secret": "", "codex_status": "failed"},
        {"id": 3, "email": "twofa@example.test", "registration_password": "", "totp_secret": "TOTP", "codex_status": ""},
        {"id": 4, "email": "neither@example.test", "registration_password": "", "totp_secret": "", "codex_status": "deactivated"},
    ]
    monkeypatch.setattr(db, "_load_accounts", lambda: rows)

    assert [r["id"] for r in db.list_accounts(twofa_filter="enabled")] == [3, 1]
    assert [r["id"] for r in db.list_accounts(twofa_filter="disabled")] == [4, 2]
    assert [r["id"] for r in db.list_accounts(password_filter="present")] == [2, 1]
    assert [r["id"] for r in db.list_accounts(password_filter="missing")] == [4, 3]
    assert [r["id"] for r in db.list_accounts(codex_filter="incomplete")] == [3, 2]

    result = db.list_accounts_page(twofa_filter="enabled", password_filter="missing")
    assert result["total"] == 1
    assert [r["id"] for r in result["items"]] == [3]


def test_account_status_snapshot_uses_same_material_filters(monkeypatch):
    rows = [
        {"id": 1, "email": "both@example.test", "registration_password": "PASS", "totp_secret": "TOTP"},
        {"id": 2, "email": "neither@example.test", "registration_password": "", "totp_secret": ""},
    ]
    monkeypatch.setattr(db, "_load_accounts", lambda: rows)

    result = db.list_account_plan_check_statuses(twofa_filter="disabled", password_filter="missing")

    assert result["total"] == 1
    assert [r["id"] for r in result["items"]] == [2]


def test_accounts_api_forwards_material_filters(monkeypatch):
    captured = {}

    def fake_list_accounts_page(**kwargs):
        captured.update(kwargs)
        return {"items": [], "total": 0, "offset": 0, "limit": 20}

    monkeypatch.setattr("webui.app.db.list_accounts_page", fake_list_accounts_page)

    response = _client().get(
        "/api/accounts?paged=1&page=2&page_size=20&twofa=disabled&password=present&codex=incomplete"
    )

    assert response.status_code == 200
    assert captured["twofa_filter"] == "disabled"
    assert captured["password_filter"] == "present"
    assert captured["codex_filter"] == "incomplete"


def test_account_status_api_forwards_material_filters(monkeypatch):
    captured = {}

    def fake_statuses(**kwargs):
        captured.update(kwargs)
        return {"items": [], "total": 0, "offset": 0, "limit": 20, "revision": "0"}

    monkeypatch.setattr("webui.app.db.list_account_plan_check_statuses", fake_statuses)

    response = _client().get(
        "/api/accounts/plan-check-status?page=1&page_size=20&twofa=enabled&password=missing"
    )

    assert response.status_code == 200
    assert captured["twofa_filter"] == "enabled"
    assert captured["password_filter"] == "missing"
