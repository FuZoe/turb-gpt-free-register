# -*- coding: utf-8 -*-
"""Plus 试用提链后台队列。"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.request import Request, urlopen

try:
    from curl_cffi import requests as curl_requests
except Exception:  # WebUI 环境未装 curl_cffi 时使用标准库兜底
    curl_requests = None

from config import extract_link as cfg
from core import db
from core.tenant_context import current_tenant, run_for_tenant

logger = logging.getLogger(__name__)


def _runtime_setting(name: str, default=None):
    """
    提链配置多数保存在 .env。服务模块会在 WebUI 启动时较早 import，
    因此每次实际读取时都重新加载 .env，避免“页面已保存但当前进程仍读到空值”。
    """
    try:
        from config.env_loader import load_env
        load_env(override=True)
    except Exception:
        pass
    raw = os.getenv(name)
    if raw is not None and str(raw).strip() != "":
        return str(raw).strip()
    return getattr(cfg, name, default)


def _int_setting(name: str, default: int, lower: int, upper: int) -> int:
    try:
        value = int(_runtime_setting(name, default) or default)
    except (TypeError, ValueError):
        value = default
    return max(lower, min(upper, value))


SUPPORTED_LINK_TYPES = {"upi"}


def _link_type(value: str | None = None) -> str:
    # upi.newzoe.cloud is UPI-only. Ignore stale PIX/KAKAO/IDEAL values left in
    # existing .env files so upgrading the service works without manual cleanup.
    return "upi"


def _api_base() -> str:
    base = str(_runtime_setting("EXTRACT_LINK_API_BASE", "") or "").strip().rstrip("/")
    if not base:
        raise ValueError("EXTRACT_LINK_API_BASE 为空")
    return base


def _cdk(value: str | None = None) -> str:
    cdk = str(value or _runtime_setting("EXTRACT_LINK_CDK", "") or "").strip()
    if not cdk:
        raise ValueError("EXTRACT_LINK_CDK/CDK 为空")
    return cdk


_WORKERS = _int_setting("EXTRACT_LINK_WORKERS", 3, 1, 16)
_QUEUE_LIMIT = _int_setting("EXTRACT_LINK_QUEUE_LIMIT", 500, _WORKERS, 5000)
_EXECUTOR = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="extract-link")
_QUEUE_SLOTS = threading.BoundedSemaphore(_QUEUE_LIMIT)


def queue_settings() -> dict:
    return {"workers": _WORKERS, "queue_limit": _QUEUE_LIMIT}


def _session():
    if curl_requests is None:
        return None
    return curl_requests.Session()


def query_cdk(*, cdk: str | None = None) -> dict:
    base = _api_base()
    code = _cdk(cdk)
    timeout = _int_setting("EXTRACT_LINK_REQUEST_TIMEOUT", 30, 5, 300)
    body_data = {"cdk": code, "cdk_type": "normal"}
    s = _session()
    try:
        if s is None:
            req = Request(
                f"{base}/api/cdk/status",
                data=json.dumps(body_data).encode("utf-8"),
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace") or "{}")
            return payload if isinstance(payload, dict) else {}
        resp = s.post(f"{base}/api/cdk/status", json=body_data, timeout=timeout)
        try:
            payload = resp.json()
        except Exception:
            payload = {"error": (resp.text or "")[:300]}
        if resp.status_code < 200 or resp.status_code >= 300:
            raise RuntimeError(payload.get("error") or f"HTTP {resp.status_code}")
        return payload if isinstance(payload, dict) else {}
    finally:
        try:
            s.close()
        except Exception:
            pass


def _normalize_upi_result(result: dict, base: str) -> dict:
    """Map upi.newzoe.cloud fields to the account-table result schema."""
    value = dict(result or {})
    upi_url = str(value.get("upi_instructions_url") or value.get("long_url") or "").strip()
    qr_url = str(value.get("qr_url") or value.get("image_url_svg") or "").strip()
    if qr_url.startswith("/"):
        qr_url = base.rstrip("/") + qr_url
    value.update({
        "long_url": upi_url,
        "image_url_svg": qr_url,
        "payment_method": "upi",
        "payment_link_type": "upi",
    })
    if value.get("cdk_remaining") is None and value.get("cdk_remaining_uses") is not None:
        value["cdk_remaining"] = value.get("cdk_remaining_uses")
    return value


def _iter_upi_events(*, token: str, cdk: str, job_id: str):
    """Submit one AT to upi.newzoe.cloud and translate its NDJSON stream."""
    base = _api_base()
    timeout = _int_setting("EXTRACT_LINK_EVENT_TIMEOUT", 600, 30, 900)
    payload = {
        "job_id": job_id,
        "tokens": [token],
        "proxy_mode": "paid",
        "cdk": _cdk(cdk),
        "payment_method_type": "upi",
        "country": "IN",
        "payment_locale": "pt-BR",
        "batch_recovery_rounds": "unlimited",
    }

    def translate(row):
        if not isinstance(row, dict):
            return None
        row_type = str(row.get("type") or "").strip().lower()
        if row_type in {"progress", "log", "step"}:
            message = str(row.get("message") or row.get("stage") or "UPI 提链处理中")
            return "log", {"message": message}
        if row_type == "stopped":
            return "error", {"message": str(row.get("message") or "UPI 提链任务已停止")}
        if row.get("index") is not None:
            result = row.get("result") if isinstance(row.get("result"), dict) else {}
            if row.get("ok"):
                normalized = _normalize_upi_result(result, base)
                if not normalized.get("long_url"):
                    return "error", {"message": "UPI 服务返回成功但缺少付款链接", "details": result}
                return "result", {"result": normalized}
            message = str(row.get("error") or result.get("message") or result.get("error") or "UPI 提链失败")
            return "error", {"message": message, "details": result}
        return None

    s = _session()
    try:
        if s is None:
            body = json.dumps(payload).encode("utf-8")
            req = Request(
                f"{base}/api/extract-batch",
                data=body,
                headers={"Accept": "application/x-ndjson", "Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=timeout) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line:
                        continue
                    event = translate(json.loads(line))
                    if event:
                        yield event
            return
        resp = s.post(f"{base}/api/extract-batch", json=payload, timeout=timeout, stream=True)
        if resp.status_code < 200 or resp.status_code >= 300:
            try:
                data = resp.json()
            except Exception:
                data = {"message": (resp.text or "")[:300]}
            raise RuntimeError(data.get("message") or data.get("error") or f"HTTP {resp.status_code}")
        for raw in resp.iter_lines():
            if not raw:
                continue
            line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
            event = translate(json.loads(line))
            if event:
                yield event
    finally:
        try:
            s.close()
        except Exception:
            pass


def _extract_error_message(data) -> str:
    """尽量从提链服务返回的任意错误结构中提取用户可读原因。"""
    if data is None:
        return ""
    if isinstance(data, str):
        return data.strip()
    if not isinstance(data, dict):
        return str(data)
    err = data.get("error")
    if isinstance(err, dict):
        for key in ("message", "detail", "reason", "error", "msg", "description"):
            value = err.get(key)
            if value:
                return str(value).strip()
        return json.dumps(err, ensure_ascii=False)[:500]
    if err:
        return str(err).strip()
    for key in ("message", "detail", "reason", "msg", "description", "raw"):
        value = data.get(key)
        if value:
            return str(value).strip()
    return json.dumps(data, ensure_ascii=False)[:500]


def _format_failure_reason(exc: Exception, logs: list[str] | None = None, last_event: dict | None = None) -> str:
    reason = f"{type(exc).__name__}: {str(exc)}".strip()
    if (not str(exc).strip()) and logs:
        reason = str(logs[-1])
    if last_event and "提链事件流结束但未返回 result" in reason:
        extracted = _extract_error_message(last_event.get("data"))
        if extracted:
            reason = f"提链事件流结束但未返回 result；最后事件 {last_event.get('event')}: {extracted}"
    return reason[:500]


def _run_extract(*, account_id: int, email: str, access_token: str, link_type: str, cdk: str, trigger: str) -> dict:
    logs: list[str] = []
    last_event = None
    try:
        if not db.mark_account_extract_running(account_id):
            return {"ok": False, "error": "账号已删除或提链状态已被重置"}
        job_id = uuid.uuid4().hex
        db.update_account_extract(account_id, {
            "ok": False,
            "status": "running",
            "job_id": job_id,
            "link_type": link_type,
            "message": "提链任务已创建，等待结果",
        })
        for event, data in _iter_upi_events(token=access_token, cdk=cdk, job_id=job_id):
            last_event = {"event": event, "data": data}
            if event == "log":
                msg = str((data or {}).get("message") or "")[:300]
                if msg:
                    logs.append(msg)
                    db.update_account_extract(account_id, {
                        "ok": False,
                        "status": "running",
                        "job_id": job_id,
                        "link_type": link_type,
                        "message": msg,
                    })
            elif event == "result":
                result = (data or {}).get("result") if isinstance(data, dict) else None
                if not isinstance(result, dict):
                    result = {}
                final = {"ok": True, "status": "success", "job_id": job_id, "link_type": link_type, "result": result, "logs": logs}
                db.update_account_extract(account_id, final)
                logger.info("[提链] 成功: %s type=%s job=%s", email, link_type, job_id)
                return final
            elif event == "error":
                msg = _extract_error_message(data)
                raise RuntimeError(msg or "提链任务失败")
            elif event == "done":
                break
        raise RuntimeError(f"提链事件流结束但未返回 result: {last_event}")
    except Exception as exc:
        reason = _format_failure_reason(exc, logs=logs, last_event=last_event)
        result = {
            "ok": False,
            "status": "failed",
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "error": reason,
            "message": reason,
        }
        try:
            db.update_account_extract(account_id, result)
        except Exception:
            logger.exception("[提链] 写入失败状态异常: account_id=%s", account_id)
        logger.exception("[提链] 失败: %s", email)
        return result
    finally:
        _QUEUE_SLOTS.release()


def enqueue_account_extract(*, account_id: int, email: str, access_token: str, trigger: str = "manual", link_type: str | None = None, cdk: str | None = None) -> dict:
    if not _QUEUE_SLOTS.acquire(blocking=False):
        return {"accepted": False, "busy": False, "error": "提链队列已满"}
    try:
        lt = _link_type(link_type)
        code = _cdk(cdk)
        if not db.claim_account_extract(account_id, trigger=trigger, link_type=lt):
            _QUEUE_SLOTS.release()
            return {"accepted": False, "busy": True, "error": "该账号正在提链中"}
        fut = _EXECUTOR.submit(
            run_for_tenant,
            current_tenant(),
            _run_extract,
            account_id=account_id,
            email=email,
            access_token=access_token,
            link_type=lt,
            cdk=code,
            trigger=trigger,
        )
        return {"accepted": True, "busy": False, "future": fut, "link_type": lt}
    except Exception:
        _QUEUE_SLOTS.release()
        raise
