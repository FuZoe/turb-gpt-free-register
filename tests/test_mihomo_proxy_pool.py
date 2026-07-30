import os
from pathlib import Path
from unittest.mock import patch

import pytest

from webui.mihomo_proxy_pool import read_proxy_pool, update_proxy_pool


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


@pytest.mark.parametrize("url", ["ftp://a:b@example:21", "socks5://missing-port"])
def test_rejects_invalid_proxy_urls(tmp_path: Path, url: str):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    with patch.dict(os.environ, {"MIHOMO_CONFIG_PATH": str(path)}):
        with pytest.raises(ValueError):
            update_proxy_pool("jp", [url])
