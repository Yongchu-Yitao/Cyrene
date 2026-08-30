"""Installed external skill storage and prompt helpers owned by the Skills pack."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import zipfile
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cyrene.config import TEMP_DIR
from cyrene.localization import localized
from cyrene.platform.settings_store import get as get_setting, set_ as set_setting

_SKILLS_DIR: Path | None = None
_ALLOWED_SKILL_EXTENSIONS = {".md", ".txt", ".prompt", ".json", ".yaml", ".yml"}
_ALLOWED_ARCHIVE_EXTENSIONS = {".zip"}
_MAX_SKILL_FILE_BYTES = 256 * 1024
_MAX_SKILL_ARCHIVE_BYTES = 8 * 1024 * 1024
_MAX_SKILL_ARCHIVE_ENTRIES = 200
_MAX_SKILL_TREE_BYTES = 32 * 1024 * 1024


def _skill_message(
    en: str,
    zh: str,
    *,
    language: Any = None,
    **values: Any,
) -> str:
    return localized(en, zh, language=language or "en", **values)


def _is_probably_text(raw: bytes) -> bool:
    if not raw:
        return True
    if b"\x00" in raw:
        return False
    sample = raw[:4096]
    printable = 0
    for byte in sample:
        # ASCII control chars (excluding whitespace) or DEL
        if byte < 32 and byte not in (9, 10, 13) or byte == 127:
            continue
        printable += 1
    return (printable / max(1, len(sample))) >= 0.85


def validate_skill_file(
    source_path: Path,
    *,
    language: Any = None,
) -> str | None:
    suffix = source_path.suffix.lower()
    if suffix not in _ALLOWED_SKILL_EXTENSIONS:
        allowed = ", ".join(sorted(_ALLOWED_SKILL_EXTENSIONS))
        return _skill_message(
            "Unsupported skill file type: {suffix}; allowed: {allowed}",
            "不支持的技能文件类型：{suffix}；允许的类型：{allowed}",
            language=language,
            suffix=suffix or "(none)",
            allowed=allowed,
        )
    try:
        stat = source_path.stat()
    except OSError:
        return _skill_message(
            "Unable to read skill file metadata.",
            "无法读取技能文件元数据。",
            language=language,
        )
    if stat.st_size > _MAX_SKILL_FILE_BYTES:
        return _skill_message(
            "The skill file is too large; maximum {limit} KB.",
            "技能文件过大；最大允许 {limit} KB。",
            language=language,
            limit=_MAX_SKILL_FILE_BYTES // 1024,
        )
    try:
        raw = source_path.read_bytes()[:4096]
    except OSError:
        return _skill_message(
            "Unable to read the skill file.",
            "无法读取技能文件。",
            language=language,
        )
    if not _is_probably_text(raw):
        return _skill_message(
            "The skill file must be plain text.",
            "技能文件必须是纯文本。",
            language=language,
        )
    return None


def _find_skill_entrypoint(root: Path) -> Path | None:
    direct = root / "SKILL.md"
    if direct.exists() and direct.is_file():
        return direct
    matches = sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.name.lower() == "skill.md"
    )
    if not matches:
        return None
    return min(matches, key=lambda path: (len(path.relative_to(root).parts), str(path).lower()))


def validate_skill_directory(
    source_path: Path,
    *,
    language: Any = None,
) -> str | None:
    if not source_path.exists() or not source_path.is_dir():
        return _skill_message(
            "The skill directory does not exist.",
            "技能目录不存在。",
            language=language,
        )
    entrypoint = _find_skill_entrypoint(source_path)
    if entrypoint is None:
        return _skill_message(
            "The skill directory must contain SKILL.md.",
            "技能目录必须包含 SKILL.md。",
            language=language,
        )
    root = entrypoint.parent.resolve()
    total_size = 0
    try:
        for child in root.rglob("*"):
            if child.is_symlink():
                return _skill_message(
                    "The skill directory contains a symbolic link: {path}",
                    "技能目录包含符号链接：{path}",
                    language=language,
                    path=child.relative_to(root),
                )
            if not child.is_file():
                continue
            resolved = child.resolve()
            if resolved != root and root not in resolved.parents:
                return _skill_message(
                    "The skill directory contains a path outside the skill root.",
                    "技能目录包含技能根目录之外的路径。",
                    language=language,
                )
            total_size += child.stat().st_size
            if total_size > _MAX_SKILL_TREE_BYTES:
                return _skill_message(
                    "The skill directory is too large; maximum {limit} MB.",
                    "技能目录过大；最大允许 {limit} MB。",
                    language=language,
                    limit=_MAX_SKILL_TREE_BYTES // (1024 * 1024),
                )
    except OSError:
        return _skill_message(
            "Unable to inspect the skill directory.",
            "无法检查技能目录。",
            language=language,
        )
    return validate_skill_file(entrypoint, language=language)


def validate_skill_archive(
    source_path: Path,
    *,
    language: Any = None,
) -> str | None:
    if source_path.suffix.lower() not in _ALLOWED_ARCHIVE_EXTENSIONS:
        allowed = ", ".join(sorted(_ALLOWED_ARCHIVE_EXTENSIONS))
        return _skill_message(
            "Unsupported archive type: {suffix}; allowed: {allowed}",
            "不支持的压缩包类型：{suffix}；允许的类型：{allowed}",
            language=language,
            suffix=source_path.suffix.lower() or "(none)",
            allowed=allowed,
        )
    try:
        stat = source_path.stat()
    except OSError:
        return _skill_message(
            "Unable to read skill archive metadata.",
            "无法读取技能压缩包元数据。",
            language=language,
        )
    if stat.st_size > _MAX_SKILL_ARCHIVE_BYTES:
        return _skill_message(
            "The skill archive is too large; maximum {limit} MB.",
            "技能压缩包过大；最大允许 {limit} MB。",
            language=language,
            limit=_MAX_SKILL_ARCHIVE_BYTES // (1024 * 1024),
        )
    try:
        with zipfile.ZipFile(source_path) as zf:
            infos = zf.infolist()
            if len(infos) > _MAX_SKILL_ARCHIVE_ENTRIES:
                return _skill_message(
                    "The skill archive has too many files; maximum {limit}.",
                    "技能压缩包文件过多；最大允许 {limit} 个。",
                    language=language,
                    limit=_MAX_SKILL_ARCHIVE_ENTRIES,
                )
            has_skill_md = False
            total_uncompressed = 0
            for info in infos:
                parts = Path(info.filename).parts
                if info.is_dir():
                    continue
                if any(part == ".." for part in parts) or Path(info.filename).is_absolute():
                    return _skill_message(
                        "The skill archive contains unsafe paths.",
                        "技能压缩包包含不安全路径。",
                        language=language,
                    )
                # Unix zip symlinks can escape after extraction even when the
                # stored filename itself is relative.
                if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                    return _skill_message(
                        "The skill archive contains symbolic links.",
                        "技能压缩包包含符号链接。",
                        language=language,
                    )
                if info.file_size > _MAX_SKILL_TREE_BYTES:
                    return _skill_message(
                        "The skill archive contains an oversized file.",
                        "技能压缩包包含过大的文件。",
                        language=language,
                    )
                total_uncompressed += info.file_size
                if total_uncompressed > _MAX_SKILL_TREE_BYTES:
                    return _skill_message(
                        "The skill archive expands beyond {limit} MB.",
                        "技能压缩包解压后超过 {limit} MB。",
                        language=language,
                        limit=_MAX_SKILL_TREE_BYTES // (1024 * 1024),
                    )
                if Path(info.filename).name.lower() == "skill.md":
                    has_skill_md = True
            if not has_skill_md:
                return _skill_message(
                    "The skill archive must contain SKILL.md.",
                    "技能压缩包必须包含 SKILL.md。",
                    language=language,
                )
    except zipfile.BadZipFile:
        return _skill_message(
            "The ZIP archive is invalid.",
            "ZIP 压缩包无效。",
            language=language,
        )
    except OSError:
        return _skill_message(
            "Unable to read the skill archive.",
            "无法读取技能压缩包。",
            language=language,
        )
    return None


def skills_storage_dir() -> Path:
    if _SKILLS_DIR is None:
        raise RuntimeError("cyrene_skills storage is not initialized")
    _SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    return _SKILLS_DIR


def configure_skills_storage(directory: str | Path | None) -> None:
    """Bind installed Skill storage to the active Plugin data directory."""

    global _SKILLS_DIR
    _SKILLS_DIR = (
        Path(directory).expanduser().resolve()
        if directory is not None
        else None
    )


def skill_settings_records() -> list[dict[str, Any]]:
    raw = get_setting("installed_skills", [])
    return raw if isinstance(raw, list) else []


def save_skill_settings_records(records: list[dict[str, Any]]) -> None:
    set_setting("installed_skills", records)


def slugify_skill_id(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return "skill"
    # Replace runs of non-word characters (whitespace, punctuation, separators)
    # with a single dash. \w with re.UNICODE preserves CJK and other Unicode letters.
    slug = re.sub(r"[^\w]+", "-", value, flags=re.UNICODE).strip("-")
    # For pure ASCII, lowercase as standard; for mixed content (e.g. Chinese + English),
    # keep original case since ASCII part is short enough to be readable.
    if all(ord(c) < 128 for c in slug):
        slug = slug.lower()
    return slug or "skill"


def unique_skill_id(base_id: str, records: list[dict[str, Any]]) -> str:
    existing = {str(record.get("id") or "").strip() for record in records}
    if base_id not in existing:
        return base_id
    suffix = 2
    while f"{base_id}-{suffix}" in existing:
        suffix += 1
    return f"{base_id}-{suffix}"


def read_skill_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _skill_entrypoint(stored_path: Path) -> Path | None:
    if stored_path.is_file():
        return stored_path
    if stored_path.is_dir():
        return _find_skill_entrypoint(stored_path)
    return None


def _resolve_stored_skill_path(path_value: Any) -> Path:
    """Resolve the canonical Plugin-owned path persisted in settings."""

    return Path(str(path_value or "").strip()).expanduser().resolve(strict=False)


def _parse_frontmatter_field(text: str, field: str) -> str | None:
    """Extract a simple `field: value` from YAML frontmatter (---...---) at the start of text."""
    stripped = text.lstrip("﻿")
    if not stripped.startswith("---"):
        return None
    end = stripped.find("---", 3)
    if end == -1:
        return None
    block = stripped[3:end]
    for line in block.splitlines():
        line_stripped = line.strip()
        if line_stripped.startswith(f"{field}:"):
            val = line_stripped[len(field) + 1:].strip().strip('"').strip("'")
            if val:
                return val
    return None


def extract_skill_summary(path: Path) -> tuple[str, str, str]:
    text = read_skill_text(path)
    fm_name = _parse_frontmatter_field(text, "name")
    fm_desc = _parse_frontmatter_field(text, "description")
    if fm_name:
        name = fm_name
    else:
        lines = [line.rstrip() for line in text.splitlines()]
        name = path.parent.name if path.stem.lower() == "skill" else path.stem
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                name = stripped.lstrip("#").strip() or name
                break
    desc = fm_desc or ""
    if not desc:
        lines = [line.rstrip() for line in text.splitlines()]
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped == "---":
                continue
            if stripped.startswith("#"):
                continue
            desc = stripped
            break
    if not desc:
        desc = "External skill file"
    return name, desc, text


def _skill_content_hash(root: Path) -> str:
    """Return a deterministic hash for one immutable installed Skill tree."""
    digest = hashlib.sha256()
    if root.is_file():
        digest.update(root.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(root.read_bytes())
        return digest.hexdigest()
    for child in sorted(path for path in root.rglob("*") if path.is_file()):
        if child.is_symlink():
            raise ValueError(f"skill contains a symbolic link: {child.relative_to(root)}")
        relative = child.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(child.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _copy_skill_tree(source: Path, destination: Path) -> None:
    """Copy a Skill without following links or retaining a surrounding repo."""
    if source.is_file():
        shutil.copy2(source, destination)
        return
    destination.mkdir(parents=True, exist_ok=False)
    for child in sorted(source.rglob("*")):
        if child.is_symlink():
            raise ValueError(f"skill contains a symbolic link: {child.relative_to(source)}")
        relative = child.relative_to(source)
        target = destination / relative
        if child.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif child.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(child, target)


def skill_payload_from_record(record: dict[str, Any]) -> dict[str, Any] | None:
    stored_path = _resolve_stored_skill_path(record.get("stored_path"))
    if not stored_path.exists():
        return None
    entrypoint = _skill_entrypoint(stored_path)
    if entrypoint is None:
        return None
    name, desc, preview = extract_skill_summary(entrypoint)
    stat = entrypoint.stat()
    files: list[dict[str, Any]] = []
    total_size = 0
    if stored_path.is_dir():
        for child in sorted(stored_path.rglob("*")):
            if child.is_file():
                rel = str(child.relative_to(stored_path))
                fs = child.stat().st_size
                files.append({"path": rel, "name": child.name, "size": fs})
                total_size += fs
    else:
        files.append({"path": stored_path.name, "name": stored_path.name, "size": stat.st_size})
        total_size = stat.st_size
    return {
        "id": str(record.get("id") or ""),
        "name": name,
        "desc": desc,
        "enabled": bool(record.get("enabled", True)),
        "installed": True,
        "installed_at": str(record.get("installed_at") or ""),
        "updated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "size_bytes": total_size,
        "source_path": str(record.get("source_path") or ""),
        "stored_path": str(stored_path),
        "entrypoint_path": str(entrypoint),
        "file_name": stored_path.name,
        "entrypoint_name": entrypoint.name,
        "source_kind": str(record.get("source_kind") or ("directory" if stored_path.is_dir() else "file")),
        "files": files,
        "preview": preview,
        "tags": ["external"],
        "version": "external",
        "author": "user",
        "agent_visible": bool(record.get("enabled", True)),
        "content_hash": str(record.get("content_hash") or _skill_content_hash(stored_path)),
        "source_url": str(record.get("source_url") or ""),
        "source_commit": str(record.get("source_commit") or ""),
        "source_subdir": str(record.get("source_subdir") or ""),
    }


def build_skills() -> list[dict[str, Any]]:
    skills: list[dict[str, Any]] = []
    for record in skill_settings_records():
        payload = skill_payload_from_record(record)
        if payload is not None:
            skills.append(payload)
    skills.sort(key=lambda item: (item.get("name") or "").lower())
    return skills


def install_skill_from_path(
    source_path: Path,
    *,
    source_metadata: dict[str, Any] | None = None,
    replace_id: str = "",
    language: Any = None,
) -> dict[str, Any]:
    if not source_path.exists():
        return {
            "ok": False,
            "code": "invalid_skill_source",
            "error": _skill_message(
                "The skill source path is invalid.",
                "技能来源路径无效。",
                language=language,
            ),
        }
    records = skill_settings_records()
    source_resolved = str(source_path.resolve())
    for record in records:
        if not replace_id and str(record.get("source_path") or "").strip() == source_resolved:
            return {"ok": True, "skill": skill_payload_from_record(record), "already_installed": True}

    source_kind = "file"
    source_suffix = source_path.suffix
    if source_path.is_dir():
        validation_error = validate_skill_directory(
            source_path,
            language=language,
        )
        source_kind = "directory"
        source_suffix = ""
    elif source_path.is_file() and source_path.suffix.lower() in _ALLOWED_ARCHIVE_EXTENSIONS:
        validation_error = validate_skill_archive(
            source_path,
            language=language,
        )
        source_kind = "archive"
        source_suffix = ""
    elif source_path.is_file():
        validation_error = validate_skill_file(source_path, language=language)
    else:
        return {
            "ok": False,
            "code": "invalid_skill_source",
            "error": _skill_message(
                "The skill source path is invalid.",
                "技能来源路径无效。",
                language=language,
            ),
        }
    if validation_error:
        return {
            "ok": False,
            "code": "invalid_skill_source",
            "error": validation_error,
        }

    base_name = source_path.name
    copy_source = source_path
    if source_kind == "directory":
        entrypoint = _find_skill_entrypoint(source_path)
        if entrypoint is None:
            return {
                "ok": False,
                "code": "skill_entrypoint_missing",
                "error": _skill_message(
                    "The skill directory must contain SKILL.md.",
                    "技能目录必须包含 SKILL.md。",
                    language=language,
                ),
            }
        # A repository may contain many Skills. One installed Skill owns only
        # the subtree rooted next to its selected SKILL.md.
        copy_source = entrypoint.parent
        base_name = copy_source.name
    elif source_kind == "archive":
        base_name = source_path.stem
    else:
        base_name = source_path.stem
    base_id = slugify_skill_id(base_name)
    skill_id = replace_id.strip() or unique_skill_id(base_id, records)
    if replace_id and not any(str(record.get("id") or "") == replace_id for record in records):
        return {
            "ok": False,
            "code": "skill_not_found",
            "error": _skill_message(
                "Skill not found: {skill_id}",
                "未找到技能：{skill_id}",
                language=language,
                skill_id=replace_id,
            ),
        }
    dest = skills_storage_dir() / (f"{skill_id}{source_suffix}" if source_kind == "file" else skill_id)

    if source_kind != "archive":
        content_hash = _skill_content_hash(copy_source)
        for record in records:
            payload = skill_payload_from_record(record)
            if payload and payload.get("content_hash") == content_hash and str(record.get("id") or "") != replace_id:
                return {"ok": True, "skill": payload, "already_installed": True, "duplicate_content": True}

    old_path: Path | None = None
    if replace_id:
        existing = next(record for record in records if str(record.get("id") or "") == replace_id)
        old_path = _resolve_stored_skill_path(existing.get("stored_path"))
        records = [record for record in records if str(record.get("id") or "") != replace_id]
    elif dest.exists():
        return {
            "ok": False,
            "code": "skill_storage_collision",
            "error": _skill_message(
                "Skill storage already contains {skill_id}.",
                "技能存储中已存在 {skill_id}。",
                language=language,
                skill_id=skill_id,
            ),
        }

    # Build the complete immutable snapshot beside the destination.  An
    # existing snapshot is kept until the replacement and settings update have
    # both succeeded, so a failed import never destroys a working Skill.
    staging_parent = Path(tempfile.mkdtemp(prefix=f".{skill_id}-", dir=skills_storage_dir()))
    staged_dest = staging_parent / dest.name
    previous_dest = staging_parent / "previous"
    previous_saved = False
    try:
        if source_kind in {"file", "directory"}:
            _copy_skill_tree(copy_source, staged_dest)
        else:
            TEMP_DIR.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="cyrene-skill-", dir=TEMP_DIR) as tmp_dir:
                tmp_root = Path(tmp_dir)
                with zipfile.ZipFile(source_path) as zf:
                    zf.extractall(tmp_root)
                extracted_root = tmp_root
                children = [child for child in tmp_root.iterdir()]
                if len(children) == 1 and children[0].is_dir():
                    extracted_root = children[0]
                archive_entrypoint = _find_skill_entrypoint(extracted_root)
                if archive_entrypoint is None:
                    return {
                        "ok": False,
                        "code": "skill_entrypoint_missing",
                        "error": _skill_message(
                            "The skill archive must contain SKILL.md.",
                            "技能压缩包必须包含 SKILL.md。",
                            language=language,
                        ),
                    }
                extracted_root = archive_entrypoint.parent
                validation_error = validate_skill_directory(
                    extracted_root,
                    language=language,
                )
                if validation_error:
                    return {
                        "ok": False,
                        "code": "invalid_skill_source",
                        "error": validation_error,
                    }
                _copy_skill_tree(extracted_root, staged_dest)

        entrypoint = _skill_entrypoint(staged_dest)
        if entrypoint is None:
            return {
                "ok": False,
                "code": "skill_entrypoint_missing",
                "error": _skill_message(
                    "The installed skill is missing SKILL.md.",
                    "已安装的技能缺少 SKILL.md。",
                    language=language,
                ),
            }
        name, desc, _preview = extract_skill_summary(entrypoint)
        content_hash = _skill_content_hash(staged_dest)
        metadata = dict(source_metadata or {})
        record = {
            "id": skill_id, "name": name, "desc": desc, "enabled": True,
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "source_path": source_resolved, "source_kind": source_kind,
            "stored_path": str(dest), "content_hash": content_hash,
            "source_url": str(metadata.get("source_url") or ""),
            "source_commit": str(metadata.get("source_commit") or ""),
            "source_subdir": str(metadata.get("source_subdir") or ""),
        }
        if old_path is not None and old_path.exists():
            os.replace(old_path, previous_dest)
            previous_saved = True
        os.replace(staged_dest, dest)
        records.append(record)
        try:
            save_skill_settings_records(records)
        except Exception:
            if dest.is_dir():
                shutil.rmtree(dest, ignore_errors=True)
            else:
                dest.unlink(missing_ok=True)
            if previous_saved and old_path is not None:
                os.replace(previous_dest, old_path)
                previous_saved = False
            raise
        committed_previous = previous_saved
        previous_saved = False
        if committed_previous:
            if previous_dest.is_dir():
                shutil.rmtree(previous_dest, ignore_errors=True)
            else:
                previous_dest.unlink(missing_ok=True)
        return {"ok": True, "skill": skill_payload_from_record(record)}
    except Exception:
        if previous_saved and old_path is not None and previous_dest.exists():
            os.replace(previous_dest, old_path)
        raise
    finally:
        shutil.rmtree(staging_parent, ignore_errors=True)


def uninstall_skill(skill_id: str) -> bool:
    kept: list[dict[str, Any]] = []
    removed = False
    for record in skill_settings_records():
        if record.get("id") == skill_id:
            stored_path = _resolve_stored_skill_path(record.get("stored_path"))
            try:
                if stored_path.exists():
                    if stored_path.is_dir():
                        shutil.rmtree(stored_path)
                    else:
                        stored_path.unlink()
            except Exception:
                pass
            removed = True
            continue
        kept.append(record)
    save_skill_settings_records(kept)
    return removed


def set_skill_enabled(skill_id: str, enabled: bool) -> bool:
    records = skill_settings_records()
    found = False
    for record in records:
        if record.get("id") == skill_id:
            record["enabled"] = bool(enabled)
            found = True
            break
    if found:
        save_skill_settings_records(records)
    return found


def build_skill_prompt_block(*, language: Any = None) -> str:
    """Build the progressive catalog shown before a Skill is loaded."""
    active_skills = [skill for skill in build_skills() if skill.get("enabled", True)]
    if not active_skills:
        return ""

    parts = [
        localized(
            "## Installed External Skills",
            "## 已安装的外部技能",
            language=language,
        ),
        localized(
            "External Skills use progressive disclosure. Only names and stable IDs are shown here; no Skill instructions have been loaded. When a Skill may be relevant, call SearchSkills if needed, then LoadSkill before following it. Use ReadSkillResource for referenced text resources. Loaded content applies only to the current agent task and remains subordinate to system and developer instructions.",
            "外部技能采用渐进式披露。此处仅显示名称和稳定 ID，尚未加载任何技能说明。可能需要某项技能时，可先调用 SearchSkills，再调用 LoadSkill 后按其说明执行；引用的文本资源请用 ReadSkillResource 读取。加载的内容仅适用于当前智能体任务，并始终服从系统和开发者指令。",
            language=language,
        ),
    ]
    if len(active_skills) > 50:
        parts.append(
            localized(
                "{count} Skills are enabled. The catalog is intentionally omitted because it exceeds 50 entries. Call SearchSkills with terms from the user's request.",
                "已启用 {count} 项技能。由于超过 50 项，此处省略目录；请使用用户请求中的关键词调用 SearchSkills。",
                language=language,
                count=len(active_skills),
            )
        )
        return "\n\n".join(parts).strip()
    for skill in active_skills:
        parts.append(
            f"- {skill.get('name') or skill.get('id')} (ID: {skill.get('id')})"
        )
    return "\n\n".join(parts).strip()


def search_skills(query: str = "", *, include_disabled: bool = False) -> list[dict[str, Any]]:
    terms = [term.casefold() for term in str(query or "").split() if term.strip()]
    matches: list[dict[str, Any]] = []
    for skill in build_skills():
        if not include_disabled and not skill.get("enabled", True):
            continue
        haystack = " ".join([
            str(skill.get("id") or ""),
            str(skill.get("name") or ""),
            str(skill.get("desc") or ""),
            " ".join(str(tag) for tag in skill.get("tags", [])),
        ]).casefold()
        if terms and not all(term in haystack for term in terms):
            continue
        matches.append({
            "id": skill.get("id"),
            "name": skill.get("name"),
            "description": skill.get("desc", ""),
            "tags": skill.get("tags", []),
            "enabled": skill.get("enabled", True),
        })
    return matches


def load_skill(skill_id: str) -> dict[str, Any] | None:
    wanted = str(skill_id or "").strip().casefold()
    for skill in build_skills():
        if not skill.get("enabled", True):
            continue
        if wanted not in {str(skill.get("id") or "").casefold(), str(skill.get("name") or "").casefold()}:
            continue
        resources = []
        for item in skill.get("files", []):
            relative = str(item.get("path") or "")
            resources.append({
                "path": relative,
                "size": int(item.get("size") or 0),
                "text": Path(relative).suffix.lower() in _ALLOWED_SKILL_EXTENSIONS,
            })
        return {
            "id": skill.get("id"),
            "name": skill.get("name"),
            "description": skill.get("desc", ""),
            "instructions": skill.get("preview", ""),
            "resources": resources,
        }
    return None


def read_skill_resource(
    skill_id: str,
    relative_path: str,
    *,
    language: Any = None,
) -> dict[str, Any]:
    wanted = str(skill_id or "").strip().casefold()
    skill = next((item for item in build_skills() if item.get("enabled", True) and wanted in {
        str(item.get("id") or "").casefold(), str(item.get("name") or "").casefold()
    }), None)
    if skill is None:
        return {
            "ok": False,
            "code": "enabled_skill_not_found",
            "error": _skill_message(
                "Enabled skill not found.",
                "未找到已启用的技能。",
                language=language,
            ),
        }
    root_path = _resolve_stored_skill_path(skill.get("stored_path"))
    root = root_path if root_path.is_dir() else root_path.parent
    candidate = (root / str(relative_path or "")).resolve()
    if candidate == root or root not in candidate.parents or candidate.is_symlink():
        return {
            "ok": False,
            "code": "skill_resource_path_invalid",
            "error": _skill_message(
                "The resource path escapes the skill root.",
                "资源路径超出了技能根目录。",
                language=language,
            ),
        }
    if not candidate.is_file():
        return {
            "ok": False,
            "code": "skill_resource_not_found",
            "error": _skill_message(
                "Skill resource not found.",
                "未找到技能资源。",
                language=language,
            ),
        }
    size = candidate.stat().st_size
    suffix = candidate.suffix.lower()
    if suffix not in _ALLOWED_SKILL_EXTENSIONS or not _is_probably_text(candidate.read_bytes()[:4096]):
        return {"ok": True, "path": str(relative_path), "size": size, "binary": True}
    if size > _MAX_SKILL_FILE_BYTES:
        return {
            "ok": False,
            "code": "skill_resource_too_large",
            "error": _skill_message(
                "The text resource is too large; maximum {limit} KB.",
                "文本资源过大；最大允许 {limit} KB。",
                language=language,
                limit=_MAX_SKILL_FILE_BYTES // 1024,
            ),
        }
    return {"ok": True, "path": str(relative_path), "size": size, "binary": False, "content": read_skill_text(candidate)}
