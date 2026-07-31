# -*- coding: utf-8 -*-
"""通过 CloakBrowser + Playwright 适配层执行 ChatGPT 注册。"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from config import cloakbrowser as _cfg
from config import twofa as _twofa_cfg
from core.account_export import save_account_data
from core.cloakbrowser_driver import build_cloak_driver
from core.email_provider import wait_for_otp, resolve_email_source
from core.humanize import delay as human_delay
from core.tenant_context import current_tenant, tenant_path, tenant_scope

# 复用 Roxy 注册流程里已维护好的页面操作函数。
from core.roxy_registration import (  # noqa: F401
    _maybe_accept, _submit_email_and_wait_next, _fill_password_page_if_present,
    _open_signup_password_from_otp,
    _clear_otp_inputs, _type_otp, _click_continue, _wait_after_email_otp_submit,
    _click_resend_email_otp, _complete_profile_page, _fetch_chatgpt_session, _check_manual_stop,
    is_cloudflare_challenge_error,
)

logger = logging.getLogger(__name__)


def _is_proxy_navigation_failure(error: object) -> bool:
    text = str(error or "").lower()
    if "page.goto" not in text:
        return False
    return any(marker in text for marker in (
        "timeout 30000ms exceeded",
        "err_connection_closed",
        "err_connection_reset",
        "err_connection_timed_out",
        "err_proxy_connection_failed",
        "err_socks_connection_failed",
        "ssl_error_syscall",
    ))


def _capture_cloak_failure_diagnostics(driver, batch_dir: Path | None = None) -> None:
    """在注册异常后采集旁路诊断；任何采集错误都只记日志。"""
    if driver is None:
        return
    try:
        snapshot = driver.diagnostic_snapshot()
        rendered = json.dumps(snapshot, ensure_ascii=False, default=str)
        logger.error("[Cloak诊断] 失败现场快照：%s", rendered[:20000])
    except Exception as exc:
        logger.warning("[Cloak诊断] 页面/网络快照采集失败：%s: %s", type(exc).__name__, str(exc)[:300])

    try:
        root = (
            Path(batch_dir) / "cloak-diagnostics"
            if batch_dir
            else tenant_path(
                Path(__file__).resolve().parent.parent,
                Path(__file__).resolve().parent.parent / "注册日志" / "cloak-diagnostics",
            )
        )
        stamp = time.strftime("%Y%m%d-%H%M%S")
        millis = int(time.time() * 1000) % 1000
        screenshot_path = root / f"cloak-failure-{stamp}-{millis:03d}.png"
        saved = driver.save_diagnostic_screenshot(screenshot_path)
        logger.error("[Cloak诊断] 失败现场截图：%s", saved)
    except Exception as exc:
        logger.warning("[Cloak诊断] 截图保存失败：%s: %s", type(exc).__name__, str(exc)[:300])


def _run_cloak_registration_impl(email: str, name: str, birthday: str, proxy: str = None, otp_code: str = None, batch_dir: Path | None = None) -> dict:
    """CloakBrowser 自动化注册入口。"""
    driver = None
    opened = None
    create_acknowledged = False
    openai_password: str | None = None
    try:
        driver, opened = build_cloak_driver(proxy=proxy)
        logger.info("[Cloak注册] 开始：%s，profile=%s", email, opened.profile_id)

        otp_after_ts = time.time()
        logger.info("[Cloak注册] 打开登录页：https://chatgpt.com/auth/login")
        driver.get("https://chatgpt.com/auth/login")
        human_delay("navigate")
        _maybe_accept(driver)
        _check_manual_stop()

        next_state = _submit_email_and_wait_next(driver, email, attempts=3)
        _check_manual_stop()

        require_password = bool(getattr(_cfg, "CLOAK_ENABLE_PASSWORD", False))
        if require_password:
            otp_after_ts = time.time()
            logger.info("[Cloak注册][浏览器密码] 使用当前认证页进入密码创建流程")
            if next_state == "otp" and not _open_signup_password_from_otp(driver, timeout=25):
                raise RuntimeError("OTP 页未进入密码创建流程")
            openai_password = _fill_password_page_if_present(
                driver,
                email,
                timeout=30,
                prefer_password=True,
            )
            if not openai_password:
                raise RuntimeError("Cloak 密码创建流程未返回密码")
            logger.info("[Cloak注册][浏览器密码] 密码创建完成：email=%s password_length=%s", email, len(openai_password))
        else:
            openai_password = None if next_state == "otp" else _fill_password_page_if_present(driver, email, timeout=25)
        _check_manual_stop()

        current_otp = otp_code
        max_otp_attempts = 3
        for otp_attempt in range(1, max_otp_attempts + 1):
            if current_otp is None:
                logger.info("[Cloak注册][OTP] 等待验证码：%s（第 %s/%s 次）", email, otp_attempt, max_otp_attempts)
                try:
                    current_otp = wait_for_otp(email, after_ts=otp_after_ts)
                except Exception as exc:
                    if otp_attempt >= max_otp_attempts:
                        raise
                    logger.warning(
                        "[Cloak注册][OTP] 一直未收到验证码，点击“重新发送电子邮件”后继续等待（下一轮 %s/%s）：%s: %s",
                        otp_attempt + 1,
                        max_otp_attempts,
                        type(exc).__name__,
                        str(exc)[:180],
                    )
                    otp_after_ts = time.time()
                    _click_resend_email_otp(driver, timeout=25)
                    human_delay("api")
                    current_otp = None
                    continue
            logger.info("[Cloak注册][OTP] 收到验证码：%s", current_otp)
            _clear_otp_inputs(driver)
            _type_otp(driver, current_otp)
            human_delay("otp_input")
            try:
                _click_continue(driver)
            except Exception as exc:
                logger.info("[Cloak注册][OTP] 未找到显式提交按钮，继续等待页面状态：%s", str(exc)[:120])

            outcome = _wait_after_email_otp_submit(driver, timeout=10)
            if outcome == "accepted":
                break
            if otp_attempt >= max_otp_attempts:
                raise RuntimeError("邮箱验证码连续错误/过期，已达到最大重试次数")
            otp_after_ts = time.time()
            _click_resend_email_otp(driver, timeout=25)
            human_delay("api")
            current_otp = None

        profile_submitted = _complete_profile_page(driver, name, birthday, timeout=60)
        if profile_submitted:
            create_acknowledged = True
            human_delay("post_auth")

        session_info = _fetch_chatgpt_session(driver, timeout=120)
        access_token = session_info["accessToken"]
        logger.info("[Cloak注册] 已拿到 accessToken：%s", email)

        totp_secret = None
        twofa_result = {
            "status": "disabled",
            "ok": True,
            "message": "ENABLE_2FA=False",
        }
        if _twofa_cfg.ENABLE_2FA:
            try:
                from core.cloakbrowser_session import setup_cloak_2fa

                proxy_url = ((opened.raw or {}).get("proxy") if opened else None) or proxy or None
                logger.info("[Cloak注册][2FA] 正在桥接 Cloak 登录态并设置 TOTP")
                totp_secret = setup_cloak_2fa(driver, email, proxy_url)
                twofa_result = {
                    "status": "success",
                    "ok": True,
                    "message": "TOTP 已设置",
                }
                logger.info("[Cloak注册][2FA] TOTP 设置完成：%s", email)
            except Exception as exc:
                twofa_result = {
                    "status": "failed",
                    "ok": False,
                    "message": f"{type(exc).__name__}: {str(exc)[:220]}",
                }
                logger.error("[Cloak注册][2FA] 设置失败：%s", twofa_result["message"], exc_info=True)
                logger.warning("[Cloak注册][2FA] 账号注册已完成，保留账号并记录 2FA 失败，后续可单独补设")

        codex_result = {
            "status": "skipped",
            "ok": True,
            "message": "ENABLE_CODEX_AUTO=False，跳过 Codex",
        }
        try:
            from config import codex as _codex_cfg
            if bool(getattr(_codex_cfg, "ENABLE_CODEX_AUTO", False)):
                from core.roxy_codex_oauth import run_roxy_codex_oauth
                logger.info("[Cloak注册][Codex] ENABLE_CODEX_AUTO=True，复用当前 CloakBrowser 窗口执行 Codex 授权")
                _check_manual_stop()
                codex_result = run_roxy_codex_oauth(
                    email,
                    reuse_existing_profile=True,
                    existing_driver=driver,
                    existing_opened=opened,
                    force=True,
                    clear_existing_state=True,
                )
            else:
                logger.info("[Cloak注册][Codex] ENABLE_CODEX_AUTO=False，注册后跳过 Codex OAuth")
        except Exception as exc:
            codex_result = {"status": "failed", "ok": False, "message": f"{type(exc).__name__}: {str(exc)[:180]}"}

        account_id = save_account_data(
            email=email,
            access_token=access_token,
            totp_secret=totp_secret,
            email_source=resolve_email_source(email),
            proxy_used=((opened.raw or {}).get("proxy") if opened else None) or proxy or None,
            batch_dir=batch_dir,
            extra={
                "user": session_info.get("user"),
                "account": session_info.get("account"),
                "expires": session_info.get("expires"),
                "cloakbrowser": {"profile_id": opened.profile_id, "open_result": opened.raw},
                "registration_password": openai_password,
                "twofa": twofa_result,
                "codex": codex_result,
            },
        )
        codex_ok = codex_result.get("ok") or codex_result.get("status") == "skipped"
        twofa_ok = bool(twofa_result.get("ok"))
        overall_ok = bool(codex_ok)
        errors = []
        if not twofa_ok:
            errors.append(f"2FA 未完成: {twofa_result.get('message')}")
        if not codex_ok:
            errors.append(f"Codex 未完成: {codex_result.get('message')}")
        return {
            "success": overall_ok,
            "email": email,
            "account_id": account_id,
            "access_token": access_token,
            "registration_password": openai_password,
            "totp_secret": totp_secret,
            "twofa": twofa_result,
            "codex": codex_result,
            "warning": "; ".join(errors) if errors else None,
            "error": None if overall_ok else "; ".join(errors),
        }
    except Exception as exc:
        logger.error("[Cloak注册] 失败：%s: %s", type(exc).__name__, exc)
        if is_cloudflare_challenge_error(exc) or _is_proxy_navigation_failure(exc):
            used_proxy = ((opened.raw or {}).get("proxy") if opened else None) or proxy or None
            try:
                from config.proxy import mark_proxy_temporarily_bad

                mark_proxy_temporarily_bad(used_proxy, ttl_seconds=900)
                reason = "Cloudflare" if is_cloudflare_challenge_error(exc) else "导航超时"
                logger.warning("[Cloak注册] %s线路已冷却 15 分钟：%s", reason, used_proxy)
            except Exception:
                logger.debug("[Cloak注册] 标记 Cloudflare 线路冷却失败", exc_info=True)
        logger.debug("[Cloak注册] 失败详情", exc_info=True)
        _capture_cloak_failure_diagnostics(driver, batch_dir=batch_dir)
        try:
            from core.email_provider import release_email
            release_email(email, status="failed" if create_acknowledged else "available", note=f"Cloak注册失败: {str(exc)[:180]}")
        except Exception:
            pass
        return {"success": False, "email": email, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    finally:
        if driver and not bool(_cfg.CLOAK_KEEP_BROWSER_OPEN):
            try:
                driver.quit()
            except Exception:
                pass


def run_cloak_registration(email: str, name: str, birthday: str, proxy: str = None, otp_code: str = None, batch_dir: Path | None = None) -> dict:
    """在一次性隔离线程中运行完整 Cloak 生命周期。

    Playwright Sync API 与创建它的线程绑定；WebUI 长驻线程池可能被其他任务留下的
    asyncio 状态污染。每个 Cloak 任务都使用新线程，结束后整条 Playwright 状态随
    线程释放，同时沿用父线程名以保持任务日志过滤有效。
    """
    result_box: dict = {}
    error_box: dict = {}
    parent_thread_name = threading.current_thread().name
    tenant_id = current_tenant()

    def _target() -> None:
        try:
            with tenant_scope(tenant_id):
                result_box["value"] = _run_cloak_registration_impl(
                    email=email,
                    name=name,
                    birthday=birthday,
                    proxy=proxy,
                    otp_code=otp_code,
                    batch_dir=batch_dir,
                )
        except BaseException as exc:  # noqa: BLE001 - 跨线程原样回传
            error_box["error"] = exc

    worker = threading.Thread(
        target=_target,
        name=parent_thread_name,
        daemon=False,
    )
    worker.start()
    worker.join()
    if "error" in error_box:
        raise error_box["error"]
    return result_box.get("value") or {
        "success": False,
        "email": email,
        "error": "Cloak 隔离线程未返回任务结果",
    }
