"""Application lifecycle, routes, storage, and learning service for Skills."""

from __future__ import annotations

import sqlite3
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cyrene.plugins.context import PluginApplicationContext
from cyrene.localization import app_language, localized
from cyrene.plugins.builtin.cyrene_extensions.extension_plugin_center import register_plugin_center_routes
from cyrene.plugins.builtin.cyrene_extensions.extension_service import application_extension_service


_MIGRATION_MARKER = ".legacy-data-migrated-v1"


def _move_missing(source: Path, target: Path) -> None:
    if not source.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        try:
            source.replace(target)
        except OSError:
            shutil.move(str(source), str(target))
        return
    if not source.is_dir() or not target.is_dir():
        return
    for child in tuple(source.iterdir()):
        _move_missing(child, target / child.name)
    try:
        source.rmdir()
    except OSError:
        pass


def migrate_legacy_learning_data(legacy_root: Path, plugin_root: Path) -> None:
    """Move pre-Plugin Skill data once, preserving any newer destination."""

    plugin_root.mkdir(parents=True, exist_ok=True)
    marker = plugin_root / _MIGRATION_MARKER
    if marker.exists():
        return
    for name in (
        "behavior-learning.db",
        "behavior-learning.db-wal",
        "behavior-learning.db-shm",
        "behavior-media",
        "learned_skill_scripts",
        "installed_skills",
    ):
        _move_missing(legacy_root / name, plugin_root / name)
    _rewrite_migrated_database_paths(
        plugin_root / "behavior-learning.db",
        legacy_root,
        plugin_root,
    )
    _rewrite_installed_skill_paths(legacy_root, plugin_root)
    marker.write_text("migrated\n", encoding="utf-8")


def _rewrite_migrated_database_paths(
    database: Path,
    legacy_root: Path,
    plugin_root: Path,
) -> None:
    """Update absolute generated-script paths stored by the legacy runtime."""

    if not database.is_file():
        return
    old_prefix = str(legacy_root.expanduser().resolve())
    new_prefix = str(plugin_root.expanduser().resolve())
    if old_prefix == new_prefix:
        return
    with sqlite3.connect(database) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        for (table_name,) in tables:
            if not str(table_name).replace("_", "").isalnum():
                continue
            columns = connection.execute(
                f'PRAGMA table_info("{table_name}")'
            ).fetchall()
            for column in columns:
                column_name = str(column[1])
                column_type = str(column[2] or "").upper()
                if "TEXT" not in column_type:
                    continue
                connection.execute(
                    f'UPDATE "{table_name}" '
                    f'SET "{column_name}" = REPLACE("{column_name}", ?, ?) '
                    f'WHERE INSTR("{column_name}", ?) > 0',
                    (old_prefix, new_prefix, old_prefix),
                )
        connection.commit()


def _rewrite_installed_skill_paths(
    legacy_root: Path,
    plugin_root: Path,
) -> None:
    """Persist the new Plugin-owned path into installed Skill settings."""

    from .skills import (
        save_skill_settings_records,
        skill_settings_records,
    )

    legacy_skills = (legacy_root / "installed_skills").expanduser().resolve()
    plugin_skills = (plugin_root / "installed_skills").expanduser().resolve()
    records = skill_settings_records()
    changed = False
    for record in records:
        raw_path = str(record.get("stored_path") or "").strip()
        if not raw_path:
            continue
        path = Path(raw_path).expanduser()
        try:
            relative = path.resolve(strict=False).relative_to(legacy_skills)
        except (OSError, ValueError):
            continue
        record["stored_path"] = str(plugin_skills / relative)
        changed = True
    if changed:
        save_skill_settings_records(records)


