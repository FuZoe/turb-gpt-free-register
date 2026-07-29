from webui.runtime_resources import read_runtime_resources


def _write(path, value):
    path.write_text(str(value), encoding="utf-8")


def test_read_runtime_resources_reports_cgroup_memory_and_swap(tmp_path):
    _write(tmp_path / "memory.current", 512 * 1024 * 1024)
    _write(tmp_path / "memory.peak", 768 * 1024 * 1024)
    _write(tmp_path / "memory.max", 1024 * 1024 * 1024)
    _write(tmp_path / "memory.swap.current", 128 * 1024 * 1024)
    _write(tmp_path / "memory.swap.max", 1024 * 1024 * 1024)
    _write(tmp_path / "pids.current", 123)
    _write(tmp_path / "pids.max", 512)
    _write(tmp_path / "memory.events", "low 0\nhigh 4\nmax 2\noom 1\noom_kill 1\n")

    result = read_runtime_resources(tmp_path)

    assert result["memory_current_bytes"] == 512 * 1024 * 1024
    assert result["memory_max_bytes"] == 1024 * 1024 * 1024
    assert result["memory_percent"] == 50.0
    assert result["swap_current_bytes"] == 128 * 1024 * 1024
    assert result["swap_max_bytes"] == 1024 * 1024 * 1024
    assert result["pids_current"] == 123
    assert result["pids_max"] == 512
    assert result["memory_events"]["oom_kill"] == 1


def test_read_runtime_resources_handles_unlimited_values(tmp_path):
    _write(tmp_path / "memory.current", 1024)
    _write(tmp_path / "memory.max", "max")
    _write(tmp_path / "memory.swap.current", 0)
    _write(tmp_path / "memory.swap.max", "max")
    _write(tmp_path / "pids.current", 1)
    _write(tmp_path / "pids.max", "max")

    result = read_runtime_resources(tmp_path)

    assert result["memory_max_bytes"] is None
    assert result["memory_percent"] is None
    assert result["swap_max_bytes"] is None
    assert result["pids_max"] is None
