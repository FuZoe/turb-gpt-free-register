import pytest

from core import roxy_registration as reg


class Clock:
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(float(seconds), 0.1)


def test_profile_completion_waits_for_navigation_and_retries(monkeypatch):
    clock = Clock()
    state = {"submits": 0}

    class Driver:
        current_url = "https://auth.openai.com/about-you"

    def snapshot(_driver):
        if state["submits"] >= 2:
            return {"url": "https://chatgpt.com/", "inputs": [], "widgets": []}
        return {
            "url": "https://auth.openai.com/about-you",
            "inputs": [{"name": "name", "autocomplete": "name"}],
            "widgets": [{"role": "spinbutton", "dataType": "year"}],
        }

    def submit(_driver):
        state["submits"] += 1
        return True

    monkeypatch.setattr(reg.time, "time", clock.time)
    monkeypatch.setattr(reg.time, "sleep", clock.sleep)
    monkeypatch.setattr(reg, "human_delay", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(reg, "_has_access_token", lambda _driver: False)
    monkeypatch.setattr(reg, "_page_snapshot", snapshot)
    monkeypatch.setattr(reg, "_select_or_type", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(reg, "_fill_birthday_or_age", lambda *_args, **_kwargs: "spinbutton")
    monkeypatch.setattr(reg, "_accept_profile_consents", lambda _driver: 0)
    monkeypatch.setattr(reg, "_click_if_enabled_submit", submit)

    assert reg._complete_profile_page(Driver(), "Test User", "1990-01-02", timeout=30) is True
    assert state["submits"] == 2


def test_session_wait_never_navigates_away_from_about_you(monkeypatch):
    clock = Clock()

    class Driver:
        current_url = "https://auth.openai.com/about-you"
        window_handles = []
        get_calls = []

        def get(self, url):
            self.get_calls.append(url)

    driver = Driver()
    monkeypatch.setattr(reg.time, "time", clock.time)
    monkeypatch.setattr(reg.time, "sleep", clock.sleep)
    monkeypatch.setattr(reg, "_switch_to_chatgpt_window_if_any", lambda _driver: False)
    monkeypatch.setattr(reg, "_page_snapshot", lambda _driver: {"url": driver.current_url})

    with pytest.raises(RuntimeError, match="仍在认证资料页"):
        reg._fetch_chatgpt_session(driver, timeout=5, auto_jump_wait=1)

    assert driver.get_calls == []


def test_access_token_probe_skips_cross_origin_auth_page():
    class Driver:
        current_url = "https://auth.openai.com/about-you"

        def execute_async_script(self, _script):
            raise AssertionError("auth.openai.com 上不应跨域读取 ChatGPT session")

    assert reg._has_access_token(Driver()) is False


@pytest.mark.parametrize("state", [
    {
        "url": "https://auth.openai.com/api/accounts/authorize",
        "title": "しばらくお待ちください...",
        "text": "セキュリティ検証の実行 Ray ID: abc123 Cloudflare",
    },
    {
        "url": "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/turnstile",
        "title": "Just a moment",
        "text": "Performing security verification",
    },
])
def test_detects_cloudflare_challenge_in_localized_pages(state):
    assert reg._is_cloudflare_challenge_state(state) is True
    with pytest.raises(reg.CloudflareChallengeError, match="Cloudflare challenge/403"):
        reg._raise_if_cloudflare_challenge(None, state)
