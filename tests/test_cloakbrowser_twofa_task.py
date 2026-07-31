from types import SimpleNamespace

from core import cloakbrowser_twofa as twofa
from core.tenant_context import current_tenant, tenant_scope


class FakeDriver:
    def __init__(self):
        self.quit_called = False

    def quit(self):
        self.quit_called = True


def test_existing_account_twofa_logs_in_then_enrolls(monkeypatch):
    driver = FakeDriver()
    opened = SimpleNamespace(raw={"proxy": "http://127.0.0.1:7901"})
    calls = []
    monkeypatch.setattr(twofa._cfg, "CLOAK_KEEP_BROWSER_OPEN", False)
    monkeypatch.setattr(twofa, "build_cloak_driver", lambda proxy=None: (driver, opened))
    monkeypatch.setattr(twofa, "_fill_email_and_otp", lambda *args: calls.append("login"))
    monkeypatch.setattr(twofa, "_fetch_chatgpt_session", lambda *args, **kwargs: {"user": {"mfa": False}})
    monkeypatch.setattr(twofa, "setup_cloak_2fa", lambda *args: calls.append("enroll") or "SECRET")

    result = twofa._run_existing_account_twofa_impl("user@example.test")

    assert result["ok"] is True
    assert result["totp_secret"] == "SECRET"
    assert calls == ["login", "enroll"]
    assert driver.quit_called is True


def test_existing_account_twofa_thread_inherits_tenant(monkeypatch):
    seen = []
    monkeypatch.setattr(
        twofa,
        "_run_existing_account_twofa_impl",
        lambda email, proxy=None: seen.append(current_tenant()) or {"ok": True},
    )

    with tenant_scope("tenant2"):
        result = twofa.run_existing_account_twofa("friend@example.test")

    assert result["ok"] is True
    assert seen == ["tenant2"]
    assert current_tenant() == "default"
