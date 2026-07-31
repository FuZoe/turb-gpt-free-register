"""Log into an existing ChatGPT account with Cloak and enroll TOTP."""
from __future__ import annotations

import logging
import threading
from typing import Any

from config import cloakbrowser as _cfg
from core.cloakbrowser_driver import build_cloak_driver
from core.cloakbrowser_registration import (
    _capture_cloak_failure_diagnostics,
    _is_proxy_navigation_failure,
)
from core.cloakbrowser_session import setup_cloak_2fa
from core.email_provider import wait_for_otp
from core.roxy_codex_oauth import _CODEX_BROWSER_KIND, _fill_email_and_otp
from core.roxy_registration import _fetch_chatgpt_session, is_cloudflare_challenge_error
from core.tenant_context import current_tenant, tenant_scope

logger = logging.getLogger(__name__)


def _run_existing_account_twofa_impl(email: str, proxy: str | None = None) -> dict:
    driver = None
    opened = None
    browser_kind_token = _CODEX_BROWSER_KIND.set("Cloak")
    try:
        driver, opened = build_cloak_driver(proxy=proxy)
        proxy_used = ((opened.raw or {}).get("proxy") if opened else None) or proxy or None
        logger.info("[2FA补跑] 开始登录已有账号：%s proxy=%s", email, proxy_used or "无")
        _fill_email_and_otp(
            driver,
            email,
            wait_for_otp,
            "https://chatgpt.com/auth/login",
        )
        session_info = _fetch_chatgpt_session(driver, timeout=120)
        user = session_info.get("user") if isinstance(session_info, dict) else {}
        if isinstance(user, dict) and bool(user.get("mfa")):
            raise RuntimeError("服务端已启用 2FA，但本地缺少原 TOTP Secret，不能重新生成")

        logger.info("[2FA补跑] 登录完成，开始第二次邮箱重认证：%s", email)
        secret = setup_cloak_2fa(driver, email, proxy_used)
        logger.info("[2FA补跑] 创建成功：%s", email)
        return {
            "ok": True,
            "status": "success",
            "email": email,
            "totp_secret": secret,
            "proxy_used": proxy_used,
            "message": "2FA 已创建并保存",
        }
    except Exception as exc:
        proxy_used = ((opened.raw or {}).get("proxy") if opened else None) or proxy or None
        if is_cloudflare_challenge_error(exc) or _is_proxy_navigation_failure(exc):
            try:
                from config.proxy import mark_proxy_temporarily_bad

                mark_proxy_temporarily_bad(proxy_used, ttl_seconds=900)
            except Exception:
                logger.debug("[2FA补跑] 标记代理冷却失败", exc_info=True)
        logger.error("[2FA补跑] 失败：%s: %s", type(exc).__name__, exc)
        _capture_cloak_failure_diagnostics(driver)
        return {
            "ok": False,
            "status": "failed",
            "email": email,
            "proxy_used": proxy_used,
            "error": f"{type(exc).__name__}: {str(exc)[:500]}",
            "message": "创建 2FA 失败",
        }
    finally:
        _CODEX_BROWSER_KIND.reset(browser_kind_token)
        if driver and not bool(_cfg.CLOAK_KEEP_BROWSER_OPEN):
            try:
                driver.quit()
            except Exception:
                pass


def run_existing_account_twofa(email: str, proxy: str | None = None) -> dict:
    """Run one Playwright lifecycle in a fresh thread while preserving tenant context."""
    result_box: dict[str, Any] = {}
    error_box: dict[str, BaseException] = {}
    tenant_id = current_tenant()
    parent_thread_name = threading.current_thread().name

    def _target() -> None:
        try:
            with tenant_scope(tenant_id):
                result_box["value"] = _run_existing_account_twofa_impl(email=email, proxy=proxy)
        except BaseException as exc:  # noqa: BLE001 - preserve cross-thread exception
            error_box["error"] = exc

    worker = threading.Thread(target=_target, name=parent_thread_name, daemon=False)
    worker.start()
    worker.join()
    if "error" in error_box:
        raise error_box["error"]
    return result_box.get("value") or {
        "ok": False,
        "status": "failed",
        "email": email,
        "error": "Cloak 2FA 隔离线程未返回任务结果",
    }
