from unittest.mock import patch

from core.cloakbrowser_session import (
    build_browser_session_from_cloak,
    setup_cloak_2fa,
    sync_browser_session_to_cloak,
)
from core import roxy_registration
from core.db import _account_credentials_line
from webui.app import _account_secret_value
from webui.config_editor import EDITABLE_FIELDS


class FakeContext:
    def __init__(self):
        self.added_cookies = []

    def cookies(self):
        return [
            {
                "name": "oai-did",
                "value": "browser-device-id",
                "domain": "chatgpt.com",
                "path": "/",
                "secure": True,
            },
            {
                "name": "__Secure-next-auth.session-token",
                "value": "session-value",
                "domain": ".chatgpt.com",
                "path": "/",
                "secure": True,
            },
        ]

    def add_cookies(self, cookies):
        self.added_cookies.extend(cookies)


class FakeDriver:
    def __init__(self):
        self.context = FakeContext()
        self.scripts = []

    def execute_script(self, script, *args):
        self.scripts.append((script, args))
        if "navigator.userAgent" in script:
            return {
                "userAgent": "CloakBrowser/Test",
                "language": "ja-JP",
                "languages": ["ja-JP", "ja", "en-US"],
                "deviceId": "browser-device-id",
            }
        return {"ok": True, "reason": "submitted_password"}


def test_cloak_session_bridge_copies_identity_cookie_and_proxy():
    driver = FakeDriver()

    session = build_browser_session_from_cloak(
        driver,
        "http://user:password@proxy.example:443",
    )
    try:
        assert session.proxy == "http://user:password@proxy.example:443"
        assert session.device_id == "browser-device-id"
        assert session.browser_profile["user_agent"] == "CloakBrowser/Test"
        assert session.browser_profile["navigator_language"] == "ja-JP"
        assert "__Secure-next-auth.session-token=session-value" in session.chatgpt_cookie_header()
    finally:
        session.session.close()


def test_cloak_session_bridge_writes_protocol_cookies_back():
    driver = FakeDriver()
    session = build_browser_session_from_cloak(driver, "")
    try:
        session.session.cookies.set(
            "new-session-cookie",
            "new-value",
            domain="chatgpt.com",
            path="/",
            secure=True,
        )
        count = sync_browser_session_to_cloak(driver, session)
    finally:
        session.session.close()

    assert count >= 1
    assert any(
        item["name"] == "new-session-cookie" and item["value"] == "new-value"
        for item in driver.context.added_cookies
    )


def test_prefer_password_does_not_click_passwordless_entry():
    driver = FakeDriver()
    with (
        patch.object(roxy_registration, "_is_email_verification_page", side_effect=[False, True]),
        patch.object(roxy_registration, "_has_access_token", return_value=False),
        patch.object(roxy_registration, "_password_page_state", return_value={"url": "https://auth.openai.com/create-account/password"}),
        patch.object(roxy_registration, "_is_signup_password_page", return_value=True),
        patch.object(roxy_registration, "_is_login_password_page", return_value=False),
        patch.object(roxy_registration, "_registration_password", return_value="StrongPass!234"),
        patch.object(roxy_registration, "_click_passwordless_signup_if_present") as passwordless,
    ):
        password = roxy_registration._fill_password_page_if_present(
            driver,
            "user@example.test",
            timeout=1,
            prefer_password=True,
        )

    assert password == "StrongPass!234"
    passwordless.assert_not_called()
    assert driver.scripts[-1][1] == ("StrongPass!234",)


def test_switch_from_otp_detects_signup_password_page():
    driver = FakeDriver()
    with patch.object(roxy_registration, "_is_signup_password_page", return_value=True):
        result = roxy_registration._switch_otp_to_password_page(driver, timeout=1)

    assert result["ok"] is True
    assert result["password_page"] == "signup"


def test_setup_cloak_2fa_validates_session_and_syncs_cookies():
    driver = FakeDriver()
    session = build_browser_session_from_cloak(driver, "")
    with (
        patch("core.cloakbrowser_session.build_browser_session_from_cloak", return_value=session),
        patch("core.account_export.fetch_session", return_value={"user": {"email": "user@example.test"}}) as fetch,
        patch("core.account_export.setup_2fa", return_value="TOTPSECRET") as setup,
        patch("core.cloakbrowser_session.sync_browser_session_to_cloak", return_value=2) as sync,
    ):
        secret = setup_cloak_2fa(driver, "user@example.test", "")

    assert secret == "TOTPSECRET"
    fetch.assert_called_once_with(session)
    setup.assert_called_once_with(session, "user@example.test")
    sync.assert_called_once_with(driver, session)


def test_cloak_password_fields_are_exposed_in_webui():
    fields = {item["key"]: item for item in EDITABLE_FIELDS}

    assert fields["CLOAK_ENABLE_PASSWORD"]["type"] == "bool"
    assert fields["REGISTER_PASSWORD"]["secret"] is True


def test_credentials_line_contains_chatgpt_password_and_totp():
    row = {
        "email": "user@example.test",
        "registration_password": "StrongPass!234",
        "totp_secret": "TOTPSECRET",
    }

    expected = "user@example.test----StrongPass!234----TOTPSECRET"
    assert _account_credentials_line(row) == expected
    assert _account_secret_value(row, "credentials_line") == expected
