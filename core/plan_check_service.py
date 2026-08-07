# -*- coding: utf-8 -*-
"""套餐/Plus 资格查询后台队列。"""
from __future__ import annotations

import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from config import proxy as proxy_cfg
from core import db
from core.chatgpt_plan import check_account_plan
from core.chatgpt_gcash import check_gcash_zero_trial
from core.tenant_context import current_tenant, run_for_tenant

logger = logging.getLogger(__name__)


def _int_setting(name: str, default: int, lower: int, upper: int) -> int:
    try:
        value = int(getattr(proxy_cfg, name, default) or default)
    except (TypeError, ValueError):
        value = default
    return max(lower, min(upper, value))


def _float_setting(name: str, default: float, lower: float, upper: float) -> float:
    try:
        value = float(getattr(proxy_cfg, name, default) or 0.0)
    except (TypeError, ValueError):
        value = default
    return max(lower, min(upper, value))


_WORKERS = _int_setting("PLAN_CHECK_WORKERS", 3, 1, 16)
_QUEUE_LIMIT = _int_setting("PLAN_CHECK_QUEUE_LIMIT", 500, _WORKERS, 5000)
_EXECUTOR = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="plan-check")
_QUEUE_SLOTS = threading.BoundedSemaphore(_QUEUE_LIMIT)
_RATE_LOCK = threading.Lock()
_NEXT_REQUEST_AT = 0.0


def _wait_for_rate_slot() -> None:
    """为所有查询线程分配错开的请求启动时间。"""
    global _NEXT_REQUEST_AT
    min_interval = _float_setting("PLAN_CHECK_MIN_INTERVAL", 0.4, 0.0, 30.0)
    jitter = _float_setting("PLAN_CHECK_JITTER", 0.3, 0.0, 30.0)
    with _RATE_LOCK:
        now = time.monotonic()
        scheduled = max(now, _NEXT_REQUEST_AT) + (random.uniform(0.0, jitter) if jitter else 0.0)
        _NEXT_REQUEST_AT = scheduled + min_interval
    wait_seconds = scheduled - now
    if wait_seconds > 0:
        time.sleep(wait_seconds)


def _registration_recheck_delay() -> float:
    return _float_setting("PLAN_CHECK_REGISTRATION_RECHECK_DELAY", 2.0, 0.0, 30.0)


def _run_plan_check(
    *,
    account_id: int,
    email: str,
    access_token: str,
    trigger: str,
    proxy: str | None,
    timezone_offset_min: str,
) -> dict:
    try:
        if not db.mark_account_plan_check_running(account_id):
            return {"ok": False, "error": "账号已删除或套餐查询状态已被重置"}

        _wait_for_rate_slot()
        result = check_account_plan(
            access_token,
            proxy=proxy,
            timezone_offset_min=timezone_offset_min,
        )

        recheck_delay = _registration_recheck_delay()
        should_recheck = (
            trigger == "registration_auto"
            and recheck_delay > 0
            and bool(result.get("ok"))
            and str(result.get("current_plan_type") or "").lower() == "free"
            and not bool(result.get("plus_trial_eligible"))
        )
        if should_recheck:
            logger.info("[Plan] 新账号暂未发现 Plus 试用资格，%.1fs 后复查一次: %s", recheck_delay, email)
            time.sleep(recheck_delay)
            _wait_for_rate_slot()
            recheck_result = check_account_plan(
                access_token,
                proxy=proxy,
                timezone_offset_min=timezone_offset_min,
                max_attempts=1,
            )
            if recheck_result.get("ok"):
                result = recheck_result
            else:
                logger.warning(
                    "[Plan] 新账号资格复查失败，保留首次成功结果: %s, %s",
                    email,
                    recheck_result.get("error") or "未知错误",
                )

        db.update_account_plan_check(acc_id=account_id, result=result)

        # Gcash 是独立支付渠道资格，不能用它的结果覆盖 OpenAI 套餐查询。
        if result.get("ok") and str(result.get("current_plan_type") or "").lower() == "free":
            gcash_result = check_gcash_zero_trial(
                access_token,
                # 原始 Gcash curl 是直连；None 会被 BrowserSession 随机替换成代理池。
                proxy=proxy if proxy is not None else "",
                timeout=12.0,
            )
            db.update_account_gcash_check(acc_id=account_id, result=gcash_result)

        if result.get("ok"):
            logger.info(
                "[Plan] 后台查询成功: %s, plan=%s, plus_trial=%s, trigger=%s",
                email,
                result.get("current_plan_type") or "unknown",
                bool(result.get("plus_trial_eligible")),
                trigger,
            )
        else:
            logger.warning(
                "[Plan] 后台查询失败: %s, trigger=%s, error=%s",
                email,
                trigger,
                result.get("error") or "未知错误",
            )
        return result
    except Exception as exc:
        result = {
            "ok": False,
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "error": f"{type(exc).__name__}: {str(exc)[:180]}",
        }
        try:
            db.update_account_plan_check(acc_id=account_id, result=result)
        except Exception:
            logger.exception("[Plan] 写入后台查询异常状态失败: account_id=%s", account_id)
        logger.exception("[Plan] 后台查询异常: %s", email)
        return result
    finally:
        _QUEUE_SLOTS.release()


