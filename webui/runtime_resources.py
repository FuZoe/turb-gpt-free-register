# -*- coding: utf-8 -*-
"""读取当前 Web 服务所在 cgroup 的资源用量。"""
from __future__ import annotations

import os
import time
from pathlib import Path


PROCESS_STARTED_AT = time.time()


def _read_int(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
        if not raw or raw == "max":
            return None
        return int(raw)
    except (OSError, TypeError, ValueError):
        return None


def _current_cgroup_dir() -> Path | None:
    """定位 cgroup v2 目录；普通本地开发环境返回 None。"""
    try:
        for line in Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines():
            parts = line.split(":", 2)
            if len(parts) == 3 and parts[0] == "0":
                relative = parts[2].lstrip("/")
                path = Path("/sys/fs/cgroup") / relative
                return path if path.exists() else None
    except OSError:
        pass
    return None


def _read_events(path: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            key, value = line.split(None, 1)
            out[key] = int(value)
    except (OSError, TypeError, ValueError):
        pass
    return out


def read_runtime_resources(cgroup_dir: Path | None = None) -> dict:
    cgroup_dir = cgroup_dir or _current_cgroup_dir()
    data = {
        "ok": True,
        "pid": os.getpid(),
        "started_at": PROCESS_STARTED_AT,
        "uptime_seconds": max(0, int(time.time() - PROCESS_STARTED_AT)),
        "cgroup": str(cgroup_dir or ""),
    }
    if cgroup_dir is None:
        data.update({
            "memory_current_bytes": None,
            "memory_peak_bytes": None,
            "memory_max_bytes": None,
            "memory_percent": None,
            "swap_current_bytes": None,
            "swap_max_bytes": None,
            "pids_current": None,
            "pids_max": None,
            "memory_events": {},
        })
        return data

    current = _read_int(cgroup_dir / "memory.current")
    limit = _read_int(cgroup_dir / "memory.max")
    data.update({
        "memory_current_bytes": current,
        "memory_peak_bytes": _read_int(cgroup_dir / "memory.peak"),
        "memory_max_bytes": limit,
        "memory_percent": round(current * 100 / limit, 1) if current is not None and limit else None,
        "swap_current_bytes": _read_int(cgroup_dir / "memory.swap.current"),
        "swap_max_bytes": _read_int(cgroup_dir / "memory.swap.max"),
        "pids_current": _read_int(cgroup_dir / "pids.current"),
        "pids_max": _read_int(cgroup_dir / "pids.max"),
        "memory_events": _read_events(cgroup_dir / "memory.events"),
    })
    return data
