"""Soul-owned personality onboarding and profile generation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.plugin import active_plugin_service
from cyrene.config import DATA_DIR
from cyrene.localization import app_language, localized


class SoulOnboardingError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def normalize_custom_soul_content(content: str, default_content: str) -> str:
    clean = str(content or "").strip()
    if not clean:
        return default_content
    if not clean.startswith("# "):
        clean = localized("# Custom Soul\n\n", "# 自定义人格\n\n") + clean
    return clean


class SoulOnboardingApplication:
    """Optional onboarding step exposed only while the Soul pack is operational."""

    def __init__(self, soul_application: Any) -> None:
        self.soul = soul_application

    @staticmethod
    def _setup_flag_path():
        return DATA_DIR / ".setup_done"

    @staticmethod
    def _state_path() -> Path:
        return DATA_DIR / "plugin_data" / "cyrene_soul" / "onboarding_state.json"

    def _load_state(self) -> dict[str, str]:
        try:
            raw = json.loads(self._state_path().read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raw = {}
        return {
            "completed_at": str(raw.get("completed_at") or "") if isinstance(raw, dict) else "",
            "source": str(raw.get("source") or "") if isinstance(raw, dict) else "",
            "mode": str(raw.get("mode") or "") if isinstance(raw, dict) else "",
            "label": str(raw.get("label") or "") if isinstance(raw, dict) else "",
        }

    def _save_state(self, state: dict[str, str]) -> None:
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _is_default(self, content: str) -> bool:
        normalized = content.strip()
        return normalized in {
            str(self.soul.default(language="en") or "").strip(),
            str(self.soul.default(language="zh") or "").strip(),
        }

    def status(self) -> dict[str, Any]:
        persisted = self._load_state()
        content = str(self.soul.read() or "")
        inferred = self._setup_flag_path().exists() or (
            bool(content.strip()) and not self._is_default(content)
        )
        configured = bool(persisted.get("completed_at")) or inferred
        mode = str(persisted.get("mode") or "")
        if not mode and configured:
            mode = "default" if self._is_default(content) else "custom"
        label = str(persisted.get("label") or "")
        if mode == "default":
            label = localized("Default persona", "默认人格")
        elif mode == "custom":
            label = localized("Custom SOUL.md", "自定义 SOUL.md")
        return {
            "configured": configured,
            "completedAt": str(persisted.get("completed_at") or ""),
            "mode": mode,
            "label": label,
            "isDefaultSoul": bool(content.strip()) and self._is_default(content),
            "path": str(self.soul.path()),
            "currentContent": content,
            "source": str(persisted.get("source") or ""),
            "pristine": not configured and (
                not content.strip() or self._is_default(content)
            ),
        }

    async def save(
        self,
        mode: str,
        *,
        name: str = "",
        content: str = "",
    ) -> dict[str, Any]:
        clean_mode = str(mode or "").strip().lower()
        if clean_mode == "default":
            soul_content = str(self.soul.default() or "")
            label = localized("Default persona", "默认人格")
        elif clean_mode == "custom":
            soul_content = normalize_custom_soul_content(
                content,
                str(self.soul.default() or ""),
            )
            label = localized("Custom SOUL.md", "自定义 SOUL.md")
        elif clean_mode == "name":
            clean_name = str(name or "").strip()
            if not clean_name:
                raise SoulOnboardingError("personality_name_required")
            soul_content = await self.create_profile_from_name(clean_name)
            label = clean_name
        else:
            raise SoulOnboardingError("personality_mode_unsupported")

        if clean_mode != "name":
            self.soul.write(soul_content)
        flag = self._setup_flag_path()
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("setup_complete", encoding="utf-8")

        self._save_state({
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "source": "wizard",
            "mode": clean_mode,
            "label": label,
        })
        from cyrene.runtime.onboarding import get_onboarding_status

        return {
            "ok": True,
            "soulContent": soul_content,
            "onboarding": get_onboarding_status(),
        }

    async def _generate_profile(
        self,
        name: str,
        bio: str = "",
        style: str = "",
        language: str = "",
    ) -> str:
        from cyrene.model_runtime.messages import assistant_text

        lang = app_language(language)
        references = ""
        if bio:
            references += f"\nBiographical reference:\n{bio[:2000]}\n"
        if style:
            references += f"\nSpeaking-style reference:\n{style[:2000]}\n"
        prompt = (
            f"Create a concise behavior profile for an AI personifying {name}."
            f"{references}\nInclude CORE IDENTITY, SPEECH PATTERNS, "
            "CONTRADICTIONS, FIXED MISTAKES, BEHAVIORAL LOOPS, and "
            "CLASSIC EXCHANGES. Write behavioral rules, not a biography. "
            + ("Write in Simplified Chinese." if lang == "zh" else "Write in English.")
        )
        try:
            gateway = active_plugin_service("model")
            if gateway is None:
                raise RuntimeError("Model Provider Plugins are not available")
            response = await gateway.complete(
                [
                    {
                        "role": "system",
                        "content": "Create concrete character behavior profiles.",
                    },
                    {"role": "user", "content": prompt},
                ],
                route="secondary",
                caller="personality_setup",
                session_id=f"personality:{name}",
            )
            result = assistant_text(response) or ""
            if len(result) > 100:
                return localized(
                    "# {name}'s Soul\n\n{result}",
                    "# {name} 的人格\n\n{result}",
                    language=lang,
                    name=name,
                    result=result,
                )
        except Exception:
            pass
        return localized(
            "# {name}'s Soul\n\n## CORE IDENTITY\n- I personify {name}.\n\n"
            "## SPEECH PATTERNS\n- Speaking style modeled after {name}.\n",
            "# {name} 的人格\n\n## 核心身份\n- 我以 {name} 为人格原型。\n\n"
            "## 说话方式\n- 说话风格以 {name} 为参考。\n",
            language=lang,
            name=name,
        )

    async def create_profile_from_name(self, name: str) -> str:
        web_search = active_plugin_service("web_search")
        search = getattr(web_search, "search", None)
        if not callable(search):
            raise RuntimeError("content Plugin web-search service is not available")
        language = app_language()
        bio = await search(
            localized(
                "{name} biography life background",
                "{name} 生平 人物经历 背景",
                language=language,
                name=name,
            ),
            detail="content",
            max_results=5,
        )
        style = await search(
            localized(
                "{name} speaking style quotes catchphrases mannerisms",
                "{name} 说话方式 语录 口头禅 名场面 梗",
                language=language,
                name=name,
            ),
            detail="content",
            max_results=5,
        )
        content = await self._generate_profile(name, bio, style, language)
        self.soul.write(content)
        return content


__all__ = [
    "SoulOnboardingApplication",
    "SoulOnboardingError",
    "normalize_custom_soul_content",
]
