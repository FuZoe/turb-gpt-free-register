import signal

from core import cloakbrowser_driver as cloak


def test_owned_process_tree_excludes_other_browser(monkeypatch):
    monkeypatch.setattr(
        cloak,
        "_browser_process_snapshot",
        lambda: {
            100: (1, "owned root"),
            101: (100, "owned child"),
            102: (101, "owned grandchild"),
            200: (1, "other root"),
            201: (200, "other child"),
        },
    )

    assert cloak._owned_process_tree({100}) == {100, 101, 102}


def test_terminate_only_signals_owned_processes(monkeypatch):
    calls = []
    monkeypatch.setattr(cloak, "_owned_process_tree", lambda seeds: {100, 101})
    monkeypatch.setattr(cloak.os, "kill", lambda pid, sig: calls.append((pid, sig)))
    monkeypatch.setattr(cloak.time, "sleep", lambda _seconds: None)

    cloak._terminate_owned_browser_processes({100})

    assert calls == [
        (101, signal.SIGTERM),
        (100, signal.SIGTERM),
        (101, getattr(signal, "SIGKILL", signal.SIGTERM)),
        (100, getattr(signal, "SIGKILL", signal.SIGTERM)),
    ]


def test_quit_cleans_owned_processes_when_browser_close_fails(monkeypatch):
    cleaned = []

    class BrokenClose:
        def close(self):
            raise RuntimeError("close failed")

    driver = cloak.CloakSeleniumDriver(browser=BrokenClose(), context=BrokenClose(), page=object())
    driver._owned_process_pids = {100}
    monkeypatch.setattr(cloak, "_owned_process_tree", lambda seeds: {100, 101})
    monkeypatch.setattr(cloak, "_terminate_owned_browser_processes", lambda pids: cleaned.append(pids))

    driver.quit()

    assert cleaned == [{100, 101}]
