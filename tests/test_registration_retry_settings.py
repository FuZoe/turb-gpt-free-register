from pathlib import Path
from unittest.mock import patch

from webui.app import create_app


TEMPLATE = Path(__file__).resolve().parent.parent / "webui" / "templates" / "index.html"


def _client():
    client = create_app(auth_code="test-auth").test_client()
    client.environ_base["HTTP_X_AUTH_CODE"] = "test-auth"
    return client


def test_registration_page_exposes_auto_retry_setting():
    text = TEMPLATE.read_text(encoding="utf-8")

    assert 'id="regAutoRetries"' in text
    assert 'id="btnSaveRegSettings"' in text
    assert "/api/registration/settings" in text
    assert "0=关闭；1=失败后再试一次" in text


@patch("webui.app.svc.get_auto_retry_limit", return_value=1)
def test_registration_settings_get_returns_current_value(get_limit):
    response = _client().get("/api/registration/settings")

    assert response.status_code == 200
    assert response.get_json()["auto_retries"] == 1
    assert response.get_json()["editable"] is True
    get_limit.assert_called_once_with()


@patch("config.reload_all")
@patch("webui.app.config_editor.update_config")
@patch("webui.app.svc.get_auto_retry_limit", return_value=1)
def test_registration_settings_save_is_immediate(get_limit, update_config, reload_all):
    update_config.return_value = {"updated": ["REGISTRATION_CF_AUTO_RETRIES"], "ignored": []}

    response = _client().post("/api/registration/settings", json={"auto_retries": 1})

    assert response.status_code == 200
    assert response.get_json()["auto_retries"] == 1
    update_config.assert_called_once_with({"REGISTRATION_CF_AUTO_RETRIES": 1})
    reload_all.assert_called_once_with()


def test_registration_settings_rejects_out_of_range_value():
    response = _client().post("/api/registration/settings", json={"auto_retries": 11})

    assert response.status_code == 400
