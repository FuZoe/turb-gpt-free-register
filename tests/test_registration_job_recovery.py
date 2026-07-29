from core import registration_service as svc


def test_recover_interrupted_jobs_marks_all_active_rows_stopped(monkeypatch):
    jobs = [
        {"id": 21, "status": "running", "email": "run@example.com"},
        {"id": 22, "status": "pending", "email": None},
        {"id": 23, "status": "stopping", "email": "stop@example.com"},
        {"id": 24, "status": "success", "email": "done@example.com"},
    ]
    updates = []
    releases = []
    logs = []
    monkeypatch.setattr(svc.db, "list_jobs", lambda limit=100: jobs)
    monkeypatch.setattr(svc.db, "update_job", lambda job_id, **values: updates.append((job_id, values)))
    monkeypatch.setattr(svc, "_release_unconsumed_job_email", lambda email, reason: releases.append((email, reason)))
    monkeypatch.setattr(svc, "_append_job_log", lambda job_id, message, source="manual-stop": logs.append((job_id, message, source)))

    count = svc.recover_interrupted_jobs()

    assert count == 3
    assert [job_id for job_id, _values in updates] == [21, 22, 23]
    assert all(values["status"] == "stopped" for _job_id, values in updates)
    assert all(values["completed_at"] for _job_id, values in updates)
    assert releases[0][0] == "run@example.com"
    assert releases[1][0] is None
    assert releases[2][0] == "stop@example.com"
    assert all(source == "startup-recovery" for _job_id, _message, source in logs)


def test_recover_interrupted_jobs_is_noop_without_active_rows(monkeypatch):
    monkeypatch.setattr(svc.db, "list_jobs", lambda limit=100: [{"id": 1, "status": "failed"}])

    assert svc.recover_interrupted_jobs() == 0
