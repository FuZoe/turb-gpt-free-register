# -*- coding: utf-8 -*-
"""Gcash 零元试用资格检测（菲律宾 Gcash 支付渠道）。"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from core.session import BrowserSession

logger = logging.getLogger(__name__)

GCASH_CHECK_URL = "https://ai.pupux.xyz/api/session/check-zero-trial"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "eligible", "available"}:
            return True
        if normalized in {"false", "0", "no", "ineligible", "unavailable"}:
            return False
    return None


def _find_eligible_value(data: dict[str, Any]) -> bool | None:
    for source in (data, data.get("data"), data.get("result")):
        if not isinstance(source, dict):
            continue
        for key in ("eligible", "can_trial", "can_use_trial", "zero_trial_eligible"):
            if key in source:
                return _as_bool(source.get(key))
    return None


def check_gcash_zero_trial(
    token: str,
    *,
    proxy: str = "",
    timeout: float = 15.0,
) -> dict:
    """检查账号是否可用菲律宾 Gcash 渠道开通零元试用。

    返回包含 gcash_eligible / gcash_checked_at / gcash_error 等字段的 dict。
    """
    token = (token or "").strip().strip('"').strip("'")
    if not token:
        return {
            "ok": False,
            "gcash_eligible": False,
            "gcash_checked_at": now_iso(),
            "gcash_error": "缺少 access_token",
        }

    body = {
        "access_token": token,
        "link_type": "gcash",
        "billing_country": "PH",
        "options": {"currency": "PHP"},
        "use_plus_free_promo": True,
    }

    env = None
    try:
        env = BrowserSession(proxy=proxy, detect_exit_geo=False)
        headers = env._get_common_headers()
        headers.update({
            "accept": "application/json",
            "content-type": "application/json",
            "origin": "https://ai.pupux.xyz",
            "referer": "https://ai.pupux.xyz/",
        })

        resp = env.session.post(
            GCASH_CHECK_URL,
            headers=headers,
            json=body,
            allow_redirects=False,
            timeout=timeout,
        )
        http_status = int(resp.status_code)
        response_text = (resp.text or "")[:2000]

        if not (200 <= http_status < 300):
            return {
                "ok": False,
                "gcash_eligible": None,
                "gcash_checked_at": now_iso(),
                "gcash_http_status": http_status,
                "gcash_error": f"HTTP {http_status}",
                "gcash_response": response_text[:500],
            }

        data: Any = resp.json() if resp.text else {}
        if not isinstance(data, dict):
            return {
                "ok": False,
                "gcash_eligible": None,
                "gcash_checked_at": now_iso(),
                "gcash_http_status": http_status,
                "gcash_error": "响应不是 JSON 对象",
                "gcash_response": response_text[:500],
            }

        error_msg = data.get("error") or data.get("message") or ""
        eligible_bool = _find_eligible_value(data)

        result = {
            "ok": True,
            "gcash_eligible": eligible_bool,
            "gcash_checked_at": now_iso(),
            "gcash_http_status": http_status,
            "gcash_error": error_msg or None,
            "gcash_raw": data,
        }
        logger.info(
            "[Gcash] 检测完成: eligible=%s, error=%s",
            eligible_bool,
            error_msg or "无",
        )
        return result

    except Exception as exc:
        logger.debug("[Gcash] 检测失败: %s: %s", type(exc).__name__, exc, exc_info=True)
        return {
            "ok": False,
            "gcash_eligible": None,
            "gcash_checked_at": now_iso(),
            "gcash_error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        if env is not None:
            try:
                env.session.close()
            except Exception:
                pass

