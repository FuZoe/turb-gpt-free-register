import os
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from webui.mihomo_proxy_pool import (
    read_proxy_pool,
    registration_local_proxy_urls,
    test_proxy_pool as run_proxy_pool,
    test_proxy_pool_batch as run_proxy_batch,
    update_proxy_pool,
)


CONFIG = """\
mixed-port: 7890
proxies:
    - name: CLIProxy-Pool-001
      type: socks5
      server: "old1.example"
      port: 443
      username: "old-user-1"
      password: "old-pass"
      udp: false
      dialer-proxy: XFLTD
    - name: CLIProxy-Pool-002
      type: socks5
      server: "old2.example"
      port: 443
      username: "old-user-2"
      password: "old-pass"
      udp: false
      dialer-proxy: XFLTD
    - name: Other-Proxy
      type: direct
proxy-groups:
    - name: CLIProxy-Pool
      type: load-balance
      strategy: round-robin
      proxies:
        - CLIProxy-Pool-001
        - CLIProxy-Pool-002
    - name: Other-Group
      type: select
      proxies:
        - Other-Proxy
"""


@pytest.fixture(autouse=True)
def isolate_runtime_registration_route(monkeypatch):
    """Temporary Mihomo fixtures must never rewrite the host application's .env."""
    monkeypatch.setattr(
        "webui.mihomo_proxy_pool.registration_route_state",
        lambda: {"pool": "", "legacy": False},
    )


