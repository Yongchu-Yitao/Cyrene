"""Application services for behavior-learning HTTP use cases."""

from __future__ import annotations

import asyncio
import mimetypes
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

from cyrene import learning


_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_FILE_PATH_PATTERN = re.compile(
    r"(?P<path>(?:/|[A-Za-z]:\\)[^\"'<>\r\n]+?\."
    r"(?:png|jpg|jpeg|webp|gif|pdf|md|txt|json|csv|tsv|xlsx|docx|pptx|py|js|jsx|ts|tsx|css|html))",
    re.IGNORECASE,
)


class LearningApplicationError(RuntimeError):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class ProjectResolver:
    """Resolve project ids and preserve legacy storage identifiers."""

    def __init__(self, resolve: Callable[[str], str | None]):
        self._resolve = resolve

    def project_id(self, project: str = "") -> str:
        raw = str(project or "").strip()
        if not raw:
            return ""
        if re.fullmatch(r"project_[A-Za-z0-9]+", raw):
            return raw
        return self._resolve(raw) or raw

    def project_ids(self, project: str = "") -> list[str]:
        raw = str(project or "").strip()
        if not raw:
            return []
        resolved = self.project_id(raw)
        return [resolved, raw] if raw != resolved else [resolved]


class MediaRepository:
    """Locate and authorize media captured in persisted tool chains."""

    def __init__(self, data_dir: Path):
        self.media_root = (data_dir / "behavior-media").resolve()

    def resolve(self, raw_path: str) -> Path:
        raw = str(raw_path or "").strip()
        direct = Path(raw).expanduser()
        if direct.exists():
            return direct.resolve()
        normalized = raw.replace("\\", "/")
        marker = "/data/behavior-media/"
        marker_index = normalized.lower().rfind(marker)
        if marker_index >= 0 and (
            normalized.startswith("/") or re.match(r"^[A-Za-z]:/", normalized)
        ):
            relative = normalized[marker_index + len(marker):]
            candidate = (self.media_root / Path(relative)).resolve()
            if candidate == self.media_root or self.media_root in candidate.parents:
                return candidate
        return direct.resolve()

    @staticmethod
    def extract_paths(value: Any) -> list[str]:
        paths: list[str] = []
        if isinstance(value, dict):
            for item in value.values():
                paths.extend(MediaRepository.extract_paths(item))
            return paths
        if isinstance(value, list):
            for item in value:
                paths.extend(MediaRepository.extract_paths(item))
            return paths
        for match in _FILE_PATH_PATTERN.finditer(str(value or "")):
            path = match.group("path").rstrip(".,);]")
            if path and path not in paths:
                paths.append(path)
        return paths

    async def authorized_image(self, raw_path: str) -> tuple[Path, str] | None:
        target = self.resolve(raw_path)
        if target.suffix.lower() not in _IMAGE_EXTENSIONS:
            return None
        if not target.exists() or not target.is_file():
            return None
        chains = await learning.list_tool_chains("", 500)
        for chain in chains:
            for step in chain.get("chain") or []:
                if not isinstance(step, dict):
                    continue
                values = (
                    step.get("args") or {},
                    step.get("input_summary") or "",
                    step.get("output_summary") or "",
                )
                for value in values:
                    for candidate in self.extract_paths(value):
                        if self.resolve(candidate) == target:
                            media_type = mimetypes.guess_type(str(target))[0] or "image/png"
                            return target, media_type
        return None


class ToolChainProjection:
    """Build the HTTP read model for tool-chain artifacts."""

    def __init__(self, media: MediaRepository):
        self.media = media

    def enrich(self, chains: list[dict[str, Any]]) -> list[dict[str, Any]]:
        enriched: list[dict[str, Any]] = []
        for raw_chain in chains:
            if not isinstance(raw_chain, dict):
                continue
            summary = raw_chain.get("summary")
            if not isinstance(summary, dict) or int(summary.get("total_steps") or 0) <= 0:
                continue
            screenshots, files = self._artifacts(raw_chain)
            chain = dict(raw_chain)
            chain["screenshots"] = screenshots
            chain["files"] = files
            enriched.append(chain)
        return enriched

    def _artifacts(self, chain: dict[str, Any]) -> tuple[list[dict], list[dict]]:
        screenshots: list[dict] = []
        files: list[dict] = []
        seen: set[str] = set()
        for step in chain.get("chain") or []:
            if not isinstance(step, dict):
                continue
            candidates: list[str] = []
            for value in (
                step.get("args") or {},
                step.get("input_summary") or "",
                step.get("output_summary") or "",
            ):
                candidates.extend(self.media.extract_paths(value))
            for raw_path in candidates:
                if raw_path in seen:
                    continue
                seen.add(raw_path)
                target = self.media.resolve(raw_path)
                item = {"path": raw_path, "name": target.name or raw_path, "tool": str(step.get("tool") or "")}
                if target.suffix.lower() in _IMAGE_EXTENSIONS and target.is_file():
                    screenshots.append({**item, "url": "/api/tool-chain-media?path=" + quote(str(target.resolve()))})
                else:
                    files.append(item)
        return screenshots, files


