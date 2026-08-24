"""Provider-family and transcript-policy boundaries for Agent runs."""

from __future__ import annotations

import json

import pytest

from cyrene.agent import state as agent_state
from cyrene.agent.lane_protocol import bind_agent_lane
from cyrene.agent.transcript_policy import (
    DUAL_LANE_CONTEXT_POLICY_VERSION,
    ProviderFamily,
    ProviderFamilyError,
    TranscriptLane,
    TranscriptPolicy,
    prompt_cache_key_for_lane,
    provider_family_for_candidate,
    require_single_provider_family,
)
from cyrene.model_runtime import client as model_client
from cyrene.runtime import model_configuration


def _candidate(candidate_id: str, provider: str) -> dict:
    return {
        "id": candidate_id,
        "provider": provider,
        "adapter": provider,
        "model": candidate_id,
        "base_url": (
            "codex://oauth"
            if provider == "codex_oauth"
            else f"https://{candidate_id}.example/v1"
        ),
        "api_key": "" if provider == "codex_oauth" else "sk-test",
        "endpoints": [
            "codex://oauth"
            if provider == "codex_oauth"
            else f"https://{candidate_id}.example/v1/chat/completions"
        ],
    }


def test_provider_family_classifier_and_same_family_fallback():
    codex = _candidate("codex", "codex_oauth")
    first = _candidate("first", "openai_compatible")
    second = _candidate("second", "openai_compatible")

    assert provider_family_for_candidate(codex) is ProviderFamily.CODEX
    assert (
        provider_family_for_candidate(first)
        is ProviderFamily.OPENAI_COMPATIBLE
    )
    assert (
        require_single_provider_family([first, second])
        is ProviderFamily.OPENAI_COMPATIBLE
    )


def test_prompt_cache_key_is_stable_and_binds_every_lane_prefix_dimension():
    base = {
        "provider_profile": {
            "profile_id": "profile-a",
            "provider": "openai",
            "adapter": "openai",
        },
        "model": "gpt-5.4",
        "lane": TranscriptLane.DECISION,
        "system_prompt": [{"role": "system", "content": "decide"}],
        "tool_schema": [{"type": "function", "function": {"name": "use_tools"}}],
        "context_policy_version": DUAL_LANE_CONTEXT_POLICY_VERSION,
        "cache_epoch": "epoch-1",
    }

    expected = prompt_cache_key_for_lane(**base)
    assert prompt_cache_key_for_lane(**base) == expected
    assert expected.startswith("cyrene-v1-decision-")

    variants = [
        {
            **base,
            "provider_profile": {
                **base["provider_profile"],
                "profile_id": "profile-b",
            },
        },
        {**base, "model": "gpt-5.5"},
        {**base, "lane": TranscriptLane.EXECUTION},
        {**base, "system_prompt": [{"role": "system", "content": "execute"}]},
        {
            **base,
            "tool_schema": [
                {"type": "function", "function": {"name": "read"}}
            ],
        },
        {**base, "context_policy_version": "dual-lane-v2"},
        {**base, "cache_epoch": "epoch-2"},
    ]
    assert all(prompt_cache_key_for_lane(**variant) != expected for variant in variants)


def test_unconfigured_run_keeps_the_legacy_empty_model_path(monkeypatch):
    monkeypatch.setattr(model_client, "_resolve_candidates", lambda _model_type: [])
    monkeypatch.setattr(
        model_client,
        "_prioritize_last_success",
        lambda candidates, _model_type, _session_id: candidates,
    )

    token = agent_state.activate_run_model_lease()
    try:
        lease = agent_state._run_model_lease.get()
        assert lease is not None
        assert agent_state.current_run_provider_family() is None
        assert (
            agent_state.current_run_transcript_policy()
            is TranscriptPolicy.LEGACY_SHARED
        )
        assert agent_state.current_run_cache_scope("execution") == ""
        assert lease.candidates_for("primary") == []
    finally:
        agent_state.reset_run_model_lease(token)


def test_direct_loop_callers_without_a_lease_keep_legacy_policy():
    token = agent_state._run_model_lease.set(None)
    try:
        assert agent_state.current_run_provider_family() is None
        assert (
            agent_state.current_run_transcript_policy()
            is TranscriptPolicy.LEGACY_SHARED
        )
        assert agent_state.current_run_cache_scope("decision") == ""
    finally:
        agent_state._run_model_lease.reset(token)


