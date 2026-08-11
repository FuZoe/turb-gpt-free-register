Exit code: 0
Wall time: 0.5 seconds
Output:
# -*- coding: utf-8 -*-
"""CloakBrowser 与协议 BrowserSession 之间的登录态桥接。"""
from __future__ import annotations

import logging
import secrets
import string
from typing import Any

from config import register as _register_cfg
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
              platform: navigator.platform || '',
              vendor: navigator.vendor || '',
              hardwareConcurrency: navigator.hardwareConcurrency || 0,
              deviceMemory: navigator.deviceMemory || 0,
              screenWidth: screen.width || 0,
              screenHeight: screen.height || 0,
              devicePixelRatio: window.devicePixelRatio || 1,
              timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || '',
              timezoneOffset: new Date().getTimezoneOffset(),
              userAgentData: navigator.userAgentData ? {
                brands: navigator.userAgentData.brands || [],
                mobile: !!navigator.userAgentData.mobile,
                platform: navigator.userAgentData.platform || ''
              } : null,
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
        session.browser_profile["navigator_languages"] = languages

    profile_map = {
        "navigator_platform": identity.get("platform"),
        "navigator_vendor": identity.get("vendor"),
        "hardware_concurrency": identity.get("hardwareConcurrency"),
        "device_memory": identity.get("deviceMemory"),
        "screen_width": identity.get("screenWidth"),
        "screen_height": identity.get("screenHeight"),
        "device_pixel_ratio": identity.get("devicePixelRatio"),
        "timezone_iana": identity.get("timezone"),
    }
    for key, value in profile_map.items():
        if value not in (None, "", 0):
            session.browser_profile[key] = value
    try:
        session.browser_profile["timezone_offset_minutes"] = -int(identity.get("timezoneOffset"))
    except (TypeError, ValueError):
        pass

    ua_data = identity.get("userAgentData")
    if isinstance(ua_data, dict):
        brands = [
            item for item in (ua_data.get("brands") or [])
            if isinstance(item, dict) and item.get("brand") and item.get("version")
        ]
        if brands:
            session.browser_profile["send_client_hints"] = True
            session.browser_profile["sec_ch_ua"] = ", ".join(
                f'"{item["brand"]}";v="{item["version"]}"' for item in brands
            )
        platform = str(ua_data.get("platform") or "")
        if platform:
            session.browser_profile["sec_ch_ua_platform"] = f'"{platform}"'
            session.browser_profile["user_agent_data_platform"] = platform
        session.browser_profile["sec_ch_ua_mobile"] = "?1" if ua_data.get("mobile") else "?0"

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


def _generate_registration_password(length: int = 14) -> str:
    """生成同时包含大小写、数字和符号的注册密码。"""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(max(12, int(length))))
        if (
            any(ch.islower() for ch in value)
            and any(ch.isupper() for ch in value)
            and any(ch.isdigit() for ch in value)
            and any(ch in "!@#$%^&*" for ch in value)
        ):
            return value


def _registration_password() -> str:
    configured = str(getattr(_register_cfg, "REGISTER_PASSWORD", "") or "").strip()
    return configured or _generate_registration_password()


def setup_cloak_password(driver: Any, email: str, proxy_url: str | None) -> str:
    """复用纯协议密码分支，在 Cloak 已建立的认证态中创建登录密码。"""
    from core.openai_auth import (
        build_sentinel_header,
        follow_password_registration,
        get_create_account_page,
        register_user,
        request_sentinel_token,
    )

    session = build_browser_session_from_cloak(driver, proxy_url)
    password = _registration_password()
    try:
        get_create_account_page(session)
        challenge = request_sentinel_token(session, "username_password_create")
        sentinel_header, so_header = build_sentinel_header(
            session,
            challenge,
            "username_password_create",
        )
        result = register_user(
            session,
            email,
            password,
            sentinel_header,
            so_header,
        )
        follow_password_registration(session, result)
        sync_browser_session_to_cloak(driver, session)
    finally:
        try:
            session.session.close()
        except Exception:
            pass

    # 协议请求改变了服务端 auth step，写回 Cookie 后让 Cloak 重新加载 OTP 页。
    driver.get("https://auth.openai.com/email-verification")
    logger.info(
        "[Cloak注册][协议密码] 密码创建完成并回写浏览器：email=%s password_length=%s",
        email,
        len(password),
    )
    return password


