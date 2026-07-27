from pathlib import Path

from core.cloakbrowser_driver import CloakSeleniumDriver


class FakePage:
    def __init__(self):
        self.url = "https://chatgpt.com/auth/login?email=secret@example.com#frag"
        self.handlers = {}
        self.screenshot_args = None

    def on(self, event_name, callback):
        self.handlers[event_name] = callback

    def evaluate(self, script):
        return {
            "url": self.url,
            "title": "开始使用",
            "readyState": "complete",
            "bodyPreview": "页面内容",
            "inputs": [{"type": "password", "filled": True, "valueLength": 12}],
            "actions": [],
        }

    def screenshot(self, **kwargs):
        self.screenshot_args = kwargs
        Path(kwargs["path"]).write_bytes(b"png")


class FakeRequest:
    method = "GET"
    resource_type = "script"
    url = "https://cdn.example.test/app.js?token=SECRET#x"
    failure = "net::ERR_CONNECTION_CLOSED"


class FakeResponse:
    status = 503
    url = "https://auth.openai.com/api/check?email=secret@example.com"
    request = FakeRequest()


class FakeConsole:
    type = "error"
    text = "Failed to load resource"
    location = {"url": "https://cdn.example.test/app.js?token=SECRET"}


def make_driver():
    page = FakePage()
    driver = CloakSeleniumDriver(browser=object(), context=None, page=page)
    return driver, page


def test_diagnostics_collect_and_strip_query_parameters():
    driver, page = make_driver()

    page.handlers["requestfailed"](FakeRequest())
    page.handlers["response"](FakeResponse())
    page.handlers["console"](FakeConsole())
    page.handlers["pageerror"](RuntimeError("script exploded"))
    snapshot = driver.diagnostic_snapshot()

    assert snapshot["page"]["url"] == "https://chatgpt.com/auth/login"
    assert snapshot["request_failures"][0]["url"] == "https://cdn.example.test/app.js"
    assert snapshot["http_errors"][0]["url"] == "https://auth.openai.com/api/check"
    assert snapshot["console_errors"][0]["url"] == "https://cdn.example.test/app.js"
    assert snapshot["page_errors"][0]["error"] == "script exploded"
    assert "secret@example.com" not in repr(snapshot)
    assert "SECRET" not in repr(snapshot)


def test_diagnostics_are_bounded_to_latest_events():
    driver, page = make_driver()

    for index in range(130):
        request = FakeRequest()
        request.url = f"https://cdn.example.test/{index}.js?token=SECRET"
        page.handlers["requestfailed"](request)

    assert len(driver.diagnostic_snapshot()["request_failures"]) == 100
    assert driver.diagnostic_snapshot()["request_failures"][0]["url"].endswith("/30.js")


def test_save_diagnostic_screenshot(tmp_path):
    driver, page = make_driver()
    output = tmp_path / "nested" / "failure.png"

    saved = driver.save_diagnostic_screenshot(output)

    assert saved == str(output)
    assert output.read_bytes() == b"png"
    assert page.screenshot_args["full_page"] is True
