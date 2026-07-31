import json

from core import extract_link_service as service


class FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, rows):
        self._rows = rows

    def iter_lines(self):
        for row in self._rows:
            yield json.dumps(row, ensure_ascii=False).encode("utf-8")


class FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.closed = False

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.rows)

    def close(self):
        self.closed = True


def test_upi_bridge_sends_at_to_newzoe_and_maps_result(monkeypatch):
    session = FakeSession([
        {"type": "progress", "message": "正在处理"},
        {
            "index": 1,
            "ok": True,
            "result": {
                "ok": True,
                "upi_instructions_url": "https://payments.stripe.com/upi/instructions/demo",
                "qr_url": "/api/qr?data=demo",
                "cdk_remaining_uses": 8,
            },
        },
    ])
    monkeypatch.setattr(service, "_session", lambda: session)
    monkeypatch.setattr(service, "_api_base", lambda: "https://upi.newzoe.cloud")

    events = list(service._iter_upi_events(token="AT_VALUE", cdk="CDK_VALUE", job_id="job-1"))

    assert session.calls[0][0] == "https://upi.newzoe.cloud/api/extract-batch"
    payload = session.calls[0][1]["json"]
    assert payload["tokens"] == ["AT_VALUE"]
    assert payload["proxy_mode"] == "paid"
    assert payload["payment_method_type"] == "upi"
    assert "email" not in payload and "password" not in payload
    assert events[0] == ("log", {"message": "正在处理"})
    result = events[1][1]["result"]
    assert result["long_url"].endswith("/demo")
    assert result["image_url_svg"] == "https://upi.newzoe.cloud/api/qr?data=demo"
    assert result["cdk_remaining"] == 8
    assert session.closed is True


def test_upi_bridge_translates_failed_row(monkeypatch):
    session = FakeSession([
        {"index": 1, "ok": False, "error": "Token 已失效", "result": {"code": "token_unauthorized"}},
    ])
    monkeypatch.setattr(service, "_session", lambda: session)
    monkeypatch.setattr(service, "_api_base", lambda: "https://upi.newzoe.cloud")

    events = list(service._iter_upi_events(token="AT_VALUE", cdk="CDK_VALUE", job_id="job-2"))

    assert events == [("error", {"message": "Token 已失效", "details": {"code": "token_unauthorized"}})]


def test_upi_bridge_ignores_stale_legacy_link_type(monkeypatch):
    monkeypatch.setenv("EXTRACT_LINK_TYPE", "pix")
    assert service._link_type() == "upi"
    assert service._link_type("ideal") == "upi"
