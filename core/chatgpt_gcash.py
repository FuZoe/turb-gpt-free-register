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
                "gcash_eligible": False,
                "gcash_checked_at": now_iso(),
                "gcash_error": f"HTTP {http_status}",
                "gcash_response": response_text[:500],
            }

        data: Any = resp.json() if resp.text else {}
        if not isinstance(data, dict):
            return {
                "ok": False,
                "gcash_eligible": False,
                "gcash_checked_at": now_iso(),
                "gcash_error": "响应不是 JSON 对象",
                "gcash_response": response_text[:500],
            }

        # 尝试从响应中提取 eligible 字段；不同 API 可能用不同 key
        eligible = (
            data.get("eligible")
            or data.get("ok")
            or data.get("success")
            or data.get("can_trial")
        )
        if eligible is None and isinstance(data.get("data"), dict):
            eligible = data["data"].get("eligible") or data["data"].get("can_trial")

        error_msg = data.get("error") or data.get("message") or ""
        eligible_bool = bool(eligible) if eligible is not None else None

        result = {
            "ok": True,
            "gcash_eligible": eligible_bool,
            "gcash_checked_at": now_iso(),
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
            "gcash_eligible": False,
            "gcash_checked_at": now_iso(),
            "gcash_error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        if env is not None:
            try:
                env.session.close()
            except Exception:
                pass

