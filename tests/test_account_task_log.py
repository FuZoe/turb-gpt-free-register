import logging

from core import account_task_log
from core.tenant_context import tenant_scope


def _use_tmp_log_root(monkeypatch, tmp_path):
    monkeypatch.setattr(account_task_log, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(account_task_log, "_LOG_DIR", tmp_path / "logs")


def test_account_task_log_initializes_appends_and_captures(monkeypatch, tmp_path):
    _use_tmp_log_root(monkeypatch, tmp_path)

    account_task_log.initialize("password", "User@example.test", account_id=7, trigger="manual")
    account_task_log.append("password", "User@example.test", "WARNING", "等待页面")
    with account_task_log.capture("password", "User@example.test"):
        logging.getLogger("account-task-test").warning("浏览器阶段失败")

    content = account_task_log.read("password", "User@example.test")
    assert "已入队：account_id=7" in content
    assert "等待页面" in content
    assert "浏览器阶段失败" in content


def test_account_task_log_isolated_by_tenant(monkeypatch, tmp_path):
    _use_tmp_log_root(monkeypatch, tmp_path)
    account_task_log.initialize("twofa", "same@example.test", account_id=1, trigger="manual")

    with tenant_scope("friend"):
        assert account_task_log.read("twofa", "same@example.test") == ""
        account_task_log.initialize("twofa", "same@example.test", account_id=2, trigger="manual")
        assert "account_id=2" in account_task_log.read("twofa", "same@example.test")

    assert "account_id=1" in account_task_log.read("twofa", "same@example.test")

