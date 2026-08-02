from unittest.mock import patch

from webui.app import create_app


def _client():
    client = create_app(auth_code="test-auth").test_client()
    client.environ_base["HTTP_X_AUTH_CODE"] = "test-auth"
    return client


@patch("webui.app.db.list_generic_api_email_pool")
def test_email_pool_api_forwards_status_filter(list_pool):
    list_pool.return_value = []

    response = _client().get(
        "/api/outlook?paged=1&page=1&page_size=20&source=generic_api&status=failed"
    )

    assert response.status_code == 200
    list_pool.assert_called_once_with(status="failed", limit=1_000_000)
