"""Focused tests for the external ACP Agent Runtime domain foundation.

Covers stable failure kinds, capability normalization, agent binding / model
access models, built-in Cyrene defaults, the driver registry, and event
envelopes.
"""

import pytest

from cyrene.agents import (
    FAILURE_KINDS,
    AgentBinding,
    AgentDescriptor,
    AgentRuntimeError,
    ModelAccess,
    builtin_binding,
    chat_agent_fields,
    event_envelope,
    failure_kind,
    get_driver,
    is_capability_available,
    is_failure_kind,
    merge_capabilities,
    normalize_agent_binding,
    normalize_agent_fields,
    normalize_capabilities,
    normalize_capability_state,
    normalize_model_access,
    sanitize_event_payload,
    with_conservative_defaults,
)
from cyrene.agents.builtin import (
    BUILTIN_AGENT_CAPABILITIES,
    BUILTIN_AGENT_ID,
    BUILTIN_DISPLAY_NAME,
    BUILTIN_DRIVER,
    BUILTIN_INSTALLATION_ID,
    builtin_descriptor,
)
from cyrene.agents.driver import DriverRegistry


# ---------------------------------------------------------------------------
# Stable failure kinds
# ---------------------------------------------------------------------------

def test_failure_kinds_cover_handoff_table():
    expected = {
        "dependency_missing",
        "agent_disabled",
        "auth_required",
        "auth_expired",
        "protocol_mismatch",
        "capability_missing",
        "model_binding_unsupported",
        "model_gateway_unavailable",
        "agent_crashed",
        "session_not_loadable",
        "request_expired",
    }
    assert set(FAILURE_KINDS) == expected


def test_failure_kind_coerces_unknown_to_unknown():
    assert failure_kind("auth_expired") == "auth_expired"
    assert failure_kind("bogus_kind") == "unknown"
    assert failure_kind(None) == "unknown"
    assert not is_failure_kind("bogus_kind")


def test_agent_runtime_error_carries_stable_kind():
    error = AgentRuntimeError(
        "model_binding_unsupported",
        "model is not compatible",
        detail={"model": "gpt-x"},
        retryable=False,
    )
    assert error.kind == "model_binding_unsupported"
    assert error.detail["model"] == "gpt-x"
    public = error.to_public_dict()
    assert public["failureKind"] == "model_binding_unsupported"
    assert public["message"] == "model is not compatible"
    assert public["retryable"] is False


# ---------------------------------------------------------------------------
# Capability normalization
# ---------------------------------------------------------------------------

def test_normalize_capabilities_empty_input():
    assert normalize_capabilities(None) == {}
    assert normalize_capabilities({}) == {}
    assert normalize_capabilities("garbage") == {}


def test_normalize_capabilities_coerces_states_and_drops_invalid_keys():
    raw = {
        "output": {"streaming": "Supported", "reasoning": "degraded", "nonsense": 1},
        "interaction": {"permission": "agent_defined"},
        "session": {"load": "unknown"},
    }
    normalized = normalize_capabilities(raw)
    assert normalized["output"]["streaming"] == "supported"
    assert normalized["output"]["reasoning"] == "degraded"
    assert "nonsense" not in normalized["output"]
    assert normalized["interaction"]["permission"] == "agent_defined"
    assert normalized["session"]["load"] == "unknown"


def test_normalize_capabilities_model_group_protocols():
    normalized = normalize_capabilities(
        {"model": {"cyreneManaged": ["openai_responses", "openai_chat"], "agentManaged": True}}
    )
    assert normalized["model"]["cyreneManaged"] == ["openai_responses", "openai_chat"]
    assert normalized["model"]["agentManaged"] == "unknown"
    single = normalize_capabilities({"model": {"cyreneManaged": "openai_chat"}})
    assert single["model"]["cyreneManaged"] == ["openai_chat"]


def test_normalize_capabilities_keeps_unknown_agent_specific_groups():
    normalized = normalize_capabilities({"custom": {"slot": "supported", "junk": object()}})
    assert normalized["custom"] == {"slot": "supported"}


