from core.account_export import inject_session_token
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


def test_session_value_returns_empty_for_legacy_row_without_full_session():
    # 老账号只存了 user/account 字段，不能拼凑出可用的完整 session
    row = {
        "email": "old@example.test",
        "access_token": "legacy-token",
        "user_id": "u9",
        "user_name": "Old User",
        "plan_type": "free",
        "extra_json": '{"user": {"id": "u9", "name": "Old User", "email": "old@example.test"}, "account": {"planType": "free"}}',
    }

    assert _account_session_value(row) == ""


def test_session_value_keeps_session_token_field():
    row = {
        "email": "user@example.test",
        "extra_json": '{"session": {"accessToken": "tok", "sessionToken": "next-auth-cookie", "user": {"id": "u1"}}}',
    }

    value = _account_session_value(row)

    assert '"sessionToken": "next-auth-cookie"' in value


def test_session_value_empty_when_no_token_or_user():
    assert _account_session_value({"email": "x@example.test"}) == ""


def test_inject_session_token_reads_nextauth_cookie_list():
    data = {"accessToken": "tok"}
    cookies = [{"name": "other", "value": "x"}, {"name": "__Secure-next-auth.session-token", "value": "secret-token"}]

    inject_session_token(data, cookies)

    assert data["sessionToken"] == "secret-token"


def test_inject_session_token_keeps_existing_value():
    data = {"accessToken": "tok", "sessionToken": "already"}

    inject_session_token(data, [{"name": "__Secure-next-auth.session-token", "value": "other"}])

    assert data["sessionToken"] == "already"
