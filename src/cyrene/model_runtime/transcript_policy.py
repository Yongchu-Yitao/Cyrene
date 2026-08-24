"""Provider-family boundaries shared by configuration and model transports."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Iterable


DUAL_LANE_CONTEXT_POLICY_VERSION = "dual-lane-v1"


class ProviderFamily(StrEnum):
    CODEX = "codex"
    OPENAI_COMPATIBLE = "openai_compatible"


class TranscriptPolicy(StrEnum):
    LEGACY_SHARED = "legacy_shared"
    DUAL_LANE = "dual_lane"


class TranscriptLane(StrEnum):
    DECISION = "decision"
    EXECUTION = "execution"


class ProviderFamilyError(ValueError):
    """Raised when an automatic route would cross provider families."""


def _provider_marker(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def provider_family_for_candidate(candidate: dict[str, Any]) -> ProviderFamily:
    """Classify one runtime candidate from either durable identity marker."""
    adapter = _provider_marker(candidate.get("adapter"))
    provider = _provider_marker(candidate.get("provider"))
    if "codex_oauth" in {adapter, provider}:
        return ProviderFamily.CODEX
    return ProviderFamily.OPENAI_COMPATIBLE


def transcript_policy_for_family(family: ProviderFamily | str) -> TranscriptPolicy:
    normalized = ProviderFamily(str(family))
    if normalized is ProviderFamily.CODEX:
        return TranscriptPolicy.LEGACY_SHARED
    return TranscriptPolicy.DUAL_LANE


def cache_scope_for_lane(
    family: ProviderFamily | str,
    lane: TranscriptLane | str,
) -> str:
    """Return the diagnostics/cache partition for one model transcript lane."""
    normalized_family = ProviderFamily(str(family))
    normalized_lane = TranscriptLane(str(lane))
    if normalized_family is ProviderFamily.CODEX:
        # Empty scope preserves the pre-policy RunModelLease key exactly.
        return ""
    return normalized_lane.value


def prompt_cache_key_for_lane(
    *,
    provider_profile: Any,
    model: str,
    lane: TranscriptLane | str,
    system_prompt: Any,
    tool_schema: Any,
    context_policy_version: str = DUAL_LANE_CONTEXT_POLICY_VERSION,
    cache_epoch: str | int = "",
) -> str:
    """Build the stable provider cache route for one independent lane.

    Only stable prefix inputs belong in this key.  Conversation turns are
    deliberately excluded: appending a user/tool turn must keep routing to the
    same provider-side cache channel.  ``cache_epoch`` is reserved for the
    coordinator to rotate one lane after compaction without invalidating the
    other lane.
    """
    normalized_lane = TranscriptLane(str(lane))
    material = {
        "version": 1,
        "provider_profile": provider_profile,
        "model": str(model or ""),
        "lane": normalized_lane.value,
        "system_prompt": system_prompt,
        "tool_schema": tool_schema,
        "context_policy_version": str(context_policy_version or ""),
        "cache_epoch": str(cache_epoch or ""),
    }
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:40]
    return f"cyrene-v1-{normalized_lane.value}-{digest}"


def candidates_in_family(
    candidates: Iterable[dict[str, Any]],
    family: ProviderFamily | str,
) -> list[dict[str, Any]]:
    """Keep candidates belonging to a frozen family, preserving route order."""
    expected = ProviderFamily(str(family))
    return [
        candidate
        for candidate in candidates
        if provider_family_for_candidate(candidate) is expected
    ]


def require_single_provider_family(
    candidates: Iterable[dict[str, Any]],
    *,
    expected: ProviderFamily | str | None = None,
    context: str = "model route",
) -> ProviderFamily:
    """Validate that an automatic candidate chain cannot cross families."""
    candidate_list = list(candidates)
    if not candidate_list:
        if expected is not None:
            family = ProviderFamily(str(expected))
            raise ProviderFamilyError(
                f"{context} has no {family.value} candidates"
            )
        raise ProviderFamilyError(f"{context} has no model candidates")
    resolved = (
        ProviderFamily(str(expected))
        if expected is not None
        else provider_family_for_candidate(candidate_list[0])
    )
    foreign = [
        candidate
        for candidate in candidate_list
        if provider_family_for_candidate(candidate) is not resolved
    ]
    if foreign:
        raise ProviderFamilyError(
            f"{context} mixes Codex and OpenAI-compatible candidates; automatic "
            "fallback across provider families is not allowed"
        )
    return resolved


__all__ = [
    "DUAL_LANE_CONTEXT_POLICY_VERSION",
    "ProviderFamily",
    "ProviderFamilyError",
    "TranscriptLane",
    "TranscriptPolicy",
    "cache_scope_for_lane",
    "candidates_in_family",
    "provider_family_for_candidate",
    "prompt_cache_key_for_lane",
    "require_single_provider_family",
    "transcript_policy_for_family",
]
