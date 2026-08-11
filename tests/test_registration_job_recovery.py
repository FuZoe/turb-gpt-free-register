from core import registration_service as svc
from core import db


def test_account_creation_failed_disables_poisoned_email():
    error = 'status=400: {"code":"account_creation_failed"}'

    assert svc._should_disable_failed_registration_email(error) is True


def test_recover_interrupted_jobs_requeues_pending_and_running(monkeypatch):
    jobs = [
        {"id": 21, "status": "running", "email": "run@example.com", "log_file": "21.log"},
        {"id": 22, "status": "pending", "email": None, "log_file": "22.log"},
        {"id": 23, "status": "stopping", "email": "stop@example.com", "log_file": "23.log"},
        {"id": 24, "status": "success", "email": "done@example.com"},
    ]
    updates = []
    requeued = []
    releases = []
    logs = []

    class ImmediateExecutor:
        def submit(self, func, *args):
            updates.append(("submit", args))

    monkeypatch.setattr(svc.db, "list_jobs", lambda limit=100: jobs)
    monkeypatch.setattr(svc.db, "update_job", lambda job_id, **values: updates.append((job_id, values)))
    monkeypatch.setattr(svc.db, "requeue_interrupted_job", lambda job_id, **values: requeued.append((job_id, values)) or True)
    monkeypatch.setattr(svc, "_release_unconsumed_job_email", lambda email, reason: releases.append((email, reason)))
    monkeypatch.setattr(svc, "_append_job_log", lambda job_id, message, source="manual-stop": logs.append((job_id, message, source)))
    monkeypatch.setattr(svc, "get_executor", lambda max_workers=None: ImmediateExecutor())

    count = svc.recover_interrupted_jobs()

    assert count == 3
    assert [job_id for job_id, _values in requeued] == [21, 22]
    assert all(values["clear_email"] is True for _job_id, values in requeued)
    assert [item[0] for item in updates if item[0] == "submit"] == ["submit", "submit"]
    stopped = next(values for job_id, values in updates if job_id == 23)
    assert stopped["status"] == "stopped"
    assert [email for email, _reason in releases] == ["run@example.com", "stop@example.com"]
    assert all(source == "startup-recovery" for _job_id, _message, source in logs)


def test_recover_interrupted_jobs_is_noop_without_active_rows(monkeypatch):
    monkeypatch.setattr(svc.db, "list_jobs", lambda limit=100: [{"id": 1, "status": "failed"}])

    assert svc.recover_interrupted_jobs() == 0


def test_requeue_interrupted_job_clears_runtime_fields(monkeypatch):
    jobs = [{
        "id": 7,
        "status": "running",
        "email": "claimed@example.test",
        "account_id": 9,
        "error_message": "old",
        "started_at": "2026-07-31T12:00:00",
        "completed_at": "2026-07-31T12:01:00",
    }]
    monkeypatch.setattr(db, "_load_jobs", lambda: jobs)
    monkeypatch.setattr(db, "_save_jobs", lambda rows: jobs.__setitem__(slice(None), rows))

    assert db.requeue_interrupted_job(7, clear_email=True) is True
    assert jobs[0]["status"] == "pending"
    assert jobs[0]["email"] is None
    assert jobs[0]["account_id"] is None
    assert jobs[0]["error_message"] is None
    assert jobs[0]["started_at"] is None
    assert jobs[0]["completed_at"] is None
