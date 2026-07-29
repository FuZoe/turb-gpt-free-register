import pytest

from config import proxy as proxy_cfg
from core.chatgpt_plan import resolve_plan_check_route


def test_invalid_named_plan_proxy_falls_back_to_proxy_pool(monkeypatch):
    monkeypatch.setattr(proxy_cfg, "PLAN_CHECK_PROXY_MODE", "proxy")
    monkeypatch.setattr(proxy_cfg, "PLAN_CHECK_PROXY", "turb-local-20260727")
    monkeypatch.setattr(proxy_cfg, "pick_proxy", lambda: "http://127.0.0.1:7901")

    route = resolve_plan_check_route()

    assert route["proxy"] == "http://127.0.0.1:7901"
    assert route["network_route"] == "proxy"
    assert "不是可连接的代理地址" in route["proxy_fallback_reason"]


def test_explicit_invalid_proxy_is_rejected():
    with pytest.raises(ValueError, match="代理地址无效"):
        resolve_plan_check_route("turb-local-20260727")


def test_host_port_proxy_without_scheme_is_valid():
    route = resolve_plan_check_route("127.0.0.1:7901")

    assert route["proxy"] == "127.0.0.1:7901"
    assert route["network_route"] == "proxy"
