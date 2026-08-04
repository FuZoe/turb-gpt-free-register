from unittest.mock import patch

from webui.app import create_app


def _client():
    client = create_app(auth_code="test-auth").test_client()
    client.environ_base["HTTP_X_AUTH_CODE"] = "test-auth"
    return client


@patch("webui.app.extract_link_service.enqueue_account_extract")
@patch("webui.app.db.get_account")
def test_external_nl_extract_endpoint_passes_user_cdk(get_account, enqueue):
    get_account.return_value = {
        "id": 7,
        "email": "user@example.test",
        "access_token": "AT_VALUE",
        "current_plan_type": "free",
        "plus_trial_eligible": True,
    }
    enqueue.return_value = {
        "accepted": True,
        "busy": False,
        "provider": "external_nl",
        "link_type": "upi_external_nl",
    }

    response = _client().post("/api/accounts/extract-link", json={
        "account_id": 7,
        "provider": "external_nl",
        "cdk": "USER_CDK",
    })

    assert response.status_code == 202
    enqueue.assert_called_once_with(
        account_id=7,
        email="user@example.test",
        access_token="AT_VALUE",
        trigger="manual",
        link_type=None,
        provider="external_nl",
        cdk="USER_CDK",
    )


@patch("webui.app.extract_link_service.query_cdk")
def test_external_nl_cdk_endpoint_passes_provider(query_cdk):
    query_cdk.return_value = {"available": 4}

    response = _client().get("/api/extract-link/cdk?provider=external_nl&code=USER_CDK")

    assert response.status_code == 200
    assert response.get_json()["available"] == 4
    query_cdk.assert_called_once_with(cdk="USER_CDK", provider="external_nl")