@dataclass(slots=True)
class SkillsApplication:
    data_directory: Path
    legacy_data_directory: Path
    workspace: Path
    bot: Any
    db_path: str

    async def startup(self) -> None:
        from . import orchestrator, skills

        migrate_legacy_learning_data(
            self.legacy_data_directory,
            self.data_directory,
        )
        skills.configure_skills_storage(self.data_directory / "installed_skills")
        await orchestrator.init(self.data_directory, self.workspace)

    async def shutdown(self) -> None:
        from . import orchestrator, skills

        await orchestrator.shutdown()
        skills.configure_skills_storage(None)

    async def reset_data(self) -> None:
        """Recreate empty Plugin state after the application data reset."""

        await self.shutdown()
        if self.data_directory.exists():
            shutil.rmtree(self.data_directory)
        await self.startup()

    async def tick(self) -> None:
        from .orchestrator import tick

        await tick(self.bot, self.db_path)

    async def record_browser_user_event(self, **event: Any) -> None:
        from .orchestrator import record_browser_user_event

        await record_browser_user_event(**event)

    async def list_recent_browser_user_events(
        self,
        *,
        session_id: str,
        round_id: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        from .orchestrator import list_recent_browser_user_events

        return await list_recent_browser_user_events(
            session_id=session_id,
            round_id=round_id,
            limit=limit,
        )

    def catalog(self) -> list[dict[str, Any]]:
        from .skills import build_skills

        return build_skills()

    def load_skill(self, skill_id: str) -> dict[str, Any] | None:
        from .skills import load_skill

        return load_skill(skill_id)

    def read_skill_resource(self, skill_id: str, resource_path: str) -> dict[str, Any]:
        from .skills import read_skill_resource

        return read_skill_resource(
            skill_id,
            resource_path,
            language=app_language(),
        )

    def install_skill(
        self,
        source_path: str | Path,
        *,
        source_metadata: dict[str, Any] | None = None,
        replace_id: str = "",
    ) -> dict[str, Any]:
        from .skills import install_skill_from_path

        return install_skill_from_path(
            Path(source_path).expanduser(),
            source_metadata=source_metadata,
            replace_id=replace_id,
            language=app_language(),
        )

    def uninstall_skill(self, skill_id: str) -> bool:
        from .skills import uninstall_skill

        return uninstall_skill(skill_id)

    def set_skill_enabled(self, skill_id: str, enabled: bool) -> bool:
        from .skills import set_skill_enabled

        return set_skill_enabled(skill_id, enabled)

    def inspect_skill_directory(self, directory: str | Path) -> dict[str, Any]:
        from .skills import (
            extract_skill_summary,
            validate_skill_directory,
        )

        root = Path(directory).expanduser()
        error = validate_skill_directory(root, language=app_language())
        if error:
            return {
                "ok": False,
                "code": "invalid_skill_source",
                "error": error,
            }
        entrypoint = root / "SKILL.md"
        if not entrypoint.is_file():
            matches = sorted(root.rglob("SKILL.md"))
            if not matches:
                return {
                    "ok": False,
                    "code": "skill_entrypoint_missing",
                    "error": localized(
                        "The skill directory must contain SKILL.md.",
                        "技能目录必须包含 SKILL.md。",
                    ),
                }
            entrypoint = matches[0]
        name, description, text = extract_skill_summary(entrypoint)
        return {
            "ok": True,
            "name": name,
            "description": description,
            "text": text,
            "entrypoint": str(entrypoint),
        }

    def storage_paths(self) -> dict[str, tuple[Path, ...]]:
        return {
            "skills": (
                self.data_directory / "installed_skills",
                self.data_directory / "learned_skill_scripts",
            ),
            "attachments": (self.data_directory / "behavior-media",),
        }

    def backup_sources(self) -> dict[str, tuple[tuple[Path, str], ...]]:
        archive_root = "data/plugin_data/cyrene_skills"
        return {
            "files": (
                (
                    self.data_directory / "behavior-learning.db",
                    f"{archive_root}/behavior-learning.db",
                ),
            ),
            "directories": (
                (
                    self.data_directory / "installed_skills",
                    f"{archive_root}/installed_skills",
                ),
                (
                    self.data_directory / "learned_skill_scripts",
                    f"{archive_root}/learned_skill_scripts",
                ),
                (
                    self.data_directory / "behavior-media",
                    f"{archive_root}/behavior-media",
                ),
            ),
        }


def setup_plugin_center(context: PluginApplicationContext) -> None:
    service = application_extension_service(context)
    if service is None:
        return
    register_plugin_center_routes(
        context.router,
        kind="skill",
        owner_pack="cyrene_skills",
        service=service,
    )


def setup_application(context: PluginApplicationContext) -> None:
    from cyrene.config import WORKSPACE_DIR
    from .application_service import (
        LearningApplicationService,
        MediaRepository,
        ProjectResolver,
        ToolChainProjection,
    )
    from cyrene.workbench.sessions.context import resolve_workbench_project_id
    from cyrene.workbench.artifacts.presentation_runtime import build_status
    from .routes import register_learning_routes

    data_root = (
        context.data_directory / "plugin_data" / "cyrene_skills"
    ).expanduser().resolve()
    service = SkillsApplication(
        data_directory=data_root,
        legacy_data_directory=context.data_directory.expanduser().resolve(),
        workspace=Path(WORKSPACE_DIR).expanduser().resolve(),
        bot=context.bot,
        db_path=context.db_path,
    )
    media = MediaRepository(data_root)
    register_learning_routes(
        context.router,
        LearningApplicationService(
            ProjectResolver(resolve_workbench_project_id),
            media,
            ToolChainProjection(media),
            build_status,
        ),
    )
    setup_plugin_center(context)
    context.provide("skills", service)
    context.expose_frontend("skills")
    from cyrene.platform.settings_service import (
        PluginSettingsContribution,
        SettingControlSpec,
        plugin_setting_spec,
    )

    context.provide(
        "skills_settings",
        PluginSettingsContribution(
            specs=(
                plugin_setting_spec(
                    "background_skill_learning", "boolean", True, tab="agents"
                ),
            ),
            controls=(
                SettingControlSpec("skills.installed", "agents", "existing_capability", "cyrene_skills", "R2"),
                SettingControlSpec("skills.install_picker", "agents", "user_ceremony", "cyrene.file_picker", "R2"),
            ),
        ),
    )
    context.on_startup(service.startup)
    context.on_shutdown(service.shutdown)


__all__ = [
    "SkillsApplication",
    "migrate_legacy_learning_data",
    "setup_application",
    "setup_plugin_center",
]
