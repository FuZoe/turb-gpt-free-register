"""Log into an existing passwordless account with Cloak and create a password."""
from __future__ import annotations

import logging
import threading
import time

from config import cloakbrowser as _cfg
from core.cloakbrowser_driver import build_cloak_driver
from core.cloakbrowser_registration import _capture_cloak_failure_diagnostics, _is_proxy_navigation_failure
from core.cloakbrowser_session import setup_cloak_password
from core.cloakbrowser_twofa import _login_existing_account, _wait_for_email_otp_page
from core.email_provider import wait_for_otp
from core.roxy_codex_oauth import _wait_after_email_otp_submit
from core.roxy_registration import (
    _clear_otp_inputs,
    _click_continue,
    _click_resend_email_otp,
    _type_otp,
)
from core.roxy_registration import _fetch_chatgpt_session, is_cloudflare_challenge_error
from core.tenant_context import current_tenant, tenant_scope

logger = logging.getLogger(__name__)

_MAX_PROXY_ATTEMPTS = 10


def _run_existing_account_password_impl(email: str, proxy: str | None = None) -> dict:
    requested_proxy = proxy
    for attempt in range(1, _MAX_PROXY_ATTEMPTS + 1):
        driver = None
        opened = None
        try:
            driver, opened = build_cloak_driver(proxy=proxy)
            proxy_used = ((opened.raw or {}).get("proxy") if opened else None) or proxy or None
            logger.info(
                "[密码补跑] 开始登录无密码账号：%s proxy=%s（%d/%d）",
                email, proxy_used or "无", attempt, _MAX_PROXY_ATTEMPTS,
            )
            _login_existing_account(driver, email, None)
            _fetch_chatgpt_session(driver, timeout=120)
            logger.info("[密码补跑] 登录完成，开始创建密码：%s", email)
            password_otp_after_ts = time.time()
            password = setup_cloak_password(driver, email, proxy_used)
            _wait_for_email_otp_page(driver)
            logger.info("[密码补跑] 等待密码创建确认 OTP：%s", email)
            outcome = "invalid"
            last_error: Exception | None = None
            for otp_round in range(1, 4):
                logger.info("[密码补跑] 提交密码确认 OTP（%d/3）：%s", otp_round, email)
                try:
                    code = wait_for_otp(
                        email,
                        after_ts=password_otp_after_ts,
                        max_wait=60,
                    )
                    _clear_otp_inputs(driver)
                    _type_otp(driver, code)
                    _click_continue(driver)
                    outcome = _wait_after_email_otp_submit(driver, timeout=45)
                    if outcome == "accepted":
                        break
                    last_error = RuntimeError(f"密码创建确认 OTP 未通过: {outcome}")
                except Exception as exc:
                    last_error = exc
                if otp_round >= 3:
                    raise last_error or RuntimeError("密码创建确认 OTP 未通过")
                logger.warning(
                    "[密码补跑] 密码确认 OTP 未通过，点击重发后继续（%d/3）：%s",
                    otp_round,
                    str(last_error)[:240] if last_error else outcome,
                )
                _click_resend_email_otp(driver, timeout=25)
                password_otp_after_ts = time.time() - 1.0
                time.sleep(2.0)
            if outcome != "accepted":
                raise last_error or RuntimeError(f"密码创建确认 OTP 未通过: {outcome}")
            logger.info("[密码补跑] 创建成功：%s", email)
            return {
                "ok": True,
                "status": "success",
                "email": email,
                "registration_password": password,
                "proxy_used": proxy_used,
                "message": "密码已创建并保存",
            }
        except Exception as exc:
            proxy_used = ((opened.raw or {}).get("proxy") if opened else None) or proxy or None
            retryable = is_cloudflare_challenge_error(exc) or _is_proxy_navigation_failure(exc)
            if retryable:
                try:
                    from config.proxy import mark_proxy_temporarily_bad

                    mark_proxy_temporarily_bad(proxy_used, ttl_seconds=900)
                except Exception:
                    logger.debug("[密码补跑] 标记代理冷却失败", exc_info=True)
            if retryable and attempt < _MAX_PROXY_ATTEMPTS and requested_proxy is None:
                logger.warning(
                    "[密码补跑] 代理 %s 失败，换线重试（%d/%d）：%s: %s",
                    proxy_used or "无", attempt, _MAX_PROXY_ATTEMPTS,
                    type(exc).__name__, str(exc)[:240],
                )
            else:
                logger.error("[密码补跑] 失败：%s: %s", type(exc).__name__, exc)
                _capture_cloak_failure_diagnostics(driver)
                return {
                    "ok": False,
                    "status": "failed",
                    "email": email,
                    "proxy_used": proxy_used,
                    "error": f"{type(exc).__name__}: {str(exc)[:500]}",
                    "message": "创建密码失败",
                }
        finally:
            if driver and not bool(_cfg.CLOAK_KEEP_BROWSER_OPEN):
                try:
                    driver.quit()
                except Exception:
                    pass
        proxy = None
        time.sleep(1.5)

    raise AssertionError("unreachable")


def run_existing_account_password(email: str, proxy: str | None = None) -> dict:
    result_box = {}
    error_box = {}
    tenant_id = current_tenant()
    parent_thread_name = threading.current_thread().name

    def _target() -> None:
        try:
            with tenant_scope(tenant_id):
                result_box["value"] = _run_existing_account_password_impl(email=email, proxy=proxy)
        except BaseException as exc:  # noqa: BLE001
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
        "error": "Cloak 密码隔离线程未返回任务结果",
    }
