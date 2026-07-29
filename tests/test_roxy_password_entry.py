from core import roxy_registration as reg
import pytest


class FakeOtpDriver:
    def __init__(self):
        self.clicked = False

    def execute_script(self, _script):
        self.clicked = True
        return {"ok": True, "reason": "clicked_password_entry"}


def test_open_signup_password_from_otp_waits_for_password_page(monkeypatch):
    driver = FakeOtpDriver()
    states = iter([False, True])
    monkeypatch.setattr(reg, "_is_email_verification_page", lambda _driver: True)
    monkeypatch.setattr(reg, "_is_signup_password_page", lambda _driver: next(states))
    monkeypatch.setattr(reg.time, "sleep", lambda _seconds: None)

    assert reg._open_signup_password_from_otp(driver, timeout=2) is True
    assert driver.clicked is True


def test_open_signup_password_waits_for_otp_dom_to_finish_loading(monkeypatch):
    class LoadingOtpDriver:
        def __init__(self):
            self.attempts = 0
            self.clicked = False

        def execute_script(self, _script):
            self.attempts += 1
            if self.attempts < 3:
                return {"ok": False, "reason": "password_entry_not_ready", "readyState": "loading"}
            self.clicked = True
            return {"ok": True, "reason": "clicked_password_entry"}

    driver = LoadingOtpDriver()
    monkeypatch.setattr(reg, "_is_email_verification_page", lambda _driver: True)
    monkeypatch.setattr(reg, "_is_signup_password_page", lambda _driver: driver.clicked)
    monkeypatch.setattr(reg.time, "sleep", lambda _seconds: None)

    assert reg._open_signup_password_from_otp(driver, timeout=2) is True
    assert driver.attempts == 3


def test_password_submit_does_not_report_success_while_spinner_is_stuck(monkeypatch):
    class StuckPasswordDriver:
        current_url = "https://auth.openai.com/create-account/password"

        def execute_script(self, script, *_args):
            if "const password = String(arguments[0])" in script:
                return {"ok": True, "reason": "submitted_password"}
            return {
                "url": self.current_url,
                "inputs": [{"type": "password", "visible": True, "autocomplete": "new-password"}],
                "buttons": [{"disabled": True, "visible": True}],
                "forms": [],
            }

    clock = [0.0]
    monkeypatch.setattr(reg.time, "time", lambda: clock[0])
    monkeypatch.setattr(reg.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(reg, "_has_access_token", lambda _driver: False)
    monkeypatch.setattr(reg, "_registration_password", lambda: "StrongPassword1!")

    with pytest.raises(RuntimeError, match="仍停留在密码页"):
        reg._fill_password_page_if_present(
            StuckPasswordDriver(),
            "user@example.com",
            timeout=1,
            prefer_password=True,
        )
