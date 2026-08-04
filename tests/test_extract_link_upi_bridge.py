import json

import pytest

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


def test_query_cdk_uses_newzoe_status_endpoint(monkeypatch):
    class JsonResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"ok": True, "remaining_uses": 7, "total_uses": 10}

    class JsonSession:
        def __init__(self):
            self.call = None

        def post(self, url, **kwargs):
            self.call = (url, kwargs)
            return JsonResponse()

        def close(self):
            pass

    session = JsonSession()
    monkeypatch.setattr(service, "_session", lambda: session)
    monkeypatch.setattr(service, "_api_base", lambda: "https://upi.newzoe.cloud")

    result = service.query_cdk(cdk="CDK_VALUE")

    assert result["remaining_uses"] == 7
    assert session.call[0] == "https://upi.newzoe.cloud/api/cdk/status"
    assert session.call[1]["json"] == {"cdk": "CDK_VALUE", "cdk_type": "normal"}


def test_external_nl_bridge_submits_at_and_polls_ready_task(monkeypatch):
    class ExternalResponse:
        text = ""
        headers = {}

        def __init__(self, status_code, payload):
            self.status_code = status_code
            self.payload = payload

        def json(self):
            return self.payload

    class ExternalSession:
        def __init__(self):
            self.calls = []
            self.polls = 0
            self.closed = False

        def post(self, url, **kwargs):
            self.calls.append(("POST", url, kwargs))
            return ExternalResponse(202, {
                "ok": True,
                "accepted": True,
                "task_id": "upi-task-1",
                "status": "queued",
            })

        def get(self, url, **kwargs):
            self.calls.append(("GET", url, kwargs))
            self.polls += 1
            if self.polls == 1:
                return ExternalResponse(200, {"ok": True, "task_id": "upi-task-1", "status": "running"})
            return ExternalResponse(200, {
                "ok": True,
                "task_id": "upi-task-1",
                "status": "ready",
                "pay_url": "https://payments.stripe.com/upi/instructions/external",
                "qr_data_url": "data:image/png;base64,AAAA",
                "expires_at": 1783900300,
                "cdk": {"available": 9},
            })

        def close(self):
            self.closed = True

    session = ExternalSession()
    monkeypatch.setattr(service, "_session", lambda: session)
    monkeypatch.setattr(service, "_external_nl_api_base", lambda: "https://ideal.169abc.xyz/upi")
    monkeypatch.setattr(service.time, "sleep", lambda _seconds: None)

    events = list(service._iter_external_nl_events(token="AT_VALUE", cdk="USER_CDK", job_id="local-job"))

    assert session.calls[0][0:2] == ("POST", "https://ideal.169abc.xyz/upi/api/pay")
    assert session.calls[0][2]["json"] == {"cdk": "USER_CDK", "at": "AT_VALUE"}
    assert session.calls[-1][0:2] == ("GET", "https://ideal.169abc.xyz/upi/api/status/upi-task-1")
    result = events[-1][1]["result"]
    assert result["long_url"].endswith("/external")
    assert result["image_url_png"].startswith("data:image/png")
    assert result["payment_link_type"] == "upi_external_nl"
    assert result["cdk_remaining"] == 9
    assert result["expires_at"].startswith("2026-")
    assert session.closed is True


def test_external_nl_cdk_must_be_supplied_by_user(monkeypatch):
    monkeypatch.setenv("EXTRACT_LINK_CDK", "INTERNAL_CDK")

    with pytest.raises(ValueError, match="用户填写 CDK"):
        service._cdk(None, provider="external_nl")


def test_query_external_nl_cdk_uses_short_status_endpoint(monkeypatch):
    class JsonResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"ok": True, "available": 6}

    class JsonSession:
        def __init__(self):
            self.call = None

        def post(self, url, **kwargs):
            self.call = (url, kwargs)
            return JsonResponse()

        def close(self):
            pass

    session = JsonSession()
    monkeypatch.setattr(service, "_session", lambda: session)
    monkeypatch.setattr(service, "_external_nl_api_base", lambda: "https://ideal.169abc.xyz/upi")

    result = service.query_cdk(cdk="USER_CDK", provider="external_nl")

    assert result["available"] == 6
    assert session.call[0] == "https://ideal.169abc.xyz/upi/api/cdk-status"
    assert session.call[1]["json"] == {"cdk": "USER_CDK"}
