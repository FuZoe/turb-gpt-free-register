from pathlib import Path


TEMPLATE = Path(__file__).resolve().parent.parent / "webui" / "templates" / "index.html"


def test_accounts_table_keeps_only_requested_visible_columns():
    text = TEMPLATE.read_text(encoding="utf-8")
    table = text.split('<table class="accounts-table">', 1)[1].split("</table>", 1)[0]

    assert "<th>来源</th>" not in table
    assert "<th>Token</th>" not in table
    assert "<th>Agent Token</th>" not in table
    assert "<th>创建时间</th>" not in table
    assert "<th>密码/2FA</th>" in table
    assert "<th>操作</th>" in table


def test_accounts_txt_uses_email_password_totp_credentials_line():
    text = TEMPLATE.read_text(encoding="utf-8")

    assert "lines = await fetchAccountSecrets(ids, 'credentials_line');" in text
    assert "accounts-password-2fa-" in text
    assert ">复制AT</button>" in text
    assert "data-account-show-agent-token" in text
