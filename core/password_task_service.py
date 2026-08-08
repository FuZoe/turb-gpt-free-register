"""Background queue for creating passwords on existing accounts."""
from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from core import account_task_log, db
from core.registration_service import run_with_global_browser_slot
from core.tenant_context import current_tenant

logger = logging.getLogger(__name__)


def _int_env(name: str, default: int, lower: int, upper: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(lower, min(upper, value))


_WORKERS = _int_env("PASSWORD_TASK_WORKERS", 1, 1, 3)
_QUEUE_LIMIT = _int_env("PASSWORD_TASK_QUEUE_LIMIT", 500, _WORKERS, 5000)
_EXECUTOR = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="password-task")
_QUEUE_SLOTS = threading.BoundedSemaphore(_QUEUE_LIMIT)


def _run_password_task(account_id: int, email: str) -> dict:
    with account_task_log.capture("password", email):
        logger.info("[密码任务] 开始：account_id=%s email=%s", account_id, email)
        try:
            if not db.mark_account_password_task_running(account_id):
                result = {"ok": False, "status": "failed", "error": "账号已删除或创建密码任务状态已被重置"}
                logger.error("[密码任务] %s", result["error"])
                return result
            from core.cloakbrowser_password import run_existing_account_password

            result = run_existing_account_password(email=email)
            result.setdefault("checked_at", datetime.now().isoformat(timespec="seconds"))
            db.update_account_password_task(account_id, result)
            if result.get("ok"):
                logger.info("[密码任务] 成功：account_id=%s email=%s proxy=%s", account_id, email, result.get("proxy_used") or "-")
            else:
                logger.error("[密码任务] 失败：account_id=%s email=%s error=%s", account_id, email, result.get("error") or result.get("message") or "-")
            return result
        except Exception as exc:
            result = {
                "ok": False,
                "status": "failed",
                "checked_at": datetime.now().isoformat(timespec="seconds"),
                "error": f"{type(exc).__name__}: {str(exc)[:500]}",
                "message": "创建密码任务异常",
            }
            try:
                db.update_account_password_task(account_id, result)
            except Exception:
                logger.exception("[密码任务] 写入异常状态失败: account_id=%s", account_id)
            logger.exception("[密码任务] 执行异常: %s", email)
            return result
        finally:
            logger.info("[密码任务] 结束：account_id=%s email=%s", account_id, email)
            _QUEUE_SLOTS.release()


def enqueue_account_password(*, account_id: int, email: str, trigger: str = "manual") -> dict:
    account_id = int(account_id)
    email = str(email or "").strip()
    if not email:
        return {"accepted": False, "busy": False, "error": "账号邮箱为空"}
    if not _QUEUE_SLOTS.acquire(blocking=False):
        return {"accepted": False, "busy": False, "queue_full": True, "error": "密码任务队列已满"}
    if not db.claim_account_password_task(account_id, trigger=trigger):
        _QUEUE_SLOTS.release()
        return {"accepted": False, "busy": True, "error": "账号已有密码或任务正在执行"}

    try:
        account_task_log.initialize("password", email, account_id=account_id, trigger=trigger)
    except Exception:
        logger.exception("[密码任务] 初始化任务日志失败: account_id=%s email=%s", account_id, email)

    try:
        tenant_id = current_tenant()
        _EXECUTOR.submit(
            run_with_global_browser_slot,
            tenant_id,
            _run_password_task,
            account_id,
            email,
        )
    except Exception as exc:
        _QUEUE_SLOTS.release()
        result = {
            "ok": False,
            "status": "failed",
            "error": f"密码任务入队失败: {type(exc).__name__}: {str(exc)[:300]}",
            "message": "创建密码入队失败",
        }
        db.update_account_password_task(account_id, result)
        try:
            account_task_log.append("password", email, "ERROR", result["error"])
        except Exception:
            logger.exception("[密码任务] 写入入队失败日志异常: account_id=%s", account_id)
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
    return {"workers": _WORKERS, "queue_limit": _QUEUE_LIMIT}