class LearningApplicationService:
    def __init__(
        self,
        projects: ProjectResolver,
        media: MediaRepository,
        tool_chains: ToolChainProjection,
        status_provider: Callable[[], Awaitable[dict[str, Any]]],
    ) -> None:
        self.projects = projects
        self.media = media
        self.tool_chains = tool_chains
        self.status_provider = status_provider

    async def media_file(self, path: str) -> tuple[Path, str]:
        target = self.media.resolve(path)
        if target.suffix.lower() not in _IMAGE_EXTENSIONS:
            raise LearningApplicationError("unsupported media type", 400)
        if not target.exists() or not target.is_file():
            raise LearningApplicationError("media not found", 404)
        authorized = await self.media.authorized_image(path)
        if authorized is None:
            raise LearningApplicationError("media not found", 404)
        return authorized

    async def evolution(self, project: str) -> dict:
        project_id = self.projects.project_id(project)
        status, skills, chains, candidates = await asyncio.gather(
            self.status_provider(),
            learning.list_learned_skills(project_id),
            learning.list_tool_chains(self.projects.project_ids(project)),
            learning.list_skill_candidates(project_id),
        )
        return {
            "phase": status.get("phase", ""),
            "state": status.get("state", ""),
            "learned_skills": skills,
            "skill_candidates": candidates,
            "tool_chains": self.tool_chains.enrich(chains),
        }

    async def learned_skills(self, project: str) -> dict:
        return {"skills": await learning.list_learned_skills(self.projects.project_id(project))}

    async def chains(self, project: str, limit: int) -> dict:
        chains = await learning.list_tool_chains(self.projects.project_ids(project), limit)
        return {"tool_chains": self.tool_chains.enrich(chains)}

    async def candidates(self, project: str, status: str) -> dict:
        return {"candidates": await learning.list_skill_candidates(self.projects.project_id(project), status)}

    async def process(self, project: str, turn_id: str) -> dict:
        tid = str(turn_id or "").strip()
        if tid:
            stats = await learning.learn_from_turn(tid)
            project_id = (await learning.project_scope_for_turn(tid))["project_id"]
        else:
            project_id = self.projects.project_id(project)
            stats = await learning.scan_for_manual_learn(project_id)
        return await self._aggregate(project_id, project_id or project, stats=stats)

    async def rebuild(self, project: str) -> dict:
        project_id = self.projects.project_id(project)
        result = await learning.rebuild_learning_state(
            reprocess_all_turns=True, project_id=project_id
        )
        return await self._aggregate(project_id, project, result=result)

    async def _aggregate(self, project_id: str, project_ref: str, **result: Any) -> dict:
        skills, candidates, chains = await asyncio.gather(
            learning.list_learned_skills(project_id),
            learning.list_skill_candidates(project_id),
            learning.list_tool_chains(self.projects.project_ids(project_ref)),
        )
        return {
            "ok": True,
            **result,
            "learned_skills": skills,
            "skill_candidates": candidates,
            "tool_chains": self.tool_chains.enrich(chains),
        }

    async def decide_candidate(self, candidate_id: str, decision: str) -> dict:
        return await learning.decide_skill_candidate(candidate_id, decision)

    async def skill(self, skill_id: str) -> dict[str, Any] | None:
        return await learning.get_learned_skill(skill_id)

    async def versions(self, skill_id: str) -> list[dict[str, Any]]:
        return await learning.list_learned_skill_versions(skill_id)

    async def patches(self, skill_id: str, status: str) -> list[dict[str, Any]]:
        return await learning.list_learned_skill_patches(skill_id, status)

    async def runs(self, skill_id: str, limit: int) -> list[dict[str, Any]]:
        return await learning.list_learned_skill_runs(skill_id, limit)

    async def update_skill(self, skill_id: str, updates: dict, reason: str):
        return await learning.update_learned_skill(skill_id, updates, reason=reason)

    async def rollback(self, skill_id: str, version: int) -> dict:
        return await learning.rollback_learned_skill(skill_id, version)

    async def apply_patch(self, skill_id: str, patch_id: str) -> dict:
        return await learning.apply_skill_patch(skill_id, patch_id)

    async def reject_patch(self, skill_id: str, patch_id: str) -> bool:
        return await learning.reject_skill_patch(skill_id, patch_id)

    async def activate(self, skill_id: str) -> bool:
        return await learning.activate_learned_skill(skill_id)

    async def deprecate(self, skill_id: str) -> bool:
        return await learning.deprecate_learned_skill(skill_id)

    async def delete(self, skill_id: str) -> bool:
        return await learning.delete_learned_skill(skill_id)

    async def run(self, skill_id: str) -> dict:
        result = await learning.run_learned_skill(skill_id)
        return {"ok": not str(result).startswith("Learned skill"), "result": result}
