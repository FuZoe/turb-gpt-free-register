import threading
from unittest.mock import MagicMock, patch

from core import cloakbrowser_registration
from core.cloakbrowser_session import (
    build_browser_session_from_cloak,
    setup_cloak_password,
    setup_cloak_2fa,
    sync_browser_session_to_cloak,
)
from core.openai_auth import follow_password_registration, register_user
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
        self.navigations = []

    def execute_script(self, script, *args):
        self.scripts.append((script, args))
        if "navigator.userAgent" in script:
            return {
                "userAgent": "CloakBrowser/Test",
                "language": "ja-JP",
                "languages": ["ja-JP", "ja", "en-US"],
                "platform": "Win32",
                "vendor": "Google Inc.",
                "hardwareConcurrency": 8,
                "deviceMemory": 8,
                "screenWidth": 1920,
                "screenHeight": 1080,
                "devicePixelRatio": 1,
                "timezone": "Asia/Tokyo",
                "timezoneOffset": -540,
                "userAgentData": {
                    "brands": [
                        {"brand": "Chromium", "version": "146"},
                        {"brand": "Google Chrome", "version": "146"},
                    ],
                    "mobile": False,
                    "platform": "Windows",
                },
                "deviceId": "browser-device-id",
            }
        return {"ok": True, "reason": "submitted_password"}

    def get(self, url):
        self.navigations.append(url)


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
        assert session.browser_profile["navigator_platform"] == "Win32"
        assert session.browser_profile["timezone_iana"] == "Asia/Tokyo"
        assert session.browser_profile["timezone_offset_minutes"] == 540
        assert session.browser_profile["sec_ch_ua_platform"] == '"Windows"'
        assert '"Chromium";v="146"' in session.browser_profile["sec_ch_ua"]
        assert "__Secure-next-auth.session-token=session-value" in session.chatgpt_cookie_header()
    finally:
        session.session.close()


def test_cloak_registration_uses_fresh_thread_and_preserves_log_thread_name():
    caller_ident = threading.get_ident()
    caller_name = threading.current_thread().name

    def fake_impl(**kwargs):
        return {
            "success": True,
            "thread_ident": threading.get_ident(),
            "thread_name": threading.current_thread().name,
            "email": kwargs["email"],
        }

    with patch.object(
        cloakbrowser_registration,
        "_run_cloak_registration_impl",
        side_effect=fake_impl,
    ):
        result = cloakbrowser_registration.run_cloak_registration(
            "user@example.test",
            "Test User",
            "1990-01-01",
        )

    assert result["success"] is True
    assert result["thread_ident"] != caller_ident
    assert result["thread_name"] == caller_name


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


def test_setup_cloak_password_reuses_protocol_chain_and_returns_to_otp_page():
    driver = FakeDriver()
    session = build_browser_session_from_cloak(driver, "")
    with (
        patch("core.cloakbrowser_session.build_browser_session_from_cloak", return_value=session),
        patch("core.cloakbrowser_session._registration_password", return_value="StrongPass!234"),
        patch("core.openai_auth.get_create_account_page") as password_page,
        patch("core.openai_auth.request_sentinel_token", return_value={"token": "challenge"}) as sentinel,
        patch("core.openai_auth.build_sentinel_header", return_value=("sentinel-header", "so-header")) as build,
        patch("core.openai_auth.register_user", return_value={"continue_url": "/api/accounts/email-otp/send"}) as register,
        patch("core.openai_auth.follow_password_registration", return_value="https://auth.openai.com/email-verification") as follow,
        patch("core.cloakbrowser_session.sync_browser_session_to_cloak", return_value=3) as sync,
    ):
        password = setup_cloak_password(driver, "user@example.test", "")

    assert password == "StrongPass!234"
    password_page.assert_called_once_with(session)
    sentinel.assert_called_once_with(session, "username_password_create")
    build.assert_called_once_with(session, {"token": "challenge"}, "username_password_create")
    register.assert_called_once_with(
        session,
        "user@example.test",
        "StrongPass!234",
        "sentinel-header",
        "so-header",
    )
    follow.assert_called_once_with(session, {"continue_url": "/api/accounts/email-otp/send"})
    sync.assert_called_once_with(driver, session)
    assert driver.navigations == ["https://auth.openai.com/email-verification"]


def test_protocol_register_user_sends_sentinel_headers_and_password_body():
    session = MagicMock()
    session.get_auth_headers.return_value = {}
    response = MagicMock(status_code=200)
    response.json.return_value = {"page": {"type": "email_otp_send"}}
    session.post.return_value = response

    result = register_user(
        session,
        "user@example.test",
        "StrongPass!234",
        "sentinel-header",
        "so-header",
    )

    assert result["page"]["type"] == "email_otp_send"
    _, kwargs = session.post.call_args
    assert kwargs["headers"]["openai-sentinel-token"] == "sentinel-header"
    assert kwargs["headers"]["openai-sentinel-so-token"] == "so-header"
    assert '"password": "StrongPass!234"' in kwargs["data"]


def test_protocol_password_flow_follows_relative_otp_url():
    session = MagicMock()
    session.get_auth_navigate_headers.return_value = {}
    response = MagicMock(status_code=200)
    response.url = "https://auth.openai.com/email-verification"
    session.get.return_value = response

    final_url = follow_password_registration(
        session,
        {
            "continue_url": "/api/accounts/email-otp/send",
            "method": "GET",
            "page": {"type": "email_otp_send"},
        },
    )

    assert final_url == "https://auth.openai.com/email-verification"
    args, kwargs = session.get.call_args
    assert args[0] == "https://auth.openai.com/api/accounts/email-otp/send"
    assert kwargs["allow_redirects"] is True


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
    assert _account_secret_value(row, "registration_password") == "StrongPass!234"
