"""CLI adapter for the optional Soul-owned personality onboarding step."""

from __future__ import annotations

import logging
from pathlib import Path

from agent.plugin import active_plugin_service
from cyrene.config import DATA_DIR
from cyrene.localization import localized

logger = logging.getLogger(__name__)
_SETUP_FLAG: Path | None = None


def _l(en: str, zh: str, **values):
    return localized(en, zh, **values)


def init_setup_flag() -> None:
    global _SETUP_FLAG
    _SETUP_FLAG = DATA_DIR / ".setup_done"


def is_setup_done() -> bool:
    """A missing Soul contribution must never block core process startup."""

    if active_plugin_service("soul_onboarding") is None:
        return True
    return True if _SETUP_FLAG is None else _SETUP_FLAG.exists()


def mark_setup_done() -> None:
    global _SETUP_FLAG
    if _SETUP_FLAG is None:
        _SETUP_FLAG = DATA_DIR / ".setup_done"
    try:
        _SETUP_FLAG.parent.mkdir(parents=True, exist_ok=True)
        _SETUP_FLAG.write_text("setup_complete", encoding="utf-8")
    except OSError:
        logger.exception("Failed to mark optional personality setup complete")


async def run_setup() -> None:
    """Collect CLI input while Soul owns validation, generation, and writes."""

    onboarding = active_plugin_service("soul_onboarding")
    save = getattr(onboarding, "save", None)
    if not callable(save):
        return

    print()
    print("=" * 50)
    print(_l("Welcome to Cyrene!", "欢迎使用 Cyrene！"))
    print("=" * 50)
    print(_l("Choose a personality option:", "请选择人格选项："))
    print(_l("  1) Generate from a name", "  1) 根据名字生成"))
    print(_l("  2) Write a custom SOUL.md", "  2) 编写自定义 SOUL.md"))
    print(_l("  3) Use the default personality", "  3) 使用默认人格"))
    choice = input(_l("Choice (1/2/3): ", "选择 (1/2/3): ")).strip()
    if choice == "1":
        name = input(
            _l(
                "Enter a real or fictional person’s name: ",
                "请输入名字（可以是真实人物或虚构角色）: ",
            )
        ).strip()
        await save("name", name=name)
    elif choice == "2":
        print(
            _l(
                "Enter SOUL.md content, then END on its own line:",
                "请输入 SOUL.md 内容，最后单独输入 END：",
            )
        )
        lines: list[str] = []
        while True:
            line = input()
            if line.strip() == "END":
                break
            lines.append(line)
        await save("custom", content="\n".join(lines))
    else:
        await save("default")
    mark_setup_done()


__all__ = ["init_setup_flag", "is_setup_done", "mark_setup_done", "run_setup"]
