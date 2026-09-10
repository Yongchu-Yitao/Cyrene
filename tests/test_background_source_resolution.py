from pathlib import Path
from types import SimpleNamespace

from cyrene.plugins.background import BackgroundPluginHost


def application(sources, failures=()):
    plugins = [SimpleNamespace(
        source=str(source), pack_id=None,
        plugin=SimpleNamespace(
            name=f"job_{i}", kind="tool", model_visible=False, handler=lambda: None,
            metadata={"background_job": {"interval_seconds": 30}},
        ),
    ) for i, source in enumerate(sources)]
    return SimpleNamespace(
        started=True, load_failures=[SimpleNamespace(path=p) for p in failures],
        registry=SimpleNamespace(list_plugins=lambda: plugins, plugin_enabled=lambda _: True),
    )


def test_healthy_reconciliation_does_not_touch_source_filesystem(monkeypatch):
    host = object.__new__(BackgroundPluginHost)
    app = application(["/tools/shared.py"] * 100)
    def unexpected(*args, **kwargs):
        raise AssertionError("Healthy plugin sources need no filesystem lookup")
    monkeypatch.setattr(Path, "resolve", unexpected)
    assert len(host._desired_bindings(app, refresh=False)) == 100


def test_failed_source_filter_tracks_symlinks_across_reconciliations(tmp_path, monkeypatch):
    failed = tmp_path / "failed.py"
    healthy = tmp_path / "healthy.py"
    failed.touch()
    healthy.touch()
    link = tmp_path / "current.py"
    link.symlink_to(failed)
    app = application([link, link, healthy], [failed])
    host = object.__new__(BackgroundPluginHost)
    original = Path.resolve
    calls = []
    def resolve(path, *args, **kwargs):
        calls.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", resolve)
    assert len(host._desired_bindings(app, refresh=False)) == 1
    assert calls.count(link) == 1
    link.unlink()
    link.symlink_to(healthy)
    assert len(host._desired_bindings(app, refresh=False)) == 3
