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

    assert ">复制Session</button>" in text
    assert "data-account-copy-secret=\"session\"" in text
    assert "Session 已复制" in text


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
    assert 'data-manual-twofa=' in text
    assert '手动填写2FA' in text


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
    assert '密码日志' in text
    assert '2FA日志' in text
    assert '/api/accounts/task-log?account_id=' in text


def test_accounts_ui_exposes_twofa_and_password_filters():
    text = TEMPLATE.read_text(encoding="utf-8")

    assert 'id="accountTwofaFilter"' in text
    assert 'id="accountPasswordFilter"' in text
    assert 'twofa=${encodeURIComponent(twofa)}' in text
    assert 'password=${encodeURIComponent(password)}' in text


def test_extract_link_ui_supports_external_ideal_user_cdk():
    text = TEMPLATE.read_text(encoding="utf-8")

    assert 'id="extractLinkPanel"' in text
    assert 'name="extractLinkProvider" value="builtin"' in text
    assert 'name="extractLinkProvider" value="external_ideal"' in text
    assert '外部荷兰 iDEAL' in text
    assert 'id="externalIdealCdk" type="password"' in text
    assert "provider === 'external_ideal'" in text
    assert "body.cdk = cdk" in text


def test_email_pool_ui_exposes_status_filter():
    text = TEMPLATE.read_text(encoding="utf-8")

    assert 'id="poolStatus"' in text
    assert '<option value="available">可用</option>' in text
    assert '<option value="used">已用</option>' in text
    assert '<option value="failed">失败</option>' in text
    assert 'status=${encodeURIComponent(status)}' in text


def test_runtime_resource_box_supports_drag_and_resize():
    text = TEMPLATE.read_text(encoding="utf-8")

    assert 'initRuntimeResourceBox' in text
    assert 'resize: both' in text
    assert 'cursor:grab' in text
    assert "'turb_runtime_box_bounds'" in text


def test_nav_uses_separate_urls():
    text = TEMPLATE.read_text(encoding="utf-8")

    assert "history.pushState({tab}" in text
    assert "location.hash !== " in text
    assert "window.addEventListener('hashchange'" in text
    assert "window.__INITIAL_TAB" in text
    assert "location.hash.replace" in text


def test_each_page_has_dedicated_flask_route():
    from webui.app import create_app

    app = create_app(auth_code="test-auth").test_client()
    app.environ_base["HTTP_X_AUTH_CODE"] = "test-auth"

    for path in ("/register", "/accounts", "/codex", "/outlook", "/proxies", "/config"):
        r = app.get(path)
        assert r.status_code == 200, path
        assert r.status_code == 200, path

    # / also returns 200
    r = app.get("/")
    assert r.status_code == 200