def test_merge_capabilities_later_sources_win():
    merged = merge_capabilities(
        {"output": {"streaming": "unknown"}},
        {"output": {"streaming": "supported"}, "input": {"text": "supported"}},
        base={"input": {"text": "unknown"}, "session": {"load": "unknown"}},
    )
    assert merged["output"]["streaming"] == "supported"
    assert merged["input"]["text"] == "supported"
    assert merged["session"]["load"] == "unknown"


def test_with_conservative_defaults_fills_unknown_and_protocol_lists():
    full = with_conservative_defaults({})
    assert sorted(full) == ["input", "interaction", "model", "output", "session"]
    assert full["input"]["text"] == "unknown"
    assert full["model"]["cyreneManaged"] == []
    assert full["interaction"]["permission"] == "unknown"


def test_capability_availability_semantics():
    assert is_capability_available("supported")
    assert is_capability_available("degraded")
    assert is_capability_available("agent_defined")
    assert not is_capability_available("unknown")
    assert not is_capability_available("unsupported")
    assert normalize_capability_state("SUPPORTED") == "supported"
    assert normalize_capability_state(None) == "unknown"


# ---------------------------------------------------------------------------
# Models: snake_case internally, camelCase public
# ---------------------------------------------------------------------------

def test_agent_binding_aliases_and_public_shape():
    binding = AgentBinding(installation_id="agent_x", agent_id="x", display_name="X", version="1.0")
    assert binding.installation_id == "agent_x"
    public = binding.to_public_dict()
    assert public["installationId"] == "agent_x"
    assert public["displayName"] == "X"
    assert public["bindingLocked"] is False
    assert public["externalSessionId"] == ""
    assert public["protocolVersion"] == 1


def test_model_access_defaults_and_mode_coercion():
    access = ModelAccess()
    assert access.mode == "cyrene_managed"
    assert access.profile_id == ""
    coerced = ModelAccess(mode="weird")
    assert coerced.mode == "cyrene_managed"
    agent_managed = ModelAccess(mode="agent_managed")
    assert agent_managed.to_public_dict()["mode"] == "agent_managed"


def test_agent_descriptor_state_normalization():
    descriptor = AgentDescriptor(
        installation_id="agent_x",
        state="weird_state",
        auth_state="expired",
        protocol_version="not-an-int",
    )
    assert descriptor.state == "unknown"
    assert descriptor.auth_state == "expired"
    assert descriptor.protocol_version == 0


# ---------------------------------------------------------------------------
# Built-in Cyrene normalization / backward compatibility
# ---------------------------------------------------------------------------

def test_builtin_binding_and_descriptor():
    binding = builtin_binding()
    assert binding.is_builtin
    assert binding.installation_id == BUILTIN_INSTALLATION_ID
    assert binding.agent_id == BUILTIN_AGENT_ID
    assert binding.display_name == BUILTIN_DISPLAY_NAME
    descriptor = builtin_descriptor()
    assert descriptor.state == "ready"
    assert descriptor.auth_state == "connected"
    assert descriptor.capabilities["input"]["text"] == "supported"


def test_normalize_agent_binding_falls_back_to_builtin():
    assert normalize_agent_binding(None).is_builtin
    assert normalize_agent_binding({}).is_builtin
    assert normalize_agent_binding({"installationId": BUILTIN_INSTALLATION_ID}).is_builtin
    external = normalize_agent_binding(
        {"installationId": "agent_opencode_default", "agentId": "opencode", "displayName": "OpenCode"}
    )
    assert not external.is_builtin
    assert external.display_name == "OpenCode"
    assert external.protocol_version == 0
    assert external.binding_locked is False


def test_normalize_model_access_legacy_fallback():
    access = normalize_model_access(None, default_model="gpt-5")
    assert access.mode == "cyrene_managed"
    assert access.model == "gpt-5"
    raw = normalize_model_access({"mode": "agent_managed"})
    assert raw.mode == "agent_managed"
    assert raw.profile_id == ""


