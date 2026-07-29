from flask import Flask, jsonify

from core import db
from core import registration_service as registration_svc
from core.tenant_context import current_tenant, tenant_scope
from webui.auth import init_auth, register_auth_routes


def test_json_storage_is_isolated_by_tenant(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_PROJECT_ROOT", tmp_path)
    path = tmp_path / "store.json"

    with tenant_scope("default"):
        db._write_json(path, [{"email": "owner@example.com"}])
    with tenant_scope("team-a"):
        assert db._read_json(path, []) == []
        db._write_json(path, [{"email": "team@example.com"}])

    with tenant_scope("default"):
        assert db._read_json(path, [])[0]["email"] == "owner@example.com"
    with tenant_scope("team-a"):
        assert db._read_json(path, [])[0]["email"] == "team@example.com"
    assert (tmp_path / "store.json").exists()
    assert (tmp_path / "tenants" / "team-a" / "store.json").exists()


def test_auth_code_selects_tenant_for_session_and_header(monkeypatch):
    monkeypatch.setenv("WEBUI_TENANT_AUTH_CODES", '{"team-a":"team-code"}')
    app = Flask(__name__)
    app.config["TESTING"] = True
    init_auth(app, auth_code="owner-code")
    register_auth_routes(app)

    @app.get("/tenant-probe")
    def tenant_probe():
        return jsonify({"tenant": current_tenant()})

    team_client = app.test_client()
    response = team_client.post("/login", data={"auth_code": "team-code"})
    assert response.status_code == 302
    assert team_client.get("/tenant-probe").get_json()["tenant"] == "team-a"

    owner_client = app.test_client()
    response = owner_client.get("/tenant-probe", headers={"X-Auth-Code": "owner-code"})
    assert response.get_json()["tenant"] == "default"


def test_tenant_context_is_reset_after_request(monkeypatch):
    monkeypatch.setenv("WEBUI_TENANT_AUTH_CODES", '{"team-a":"team-code"}')
    app = Flask(__name__)
    app.config["TESTING"] = True
    init_auth(app, auth_code="owner-code")
    register_auth_routes(app)

    @app.get("/tenant-probe")
    def tenant_probe():
        return jsonify({"tenant": current_tenant()})

    client = app.test_client()
    assert client.get("/tenant-probe", headers={"X-Auth-Code": "team-code"}).get_json()["tenant"] == "team-a"
    assert current_tenant() == "default"


def test_registration_worker_keeps_submitting_tenant(monkeypatch):
    seen = []

    class ImmediateExecutor:
        def submit(self, func, *args, **kwargs):
            func(*args, **kwargs)

    monkeypatch.setattr(registration_svc, "get_executor", lambda max_workers=None: ImmediateExecutor())
    monkeypatch.setattr(registration_svc, "get_executor_workers", lambda: 1)
    monkeypatch.setattr(
        registration_svc.db,
        "create_job",
        lambda email_source: {"id": 1, "log_file": "tenant.log", "status": "pending"},
    )
    monkeypatch.setattr(registration_svc.db, "get_job", lambda job_id: {"id": job_id, "status": "pending"})
    monkeypatch.setattr(registration_svc, "_run_one_job", lambda *_args: seen.append(current_tenant()))

    with tenant_scope("team-a"):
        registration_svc.submit_registration(count=1, email_source="generic_api", workers=1)

    assert seen == ["team-a"]


def test_registration_worker_limit_caps_requested_concurrency(monkeypatch):
    monkeypatch.setenv("REGISTRATION_WORKERS_LIMIT", "1")

    assert registration_svc._normalize_workers(16) == 1
    assert registration_svc._normalize_workers(None) == 1
