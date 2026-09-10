import json

from cyrene.platform.startup_progress import report_startup_progress


def test_progress_is_optional_and_replaces_complete_snapshot(tmp_path, monkeypatch):
    monkeypatch.delenv("CYRENE_STARTUP_PROGRESS_PATH", raising=False)
    report_startup_progress("backend_plugins", 1, 3)
    assert not list(tmp_path.iterdir())
    path = tmp_path / "progress.json"
    monkeypatch.setenv("CYRENE_STARTUP_PROGRESS_PATH", str(path))
    report_startup_progress("backend_plugins", 1, 3)
    assert json.loads(path.read_text()) == {
        "stage": "backend_plugins", "completed": 1, "total": 3,
    }
    report_startup_progress("backend_services")
    assert json.loads(path.read_text()) == {
        "stage": "backend_services", "completed": 0, "total": 0,
    }
    assert list(tmp_path.iterdir()) == [path]


def test_progress_failure_cannot_fail_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("CYRENE_STARTUP_PROGRESS_PATH", str(tmp_path / "missing" / "progress.json"))
    report_startup_progress("backend_plugins", 0, 3)


def test_plugin_progress_counts_only_loadable_entries_including_failures(tmp_path, monkeypatch):
    from cyrene.core.plugin.registry import PluginRegistry
    import cyrene.core.startup_progress as progress

    # Invalid contributions must still be reported and counted as processed;
    # progress reporting must not swallow their existing failure semantics.
    (tmp_path / "one.py").write_text("plugin = None\n")
    (tmp_path / "two.py").write_text("plugin = None\n")
    (tmp_path / "ignored.txt").write_text("ignored")
    (tmp_path / "_private.py").write_text("raise RuntimeError('not loaded')")
    (tmp_path / "empty").mkdir()
    observed = []
    monkeypatch.setattr(progress, "report_startup_progress", lambda *args: observed.append(args))
    failures = PluginRegistry().load_directory(tmp_path)
    assert len(failures) == 2
    assert observed == [
        ("backend_plugins", 0, 2),
        ("backend_plugins", 1, 2),
        ("backend_plugins", 2, 2),
    ]
