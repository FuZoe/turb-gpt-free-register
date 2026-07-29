"""WebUI 多租户上下文；默认租户继续使用项目根目录中的现有数据。"""
from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any, Callable

DEFAULT_TENANT = "default"
_CURRENT_TENANT: ContextVar[str] = ContextVar("turb_tenant", default=DEFAULT_TENANT)


def normalize_tenant_id(value: object) -> str:
    raw = str(value or DEFAULT_TENANT).strip().lower()
    clean = re.sub(r"[^a-z0-9_-]+", "-", raw).strip("-_")[:48]
    return clean or DEFAULT_TENANT


def current_tenant() -> str:
    return normalize_tenant_id(_CURRENT_TENANT.get())


def set_current_tenant(tenant_id: str) -> Token:
    return _CURRENT_TENANT.set(normalize_tenant_id(tenant_id))


def reset_current_tenant(token: Token) -> None:
    _CURRENT_TENANT.reset(token)


@contextmanager
def tenant_scope(tenant_id: str):
    token = set_current_tenant(tenant_id)
    try:
        yield current_tenant()
    finally:
        reset_current_tenant(token)


def run_for_tenant(tenant_id: str, func: Callable[..., Any], *args, **kwargs):
    with tenant_scope(tenant_id):
        return func(*args, **kwargs)


def tenant_root(project_root: str | Path) -> Path:
    root = Path(project_root)
    tenant = current_tenant()
    return root if tenant == DEFAULT_TENANT else root / "tenants" / tenant


def tenant_path(project_root: str | Path, path: str | Path) -> Path:
    root = Path(project_root)
    target = Path(path)
    if current_tenant() == DEFAULT_TENANT:
        return target
    try:
        relative = target.relative_to(root)
    except ValueError:
        return target
    return tenant_root(root) / relative
