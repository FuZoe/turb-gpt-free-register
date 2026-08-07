from core import db
from core import chatgpt_gcash


def test_gcash_check_keeps_explicit_false(monkeypatch):
    class Response:
        status_code = 200
        text = '{"eligible": false}'

        def json(self):
            return {"eligible": False}

    class Session:
        def post(self, *_args, **_kwargs):
            return Response()

        def close(self):
            pass

    class Browser:
        session = Session()

        def __init__(self, **_kwargs):
            pass

        def _get_common_headers(self):
            return {}

    monkeypatch.setattr(chatgpt_gcash, "BrowserSession", Browser)

    result = chatgpt_gcash.check_gcash_zero_trial("token")

    assert result["ok"] is True
    assert result["gcash_eligible"] is False
    assert result["gcash_http_status"] == 200


def test_gcash_update_does_not_overwrite_plan_check(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(db, "_ACCOUNTS_JSON", tmp_path / "accounts.json")
    monkeypatch.setattr(db, "_ACCOUNTS_TXT", tmp_path / "accounts.txt")
    monkeypatch.setattr(db, "_TOKENS_TXT", tmp_path / "tokens.txt")
    db._write_json(db._ACCOUNTS_JSON, [{
        "id": 1,
        "email": "test@example.com",
        "plan_check_status": "success",
        "plan_check_ok": True,
        "current_plan_type": "free",
        "plus_trial_eligible": True,
    }])

    assert db.update_account_gcash_check(1, result={
        "ok": True,
        "gcash_eligible": False,
        "gcash_checked_at": "2026-08-07T17:00:00",
        "gcash_http_status": 200,
    })

    row = db.get_account(1)
    assert row["plan_check_status"] == "success"
    assert row["plan_check_ok"] is True
    assert row["plus_trial_eligible"] is True
    assert row["gcash_eligible"] is False
    assert row["gcash_check_ok"] is True
