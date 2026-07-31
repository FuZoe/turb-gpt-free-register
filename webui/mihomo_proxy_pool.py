# -*- coding: utf-8 -*-
"""Manage and test the Mihomo proxy pools used by the registration service."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urlsplit
from urllib.request import Request, urlopen


DEFAULT_CONFIG_PATH = Path.home() / ".config" / "mihomo" / "config.yaml"
DEFAULT_CONTROLLER = "http://127.0.0.1:9090"
TEST_URL = "https://www.gstatic.com/generate_204"
MAX_TEST_CONCURRENCY = 5

POOL_DEFINITIONS = {
    "jp": {
        "label": "JP 代理池",
        "description": "由 127.0.0.1:7891 提供 round-robin 负载均衡",
        "prefix": "CLIProxy-Pool-",
        "group": "CLIProxy-Pool",
        "local_entry": "http://127.0.0.1:7891",
        "listener_name": "cliproxy-in",
        "listener_port": 7891,
        "min_count": 0,
        "max_count": 500,
    },
    "vn": {
        "label": "VN 代理池",
        "description": "由 127.0.0.1:7893 提供 round-robin 负载均衡",
        "prefix": "CLIProxy-VN-",
        "group": "CLIProxy-VN-Pool",
        "local_entry": "http://127.0.0.1:7893",
        "listener_name": "cliproxy-vn-in",
        "listener_port": 7893,
        "min_count": 0,
        "max_count": 500,
    },
    "tr": {
        "label": "TR 代理池",
        "description": "由 127.0.0.1:7892 提供 round-robin 负载均衡",
        "prefix": "CLIProxy-TR-",
        "group": "CLIProxy-TR-Pool",
        "local_entry": "http://127.0.0.1:7892",
        "listener_name": "cliproxy-tr-in",
        "listener_port": 7892,
        "min_count": 0,
        "max_count": 500,
    },
    "mx": {
        "label": "MX 代理池",
        "description": "由 127.0.0.1:7894 提供 round-robin 负载均衡",
        "prefix": "CLIProxy-MX-",
        "group": "CLIProxy-MX-Pool",
        "local_entry": "http://127.0.0.1:7894",
        "listener_name": "cliproxy-mx-in",
        "listener_port": 7894,
        "min_count": 0,
        "max_count": 500,
    },
}


def _config_path() -> Path:
    return Path(os.getenv("MIHOMO_CONFIG_PATH", str(DEFAULT_CONFIG_PATH))).expanduser()


def _controller_base() -> str:
    return os.getenv("MIHOMO_CONTROLLER_URL", DEFAULT_CONTROLLER).rstrip("/")


def _definition(pool_key: str) -> dict[str, object]:
    try:
        return POOL_DEFINITIONS[pool_key]
    except KeyError as exc:
        raise ValueError(f"未知代理池：{pool_key}") from exc


def _parse_proxy_url(raw: str) -> dict[str, object]:
    value = str(raw or "").strip()
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"代理 URL 无效：{value!r}（{exc}）") from exc
    scheme = parsed.scheme.lower()
    if scheme not in {"socks5", "socks5h", "http"}:
        raise ValueError(f"仅支持 socks5:// 或 http:// 代理：{value!r}")
    if not parsed.hostname or port is None:
        raise ValueError(f"代理 URL 必须包含主机和端口：{value!r}")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError(f"代理 URL 不能包含路径、查询参数或片段：{value!r}")
    return {
        "type": "socks5" if scheme.startswith("socks5") else "http",
        "server": parsed.hostname,
        "port": port,
        "username": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
    }


def _yaml_scalar(value: object) -> str:
    if isinstance(value, int):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _all_proxy_blocks(source: str, prefix: str) -> dict[int, tuple[int, int, str]]:
    pattern = re.compile(
        rf"(?ms)^    - name:\s*{re.escape(prefix)}(?P<num>\d+)\s*$"
        rf".*?(?=^    - name:|\Z)"
    )
    result: dict[int, tuple[int, int, str]] = {}
    for match in pattern.finditer(source):
        result[int(match.group("num"))] = (match.start(), match.end(), match.group(0))
    return result


def _block_value(block: str, key: str) -> str:
    match = re.search(rf"(?m)^      {re.escape(key)}:\s*(.*?)\s*$", block)
    if not match:
        return ""
    raw = match.group(1).strip()
    try:
        value = json.loads(raw)
    except Exception:
        value = raw.strip("\"'")
    return str(value)


def _block_to_url(block: str) -> str | None:
    proxy_type = _block_value(block, "type")
    server = _block_value(block, "server")
    port = _block_value(block, "port")
    username = _block_value(block, "username")
    password = _block_value(block, "password")
    if not proxy_type or not server or not port:
        return None
    auth = ""
    if username or password:
        auth = f"{quote(username, safe='')}:{quote(password, safe='')}@"
    return f"{proxy_type}://{auth}{server}:{port}"


def read_proxy_pool(pool_key: str) -> dict[str, object]:
    definition = _definition(pool_key)
    source = _config_path().read_text(encoding="utf-8")
    blocks = _all_proxy_blocks(source, str(definition["prefix"]))
    proxies = []
    names = []
    for number in sorted(blocks):
        url = _block_to_url(blocks[number][2])
        if url:
            proxies.append(url)
            names.append(f"{definition['prefix']}{number:03d}")
    return {
        "key": pool_key,
        "label": definition["label"],
        "description": definition["description"],
        "local_entry": definition["local_entry"],
        "min_count": definition["min_count"],
        "max_count": definition["max_count"],
        "count": len(proxies),
        "proxies": proxies,
        "names": names,
    }


def read_all_proxy_pools() -> list[dict[str, object]]:
    return [read_proxy_pool(key) for key in POOL_DEFINITIONS]


def registration_local_proxy_urls() -> list[str]:
    return [f"http://127.0.0.1:{port}" for port in range(7901, 7911)]


def _render_proxy_block(name: str, proxy: dict[str, object]) -> str:
    return (
        f"    - name: {name}\n"
        f"      type: {_yaml_scalar(proxy['type'])}\n"
        f"      server: {_yaml_scalar(proxy['server'])}\n"
        f"      port: {proxy['port']}\n"
        f"      username: {_yaml_scalar(proxy['username'])}\n"
        f"      password: {_yaml_scalar(proxy['password'])}\n"
        "      udp: false\n"
        "      dialer-proxy: XFLTD\n"
    )


def _replace_group_members(source: str, group_name: str, names: list[str]) -> str:
    group_pattern = re.compile(
        rf"(?ms)^    - name:\s*{re.escape(group_name)}\s*$.*?(?=^    - name:|\Z)"
    )
    match = group_pattern.search(source)
    if not match:
        raise RuntimeError(f"Mihomo 缺少代理组：{group_name}")
    block = match.group(0)
    members_pattern = re.compile(r"(?m)^(      proxies:\s*\n)(?:        - .*\n)*")
    if not members_pattern.search(block):
        raise RuntimeError(f"代理组 {group_name} 缺少 proxies 列表")
    members = "".join(f"        - {name}\n" for name in names) if names else "        - DIRECT\n"
    updated_block = members_pattern.sub(lambda m: m.group(1) + members, block, count=1)
    return source[: match.start()] + updated_block + source[match.end() :]


def _validate_config(path: Path) -> None:
    binary = os.getenv("MIHOMO_BINARY", "/usr/local/bin/mihomo")
    result = subprocess.run(
        [binary, "-t", "-f", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "未知错误").strip()
        raise RuntimeError(f"Mihomo 配置校验失败：{detail[-1200:]}")


def _reload_config(path: Path) -> None:
    body = json.dumps({"path": str(path)}).encode("utf-8")
    request = Request(
        f"{_controller_base()}/configs?force=true",
        data=body,
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=10) as response:
            if response.status >= 300:
                raise RuntimeError(f"Mihomo 热加载返回 HTTP {response.status}")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"Mihomo 热加载失败：{exc}") from exc


def update_proxy_pool(pool_key: str, lines: list[str]) -> dict[str, object]:
    definition = _definition(pool_key)
    values = [str(line or "").strip() for line in lines if str(line or "").strip()]
    minimum = int(definition["min_count"])
    maximum = int(definition["max_count"])
    if not minimum <= len(values) <= maximum:
        raise ValueError(f"{definition['label']}需填写 {minimum}–{maximum} 条代理，当前为 {len(values)} 条")
    parsed = [_parse_proxy_url(value) for value in values]

    path = _config_path()
    source = path.read_text(encoding="utf-8")
    blocks = _all_proxy_blocks(source, str(definition["prefix"]))
    names = [f"{definition['prefix']}{index:03d}" for index in range(1, len(parsed) + 1)]
    rendered = "".join(_render_proxy_block(name, proxy) for name, proxy in zip(names, parsed))
    if blocks:
        ordered = [blocks[number] for number in sorted(blocks)]
        first_start, last_end = ordered[0][0], ordered[-1][1]
        updated = source[:first_start] + rendered + source[last_end:]
    else:
        marker = re.search(r"(?m)^proxy-groups:\s*$", source)
        if not marker:
            raise RuntimeError("Mihomo 配置缺少 proxy-groups 段")
        updated = source[: marker.start()] + rendered + source[marker.start() :]
    updated = _replace_group_members(updated, str(definition["group"]), names)

    if updated == source:
        return {"pool": pool_key, "count": len(values), "changed": False}

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.backup-webui-{pool_key}-{timestamp}")
    shutil.copy2(path, backup)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, path.stat().st_mode)
        temp_path.replace(path)
        try:
            _validate_config(path)
            _reload_config(path)
        except Exception:
            shutil.copy2(backup, path)
            try:
                _reload_config(path)
            except Exception:
                pass
            raise
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return {
        "pool": pool_key,
        "count": len(values),
        "changed": True,
        "backup": str(backup),
    }


def _test_one(name: str, timeout_ms: int) -> dict[str, object]:
    params = urlencode({"timeout": timeout_ms, "url": TEST_URL})
    url = f"{_controller_base()}/proxies/{quote(name, safe='')}/delay?{params}"
    try:
        with urlopen(url, timeout=(timeout_ms / 1000) + 3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        delay = int(payload.get("delay") or 0)
        return {"name": name, "ok": delay > 0, "delay": delay, "error": ""}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        return {"name": name, "ok": False, "delay": None, "error": f"HTTP {exc.code}: {detail}"}
    except Exception as exc:
        return {"name": name, "ok": False, "delay": None, "error": str(exc)}


def test_proxy_pool(pool_key: str, timeout_ms: int = 8000, workers: int = 24) -> dict[str, object]:
    pool = read_proxy_pool(pool_key)
    names = list(pool["names"])
    timeout_ms = max(1000, min(int(timeout_ms), 30000))
    workers = max(1, min(int(workers), MAX_TEST_CONCURRENCY, len(names) or 1))
    indexed_results: dict[int, dict[str, object]] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_test_one, name, timeout_ms): index
            for index, name in enumerate(names, start=1)
        }
        for future in as_completed(futures):
            index = futures[future]
            result = future.result()
            result["index"] = index
            indexed_results[index] = result
    # 大批代理通常共用同一个供应商网关。首轮高并发会让少数本来可用的
    # 线路收到 Mihomo 503/504；等待片刻后仅以低并发重试失败项，避免把
    # 网关瞬时拥塞误报成代理永久失效。
    failed_indexes = [index for index, item in indexed_results.items() if not item["ok"]]
    recovered = 0
    if failed_indexes:
        time.sleep(0.5)
        retry_workers = max(1, min(MAX_TEST_CONCURRENCY, len(failed_indexes)))
        with ThreadPoolExecutor(max_workers=retry_workers) as executor:
            retry_futures = {
                executor.submit(_test_one, names[index - 1], timeout_ms): index
                for index in failed_indexes
            }
            for future in as_completed(retry_futures):
                index = retry_futures[future]
                retry_result = future.result()
                retry_result["index"] = index
                retry_result["retried"] = True
                if retry_result["ok"]:
                    recovered += 1
                indexed_results[index] = retry_result

    results = [indexed_results[index] for index in sorted(indexed_results)]
    success = sum(1 for item in results if item["ok"])
    delays = [int(item["delay"]) for item in results if item["ok"] and item["delay"] is not None]
    return {
        "pool": pool_key,
        "count": len(results),
        "success": success,
        "failed": len(results) - success,
        "concurrency": workers,
        "retried": len(failed_indexes),
        "recovered": recovered,
        "average_delay": round(sum(delays) / len(delays)) if delays else None,
        "results": results,
    }


def _listener_block(definition: dict[str, object]) -> str:
    return (
        f"    - name: {definition['listener_name']}\n"
        "      type: mixed\n"
        f"      port: {definition['listener_port']}\n"
        "      listen: 127.0.0.1\n"
        f"      proxy: {definition['group']}\n\n"
    )


def _group_block(definition: dict[str, object]) -> str:
    return (
        f"    - name: {definition['group']}\n"
        "      type: load-balance\n"
        "      strategy: round-robin\n"
        f"      url: {TEST_URL}\n"
        "      interval: 300\n"
        "      proxies:\n"
        "        - DIRECT\n"
    )


def provision_missing_pool_structures() -> dict[str, object]:
    """Add empty VN/MX listeners and groups without touching existing pools."""
    path = _config_path()
    source = path.read_text(encoding="utf-8")
    updated = source
    added = []
    for pool_key, definition in POOL_DEFINITIONS.items():
        listener_name = str(definition["listener_name"])
        group_name = str(definition["group"])
        if not re.search(rf"(?m)^    - name:\s*{re.escape(listener_name)}\s*$", updated):
            listeners = re.search(r"(?m)^listeners:\s*$", updated)
            dns = re.search(r"(?m)^dns:\s*$", updated)
            if not listeners or not dns or dns.start() <= listeners.end():
                raise RuntimeError("无法定位 Mihomo listeners 段")
            updated = updated[: dns.start()] + _listener_block(definition) + "\n" + updated[dns.start() :]
            added.append(f"listener:{listener_name}")
        if not re.search(rf"(?m)^    - name:\s*{re.escape(group_name)}\s*$", updated):
            marker = re.search(r"(?m)^proxy-groups:\s*$", updated)
            if not marker:
                raise RuntimeError("无法定位 Mihomo proxy-groups 段")
            insert_at = marker.end()
            updated = updated[:insert_at] + "\n" + _group_block(definition) + updated[insert_at:]
            added.append(f"group:{group_name}")
    if updated == source:
        return {"changed": False, "added": []}

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.backup-webui-pool-structure-{timestamp}")
    shutil.copy2(path, backup)
    path.write_text(updated, encoding="utf-8")
    try:
        _validate_config(path)
        _reload_config(path)
    except Exception:
        shutil.copy2(backup, path)
        try:
            _reload_config(path)
        except Exception:
            pass
        raise
    return {"changed": True, "added": added, "backup": str(backup)}


def registration_route_state() -> dict[str, object]:
    try:
        import config as config_pkg
        configured = list(getattr(config_pkg, "PROXY_POOL", []) or [])
    except Exception:
        configured = []
    for pool_key, definition in POOL_DEFINITIONS.items():
        if configured == [definition["local_entry"]]:
            pool = read_proxy_pool(pool_key)
            return {
                "pool": pool_key,
                "label": definition["label"],
                "local_entry": definition["local_entry"],
                "count": pool["count"],
                "legacy": False,
            }
    return {
        "pool": "",
        "label": "旧注册固定线路（待切换）" if configured else "未配置",
        "local_entry": "http://127.0.0.1:7901–7910" if configured else "",
        "count": len(configured),
        "legacy": bool(configured),
    }


def select_registration_pool(pool_key: str) -> dict[str, object]:
    definition = _definition(pool_key)
    pool = read_proxy_pool(pool_key)
    if int(pool["count"]) <= 0:
        raise ValueError(f"{definition['label']}还是空的，请先添加代理")
    from config.env_loader import load_env, write_env_values
    write_env_values({"PROXY_POOL": str(definition["local_entry"])})
    load_env(override=True)
    try:
        import config as config_pkg
        config_pkg.reload_all()
    except Exception:
        pass
    return registration_route_state()


# Backward-compatible helpers used by older callers.
def read_upstream_proxy_urls() -> list[str]:
    return []


def local_proxy_urls(count: int) -> list[str]:
    return registration_local_proxy_urls()[:count]


def update_upstream_proxy_urls(lines: list[str]) -> dict[str, object]:
    raise ValueError("注册线路已改为选择国家代理池，请使用 select_registration_pool")
