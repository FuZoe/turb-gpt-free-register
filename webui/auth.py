# -*- coding: utf-8 -*-
"""WebUI 授权码登录与接口鉴权。"""
from __future__ import annotations

import hmac
import hashlib
import json
import logging
import os
import secrets
from datetime import timedelta
from typing import Any

from flask import Response, g, jsonify, redirect, render_template, request, session, url_for

from core.tenant_context import DEFAULT_TENANT, normalize_tenant_id, reset_current_tenant, set_current_tenant

logger = logging.getLogger(__name__)

AUTH_ENV_KEYS = ("WEBUI_AUTH_CODE", "AUTH_CODE", "WEB_AUTH_CODE")
_SESSION_KEY = "webui_auth_ok"
_SESSION_TENANT_KEY = "webui_tenant"
_AUTH_CODE: str | None = None
_TENANT_CODES: dict[str, str] = {}
_GENERATED = False

# 代理管理操作的是同一份 Mihomo 配置，不属于任何租户的数据目录。默认只额外
# 放行用户指定的朋友租户；部署时可用环境变量覆盖或追加其他租户。
_DEFAULT_SHARED_PROXY_MANAGER_TENANTS = frozenset({"tenant2"})


def _shared_proxy_manager_tenants() -> set[str]:
    """返回可与管理员共享全局代理管理的租户集合。"""
    raw = str(os.getenv("WEBUI_PROXY_MANAGER_TENANTS", "") or "").strip()
    if not raw:
        tenants = set(_DEFAULT_SHARED_PROXY_MANAGER_TENANTS)
    else:
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = None
        values = parsed if isinstance(parsed, list) else raw.replace("\n", ",").split(",")
        tenants = {normalize_tenant_id(value) for value in values if str(value or "").strip()}
    tenants.add(DEFAULT_TENANT)
    return tenants


def can_manage_shared_proxies(tenant_id: str) -> bool:
    """判断租户是否可读写管理员的全局 Mihomo 代理配置。"""
    return normalize_tenant_id(tenant_id) in _shared_proxy_manager_tenants()



def _parse_tenant_codes(raw: str) -> dict[str, str]:
    value = str(raw or "").strip()
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        parsed = None
    rows = parsed.items() if isinstance(parsed, dict) else (
        line.split("=", 1) for line in value.splitlines() if "=" in line
    )
    out: dict[str, str] = {}
    for tenant, code in rows:
        tenant_id = normalize_tenant_id(tenant)
        auth_code = str(code or "").strip()
        if auth_code and tenant_id != DEFAULT_TENANT:
            out[tenant_id] = auth_code
    return out

def init_auth(app: Any, *, auth_code: str | None = None) -> str:
    """初始化授权码和 Flask session。未显式配置时生成临时授权码。"""
    global _AUTH_CODE, _TENANT_CODES, _GENERATED

    code = (auth_code or "").strip()
    if not code:
        try:
            from config.env_loader import load_env, env_str
            load_env(override=False)
            for key in AUTH_ENV_KEYS:
                code = env_str(key, "")
                if code:
                    break
        except Exception:
            for key in AUTH_ENV_KEYS:
                code = (os.getenv(key) or "").strip()
                if code:
                    break

    if not code:
        code = secrets.token_urlsafe(18)
        _GENERATED = True
    else:
        _GENERATED = False

    _AUTH_CODE = code
    try:
        from config.env_loader import env_str
        _TENANT_CODES = _parse_tenant_codes(env_str("WEBUI_TENANT_AUTH_CODES", ""))
    except Exception:
        _TENANT_CODES = _parse_tenant_codes(os.getenv("WEBUI_TENANT_AUTH_CODES", ""))
    _TENANT_CODES[DEFAULT_TENANT] = code
    session_secret = os.getenv("WEBUI_SESSION_SECRET") or os.getenv("FLASK_SECRET_KEY")
    if not session_secret:
        # 授权码来自 .env 时，用带命名空间的摘要生成稳定签名密钥；修改授权码会自然注销旧会话。
        session_secret = hashlib.sha256(f"turb-gpt-webui-session:{code}".encode("utf-8")).hexdigest()
    app.secret_key = session_secret
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    )
    return code


def is_generated_code() -> bool:
    return _GENERATED


def expected_auth_code() -> str:
    return _AUTH_CODE or ""


def configured_tenants() -> list[str]:
    return sorted(_TENANT_CODES) or [DEFAULT_TENANT]


def tenant_for_code(code: str) -> str | None:
    candidate = str(code or "")
    if not candidate:
        return None
    for tenant_id, expected in _TENANT_CODES.items():
        if expected and hmac.compare_digest(candidate, expected):
            return tenant_id
    return None


def _extract_auth_code() -> str:
    # 非登录接口只接受 Header 授权码，避免 query/body 中的授权码进入日志、Referer 或业务数据。
    header_code = (request.headers.get("X-Auth-Code") or request.headers.get("X-Authorization-Code") or "").strip()
    if header_code:
        return header_code
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def code_is_valid(code: str) -> bool:
    return tenant_for_code(code) is not None


def request_tenant() -> str | None:
    if session.get(_SESSION_KEY) is True:
        tenant_id = normalize_tenant_id(session.get(_SESSION_TENANT_KEY) or DEFAULT_TENANT)
        if tenant_id in _TENANT_CODES:
            return tenant_id
    return tenant_for_code(_extract_auth_code())


def request_is_authorized() -> bool:
    return request_tenant() is not None


def _wants_json() -> bool:
    if request.path.startswith("/api/"):
        return True
    accept = request.headers.get("Accept") or ""
    return "application/json" in accept and "text/html" not in accept


def _unauthorized_response():
    if _wants_json():
        return jsonify({"ok": False, "error": "未授权：请先登录或提供授权码"}), 401
    return redirect(url_for("auth_login", next=request.path))


def register_auth_routes(app: Any) -> None:
    @app.before_request
    def _require_auth_code():
        endpoint = request.endpoint or ""
        if endpoint in {"auth_login", "auth_logout", "static"}:
            return None
        if request.path in ("/favicon.ico",):
            return Response(status=204)
        tenant_id = request_tenant()
        if tenant_id:
            g.webui_tenant = tenant_id
            g._tenant_context_token = set_current_tenant(tenant_id)
            return None
        return _unauthorized_response()

    @app.teardown_request
    def _reset_tenant_context(_error=None):
        token = getattr(g, "_tenant_context_token", None)
        if token is not None:
            reset_current_tenant(token)

    @app.route("/login", methods=["GET", "POST"], endpoint="auth_login")
    def _auth_login():
        error = ""
        next_url = request.values.get("next") or "/"
        if not str(next_url).startswith("/") or str(next_url).startswith("//"):
            next_url = "/"
        if request.method == "POST":
            code = (request.form.get("auth_code") or "").strip()
            remember = (request.form.get("remember") or "").strip().lower() in ("1", "true", "on", "yes")
            tenant_id = tenant_for_code(code)
            if tenant_id:
                session.permanent = remember
                session[_SESSION_KEY] = True
                session[_SESSION_TENANT_KEY] = tenant_id
                return redirect(next_url)
            error = "授权码错误"
        return render_template("login.html", error=error, next_url=next_url, login_url=url_for("auth_login"))

    @app.post("/logout", endpoint="auth_logout")
    def _auth_logout():
        session.pop(_SESSION_KEY, None)
        session.pop(_SESSION_TENANT_KEY, None)
        if _wants_json():
            return jsonify({"ok": True})
        return redirect(url_for("auth_login"))
