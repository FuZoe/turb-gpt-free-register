from core import cloakbrowser_registration as registration
from core.tenant_context import current_tenant, tenant_scope


def test_cloak_isolation_thread_inherits_tenant_context(monkeypatch):
    seen = []

    def fake_registration_impl(**_kwargs):
        seen.append(current_tenant())
        return {"success": True}

    monkeypatch.setattr(registration, "_run_cloak_registration_impl", fake_registration_impl)

    with tenant_scope("tenant2"):
        result = registration.run_cloak_registration(
            email="friend@example.test",
            name="Friend User",
            birthday="1998-01-02",
        )

    assert result["success"] is True
    assert seen == ["tenant2"]
    assert current_tenant() == "default"
