import threading
import time

from core import registration_service as service


def test_global_limit_defaults_to_configured_workers_limit(monkeypatch):
    monkeypatch.delenv("REGISTRATION_GLOBAL_CONCURRENCY", raising=False)
    monkeypatch.setenv("REGISTRATION_WORKERS_LIMIT", "2")

    assert service._global_registration_limit() == 2


def test_global_registration_slot_caps_overlapping_executor_generations(monkeypatch):
    slots = threading.BoundedSemaphore(2)
    monkeypatch.setattr(service, "_GLOBAL_REGISTRATION_SLOTS", slots)
    active = 0
    peak = 0
    lock = threading.Lock()
    release = threading.Event()

    def work():
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        release.wait(timeout=2)
        with lock:
            active -= 1

    threads = [
        threading.Thread(
            target=service._run_registration_with_global_slot,
            args=("default", work),
        )
        for _ in range(5)
    ]
    for thread in threads:
        thread.start()
    time.sleep(0.1)

    assert peak == 2

    release.set()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()