def test_read_pool_in_numeric_order(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    with patch.dict(os.environ, {"MIHOMO_CONFIG_PATH": str(path)}):
        pool = read_proxy_pool("jp")
    assert pool["count"] == 2
    assert pool["proxies"] == [
        "socks5://old-user-1:old-pass@old1.example:443",
        "socks5://old-user-2:old-pass@old2.example:443",
    ]


def test_update_pool_replaces_blocks_and_group_members(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    urls = [
        "socks5://new%20user:p%40ss@sg1.example:1080",
        "http://plain:secret@sg2.example:8080",
        "socks5://third:secret@sg3.example:443",
    ]
    with (
        patch.dict(os.environ, {"MIHOMO_CONFIG_PATH": str(path)}),
        patch("webui.mihomo_proxy_pool._validate_config"),
        patch("webui.mihomo_proxy_pool._reload_config"),
    ):
        result = update_proxy_pool("jp", urls)

    updated = path.read_text(encoding="utf-8")
    assert updated.count("- name: CLIProxy-Pool-") == 3
    assert "    - name: CLIProxy-Pool\n" in updated
    assert 'server: "sg1.example"' in updated
    assert 'username: "new user"' in updated
    assert 'password: "p@ss"' in updated
    assert "        - CLIProxy-Pool-003" in updated
    assert "- name: turb-registration-jp-001" in updated
    assert "      port: 7901" in updated
    assert "      proxy: CLIProxy-Pool-001" in updated
    assert "- name: turb-registration-jp-003" in updated
    assert "      port: 7903" in updated
    assert "Other-Proxy" in updated
    assert "Other-Group" in updated
    assert result["count"] == 3


def test_empty_country_pool_is_allowed_with_direct_placeholder(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    with (
        patch.dict(os.environ, {"MIHOMO_CONFIG_PATH": str(path)}),
        patch("webui.mihomo_proxy_pool._validate_config"),
        patch("webui.mihomo_proxy_pool._reload_config"),
    ):
        result = update_proxy_pool("jp", [])
    updated = path.read_text(encoding="utf-8")
    assert "- name: CLIProxy-Pool-001" not in updated
    assert "        - DIRECT" in updated
    assert result["count"] == 0


def test_registration_ports_are_disjoint_and_fixed_per_country():
    assert registration_local_proxy_urls("jp", 3) == [
        "http://127.0.0.1:7901",
        "http://127.0.0.1:7902",
        "http://127.0.0.1:7903",
    ]
    assert registration_local_proxy_urls("tr", 2) == [
        "http://127.0.0.1:8401",
        "http://127.0.0.1:8402",
    ]
    assert registration_local_proxy_urls("vn", 1) == ["http://127.0.0.1:8901"]
    assert registration_local_proxy_urls("mx", 1) == ["http://127.0.0.1:9401"]


def test_jp_update_replaces_legacy_fixed_listeners(tmp_path: Path):
    path = tmp_path / "config.yaml"
    source = (
        "listeners:\n"
        "    - name: turb-cliproxy-01\n"
        "      type: mixed\n"
        "      port: 7901\n"
        "      listen: 127.0.0.1\n"
        "      proxy: Old-Proxy\n\n"
        + CONFIG
    )
    path.write_text(source, encoding="utf-8")
    with (
        patch.dict(os.environ, {"MIHOMO_CONFIG_PATH": str(path)}),
        patch("webui.mihomo_proxy_pool._validate_config"),
        patch("webui.mihomo_proxy_pool._reload_config"),
    ):
        update_proxy_pool("jp", ["http://user:pass@example.com:8080"])

    updated = path.read_text(encoding="utf-8")
    assert "turb-cliproxy-01" not in updated
    assert updated.count("port: 7901") == 1
    assert "proxy: CLIProxy-Pool-001" in updated


@pytest.mark.parametrize("url", ["ftp://a:b@example:21", "socks5://missing-port"])
def test_rejects_invalid_proxy_urls(tmp_path: Path, url: str):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    with patch.dict(os.environ, {"MIHOMO_CONFIG_PATH": str(path)}):
        with pytest.raises(ValueError):
            update_proxy_pool("jp", [url])


def test_proxy_test_retries_only_initial_failures(monkeypatch):
    calls = {"proxy-a": 0, "proxy-b": 0, "proxy-c": 0}

    def fake_test(name, _timeout_ms):
        calls[name] += 1
        if name == "proxy-a" or (name == "proxy-b" and calls[name] > 1):
            return {"name": name, "ok": True, "delay": 123, "error": ""}
        return {"name": name, "ok": False, "delay": None, "error": "HTTP 504: Timeout"}

    monkeypatch.setattr(
        "webui.mihomo_proxy_pool.read_proxy_pool",
        lambda _pool: {"names": ["proxy-a", "proxy-b", "proxy-c"]},
    )
    monkeypatch.setattr("webui.mihomo_proxy_pool._test_one", fake_test)
    monkeypatch.setattr("webui.mihomo_proxy_pool.time.sleep", lambda _seconds: None)

    result = run_proxy_pool("jp", timeout_ms=1000, workers=3)

    assert calls == {"proxy-a": 1, "proxy-b": 2, "proxy-c": 2}
    assert result["success"] == 2
    assert result["failed"] == 1
    assert result["retried"] == 2
    assert result["recovered"] == 1
    assert result["results"][1]["retried"] is True


def test_proxy_test_queues_at_most_five_concurrent_checks(monkeypatch):
    state = {"active": 0, "peak": 0}
    lock = threading.Lock()
    names = [f"proxy-{index}" for index in range(12)]

    def fake_test(name, _timeout_ms):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.02)
        with lock:
            state["active"] -= 1
        return {"name": name, "ok": True, "delay": 100, "error": ""}

    monkeypatch.setattr(
        "webui.mihomo_proxy_pool.read_proxy_pool",
        lambda _pool: {"names": names},
    )
    monkeypatch.setattr("webui.mihomo_proxy_pool._test_one", fake_test)

    result = run_proxy_pool("jp", timeout_ms=1000, workers=24)

    assert state["peak"] == 5
    assert result["concurrency"] == 5
    assert result["success"] == 12
    assert result["failed"] == 0


def test_proxy_batch_returns_five_rows_with_global_indexes(monkeypatch):
    names = [f"proxy-{index}" for index in range(12)]
    seen = []

    def fake_test(name, _timeout_ms):
        seen.append(name)
        return {"name": name, "ok": True, "delay": 100, "error": ""}

    monkeypatch.setattr(
        "webui.mihomo_proxy_pool.read_proxy_pool",
        lambda _pool: {"names": names},
    )
    monkeypatch.setattr("webui.mihomo_proxy_pool._test_one", fake_test)

    result = run_proxy_batch("jp", offset=5, limit=99, timeout_ms=1000)

    assert seen == names[5:10]
    assert [item["index"] for item in result["results"]] == [6, 7, 8, 9, 10]
    assert result["count"] == 5
    assert result["total"] == 12
    assert result["next_offset"] == 10
    assert result["done"] is False
    assert result["concurrency"] == 5
