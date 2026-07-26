from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_onboarding_timezone_row_uses_wrapping_long_value_layout():
    source = (ROOT / "src" / "webui" / "frontend" / "workbench-welcome.jsx").read_text(
        encoding="utf-8"
    )
    styles = (ROOT / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    assert 'row.id === "timezone" ? " is-long-value" : ""' in source
    assert "title={row.value}" in source
    assert ".wb-wel-pref.is-long-value" in styles
    assert "grid-template-rows: auto auto;" in styles
    assert "overflow-wrap: anywhere;" in styles


def test_ui_timezone_resolver_accepts_browser_iana_timezone():
    from cyrene.workbench.runtime import _resolve_ui_tz

    shanghai = _resolve_ui_tz("Asia/Shanghai")
    new_york = _resolve_ui_tz("America/New_York")

    assert getattr(shanghai, "key", "") == "Asia/Shanghai"
    assert datetime(2026, 6, 18, tzinfo=shanghai).utcoffset().total_seconds() == 8 * 3600
    assert datetime(2026, 1, 18, tzinfo=new_york).utcoffset().total_seconds() == -5 * 3600
    assert datetime(2026, 6, 18, tzinfo=new_york).utcoffset().total_seconds() == -4 * 3600


def test_ui_bootstrap_passes_browser_timezone_to_backend():
    source = (ROOT / "src" / "webui" / "frontend" / "platform" / "data-store.jsx").read_text(
        encoding="utf-8"
    )

    assert "Intl.DateTimeFormat().resolvedOptions().timeZone" in source
    assert 'fetch("/api/ui-data?tz=" + encodeURIComponent(tz))' in source
