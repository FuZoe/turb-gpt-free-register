"""Persistent per-account logs for password and 2FA background tasks."""
from __future__ import annotations

import logging
import re
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from core.tenant_context import tenant_path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOG_DIR = _PROJECT_ROOT / "注册日志" / "account-tasks"
_TASK_TYPES = {"password", "twofa"}
_TASK_LABELS = {"password": "密码补跑", "twofa": "2FA补跑"}


def normalize_task_type(task_type: str) -> str:
    value = str(task_type or "").strip().lower()
    if value not in _TASK_TYPES:
        raise ValueError("task_type 仅支持 password/twofa")
    return value


def _safe_email(email: str) -> str:
    value = str(email or "").strip().lower()
    return re.sub(r"[^a-z0-9@._+-]+", "_", value) or "unknown"


def log_path(task_type: str, email: str) -> Path:
    task_type = normalize_task_type(task_type)
    return tenant_path(_PROJECT_ROOT, _LOG_DIR) / f"{task_type}-{_safe_email(email)}.log"


def append(task_type: str, email: str, level: str, message: str) -> None:
    path = log_path(task_type, email)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H:%M:%S")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"{stamp} [{str(level or 'INFO').upper()}] {message}\n")


def initialize(task_type: str, email: str, *, account_id: int, trigger: str) -> Path:
    task_type = normalize_task_type(task_type)
    path = log_path(task_type, email)
    path.parent.mkdir(parents=True, exist_ok=True)
    label = _TASK_LABELS[task_type]
    path.write_text(
        f"{datetime.now().strftime('%H:%M:%S')} [INFO] "
        f"[{label}] 已入队：account_id={int(account_id)} email={email} trigger={trigger or 'manual'}\n",
        encoding="utf-8",
    )
    return path


@contextmanager
def capture(task_type: str, email: str) -> Iterator[Path]:
    """Append logs emitted by the current task thread and its same-name child thread."""
    path = log_path(task_type, email)
    path.parent.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    handler: logging.FileHandler | None = None
    thread_name = threading.current_thread().name
    try:
        handler = logging.FileHandler(str(path), mode="a", encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%H:%M:%S",
        ))
        handler.addFilter(lambda record: record.threadName == thread_name)
        root_logger.addHandler(handler)
    except Exception:
        handler = None
    try:
        yield path
    finally:
        if handler is not None:
            try:
                handler.flush()
                root_logger.removeHandler(handler)
                handler.close()
            except Exception:
                pass


def read(task_type: str, email: str, *, max_bytes: int = 100_000) -> str:
    path = log_path(task_type, email)
    if not path.exists():
        return ""
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > max_bytes:
            fh.seek(size - max_bytes)
        return fh.read().decode("utf-8", errors="replace")
