"""Log into an existing ChatGPT account with Cloak and enroll TOTP."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from config import cloakbrowser as _cfg
from core.cloakbrowser_driver import build_cloak_driver
from core.cloakbrowser_registration import (
    _capture_cloak_failure_diagnostics,
    _is_proxy_navigation_failure,
)
from core.cloakbrowser_session import setup_cloak_2fa
from core.email_provider import wait_for_otp
from core.roxy_codex_oauth import _wait_after_email_otp_submit
from core.roxy_registration import (
    _clear_otp_inputs,
    _click_continue,
    _click_passwordless_signup_if_present,
    _fetch_chatgpt_session,
    _is_email_verification_page,
    _maybe_accept,
    _submit_email_and_wait_next,
    _type_otp,
    is_cloudflare_challenge_error,
)
from core.tenant_context import current_tenant, tenant_scope

logger = logging.getLogger(__name__)


def _fill_existing_login_password(driver: Any, password: str, timeout: int = 60) -> None:
    result = driver.execute_script(
        r"""
        const password = String(arguments[0] || '');
        const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
          && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none'
          && !el.disabled && !el.readOnly;
        const input = [...document.querySelectorAll('input[type="password"],input[autocomplete*="current-password"]')].find(visible);
        if (!input) return {ok:false, reason:'missing_password_input'};
        input.focus();
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
        if (setter) setter.call(input, password); else input.value = password;
        input.dispatchEvent(new Event('input', {bubbles:true}));
        input.dispatchEvent(new Event('change', {bubbles:true}));
        const form = input.closest('form');
        const buttons = [...(form || document).querySelectorAll('button[type="submit"],input[type="submit"]')].filter(visible);
        if (!buttons.length) return {ok:false, reason:'missing_submit'};
        buttons[0].click();
        return {ok:true};
        """,
        password,
    ) or {}
    if not result.get("ok"):
        raise RuntimeError(f"登录密码填写失败: {result}")
    end = time.time() + timeout
    while time.time() < end:
        if "/log-in/password" not in str(getattr(driver, "current_url", "") or "").lower():
            logger.info("[2FA补跑] 密码登录已离开登录密码页")
            return
        time.sleep(0.5)
    raise RuntimeError("提交登录密码后页面未跳转，请检查保存的密码是否正确")


def _wait_for_email_otp_page(driver: Any, timeout: int = 30) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if _is_email_verification_page(driver):
            return
        time.sleep(0.25)
    raise RuntimeError(f"点击一次性验证码登录后未进入邮箱验证码页: url={getattr(driver, 'current_url', '')}")


def _login_existing_account(driver: Any, email: str, password: str | None) -> None:
    login_otp_after_ts = time.time()
    driver.get("https://chatgpt.com/auth/login")
    _maybe_accept(driver)
    state = _submit_email_and_wait_next(
        driver,
        email,
        attempts=3,
        allow_login_password=True,
    )
    if state == "logged_in":
        return
    if state == "login_password" and password:
        logger.info("[2FA补跑] 使用已保存的 ChatGPT 密码登录：%s", email)
        _fill_existing_login_password(driver, password)
        return
    if state == "login_password":
        login_otp_after_ts = time.time()
        clicked = _click_passwordless_signup_if_present(driver)
        if not clicked.get("ok"):
            raise RuntimeError(f"账号没有保存密码，且未找到一次性验证码登录入口: {clicked}")
        _wait_for_email_otp_page(driver)
    elif state != "otp":
        raise RuntimeError(f"已有账号登录进入未知状态: {state}")

    logger.info("[2FA补跑] 等待账号登录 OTP：%s", email)
    code = wait_for_otp(email, after_ts=login_otp_after_ts)
    _clear_otp_inputs(driver)
    _type_otp(driver, code)
    _click_continue(driver)
    outcome = _wait_after_email_otp_submit(driver, timeout=45)
    if outcome != "accepted":
        raise RuntimeError(f"账号登录 OTP 未通过: {outcome}")


def _run_existing_account_twofa_impl(
    email: str,
    password: str | None = None,
    proxy: str | None = None,
) -> dict:
    driver = None
    opened = None
    try:
        driver, opened = build_cloak_driver(proxy=proxy)
        proxy_used = ((opened.raw or {}).get("proxy") if opened else None) or proxy or None
        logger.info("[2FA补跑] 开始登录已有账号：%s proxy=%s", email, proxy_used or "无")
        _login_existing_account(driver, email, password)
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
        if driver and not bool(_cfg.CLOAK_KEEP_BROWSER_OPEN):
            try:
                driver.quit()
            except Exception:
                pass


def run_existing_account_twofa(
    email: str,
    password: str | None = None,
    proxy: str | None = None,
) -> dict:
    """Run one Playwright lifecycle in a fresh thread while preserving tenant context."""
    result_box: dict[str, Any] = {}
    error_box: dict[str, BaseException] = {}
    tenant_id = current_tenant()
    parent_thread_name = threading.current_thread().name

    def _target() -> None:
        try:
            with tenant_scope(tenant_id):
                result_box["value"] = _run_existing_account_twofa_impl(
                    email=email,
                    password=password,
                    proxy=proxy,
                )
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