def _run_gcash_check(*, account_id: int, email: str, access_token: str, proxy: str | None) -> dict:
    """执行独立 Gcash 检测，不改变套餐查询状态。"""
    try:
        if not db.mark_account_gcash_check_running(account_id):
            return {"ok": False, "error": "账号已删除或 Gcash 检测状态已被重置"}
        _wait_for_rate_slot()
        result = check_gcash_zero_trial(
            access_token,
            # Gcash 接口默认直连，显式传入代理时才走调用方指定线路。
            proxy=proxy if proxy is not None else "",
            timeout=12.0,
        )
        db.update_account_gcash_check(acc_id=account_id, result=result)
        logger.info(
            "[Gcash] 后台检测完成: %s, eligible=%s, ok=%s",
            email,
            result.get("gcash_eligible"),
            bool(result.get("ok")),
        )
        return result
    except Exception as exc:
        result = {
            "ok": False,
            "gcash_checked_at": datetime.now().isoformat(timespec="seconds"),
            "gcash_error": f"{type(exc).__name__}: {str(exc)[:180]}",
        }
        try:
            db.update_account_gcash_check(acc_id=account_id, result=result)
        except Exception:
            logger.exception("[Gcash] 写入异常状态失败: account_id=%s", account_id)
        logger.exception("[Gcash] 后台检测异常: %s", email)
        return result
    finally:
        _QUEUE_SLOTS.release()


def enqueue_account_gcash_check(
    *,
    account_id: int,
    email: str,
    access_token: str,
    proxy: str | None = None,
) -> dict:
    """把单个 Gcash 检测放入套餐查询共用的限速队列。"""
    account_id = int(account_id)
    if not str(access_token or "").strip():
        return {"accepted": False, "busy": False, "error": "账号缺少 access_token"}
    if not _QUEUE_SLOTS.acquire(blocking=False):
        return {"accepted": False, "busy": False, "queue_full": True, "error": "Gcash 检测队列已满"}
    if not db.claim_account_gcash_check(account_id):
        _QUEUE_SLOTS.release()
        return {"accepted": False, "busy": True, "error": "该账号正在检测 Gcash"}
    try:
        tenant_id = current_tenant()
        _EXECUTOR.submit(
            run_for_tenant,
            tenant_id,
            _run_gcash_check,
            account_id=account_id,
            email=str(email or ""),
            access_token=str(access_token or "").strip(),
            proxy=proxy,
        )
    except Exception as exc:
        _QUEUE_SLOTS.release()
        db.update_account_gcash_check(acc_id=account_id, result={
            "ok": False,
            "gcash_checked_at": datetime.now().isoformat(timespec="seconds"),
            "gcash_error": f"Gcash 检测入队失败: {type(exc).__name__}: {str(exc)[:160]}",
        })
        return {"accepted": False, "busy": False, "error": "Gcash 检测入队失败"}
    return {"accepted": True, "busy": False, "account_id": account_id, "status": "queued"}


def enqueue_missing_gcash_checks(limit: int = 500) -> dict:
    """服务启动时补检历史 free 账号；只处理从未检测过的记录。"""
    candidates = db.list_unchecked_gcash_free_accounts(limit=limit)
    accepted = busy = full = 0
    for account in candidates:
        queued = enqueue_account_gcash_check(
            account_id=int(account.get("id") or 0),
            email=account.get("email") or "",
            access_token=account.get("access_token") or "",
        )
        if queued.get("accepted"):
            accepted += 1
        elif queued.get("queue_full"):
            full += 1
            break
        elif queued.get("busy"):
            busy += 1
    return {"candidates": len(candidates), "accepted": accepted, "busy": busy, "queue_full": full}


def enqueue_account_plan_check(
    *,
    account_id: int,
    email: str,
    access_token: str,
    trigger: str,
    proxy: str | None = None,
    timezone_offset_min: str = "-",
) -> dict:
    """把查询放入统一线程池；重复查询或队列满时不提交。"""
    account_id = int(account_id)
    email = str(email or "").strip()
    access_token = str(access_token or "").strip()
    if not access_token:
        return {"accepted": False, "busy": False, "error": "账号缺少 access_token"}
    if not _QUEUE_SLOTS.acquire(blocking=False):
        return {"accepted": False, "busy": False, "queue_full": True, "error": "套餐查询队列已满，请稍后重试"}

    if not db.claim_account_plan_check(acc_id=account_id, trigger=trigger):
        _QUEUE_SLOTS.release()
        return {"accepted": False, "busy": True, "error": "该账号正在查询套餐"}

    try:
        tenant_id = current_tenant()
        _EXECUTOR.submit(
            run_for_tenant,
            tenant_id,
            _run_plan_check,
            account_id=account_id,
            email=email,
            access_token=access_token,
            trigger=str(trigger or "manual"),
            proxy=proxy,
            timezone_offset_min=str(timezone_offset_min or "-"),
        )
    except Exception as exc:
        _QUEUE_SLOTS.release()
        result = {
            "ok": False,
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "error": f"套餐查询入队失败: {type(exc).__name__}: {str(exc)[:160]}",
        }
        db.update_account_plan_check(acc_id=account_id, result=result)
        return {"accepted": False, "busy": False, "error": result["error"]}

    return {
        "accepted": True,
        "busy": False,
        "account_id": account_id,
        "email": email,
        "status": "queued",
        "trigger": str(trigger or "manual"),
    }


def queue_settings() -> dict:
    return {
        "workers": _WORKERS,
        "queue_limit": _QUEUE_LIMIT,
        "min_interval": _float_setting("PLAN_CHECK_MIN_INTERVAL", 0.4, 0.0, 30.0),
        "jitter": _float_setting("PLAN_CHECK_JITTER", 0.3, 0.0, 30.0),
    }
