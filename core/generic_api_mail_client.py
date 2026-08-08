# -*- coding: utf-8 -*-
"""
通用 API 取码邮箱客户端。

邮箱池导入格式：
    email----code_url

注册时领取 email；取码时直接 GET code_url，并从响应中提取 6 位验证码。
响应可以是纯文本、HTML 或 JSON，只要其中包含 6 位验证码即可。
"""
import json
import logging
import re
import time
import base64
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests

from config import email as _email_cfg
from core.otp_utils import extract_otp
from core.tenant_context import current_tenant, tenant_path, tenant_root

logger = logging.getLogger(__name__)

_CODE_REGEX = re.compile(r"\b(\d{6})\b")
_CONTEXT_WORDS = ("code", "verify", "verification", "验证码", "代码", "确认码", "認証", "コード")
_CONTEXT_CACHE: dict[object, "GenericApiEmailAccount"] = {}
_LAST_RETURNED_OTP: dict[object, str] = {}
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ACCOUNTS_FILE = _PROJECT_ROOT / "用于注册的API邮箱.txt"


def _cache_key(email: str) -> object:
    tenant_id = current_tenant()
    normalized = str(email or "").strip().lower()
    return normalized if tenant_id == "default" else (tenant_id, normalized)


class GenericApiMailError(RuntimeError):
    """通用 API 取码邮箱错误。"""


@dataclass
class GenericApiEmailAccount:
    email: str
    code_url: str


def _flatten_json(obj) -> str:
    parts: list[str] = []
    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif x is not None:
            parts.append(str(x))
    walk(obj)
    return "\n".join(parts)


def _extract_code(text: str) -> str | None:
    """从纯文本/HTML/JSON 文本中提取 6 位 OTP。"""
    if not text:
        return None

    # 兼容 JSON：优先把所有 value 拉平再抽取。
    candidates_text = [text]
    try:
        parsed = json.loads(text)
        candidates_text.insert(0, _flatten_json(parsed))
    except Exception:
        pass

    for body in candidates_text:
        # 复用邮件 OTP 抽取逻辑。
        code = extract_otp({"text": body, "content": body, "subject": body[:200]})
        if code:
            return code

        codes = _CODE_REGEX.findall(body)
        if not codes:
            continue
        lower = body.lower()
        for code in codes:
            idx = lower.find(code)
            window = lower[max(0, idx - 80): idx + 86]
            if any(w.lower() in window for w in _CONTEXT_WORDS):
                return code
        return codes[-1]
    return None


def _decode_message_body(value: object) -> str:
    """Decode a mailbox detail body's data URL while keeping plain HTML/text intact."""
    text = str(value or "")
    if not text.startswith("data:") or "," not in text:
        return text
    metadata, payload = text.split(",", 1)
    try:
        if ";base64" in metadata.lower():
            return base64.b64decode(payload).decode("utf-8", errors="ignore")
    except Exception:
        logger.debug("[GenericAPI] 邮件详情 data URL 解码失败", exc_info=True)
    return payload


