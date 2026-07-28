from core.cloakbrowser_driver import CloakSeleniumDriver


class FakePage:
    def __init__(self, goto_errors=None):
        self.handlers = {}
        self.goto_errors = list(goto_errors or [])
        self.goto_calls = []
        self.wait_calls = []

    def on(self, event_name, callback):
        self.handlers[event_name] = callback

    def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))
        if self.goto_errors:
            error = self.goto_errors.pop(0)
            if error is not None:
                raise error

    def wait_for_selector(self, selector, **kwargs):
        self.wait_calls.append((selector, kwargs))


def make_driver(page):
    return CloakSeleniumDriver(browser=object(), context=None, page=page)


def test_get_waits_for_domcontentloaded_in_shorter_attempts():
    page = FakePage()
    driver = make_driver(page)

    driver.get("https://chatgpt.com/auth/login")

    assert page.goto_calls == [(
        "https://chatgpt.com/auth/login",
        {"wait_until": "domcontentloaded", "timeout": 30000},
    )]
    assert page.wait_calls == []


def test_get_retries_transient_proxy_disconnect(monkeypatch):
    page = FakePage([
        RuntimeError("Page.goto: net::ERR_CONNECTION_CLOSED"),
        None,
    ])
    driver = make_driver(page)
    monkeypatch.setattr("core.cloakbrowser_driver.time.sleep", lambda _seconds: None)

    driver.get("https://chatgpt.com/auth/login")

    assert len(page.goto_calls) == 2
    assert page.wait_calls == []


def test_get_propagates_non_transient_navigation_error():
    page = FakePage([RuntimeError("Page.goto: net::ERR_CERT_AUTHORITY_INVALID")])
    driver = make_driver(page)

    try:
        driver.get("https://chatgpt.com/auth/login")
    except RuntimeError as exc:
        assert "ERR_CERT_AUTHORITY_INVALID" in str(exc)
    else:
        raise AssertionError("expected navigation error")
