from core import roxy_registration as reg


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