def test_direct_legacy_lease_override_keeps_openai_family_and_shared_cache(
    monkeypatch,
):
    selected = _candidate("selected", "openai_compatible")
    routes = {
        "primary": [selected],
        "secondary": [_candidate("secondary", "openai_compatible")],
        "vision": [],
    }
    monkeypatch.setattr(
        model_client,
        "_resolve_candidates",
        lambda model_type: list(routes[model_type]),
    )
    monkeypatch.setattr(
        model_client,
        "_prioritize_last_success",
        lambda candidates, _model_type, _session_id: candidates,
    )

    token = agent_state.activate_run_model_lease(
        transcript_policy_override=TranscriptPolicy.LEGACY_SHARED
    )
    try:
        lease = agent_state._run_model_lease.get()
        assert lease is not None
        assert lease.provider_family is ProviderFamily.OPENAI_COMPATIBLE
        assert lease.transcript_policy is TranscriptPolicy.LEGACY_SHARED
        assert agent_state.current_run_cache_scope("decision") == ""
        assert agent_state.current_run_cache_scope("execution") == ""
        assert [item["id"] for item in lease.candidates_for("primary")] == [
            "selected"
        ]
    finally:
        agent_state.reset_run_model_lease(token)


@pytest.mark.parametrize(
    ("first_provider", "expected_family", "expected_policy", "decision_scope", "execution_scope"),
    [
        (
            "codex_oauth",
            ProviderFamily.CODEX,
            TranscriptPolicy.LEGACY_SHARED,
            "",
            "",
        ),
        (
            "openai_compatible",
            ProviderFamily.OPENAI_COMPATIBLE,
            TranscriptPolicy.DUAL_LANE,
            "decision",
            "execution",
        ),
    ],
)
def test_run_lease_freezes_policy_and_filters_every_snapshot_family(
    monkeypatch,
    first_provider,
    expected_family,
    expected_policy,
    decision_scope,
    execution_scope,
):
    other_provider = (
        "openai_compatible"
        if first_provider == "codex_oauth"
        else "codex_oauth"
    )
    routes = {
        "primary": [
            _candidate("selected", first_provider),
            _candidate("foreign-primary", other_provider),
        ],
        "secondary": [
            _candidate("foreign-secondary", other_provider),
            _candidate("same-secondary", first_provider),
        ],
        "vision": [
            _candidate("foreign-vision", other_provider),
            _candidate("same-vision", first_provider),
        ],
    }
    monkeypatch.setattr(
        model_client,
        "_resolve_candidates",
        lambda model_type: list(routes[model_type]),
    )
    monkeypatch.setattr(
        model_client,
        "_prioritize_last_success",
        lambda candidates, _model_type, _session_id: candidates,
    )

    token = agent_state.activate_run_model_lease()
    try:
        lease = agent_state._run_model_lease.get()
        assert lease is not None
        assert agent_state.current_run_provider_family() is expected_family
        assert agent_state.current_run_transcript_policy() is expected_policy
        assert agent_state.current_run_cache_scope("decision") == decision_scope
        assert agent_state.current_run_cache_scope("execution") == execution_scope
        assert all(
            provider_family_for_candidate(candidate) is expected_family
            for model_type in ("primary", "secondary", "vision")
            for candidate in lease.candidates_for(model_type)
        )
        assert [item["id"] for item in lease.candidates_for("primary")] == [
            "selected"
        ]
        assert [item["id"] for item in lease.candidates_for("secondary")] == [
            "same-secondary"
        ]
        assert [item["id"] for item in lease.candidates_for("vision")] == [
            "same-vision"
        ]
    finally:
        agent_state.reset_run_model_lease(token)


@pytest.mark.parametrize(
    ("family", "policy", "expected_scope"),
    [
        (ProviderFamily.CODEX, TranscriptPolicy.LEGACY_SHARED, ""),
        (
            ProviderFamily.OPENAI_COMPATIBLE,
            TranscriptPolicy.LEGACY_SHARED,
            "",
        ),
        (
            ProviderFamily.OPENAI_COMPATIBLE,
            TranscriptPolicy.DUAL_LANE,
            "execution",
        ),
    ],
)
async def test_state_model_wrapper_uses_the_canonical_bound_lane(
    monkeypatch,
    family,
    policy,
    expected_scope,
):
    provider = (
        "codex_oauth"
        if family is ProviderFamily.CODEX
        else "openai_compatible"
    )
    candidate = _candidate("selected", provider)
    lease = agent_state.RunModelLease(
        "lease-bound-lane",
        {"primary": (candidate,)},
        provider_family=family,
        transcript_policy=policy,
    )
    captured = []

    async def fake_call_llm(_messages, **kwargs):
        captured.append(kwargs)
        return {"role": "assistant", "content": "ok"}

    monkeypatch.setattr(model_client, "call_llm", fake_call_llm)
    lease_token = agent_state._run_model_lease.set(lease)
    try:
        with bind_agent_lane("execution"):
            await agent_state._call_llm([{"role": "user", "content": "go"}])
    finally:
        agent_state._run_model_lease.reset(lease_token)

    assert captured[0]["cache_scope"] == expected_scope


async def test_call_llm_rejects_cross_family_explicit_fallback_before_transport():
    with pytest.raises(ProviderFamilyError, match="automatic fallback"):
        await model_client.call_llm(
            [{"role": "user", "content": "hello"}],
            candidates=[
                _candidate("custom", "openai_compatible"),
                _candidate("codex", "codex_oauth"),
            ],
            publish_events=False,
            record_usage=False,
            record_latency=False,
        )