def test_normalize_agent_fields_chat_storage_block():
    fields = normalize_agent_fields(
        {"installationId": "agent_opencode_default", "agentId": "opencode", "displayName": "OpenCode"},
        {"mode": "cyrene_managed", "profileId": "primary"},
        default_model="gpt-5",
    )
    assert set(fields) == {"agent", "modelAccess", "capabilities", "capabilitiesRevision"}
    assert fields["agent"]["installationId"] == "agent_opencode_default"
    assert fields["modelAccess"]["profileId"] == "primary"
    assert fields["capabilities"] == {}
    assert fields["capabilitiesRevision"] == 1

    builtin_fields = normalize_agent_fields(None, None, default_model="gpt-5")
    assert builtin_fields["agent"]["installationId"] == BUILTIN_INSTALLATION_ID
    assert builtin_fields["capabilities"] == normalize_capabilities(BUILTIN_AGENT_CAPABILITIES)


def test_chat_agent_fields_normalizes_legacy_chat_without_mutation():
    legacy = {"id": "wbchat_1", "model": "gpt-4", "messages": []}
    fields = chat_agent_fields(legacy)
    assert fields["agent"]["agentId"] == BUILTIN_AGENT_ID
    assert fields["agent"]["installationId"] == BUILTIN_INSTALLATION_ID
    assert fields["modelAccess"]["model"] == "gpt-4"
    assert "agent" not in legacy  # read-only normalization

    stored = chat_agent_fields(
        {
            "agent": {"installationId": "agent_opencode_default", "agentId": "opencode"},
            "modelAccess": {"mode": "agent_managed"},
            "capabilities": {"output": {"streaming": "supported"}},
            "capabilitiesRevision": 3,
        }
    )
    assert stored["agent"]["installationId"] == "agent_opencode_default"
    assert stored["modelAccess"]["mode"] == "agent_managed"
    assert stored["capabilities"]["output"]["streaming"] == "supported"
    assert stored["capabilitiesRevision"] == 3


# ---------------------------------------------------------------------------
# Driver registry
# ---------------------------------------------------------------------------

async def test_driver_registry_unknown_driver_is_protocol_mismatch():
    registry = DriverRegistry()
    with pytest.raises(AgentRuntimeError) as exc:
        registry.create("missing_driver")
    assert exc.value.kind == "protocol_mismatch"


async def test_driver_registry_register_and_names():
    registry = DriverRegistry()

    class FakeDriver:
        async def inspect(self, installation=None):
            return builtin_descriptor()

        async def connect(self, request):
            raise AssertionError("not used in this test")

    registry.register("fake", FakeDriver, protocol_version=2, description="fake driver")
    assert registry.names() == ["fake"]
    assert registry.info("fake").protocol_version == 2
    assert registry.contains("fake")
    driver = registry.create("fake")
    descriptor = await driver.inspect(None)
    assert descriptor.display_name == BUILTIN_DISPLAY_NAME


async def test_default_registry_exposes_builtin_driver():
    driver = get_driver(BUILTIN_DRIVER)
    descriptor = await driver.inspect(None)
    assert descriptor.installation_id == BUILTIN_INSTALLATION_ID
    with pytest.raises(AgentRuntimeError) as exc:
        await driver.connect(None)
    assert exc.value.kind == "capability_missing"


# ---------------------------------------------------------------------------
# Unified event envelope
# ---------------------------------------------------------------------------

def test_event_envelope_shape_and_sanitization():
    envelope = event_envelope(
        type="message.delta",
        payload={"text": "hi", "api_key": "secret", "authorization": "Bearer x"},
    )
    assert envelope["schemaVersion"] == 1
    assert envelope["type"] == "message.delta"
    assert envelope["payload"] == {"text": "hi"}
    assert envelope["actorId"] == "primary"
    assert envelope["parentRunId"] is None


def test_sanitize_event_payload_strips_secret_like_keys():
    sanitized = sanitize_event_payload(
        {"content": "ok", "access_token": "t", "x-api-key": "k", "password": "p", "oauth": "o"}
    )
    assert sanitized == {"content": "ok"}
