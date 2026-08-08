from types import SimpleNamespace

from core import cloakbrowser_twofa as twofa
from core.tenant_context import current_tenant, tenant_scope


class FakeDriver:
    def __init__(self):
        self.quit_called = False
        self.current_url = "about:blank"

    def get(self, url):
        self.current_url = url

    def quit(self):
        self.quit_called = True


def test_existing_account_twofa_logs_in_then_enrolls(monkeypatch):
    driver = FakeDriver()
    opened = SimpleNamespace(raw={"proxy": "http://127.0.0.1:7901"})
    calls = []
    monkeypatch.setattr(twofa._cfg, "CLOAK_KEEP_BROWSER_OPEN", False)
    monkeypatch.setattr(twofa, "build_cloak_driver", lambda proxy=None: (driver, opened))
    monkeypatch.setattr(twofa, "_login_existing_account", lambda *args: calls.append("login"))
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
        lambda email, password=None, proxy=None: seen.append(current_tenant()) or {"ok": True},
    )

    with tenant_scope("tenant2"):
        result = twofa.run_existing_account_twofa("friend@example.test")

    assert result["ok"] is True
    assert seen == ["tenant2"]
    assert current_tenant() == "default"


def test_existing_account_login_uses_saved_password(monkeypatch):
    driver = FakeDriver()
    calls = []
    monkeypatch.setattr(twofa, "_maybe_accept", lambda _driver: calls.append("accept"))
    monkeypatch.setattr(
        twofa,
        "_submit_email_and_wait_next",
        lambda *_args, **kwargs: calls.append(("email", kwargs.get("allow_login_password"))) or "login_password",
    )
    monkeypatch.setattr(
        twofa,
        "_fill_existing_login_password",
        lambda _driver, password: calls.append(("password", password)),
    )

    twofa._login_existing_account(driver, "user@example.test", "saved-password")

    assert calls == ["accept", ("email", True), ("password", "saved-password")]


def test_existing_account_login_without_password_uses_email_otp(monkeypatch):
    driver = FakeDriver()
    calls = []
    monkeypatch.setattr(twofa, "_maybe_accept", lambda _driver: None)
    monkeypatch.setattr(twofa, "_submit_email_and_wait_next", lambda *_args, **_kwargs: "login_password")
    monkeypatch.setattr(
        twofa,
        "_click_passwordless_signup_if_present",
        lambda _driver: calls.append("passwordless") or {"ok": True},
    )
    monkeypatch.setattr(twofa, "_wait_for_email_otp_page", lambda _driver: calls.append("otp_page"))
    monkeypatch.setattr(twofa, "wait_for_otp", lambda *_args, **_kwargs: calls.append("fetch_otp") or "123456")
    monkeypatch.setattr(twofa, "_clear_otp_inputs", lambda _driver: calls.append("clear"))
    monkeypatch.setattr(twofa, "_type_otp", lambda _driver, code: calls.append(("type", code)))
    monkeypatch.setattr(twofa, "_click_continue", lambda _driver: calls.append("continue"))
    monkeypatch.setattr(twofa, "_wait_after_email_otp_submit", lambda *_args, **_kwargs: "accepted")

    twofa._login_existing_account(driver, "user@example.test", None)

    assert calls == [
        "passwordless",
        "otp_page",
        "fetch_otp",
        "clear",
        ("type", "123456"),
        "continue",
    ]
