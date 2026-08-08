from config import proxy as proxy_cfg
from core import registration_service as service
from core import roxy_registration as reg
from core import roxy_codex_oauth as codex_oauth
import pytest


def test_proxy_picker_skips_temporarily_bad_endpoint(monkeypatch):
    monkeypatch.setattr(proxy_cfg, "PROXY_POOL", ["http://127.0.0.1:7901", "http://127.0.0.1:7902"])
    monkeypatch.setattr(proxy_cfg.random, "choice", lambda rows: rows[0])
    proxy_cfg._BAD_PROXY_UNTIL.clear()

    proxy_cfg.mark_proxy_temporarily_bad("http://127.0.0.1:7901", ttl_seconds=900)

    assert proxy_cfg.pick_proxy() == "http://127.0.0.1:7902"


def test_cloudflare_failure_creates_tracked_auto_retry(monkeypatch):
    logs = []
    monkeypatch.setenv("REGISTRATION_CF_AUTO_RETRIES", "3")
    monkeypatch.setattr(service.db, "get_job", lambda _job_id: {"id": 312, "retry_attempt": 0})
    monkeypatch.setattr(service, "get_executor_workers", lambda: 1)
    monkeypatch.setattr(
        service,
        "retry_job",
        lambda job_id, workers=None: {"ok": True, "created": True, "job": {"id": 313}},
    )
    monkeypatch.setattr(
        service,
        "_append_job_log",
        lambda job_id, message, source="": logs.append((job_id, message, source)),
    )

    result = service._maybe_auto_retry_cloudflare(
        312,
        "CloudflareChallengeError: Cloudflare challenge/403 阻断认证页",
    )

    assert result["job"]["id"] == 313
    assert any(job_id == 312 and "自动冷却" in message for job_id, message, _source in logs)
    assert any(job_id == 313 and "自动换线路" in message for job_id, message, _source in logs)


def test_cloudflare_auto_retry_stops_at_limit(monkeypatch):
    called = []
    monkeypatch.setenv("REGISTRATION_CF_AUTO_RETRIES", "2")
    monkeypatch.setattr(service.db, "get_job", lambda _job_id: {"id": 314, "retry_attempt": 2})
    monkeypatch.setattr(service, "retry_job", lambda *_args, **_kwargs: called.append(True))
    monkeypatch.setattr(service, "_append_job_log", lambda *_args, **_kwargs: None)

    result = service._maybe_auto_retry_cloudflare(314, "Cloudflare challenge/403")

    assert result["created"] is False
    assert called == []


def test_navigation_timeout_creates_tracked_auto_retry(monkeypatch):
    logs = []
    monkeypatch.setenv("REGISTRATION_CF_AUTO_RETRIES", "3")
    monkeypatch.setattr(service.db, "get_job", lambda _job_id: {"id": 332, "retry_attempt": 0})
    monkeypatch.setattr(service, "get_executor_workers", lambda: 3)
    monkeypatch.setattr(
        service,
        "retry_job",
        lambda job_id, workers=None: {"ok": True, "created": True, "job": {"id": 335}},
    )
    monkeypatch.setattr(
        service,
        "_append_job_log",
        lambda job_id, message, source="": logs.append((job_id, message, source)),
    )

    result = service._maybe_auto_retry_cloudflare(
        332,
        "TimeoutError: Page.goto: Timeout 30000ms exceeded.",
    )

    assert result["job"]["id"] == 335
    assert any("代理导航超时" in message for _job_id, message, _source in logs)


def test_unrelated_timeout_does_not_rotate_proxy():
    assert service._is_proxy_navigation_failure("等待 OTP 输入框超时") is False


def test_wrapped_browser_error_page_rotates_proxy():
    assert service._is_proxy_navigation_failure(
        "RuntimeError: state={'url': 'chrome-error://chromewebdata/', 'text': 'ERR_CONNECTION_CLOSED'}"
    ) is True


@pytest.mark.parametrize("error", [
    "SSLError: Failed to perform, curl: (35) TLS connect error",
    "CurlError: Failed to perform, curl: (52) Empty reply from server",
    "ConnectionError: Failed to perform, curl: (56) Recv failure",
])
def test_curl_proxy_transport_error_rotates_proxy(error):
    assert service._is_proxy_navigation_failure(error) is True


def test_otp_wait_ignores_diagnostic_script_noise_until_navigation(monkeypatch):
    class Driver:
        calls = 0

        @property
        def current_url(self):
            self.calls += 1
            return "https://auth.openai.com/email-verification" if self.calls == 1 else "https://chatgpt.com/"

    monkeypatch.setattr(codex_oauth, "_read_email_otp_validate_dead_code", lambda _driver: "")
    monkeypatch.setattr(codex_oauth, "_is_callback_url", lambda _url: False)
    monkeypatch.setattr(codex_oauth, "_has_strict_add_phone_form", lambda _driver: False)
    monkeypatch.setattr(codex_oauth, "_is_phone_code_page", lambda _driver: False)
    monkeypatch.setattr(
        codex_oauth,
        "_email_otp_page_state",
        lambda _driver: {"inputs": [], "errors": ["window.__oai_logHTML?window.__oai_logHTML():"], "text": ""},
    )
    monkeypatch.setattr(codex_oauth.time, "sleep", lambda _seconds: None)

    assert codex_oauth._wait_after_email_otp_submit(Driver(), timeout=1) == "accepted"


def test_otp_input_stage_surfaces_cloudflare_instead_of_timing_out(monkeypatch):
    state = {
        "url": "https://auth.openai.com/api/accounts/email-otp/send",
        "title": "しばらくお待ちください...",
        "text": "セキュリティ検証の実行 Cloudflare Ray ID: test",
        "inputs": [],
    }
    monkeypatch.setattr(reg, "_email_otp_page_state", lambda _driver: state)

    with pytest.raises(reg.CloudflareChallengeError):
        reg._type_otp(object(), "123456", timeout=1)
