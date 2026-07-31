"""Background queue for enrolling TOTP on existing accounts."""
from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from core import db
from core.registration_service import run_with_global_browser_slot
from core.tenant_context import current_tenant

logger = logging.getLogger(__name__)


def _int_env(name: str, default: int, lower: int, upper: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(lower, min(upper, value))


_WORKERS = _int_env("TWOFA_TASK_WORKERS", 1, 1, 3)
_QUEUE_LIMIT = _int_env("TWOFA_TASK_QUEUE_LIMIT", 500, _WORKERS, 5000)
_EXECUTOR = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="twofa-task")
_QUEUE_SLOTS = threading.BoundedSemaphore(_QUEUE_LIMIT)


def _run_twofa_task(account_id: int, email: str) -> dict:
    try:
        if not db.mark_account_twofa_task_running(account_id):
            return {"ok": False, "error": "账号已删除或 2FA 任务状态已被重置"}
        from core.cloakbrowser_twofa import run_existing_account_twofa

        account = db.get_account(account_id) or {}
        result = run_existing_account_twofa(
            email=email,
            password=str(account.get("registration_password") or "").strip() or None,
        )
        result.setdefault("checked_at", datetime.now().isoformat(timespec="seconds"))
        db.update_account_twofa_task(account_id, result)
        return result
    except Exception as exc:
        result = {
            "ok": False,
            "status": "failed",
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "error": f"{type(exc).__name__}: {str(exc)[:500]}",
            "message": "创建 2FA 任务异常",
        }
        try:
            db.update_account_twofa_task(account_id, result)
        except Exception:
            logger.exception("[2FA任务] 写入异常状态失败: account_id=%s", account_id)
        logger.exception("[2FA任务] 执行异常: %s", email)
        return result
    finally:
        _QUEUE_SLOTS.release()


def enqueue_account_twofa(*, account_id: int, email: str, trigger: str = "manual") -> dict:
    account_id = int(account_id)
    email = str(email or "").strip()
    if not email:
        return {"accepted": False, "busy": False, "error": "账号邮箱为空"}
    if not _QUEUE_SLOTS.acquire(blocking=False):
        return {"accepted": False, "busy": False, "queue_full": True, "error": "2FA 任务队列已满"}
    if not db.claim_account_twofa_task(account_id, trigger=trigger):
        _QUEUE_SLOTS.release()
        return {"accepted": False, "busy": True, "error": "账号已启用 2FA 或任务正在执行"}

    try:
        tenant_id = current_tenant()
        _EXECUTOR.submit(
            run_with_global_browser_slot,
            tenant_id,
            _run_twofa_task,
            account_id,
            email,
        )
    except Exception as exc:
        _QUEUE_SLOTS.release()
        result = {
            "ok": False,
            "status": "failed",
            "error": f"2FA 任务入队失败: {type(exc).__name__}: {str(exc)[:300]}",
            "message": "创建 2FA 入队失败",
        }
        db.update_account_twofa_task(account_id, result)
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
