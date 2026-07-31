from config import proxy as proxy_cfg
from core import registration_service as service


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
