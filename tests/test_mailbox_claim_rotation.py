from concurrent.futures import ThreadPoolExecutor

from core import db


def _generic_row(row_id: int) -> dict:
    return {
        "id": row_id,
        "email": f"mail{row_id}@example.com",
        "code_url": f"https://mail.example/{row_id}",
        "status": "available",
    }


def _fake_generic_store(monkeypatch, count: int = 5) -> list[dict]:
    store = [_generic_row(row_id) for row_id in range(1, count + 1)]
    monkeypatch.setattr(db, "_load_generic_api_emails", lambda: store)

    def save(rows):
        store[:] = [dict(row) for row in rows]

    monkeypatch.setattr(db, "_save_generic_api_emails", save)
    return store


def test_three_concurrent_claims_use_three_distinct_mailboxes(monkeypatch):
    _fake_generic_store(monkeypatch)

    with ThreadPoolExecutor(max_workers=3) as executor:
        claimed = list(executor.map(lambda _index: db.claim_next_generic_api_email(), range(3)))

    assert {row["email"] for row in claimed} == {
        "mail1@example.com",
        "mail2@example.com",
        "mail3@example.com",
    }


def test_failed_mailbox_moves_behind_untried_mailboxes(monkeypatch):
    store = _fake_generic_store(monkeypatch, count=3)

    first = db.claim_next_generic_api_email()
    db.release_unconsumed_generic_api_email(first["email"], note="first attempt failed")
    second = db.claim_next_generic_api_email()
    db.release_unconsumed_generic_api_email(second["email"], note="second attempt failed")
    third = db.claim_next_generic_api_email()

    assert [first["email"], second["email"], third["email"]] == [
        "mail1@example.com",
        "mail2@example.com",
        "mail3@example.com",
    ]
    first_row = next(row for row in store if row["email"] == first["email"])
    assert first_row["attempt_count"] == 1
    assert first_row["released_at"]

