"""Provider-family boundaries shared by configuration and model transports."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Iterable


class ProviderFamily(StrEnum):
    CODEX = "codex"
    OPENAI_COMPATIBLE = "openai_compatible"


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
    "ProviderFamily",
    "ProviderFamilyError",
    "provider_family_for_candidate",
    "require_single_provider_family",
]
