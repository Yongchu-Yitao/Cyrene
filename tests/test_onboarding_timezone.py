from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_ui_timezone_resolver_accepts_browser_iana_timezone():
    from cyrene.workbench.artifacts.presentation_runtime import _resolve_ui_tz

    shanghai = _resolve_ui_tz("Asia/Shanghai")
    new_york = _resolve_ui_tz("America/New_York")

    assert getattr(shanghai, "key", "") == "Asia/Shanghai"
    assert datetime(2026, 6, 18, tzinfo=shanghai).utcoffset().total_seconds() == 8 * 3600
    assert datetime(2026, 1, 18, tzinfo=new_york).utcoffset().total_seconds() == -5 * 3600
    assert datetime(2026, 6, 18, tzinfo=new_york).utcoffset().total_seconds() == -4 * 3600


def test_ui_bootstrap_passes_saved_supported_timezone_to_backend():
    source = (ROOT / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "platform" / "data-store.jsx").read_text(
        encoding="utf-8"
    )

    assert 'let tz = "Asia/Shanghai"' in source
    assert 'localStorage.getItem("cyrene-timezone")' in source
    assert "supportedTimezones.includes(storedTimezone)" in source
    assert 'fetch("/api/ui-data?tz=" + encodeURIComponent(tz))' in source
