from core import roxy_registration as reg


class FakeDriver:
    current_url = "https://auth.openai.com/email-verification"


def test_wait_checks_terminal_state_once_more_after_timeout(monkeypatch):
    monkeypatch.setattr(reg, "_has_access_token", lambda _driver: False)
    monkeypatch.setattr(reg, "_is_login_password_page", lambda _driver: False)
    monkeypatch.setattr(reg, "_is_signup_password_page", lambda _driver: False)
    monkeypatch.setattr(reg, "_is_email_verification_page", lambda _driver: True)

    assert reg._wait_email_submit_next_state(FakeDriver(), "user@example.com", timeout=0) == "otp"


def test_submit_retry_does_not_refill_after_late_otp_navigation(monkeypatch):
    monkeypatch.setattr(reg, "_has_access_token", lambda _driver: False)
    monkeypatch.setattr(reg, "_is_login_password_page", lambda _driver: False)
    monkeypatch.setattr(reg, "_is_signup_password_page", lambda _driver: False)
    monkeypatch.setattr(reg, "_is_email_verification_page", lambda _driver: True)
    monkeypatch.setattr(
        reg,
        "_type_email_address",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("email must not be refilled")),
    )

    assert reg._submit_email_and_wait_next(FakeDriver(), "user@example.com", attempts=2) == "otp"
