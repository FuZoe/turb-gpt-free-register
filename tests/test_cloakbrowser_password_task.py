from types import SimpleNamespace

from core import cloakbrowser_password as password


class FakeDriver:
    def __init__(self):
        self.quit_called = False

    def quit(self):
        self.quit_called = True


def test_existing_account_password_logs_in_then_creates_password(monkeypatch):
    driver = FakeDriver()
    opened = SimpleNamespace(raw={"proxy": "http://127.0.0.1:7901"})
    calls = []
    monkeypatch.setattr(password._cfg, "CLOAK_KEEP_BROWSER_OPEN", False)
    monkeypatch.setattr(password, "build_cloak_driver", lambda proxy=None: (driver, opened))
    monkeypatch.setattr(password, "_login_existing_account", lambda *args: calls.append("login"))
    monkeypatch.setattr(password, "_fetch_chatgpt_session", lambda *args, **kwargs: {"user": {"id": "u1"}})
    monkeypatch.setattr(password, "setup_cloak_password", lambda *args: calls.append("password") or "StrongPass!234")
    monkeypatch.setattr(password, "_wait_for_email_otp_page", lambda *args: calls.append("otp_page"))
    monkeypatch.setattr(password, "wait_for_otp", lambda *args, **kwargs: calls.append("fetch_otp") or "123456")
    monkeypatch.setattr(password, "_clear_otp_inputs", lambda *args: calls.append("clear"))
    monkeypatch.setattr(password, "_type_otp", lambda *args: calls.append("type"))
    monkeypatch.setattr(password, "_click_continue", lambda *args: calls.append("continue"))
    monkeypatch.setattr(password, "_wait_after_email_otp_submit", lambda *args, **kwargs: "accepted")

    result = password._run_existing_account_password_impl("user@example.test")

    assert result["ok"] is True
    assert result["registration_password"] == "StrongPass!234"
    assert calls == ["login", "password", "otp_page", "fetch_otp", "clear", "type", "continue"]
    assert driver.quit_called is True
