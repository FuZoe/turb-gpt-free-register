from unittest.mock import patch

from webui.app import create_app


def _app(monkeypatch):
    monkeypatch.setenv("WEBUI_AUTH_CODE", "owner-code")
    monkeypatch.setenv(
        "WEBUI_TENANT_AUTH_CODES",
        '{"tenant2":"friend-code","team-a":"other-code"}',
    )
    monkeypatch.setenv("WEBUI_PROXY_MANAGER_TENANTS", "tenant2")
    app = create_app()
    app.config.update(TESTING=True)
    return app


def test_friend_tenant_can_read_and_modify_shared_proxy_manager(monkeypatch):
    app = _app(monkeypatch)
    headers = {"X-Auth-Code": "friend-code"}
    pools = [{"key": "jp", "label": "JP", "count": 1, "proxies": ["socks5://u:p@host:443"]}]

    with (
        patch("webui.mihomo_proxy_pool.read_all_proxy_pools", return_value=pools),
        patch("webui.mihomo_proxy_pool.registration_route_state", return_value={"pool": "jp"}),
        patch("webui.mihomo_proxy_pool.update_proxy_pool", return_value={"pool": "jp", "count": 1}),
        patch("webui.mihomo_proxy_pool.test_proxy_pool", return_value={"pool": "jp", "results": []}),
        patch("webui.mihomo_proxy_pool.select_registration_pool", return_value={"pool": "jp"}),
    ):
        client = app.test_client()
        assert client.get("/api/proxy-manager", headers=headers).status_code == 200
        assert client.post(
            "/api/proxy-manager/save",
            headers=headers,
            json={"pool": "jp", "proxies": ["socks5://u:p@host:443"]},
        ).status_code == 200
        assert client.post(
            "/api/proxy-manager/test", headers=headers, json={"pool": "jp"}
        ).status_code == 200
        assert client.post(
            "/api/proxy-manager/registration-route", headers=headers, json={"pool": "jp"}
        ).status_code == 200


def test_unlisted_tenant_cannot_access_shared_proxy_manager(monkeypatch):
    app = _app(monkeypatch)
    headers = {"X-Auth-Code": "other-code"}
    client = app.test_client()

    assert client.get("/api/proxy-manager", headers=headers).status_code == 403
    assert client.post("/api/proxy-manager/save", headers=headers, json={}).status_code == 403
    assert client.post("/api/proxy-manager/test", headers=headers, json={}).status_code == 403
    assert client.post(
        "/api/proxy-manager/registration-route", headers=headers, json={}
    ).status_code == 403
