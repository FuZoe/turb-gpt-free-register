from unittest.mock import patch

from webui.app import create_app


def _client():
    client = create_app(auth_code="test-auth").test_client()
    client.environ_base["HTTP_X_AUTH_CODE"] = "test-auth"
    return client


@patch("webui.app.extract_link_service.enqueue_account_extract")
@patch("webui.app.db.get_account")
def test_external_ideal_extract_endpoint_passes_user_cdk(get_account, enqueue):
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
        "provider": "external_ideal",
        "link_type": "ideal_external",
    }

    response = _client().post("/api/accounts/extract-link", json={
        "account_id": 7,
        "provider": "external_ideal",
        "cdk": "USER_CDK",
    })

    assert response.status_code == 202
    enqueue.assert_called_once_with(
        account_id=7,
        email="user@example.test",
        access_token="AT_VALUE",
        trigger="manual",
        link_type=None,
        provider="external_ideal",
        cdk="USER_CDK",
    )


@patch("webui.app.extract_link_service.query_cdk")
def test_external_ideal_cdk_endpoint_passes_provider(query_cdk):
    query_cdk.return_value = {"available": 4}

    response = _client().get("/api/extract-link/cdk?provider=external_ideal&code=USER_CDK")

    assert response.status_code == 200
    assert response.get_json()["available"] == 4
    query_cdk.assert_called_once_with(cdk="USER_CDK", provider="external_ideal")
