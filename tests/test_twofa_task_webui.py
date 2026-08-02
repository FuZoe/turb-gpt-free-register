from unittest.mock import patch

from webui.app import _normalize_totp_secret, create_app


def _client():
    client = create_app(auth_code="test-auth").test_client()
    client.environ_base["HTTP_X_AUTH_CODE"] = "test-auth"
    return client


@patch("webui.app.twofa_task_service.enqueue_account_twofa")
@patch("webui.app.db.get_account")
def test_create_twofa_endpoint_queues_account(get_account, enqueue):
    get_account.return_value = {"id": 7, "email": "user@example.test", "totp_secret": ""}
    enqueue.return_value = {"accepted": True, "busy": False, "status": "queued"}

    response = _client().post("/api/accounts/create-2fa", json={"account_id": 7})

    assert response.status_code == 202
    assert response.get_json()["started"] is True
    enqueue.assert_called_once_with(account_id=7, email="user@example.test", trigger="manual")


@patch("webui.app.twofa_task_service.enqueue_account_twofa")
@patch("webui.app.db.get_account")
def test_create_twofa_bulk_skips_enabled_accounts(get_account, enqueue):
    get_account.side_effect = lambda account_id: {
        1: {"id": 1, "email": "enabled@example.test", "totp_secret": "SECRET"},
        2: {"id": 2, "email": "missing@example.test", "totp_secret": ""},
    }.get(account_id)
    enqueue.return_value = {"accepted": True, "busy": False, "status": "queued"}

    response = _client().post("/api/accounts/create-2fa-bulk", json={"account_ids": [1, 2]})

    assert response.status_code == 202
    payload = response.get_json()
    assert payload["started_count"] == 1
    assert payload["skipped_count"] == 1
    enqueue.assert_called_once_with(account_id=2, email="missing@example.test", trigger="manual_bulk")


def test_normalize_totp_secret_accepts_base32_and_otpauth_uri():
    assert _normalize_totp_secret("jbsw y3dp-ehpk3pxp") == "JBSWY3DPEHPK3PXP"
    assert _normalize_totp_secret("otpauth://totp/Test?secret=JBSWY3DPEHPK3PXP&issuer=Test") == "JBSWY3DPEHPK3PXP"


@patch("webui.app.db.update_account_twofa_task")
@patch("webui.app.db.get_account")
def test_manual_twofa_endpoint_validates_and_saves_secret(get_account, update_twofa):
    get_account.return_value = {"id": 7, "email": "user@example.test", "totp_secret": "", "twofa_task_status": "failed"}
    update_twofa.return_value = True

    response = _client().post(
        "/api/accounts/7/twofa-secret",
        json={"secret": "jbsw y3dp ehpk3pxp"},
    )

    assert response.status_code == 200
    result = update_twofa.call_args.args[1]
    assert result["totp_secret"] == "JBSWY3DPEHPK3PXP"
    assert result["status"] == "success"


@patch("webui.app.db.get_account")
def test_manual_twofa_endpoint_rejects_invalid_secret(get_account):
    get_account.return_value = {"id": 7, "totp_secret": "", "twofa_task_status": "failed"}

    response = _client().post("/api/accounts/7/twofa-secret", json={"secret": "not-a-secret"})

    assert response.status_code == 400