async def test_call_llm_rejects_foreign_explicit_candidate_against_frozen_lease():
    lease = agent_state.RunModelLease(
        "lease-codex",
        {"primary": (_candidate("codex", "codex_oauth"),)},
        provider_family=ProviderFamily.CODEX,
        transcript_policy=TranscriptPolicy.LEGACY_SHARED,
    )

    with pytest.raises(ProviderFamilyError, match="automatic fallback"):
        await model_client.call_llm(
            [{"role": "user", "content": "hello"}],
            candidates=[_candidate("custom", "openai_compatible")],
            candidate_lease=lease,
            publish_events=False,
            record_usage=False,
            record_latency=False,
        )


async def test_call_llm_does_not_escape_an_empty_frozen_family_snapshot():
    lease = agent_state.RunModelLease(
        "lease-empty-codex",
        {"primary": ()},
        provider_family=ProviderFamily.CODEX,
        transcript_policy=TranscriptPolicy.LEGACY_SHARED,
    )

    with pytest.raises(ProviderFamilyError, match="no codex candidates"):
        await model_client.call_llm(
            [{"role": "user", "content": "hello"}],
            candidates=[],
            candidate_lease=lease,
            publish_events=False,
            record_usage=False,
            record_latency=False,
        )


def _route_configuration(*, mixed_primary: bool = False) -> dict:
    connections = [
        {
            "id": "openai",
            "name": "OpenAI-compatible",
            "adapter": "openai",
            "base_url": "https://api.example/v1",
            "api_key": "sk-test",
        },
        {
            "id": "codex",
            "name": "Codex",
            "adapter": "codex_oauth",
        },
    ]
    profiles = [
        {
            "id": "openai-primary",
            "connection_id": "openai",
            "model": "model-a",
            "capabilities": ["chat"],
        },
        {
            "id": "openai-fallback",
            "connection_id": "openai",
            "model": "model-b",
            "capabilities": ["chat"],
        },
        {
            "id": "codex-fallback",
            "connection_id": "codex",
            "model": "gpt-5.6-sol",
            "capabilities": ["chat"],
        },
    ]
    return {
        "connections": connections,
        "profiles": profiles,
        "routes": {
            "primary": [
                "openai-primary",
                "codex-fallback" if mixed_primary else "openai-fallback",
            ],
            "secondary": [],
            "vision": [],
            "embedding": [],
        },
    }


def test_model_configuration_save_rejects_mixed_primary_route(monkeypatch):
    raw = _route_configuration(mixed_primary=True)
    previous = model_configuration.normalize_model_configuration(raw)
    monkeypatch.setattr(
        model_configuration,
        "get_model_configuration",
        lambda: previous,
    )

    with pytest.raises(ProviderFamilyError, match="route 'primary'.*automatic fallback"):
        model_configuration.save_model_configuration(raw)


def test_model_configuration_rejects_cross_family_secondary_route():
    raw = _route_configuration()
    raw["routes"]["primary"] = ["openai-primary"]
    raw["routes"]["secondary"] = ["codex-fallback"]
    normalized = model_configuration.normalize_model_configuration(raw)

    with pytest.raises(ProviderFamilyError, match="route 'secondary'.*primary"):
        model_configuration.validate_active_route_provider_families(normalized)


def test_model_configuration_save_allows_same_family_fallback(monkeypatch):
    raw = _route_configuration()
    previous = model_configuration.normalize_model_configuration(raw)
    persisted = []
    monkeypatch.setattr(
        model_configuration,
        "get_model_configuration",
        lambda: previous,
    )
    monkeypatch.setattr(
        model_configuration.config_store,
        "update_settings_and_env_atomic",
        lambda settings, env, **_kwargs: (persisted.append((settings, env)) or 7, {}),
    )
    monkeypatch.setattr(
        model_configuration,
        "invalidate_model_runtime_caches",
        lambda: None,
    )

    saved, revision = model_configuration.save_model_configuration(raw)

    assert revision == 7
    assert saved["routes"]["primary"] == [
        "openai-primary",
        "openai-fallback",
    ]
    assert len(persisted) == 1


def test_lane_storage_metadata_is_not_sent_to_provider_wire():
    protocol_content = json.dumps({
        "type": "execution_handoff",
        "event_id": "event-in-content",
    })
    prepared = model_client.sanitize_messages_for_llm([{
        "role": "user",
        "content": protocol_content,
        "lane_refs": ["decision"],
        "record_kind": "lane_protocol",
        "persist_model_record": True,
        "event_id": "event-local",
        "turn_id": "turn-local",
        "owner_lane": "execution",
        "attempt": 1,
    }])

    assert prepared == [{"role": "user", "content": protocol_content}]