def setup_cloak_2fa(driver: Any, email: str, proxy_url: str | None) -> str:
    """Browser-native 2FA via fetch() to avoid CF 403 on protocol HTTP."""
    import pyotp
    import time as _t
    from urllib.parse import urlencode as _enc
    from core.email_provider import wait_for_otp as _wotp
    from core.humanize import delay as _hd
    from core.roxy_registration import _click_resend_email_otp as _resend

    logger.info("[Cloak 2FA] browser-native TOTP start")
    did = str(driver.execute_script("return localStorage.getItem('oai-device-id') || '';") or "")

    csrf = driver.execute_script("const r=await fetch('/api/auth/csrf');const d=await r.json();return d.csrfToken;")
    logger.info("[Cloak 2FA] CSRF=%s...", csrf[:20])

    cb = "https://chatgpt.com/?action=enable&factor=totp"
    q = _enc({"connection":"password","login_hint":email,"reauth":"password","max_age":"0","ext-oai-did":did})
    b = _enc({"callbackUrl":cb,"csrfToken":csrf,"json":"true"})
    au = driver.execute_script(
        "const r=await fetch(arguments[0],{method:'POST',headers:{'content-type':'application/x-www-form-urlencoded'},body:arguments[1]});const d=await r.json();return d.url;",
        f"https://chatgpt.com/api/auth/signin/openai?{q}", b,
    )
    logger.info("[Cloak 2FA] auth_url=%s...", au[:80])

    ots = _t.time()
    driver.get(au)
    _hd("navigate")

    otp = None
    for rn in range(1, 4):
        logger.info("[Cloak 2FA] OTP (%d/3)...", rn)
        try:
            otp = _wotp(email, after_ts=ots, max_wait=60)
            break
        except Exception as e:
            if rn >= 3: raise RuntimeError(f"OTP timeout: {e}") from e
            logger.warning("[Cloak 2FA] resend (%d/3)", rn)
            _resend(driver, timeout=25)
            ots = _t.time()
    logger.info("[Cloak 2FA] OTP=%s", otp)

    cont = driver.execute_script(
        "const r=await fetch('https://auth.openai.com/api/accounts/email-otp/validate',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({code:arguments[0]})});const d=await r.json();return d.continue_url;",
        otp,
    )
    if not cont: raise RuntimeError("OTP validate missing continue_url")

    driver.get(cont)
    _hd("navigate")

    nat = driver.execute_script("const r=await fetch('/api/auth/session');const d=await r.json();return d.accessToken;")
    logger.info("[Cloak 2FA] new AT=%s...", nat[:40])

    lang = str(driver.execute_script("return navigator.language || 'en-US';") or "en-US")
    dv2 = str(driver.execute_script("return localStorage.getItem('oai-device-id') || '';") or did)
    enr = driver.execute_script(
        "const r=await fetch('https://chatgpt.com/backend-api/accounts/mfa/enroll',{method:'POST',headers:{'content-type':'application/json',authorization:'Bearer '+arguments[0],'oai-device-id':arguments[1],'oai-language':arguments[2]},body:JSON.stringify({factor_type:'totp'})});const d=await r.json();return d;",
        nat, dv2, lang,
    )
    if isinstance(enr, dict) and enr.get("error"): raise RuntimeError(f"enroll: {enr}")
    sec = enr.get("secret") or enr.get("totp_secret")
    sid = enr.get("session_id")
    if not sec or not sid: raise RuntimeError(f"enroll missing fields: {enr}")
    logger.info("[Cloak 2FA] secret=%s...%s", sec[:4], sec[-4:])

    code = pyotp.TOTP(str(sec)).now()
    act = driver.execute_script(
        "const r=await fetch('https://chatgpt.com/backend-api/accounts/mfa/user/activate_enrollment',{method:'POST',headers:{'content-type':'application/json',authorization:'Bearer '+arguments[0],'oai-device-id':arguments[1],'oai-language':arguments[2]},body:JSON.stringify({code:arguments[3],factor_type:'totp',session_id:arguments[4]})});const d=await r.json();return d;",
        nat, dv2, lang, str(code), str(sid),
    )
    if not act.get("success"): raise RuntimeError(f"activate: {act}")
    logger.info("[Cloak 2FA] done: %s", email)
    return str(sec)