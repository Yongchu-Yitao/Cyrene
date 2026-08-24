"""Agent-facing facade for provider-family transcript policy."""

from __future__ import annotations

from cyrene.agent.lane_protocol import bind_agent_lane, current_agent_lane
from cyrene.model_runtime import transcript_policy as _policy

ProviderFamily = _policy.ProviderFamily
ProviderFamilyError = _policy.ProviderFamilyError
TranscriptLane = _policy.TranscriptLane
TranscriptPolicy = _policy.TranscriptPolicy
DUAL_LANE_CONTEXT_POLICY_VERSION = _policy.DUAL_LANE_CONTEXT_POLICY_VERSION
cache_scope_for_lane = _policy.cache_scope_for_lane
candidates_in_family = _policy.candidates_in_family
provider_family_for_candidate = _policy.provider_family_for_candidate
prompt_cache_key_for_lane = _policy.prompt_cache_key_for_lane
require_single_provider_family = _policy.require_single_provider_family
transcript_policy_for_family = _policy.transcript_policy_for_family

__all__ = [*_policy.__all__, "bind_agent_lane", "current_agent_lane"]