def _extract_detail_otp(
    base_url: str,
    listing_html: str,
    headers: dict[str, str],
    after_ts: float | None,
) -> str | None:
    """Read the newest message detail from HTML mailbox viewers such as yangyang.website."""
    route = re.search(
        r"var\s+detailBase=['\"]([^'\"]+)['\"];\s*var\s+detailSuffix=['\"]([^'\"]+)['\"]",
        listing_html,
    )
    if not route:
        return None
    detail_base, detail_suffix = route.groups()
    message_ids: list[str] = []
    item_pattern = re.compile(
        r'<a\b[^>]*data-id=["\']([0-9]+)["\'][^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    matched_items = False
    for item in item_pattern.finditer(listing_html):
        matched_items = True
        message_id, fragment = item.groups()
        time_match = re.search(r'class=["\']time["\'][^>]*>([^<]+)<', fragment, re.IGNORECASE)
        if after_ts and time_match:
            try:
                received_epoch = time.mktime(time.strptime(time_match.group(1).strip(), "%Y-%m-%d %H:%M:%S"))
                if received_epoch + 2.0 < float(after_ts):
                    continue
            except (TypeError, ValueError, OverflowError):
                pass
        message_ids.append(message_id)
    if not matched_items:
        message_ids = re.findall(r'data-id=["\']([0-9]+)["\']', listing_html)
    if not message_ids:
        return None
    parsed = urlsplit(base_url)
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    detail_headers = {**headers, "Accept": "application/json"}
    for message_id in message_ids[:8]:
        detail_url = f"{origin}{detail_base}{message_id}{detail_suffix}"
        try:
            detail_resp = requests.get(detail_url, headers=detail_headers, timeout=12, verify=False)
            if detail_resp.status_code != 200:
                continue
            payload = detail_resp.json()
            if not isinstance(payload, dict):
                continue
            body = _decode_message_body(payload.get("body") or payload.get("html") or payload.get("text"))
            code = _extract_code(json.dumps({"subject": payload.get("subject", ""), "html": body}, ensure_ascii=False))
            if code:
                logger.info("[GenericAPI] 从邮件详情提取 OTP=%s message_id=%s", code, message_id)
                return code
        except Exception as exc:
            logger.debug("[GenericAPI] 邮件详情读取失败 id=%s: %s", message_id, exc)
    return None


def pick_account() -> GenericApiEmailAccount:
    """领取一个可用通用 API 邮箱。"""
    from core.db import claim_next_generic_api_email, generic_api_email_pool_summary

    inserted, skipped = import_from_file()
    if inserted:
        logger.info(f"[GenericAPI] 已自动从 {_ACCOUNTS_FILE.name} 导入 {inserted} 个邮箱（跳过 {skipped} 个）")

    row = claim_next_generic_api_email()
    if row is None:
        summary = generic_api_email_pool_summary()
        raise GenericApiMailError(
            f"通用 API 邮箱池没有可用账号: {summary}. 请在 WebUI 邮箱池导入：邮箱----取码地址"
        )
    account = GenericApiEmailAccount(email=row["email"], code_url=row["code_url"])
    _CONTEXT_CACHE[_cache_key(account.email)] = account
    logger.info(f"[GenericAPI] 选中邮箱: {account.email}（DB id={row.get('id')}）")
    return account


def import_from_file(path: str | Path | None = None) -> tuple[int, int]:
    """从文本文件导入通用 API 邮箱，每行：email----code_url 或 email====code_url。"""
    from core.db import import_generic_api_emails
    p = Path(path) if path else tenant_path(_PROJECT_ROOT, _ACCOUNTS_FILE)
    if not p.is_absolute():
        p = tenant_root(_PROJECT_ROOT) / p
    if not p.exists():
        return 0, 0
    records = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("----") if "----" in line else line.split("====")
        parts = [x.strip() for x in parts]
        if len(parts) < 2:
            continue
        records.append({"email": parts[0], "code_url": parts[1]})
    return import_generic_api_emails(records)


def get_account_context(email: str) -> GenericApiEmailAccount | None:
    key = _cache_key(email)
    if key in _CONTEXT_CACHE:
        return _CONTEXT_CACHE[key]
    from core.db import get_generic_api_email_by_email
    row = get_generic_api_email_by_email(email)
    if row is None:
        return None
    account = GenericApiEmailAccount(email=row["email"], code_url=row["code_url"])
    _CONTEXT_CACHE[key] = account
    return account


def release_account(email: str, status: str = "available", note: str | None = None) -> None:
    from core.db import release_generic_api_email
    release_generic_api_email(email, status=status, note=note)
    key = _cache_key(email)
    _CONTEXT_CACHE.pop(key, None)
    _LAST_RETURNED_OTP.pop(key, None)


def fetch_latest_otp(
    email: str,
    after_ts: float | None = None,
    max_wait: int | None = None,
    poll_interval: int | None = None,
    settle_seconds: int | None = None,
) -> str:
    """
    轮询该邮箱配置的 code_url，直到提取到 6 位验证码或超时。

    settle 机制：首次拿到验证码后不立刻返回，而是继续等 OTP_SETTLE_SECONDS 秒。
    如果期间取码地址返回了不同验证码，则替换候选并重置 settle 倒计时；
    连续 settle 秒没有变化后才返回，避免取到接口缓存中的旧码。
    """
    account = get_account_context(email)
    if account is None:
        raise GenericApiMailError(f"通用 API 邮箱不存在或未导入: {email}")

    cache_key = _cache_key(email)
    previous_otp = _LAST_RETURNED_OTP.get(cache_key)
    deadline = time.time() + (max_wait or _email_cfg.OTP_MAX_WAIT)
    interval = poll_interval or _email_cfg.OTP_POLL_INTERVAL
    settle = settle_seconds if settle_seconds is not None else _email_cfg.OTP_SETTLE_SECONDS
    headers = {
        "Accept": "application/json,text/plain,*/*",
        "User-Agent": "Mozilla/5.0 (compatible; gpt-register/1.0)",
    }
    last_error = ""
    best_otp: str | None = None
    best_seen_at: float = 0.0
    settle_until: float | None = None
    logger.info(
        f"[GenericAPI] 开始轮询取码地址: {email}，"
        f"最长 {max_wait or _email_cfg.OTP_MAX_WAIT}s, settle={settle}s"
    )

    while time.time() < deadline:
        try:
            resp = requests.get(account.code_url, headers=headers, timeout=20, verify=False)
            text = resp.text or ""
            if resp.status_code == 200:
                code = _extract_code(text)
                if not code:
                    code = _extract_detail_otp(account.code_url, text, headers, after_ts)
                if code:
                    now_seen = time.time()
                    if code == previous_otp and not best_otp:
                        last_error = f"取码接口仍返回上次已消费的 OTP={code}"
                        logger.info(
                            "[GenericAPI] 仍是上次已消费的 OTP=%s，等待新验证码...",
                            code,
                        )
                    elif not best_otp:
                        best_otp = code
                        best_seen_at = now_seen
                        settle_until = now_seen + settle
                        logger.info(
                            f"[GenericAPI] 首次锁定 OTP={code}, "
                            f"等 {settle}s 看取码接口是否出现更新验证码..."
                        )
                    elif code != best_otp:
                        logger.info(
                            f"[GenericAPI] 发现更新 OTP={code}，"
                            f"替换之前的 {best_otp}, 重置 settle 计时"
                        )
                        best_otp = code
                        best_seen_at = now_seen
                        settle_until = now_seen + settle
                    else:
                        logger.debug(f"[GenericAPI] 取码接口仍返回候选 OTP={best_otp}")
                else:
                    last_error = f"HTTP 200 但未提取到 6 位验证码，响应预览: {text[:160]}"
            else:
                last_error = f"HTTP {resp.status_code}: {text[:160]}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"

        now = time.time()
        if best_otp and settle_until is not None and now >= settle_until:
            logger.info(
                f"[GenericAPI] settle 完成，返回 OTP={best_otp}, "
                f"候选锁定时间={time.strftime('%H:%M:%S', time.localtime(best_seen_at))}"
            )
            _LAST_RETURNED_OTP[cache_key] = best_otp
            return best_otp

        remaining = int(deadline - now)
        if best_otp and settle_until is not None:
            logger.info(
                f"[GenericAPI] 已锁定候选 OTP={best_otp}，等 settle 中"
                f"（剩余 settle ~{max(0, int(settle_until - now))}s, 总剩余 {remaining}s）..."
            )
        else:
            logger.info(
                f"[GenericAPI] 暂未从取码接口拿到验证码，"
                f"{interval}s 后重试（剩余 {remaining}s）..."
            )
        time.sleep(interval)

    if best_otp:
        logger.warning(f"[GenericAPI] 总超时但已有候选，返回 OTP={best_otp}")
        _LAST_RETURNED_OTP[cache_key] = best_otp
        return best_otp

    raise GenericApiMailError(f"等待通用 API 验证码超时: {email}; {last_error}")
