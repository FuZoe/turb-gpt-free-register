from webui.app import _account_session_value


def test_session_value_uses_saved_full_session_json():
    row = {
        "email": "user@example.test",
        "access_token": "saved-token",
        "extra_json": '{"session": {"accessToken": "full-token", "user": {"id": "u1", "email": "user@example.test"}}}',
    }

    value = _account_session_value(row)

    assert '"accessToken": "full-token"' in value
    assert '"user": {"id": "u1"' in value


def test_session_value_falls_back_to_saved_fields():
    row = {
        "email": "old@example.test",
        "access_token": "legacy-token",
        "user_id": "u9",
        "user_name": "Old User",
        "plan_type": "free",
        "extra_json": '{"user": {"id": "u9", "name": "Old User", "email": "old@example.test"}, "account": {"planType": "free"}}',
    }

    value = _account_session_value(row)

    assert '"accessToken": "legacy-token"' in value
    assert '"email": "old@example.test"' in value


def test_session_value_empty_when_no_token_or_user():
    assert _account_session_value({"email": "x@example.test"}) == ""
