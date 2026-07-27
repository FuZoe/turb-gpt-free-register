# -*- coding: utf-8 -*-
"""CloakBrowser 与协议 BrowserSession 之间的登录态桥接。"""
from __future__ import annotations

import logging
from typing import Any

from core.session import BrowserSession

logger = logging.getLogger(__name__)


def _cloak_context(driver: Any):
    context = getattr(driver, "context", None)
    if context is not None:
        return context
    page = getattr(driver, "page", None)
    return getattr(page, "context", None)


def _browser_identity(driver: Any) -> dict:
    try:
        return driver.execute_script(
            """
            let deviceId = '';
            try {
              deviceId = localStorage.getItem('oai-did') || localStorage.getItem('oai-device-id') || '';
            } catch (e) {}
            return {
              userAgent: navigator.userAgent || '',
              language: navigator.language || '',
              languages: Array.isArray(navigator.languages) ? navigator.languages : [],
              deviceId
            };
            """
        ) or {}
    except Exception as exc:
        logger.debug("[Cloak会话] 读取浏览器身份失败：%s: %s", type(exc).__name__, exc)
        return {}


def build_browser_session_from_cloak(driver: Any, proxy_url: str | None) -> BrowserSession:
    """复制 Cloak Cookie/UA/语言，并继续使用同一代理出口。"""
    context = _cloak_context(driver)
    if context is None:
        raise RuntimeError("Cloak 上下文为空，未建立 2FA 会话桥接")

    cookies = list(context.cookies() or [])
    if not cookies:
        raise RuntimeError("Cloak Cookie 为空，未建立 2FA 会话桥接")

    # 显式空字符串代表直连，避免 BrowserSession 再从代理池随机抽取另一条线路。
    session = BrowserSession(proxy=proxy_url or "", detect_exit_geo=False)
    copied = 0
    browser_device_id = ""
    for cookie in cookies:
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        if not name:
            continue
        domain = str(cookie.get("domain") or "")
        path = str(cookie.get("path") or "/")
        secure = bool(cookie.get("secure"))
        session.session.cookies.set(name, value, domain=domain, path=path, secure=secure)
        copied += 1
        if name == "oai-did" and value:
            browser_device_id = value

    identity = _browser_identity(driver)
    browser_device_id = browser_device_id or str(identity.get("deviceId") or "")
    if browser_device_id:
        session.device_id = browser_device_id
        for domain in ("chatgpt.com", "auth.openai.com", "sentinel.openai.com"):
            session.session.cookies.set("oai-did", browser_device_id, domain=domain, path="/", secure=True)

    user_agent = str(identity.get("userAgent") or "")
    language = str(identity.get("language") or "")
    languages = [str(x) for x in (identity.get("languages") or []) if str(x)]
    if user_agent:
        session.browser_profile["user_agent"] = user_agent
    if language:
        session.browser_profile["navigator_language"] = language
    if languages:
        session.browser_profile["accept_language"] = ",".join(languages)

    session._cf_cookie_seen = session.cf_cookie_snapshot()
    logger.info(
        "[Cloak会话] 已桥接浏览器登录态：cookies=%s device_id=%s ua=%s proxy=%s",
        copied,
        "matched" if browser_device_id else "generated",
        "matched" if user_agent else "default",
        "same" if proxy_url else "direct",
    )
    return session


def sync_browser_session_to_cloak(driver: Any, session: BrowserSession) -> int:
    """把 2FA 重认证产生的新 Cookie 写回 Cloak，保留后续浏览器登录态。"""
    context = _cloak_context(driver)
    if context is None:
        return 0
    payload = []
    for cookie in session.session.cookies.jar:
        name = str(getattr(cookie, "name", "") or "")
        value = str(getattr(cookie, "value", "") or "")
        domain = str(getattr(cookie, "domain", "") or "")
        if not name or not domain:
            continue
        item = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": str(getattr(cookie, "path", "/") or "/"),
            "secure": bool(getattr(cookie, "secure", False)),
        }
        expires = getattr(cookie, "expires", None)
        if expires:
            item["expires"] = float(expires)
        payload.append(item)
    if payload:
        context.add_cookies(payload)
    logger.info("[Cloak会话] 已把 2FA 重认证 Cookie 写回浏览器：cookies=%s", len(payload))
    return len(payload)


def setup_cloak_2fa(driver: Any, email: str, proxy_url: str | None) -> str:
    """基于 Cloak 的真实登录态调用 Turb 现有 2FA 协议流程。"""
    from core.account_export import fetch_session, setup_2fa

    session = build_browser_session_from_cloak(driver, proxy_url)
    try:
        linked = fetch_session(session)
        linked_email = str((linked.get("user") or {}).get("email") or "")
        if linked_email and linked_email.lower() != str(email or "").lower():
            raise RuntimeError(f"Cloak 会话邮箱不一致：expected={email} actual={linked_email}")
        secret = setup_2fa(session, email)
        sync_browser_session_to_cloak(driver, session)
        return secret
    finally:
        try:
            session.session.close()
        except Exception:
            pass
