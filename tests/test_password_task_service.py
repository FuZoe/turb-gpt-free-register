import threading

from core import db
from core import password_task_service as service
from core.tenant_context import current_tenant, run_for_tenant, tenant_scope


def _memory_account_store(monkeypatch):
    accounts = [{"id": 1, "email": "user@example.test", "registration_password": ""}]
    monkeypatch.setattr(db, "_load_accounts", lambda: accounts)
    monkeypatch.setattr(db, "_save_accounts", lambda rows: accounts.__setitem__(slice(None), rows))
    return accounts


def test_password_task_lifecycle_saves_created_password(monkeypatch):
    accounts = _memory_account_store(monkeypatch)

    assert db.claim_account_password_task(1, trigger="manual") is True
    assert accounts[0]["password_task_status"] == "queued"
    assert db.mark_account_password_task_running(1) is True

    assert db.update_account_password_task(1, {
        "ok": True,
        "status": "success",
        "registration_password": "StrongPass!234",
        "proxy_used": "http://127.0.0.1:7901",
    }) is True

    assert accounts[0]["registration_password"] == "StrongPass!234"
    assert accounts[0]["password_task_status"] == "success"
    assert db.claim_account_password_task(1) is False


def test_password_queue_preserves_submitting_tenant(monkeypatch):
    seen = []

    class ImmediateExecutor:
        def submit(self, func, *args, **kwargs):
            return func(*args, **kwargs)

    monkeypatch.setattr(service, "_EXECUTOR", ImmediateExecutor())
    monkeypatch.setattr(service, "_QUEUE_SLOTS", threading.BoundedSemaphore(1))
    monkeypatch.setattr(service.db, "claim_account_password_task", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(service, "_run_password_task", lambda *_args: seen.append(current_tenant()))
    monkeypatch.setattr(
        service,
        "run_with_global_browser_slot",
        lambda tenant_id, func, *args: run_for_tenant(tenant_id, func, *args),
    )

    with tenant_scope("tenant2"):
        result = service.enqueue_account_password(account_id=1, email="friend@example.test")

    assert result["accepted"] is True
    assert seen == ["tenant2"]
