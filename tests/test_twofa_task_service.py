import threading

from core import db
from core import twofa_task_service as service
from core.tenant_context import current_tenant, run_for_tenant, tenant_scope


def _memory_account_store(monkeypatch):
    accounts = [{"id": 1, "email": "user@example.test", "access_token": "AT"}]
    outlook = [{"id": 1, "email": "user@example.test"}]
    generic = [{"id": 1, "email": "user@example.test", "code_url": "https://mail.test/code"}]
    monkeypatch.setattr(db, "_load_accounts", lambda: accounts)
    monkeypatch.setattr(db, "_save_accounts", lambda rows: accounts.__setitem__(slice(None), rows))
    monkeypatch.setattr(db, "_load_outlook", lambda: outlook)
    monkeypatch.setattr(db, "_save_outlook", lambda rows: outlook.__setitem__(slice(None), rows))
    monkeypatch.setattr(db, "_load_generic_api_emails", lambda: generic)
    monkeypatch.setattr(db, "_save_generic_api_emails", lambda rows: generic.__setitem__(slice(None), rows))
    return accounts, outlook, generic


def test_twofa_task_lifecycle_saves_secret_to_account_and_mailboxes(monkeypatch):
    accounts, outlook, generic = _memory_account_store(monkeypatch)

    assert db.claim_account_twofa_task(1, trigger="manual") is True
    assert accounts[0]["twofa_task_status"] == "queued"
    assert db.mark_account_twofa_task_running(1) is True
    assert accounts[0]["twofa_task_status"] == "running"

    assert db.update_account_twofa_task(1, {
        "ok": True,
        "status": "success",
        "totp_secret": "TOTPSECRET",
        "proxy_used": "http://127.0.0.1:7901",
    }) is True

    assert accounts[0]["totp_secret"] == "TOTPSECRET"
    assert accounts[0]["twofa_task_status"] == "success"
    assert outlook[0]["totp_secret"] == "TOTPSECRET"
    assert generic[0]["totp_secret"] == "TOTPSECRET"
    assert db.claim_account_twofa_task(1) is False


def test_twofa_queue_preserves_submitting_tenant(monkeypatch):
    seen = []

    class ImmediateExecutor:
        def submit(self, func, *args, **kwargs):
            return func(*args, **kwargs)

    monkeypatch.setattr(service, "_EXECUTOR", ImmediateExecutor())
    monkeypatch.setattr(service, "_QUEUE_SLOTS", threading.BoundedSemaphore(1))
    monkeypatch.setattr(service.db, "claim_account_twofa_task", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(service, "_run_twofa_task", lambda *_args: seen.append(current_tenant()))
    monkeypatch.setattr(
        service,
        "run_with_global_browser_slot",
        lambda tenant_id, func, *args: run_for_tenant(tenant_id, func, *args),
    )

    with tenant_scope("tenant2"):
        result = service.enqueue_account_twofa(account_id=1, email="friend@example.test")

    assert result["accepted"] is True
    assert seen == ["tenant2"]
