import base64
import json

from core import generic_api_mail_client as client


def test_extract_detail_otp_from_mail_viewer_data_url(monkeypatch):
    html = """
    <html><style>.size{width:464779px}</style>
    <p>Enter this temporary verification code to continue:</p>
    <p>464779</p></html>
    """
    body = "data:text/html;base64," + base64.b64encode(html.encode()).decode()
    listing = (
        "<a class='item' data-id='1359417'></a>"
        "<script>var detailBase='/message/';"
        "var detailSuffix='/secret/mail@example.test';</script>"
    )

    class Response:
        status_code = 200

        def json(self):
            return {"subject": "Your temporary ChatGPT verification code", "body": body}

    seen = []
    monkeypatch.setattr(client.requests, "get", lambda url, **kwargs: seen.append(url) or Response())

    code = client._extract_detail_otp(
        "http://mail.test/inbox",
        listing,
        {"Accept": "application/json"},
        after_ts=None,
    )

    assert code == "464779"
    assert seen == ["http://mail.test/message/1359417/secret/mail@example.test"]


def test_extract_detail_otp_skips_messages_older_than_attempt(monkeypatch):
    listing = (
        '<a class="item" data-id="2"><div class="time">2026-08-02 23:20:00</div></a>'
        '<a class="item" data-id="1"><div class="time">2026-08-02 22:00:00</div></a>'
        "<script>var detailBase='/message/';"
        "var detailSuffix='/secret/mail@example.test';</script>"
    )
    after_ts = client.time.mktime(client.time.strptime("2026-08-02 23:00:00", "%Y-%m-%d %H:%M:%S"))

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"subject": "Verification code", "body": "Your verification code is 654321"}

    seen = []
    monkeypatch.setattr(client.requests, "get", lambda url, **kwargs: seen.append(url) or Response())

    code = client._extract_detail_otp(
        "http://mail.test/inbox",
        listing,
        {"Accept": "application/json"},
        after_ts=after_ts,
    )

    assert code == "654321"
    assert seen == ["http://mail.test/message/2/secret/mail@example.test"]


def test_fetch_latest_otp_waits_for_code_after_last_consumed(monkeypatch):
    email = "mail@example.test"
    account = client.GenericApiEmailAccount(email=email, code_url="https://mail.test/code")
    responses = iter(("Verification code: 111111", "Verification code: 222222"))

    class Response:
        status_code = 200

        def __init__(self, text):
            self.text = text

    monkeypatch.setattr(client, "get_account_context", lambda _email: account)
    monkeypatch.setattr(
        client.requests,
        "get",
        lambda *_args, **_kwargs: Response(next(responses)),
    )
    monkeypatch.setattr(client.time, "sleep", lambda _seconds: None)
    key = client._cache_key(email)
    client._LAST_RETURNED_OTP[key] = "111111"
    try:
        code = client.fetch_latest_otp(
            email,
            max_wait=1,
            poll_interval=1,
            settle_seconds=0,
        )
    finally:
        client._LAST_RETURNED_OTP.pop(key, None)

    assert code == "222222"
