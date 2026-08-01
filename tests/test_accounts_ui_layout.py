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


def test_accounts_ui_exposes_queued_twofa_automation():
    text = TEMPLATE.read_text(encoding="utf-8")

    assert 'id="btnOpenAutoTask"' in text
    assert 'id="autoTaskPanel"' in text
    assert 'id="autoTaskSelectAll"' in text
    assert '全选/取消全选全部候选账号' in text
    assert 'data-create-twofa=' in text
    assert '/api/accounts/create-2fa' in text
    assert '2FA排队' in text
    assert '创建2FA中' in text


def test_accounts_automation_panel_exposes_all_candidate_modes():
    text = TEMPLATE.read_text(encoding="utf-8")

    assert 'name="autoTaskMode" value="twofa"' in text
    assert 'name="autoTaskMode" value="password"' in text
    assert 'name="autoTaskMode" value="codex"' in text
    assert "query:'twofa=disabled'" in text
    assert "query:'password=missing'" in text
    assert "query:'codex=incomplete'" in text
    assert '/api/accounts/create-password-bulk' in text
    assert '/api/codex/retry-bulk' in text
    assert 'fetchAllAutoTaskCandidateIds' in text


def test_accounts_ui_exposes_twofa_and_password_filters():
    text = TEMPLATE.read_text(encoding="utf-8")

    assert 'id="accountTwofaFilter"' in text
    assert 'id="accountPasswordFilter"' in text
    assert 'twofa=${encodeURIComponent(twofa)}' in text
    assert 'password=${encodeURIComponent(password)}' in text
