from __future__ import annotations

from agent.plugin.plugin_impl.cyrene_browser import runtime as browser
from cyrene import localization
from cyrene.runtime import settings_store
from cyrene.workbench import notifications


def test_effective_language_precedence(monkeypatch) -> None:
    monkeypatch.setattr(settings_store, "get", lambda key, default="": "zh")
    monkeypatch.setattr(localization, "system_language", lambda: "en")

    assert localization.app_language("en-US") == "en"
    assert localization.app_language() == "zh"

    monkeypatch.setattr(settings_store, "get", lambda key, default="": "")
    assert localization.app_language() == "en"


def test_browser_language_uses_explicit_override_then_app_language(
    monkeypatch,
) -> None:
    overrides = {
        "CYRENE_BROWSER_LOCALE": "ja-JP",
        "CYRENE_BROWSER_ACCEPT_LANGUAGE": "ja-JP,ja;q=0.9",
    }
    monkeypatch.setattr(
        browser,
        "_cfg",
        lambda key, default: overrides.get(key, default),
    )
    assert browser._browser_locale() == "ja-JP"
    assert browser._browser_accept_language() == "ja-JP,ja;q=0.9"

    overrides.clear()
    monkeypatch.setattr(browser, "locale_tag", lambda: "en-US")
    monkeypatch.setattr(
        browser,
        "accept_language",
        lambda: "en-US,en;q=0.9,zh-CN;q=0.6,zh;q=0.5",
    )
    assert browser._browser_locale() == "en-US"
    assert browser._browser_accept_language().startswith("en-US")


def test_notification_records_the_language_used_to_render_it(
    tmp_path,
) -> None:
    notifications.configure_store(str(tmp_path / "workbench.sqlite3"))
    item = notifications.append_notification(
        title="Ready",
        body="Done",
        language="en-US",
    )

    assert item["language"] == "en"
    assert notifications.list_notifications()["items"][0]["language"] == "en"


def test_localized_interpolation_is_consistent(monkeypatch) -> None:
    monkeypatch.setattr(settings_store, "get", lambda key, default="": "en")
    assert localization.localized(
        "Created {count} tasks.",
        "已创建 {count} 个任务。",
        count=2,
    ) == "Created 2 tasks."

    monkeypatch.setattr(settings_store, "get", lambda key, default="": "zh")
    assert localization.localized(
        "Created {count} tasks.",
        "已创建 {count} 个任务。",
        count=2,
    ) == "已创建 2 个任务。"


def test_localized_plural_selects_english_grammar(monkeypatch) -> None:
    monkeypatch.setattr(settings_store, "get", lambda key, default="": "en")
    assert localization.localized_plural(
        "Created {count} task.",
        "Created {count} tasks.",
        "已创建 {count} 个任务。",
        count=1,
    ) == "Created 1 task."
    assert localization.localized_plural(
        "Created {count} task.",
        "Created {count} tasks.",
        "已创建 {count} 个任务。",
        count=2,
    ) == "Created 2 tasks."
