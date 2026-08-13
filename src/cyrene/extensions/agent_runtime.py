"""External Agent proposals, installation records, and settings.

Cyrene validates ``cyrene.agent/v1`` manifests and persists declarative
proposal/installation state. The Agent Runtime consumes these records to start
ACP stdio processes; manifests still cannot load Python code or execute
arbitrary installer arguments.

Installation records live in the settings store (``extension_agent_installations``)
so any externally proposed, non-recommended Agent appears in the same
"installed" enumeration as recommended Agents.  Proposals live separately
(``extension_agent_proposals``) and must be explicitly confirmed before an
install task is created.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import os
import platform
import urllib.parse
import uuid
import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx

from cyrene.extensions.catalog import AGENT_MANIFEST_API, RECOMMENDED_AGENTS, RECOMMENDED_AGENT_ORDER
from cyrene.runtime.settings_store import get as get_setting, set_ as set_setting

INSTALLATIONS_KEY = "extension_agent_installations"
PROPOSALS_KEY = "extension_agent_proposals"
ALLOWED_DRIVERS = frozenset({"acp_stdio"})
ALLOWED_MODEL_ACCESS = frozenset({"cyrene_managed", "agent_managed"})
ALLOWED_CAPABILITY_STATES = frozenset({"supported", "unsupported", "unknown", "degraded", "agent_defined"})
ALLOWED_CAPABILITY_GROUPS = frozenset({"session", "input", "output", "interaction", "model"})
_AUTH_ALLOWED_FIELDS = frozenset({"type", "method", "label", "description", "hint"})
_MAX_MANIFEST_BYTES = 1_048_576
_REDACT_MARKERS = ("token", "secret", "password", "authorization", "apikey", "cookie", "credential", "privatekey")

_AGENT_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]*")
_COMMAND_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_PROFILE_ID_PATTERN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*")


def _agent_platform_key() -> str:
    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "x64"
    system = "windows" if os.name == "nt" else "macos" if os.sys.platform == "darwin" else "linux"
    return f"{system}-{arch}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact(value: Any) -> Any:
    """Recursively drop credentials from stored manifests and metadata."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]+", "", str(key).casefold())
            if any(marker in normalized for marker in _REDACT_MARKERS):
                continue
            result[str(key)] = _redact(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _clean_text(value: Any, *, max_len: int = 200) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_len]


def _validate_https_url(value: str, message: str, *, allow_loopback_http: bool = False) -> None:
    parsed = urllib.parse.urlparse(value)
    local_http = allow_loopback_http and parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not local_http:
        raise ValueError(message)
    if parsed.username or parsed.password:
        raise ValueError("URL must not embed credentials")


async def _validate_manifest_fetch_url(value: str) -> str:
    """Reject redirect and DNS targets that can reach local/private services."""
    _validate_https_url(value, "proposal_source_invalid: manifest URL must use HTTPS")
    parsed = urllib.parse.urlparse(value)
    hostname = str(parsed.hostname or "").strip().rstrip(".")
    if not hostname:
        raise ValueError("proposal_source_invalid: manifest URL must include a host")
    try:
        literal = ipaddress.ip_address(hostname)
        addresses = [literal]
    except ValueError:
        try:
            rows = await asyncio.to_thread(socket.getaddrinfo, hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ValueError("proposal_source_invalid: manifest host could not be resolved") from exc
        addresses = []
        for row in rows:
            try:
                addresses.append(ipaddress.ip_address(row[4][0]))
            except (ValueError, IndexError):
                continue
    if not addresses:
        raise ValueError("proposal_source_invalid: manifest host did not resolve to a usable address")
    if any(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        for address in addresses
    ):
        raise ValueError("proposal_source_invalid: manifest URL must not target a private or local address")
    return value


def _sanitize_capabilities(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for group, entries in value.items():
        group = str(group).strip().casefold()
        if group not in ALLOWED_CAPABILITY_GROUPS or not isinstance(entries, dict):
            continue
        clean_entries: dict[str, Any] = {}
        for feature, state in entries.items():
            feature = str(feature).strip()
            if not feature or len(feature) > 40:
                continue
            if isinstance(state, list):
                if group == "model" and feature == "cyreneManaged":
                    tokens = [str(item).strip() for item in state if re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", str(item).strip())]
                    clean_entries[feature] = tokens[:20]
                else:
                    clean_entries[feature] = [item for item in state if isinstance(item, str) and item in ALLOWED_CAPABILITY_STATES][:20]
            elif isinstance(state, str) and state in ALLOWED_CAPABILITY_STATES:
                clean_entries[feature] = state
            elif isinstance(state, bool):
                clean_entries[feature] = "supported" if state else "unsupported"
        if clean_entries:
            result[group] = clean_entries
    return result


def _sanitize_auth(value: Any) -> dict[str, Any]:
    """Keep only declarative, credential-free auth metadata.

    A strict allowlist drops credential-shaped or unknown fields (key, bearer,
    accessKey, token, ...) so they can never persist in or leak from a stored
    or returned manifest.
    """
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key, item in value.items():
        field = str(key).strip()
        if field not in _AUTH_ALLOWED_FIELDS or not isinstance(item, str):
            continue
        text = item.strip()[:200]
        if text:
            result[field] = text
    return result


def validate_agent_manifest(manifest: Any) -> dict[str, Any]:
    """Validate and normalize a ``cyrene.agent/v1`` manifest.

    Raises ``ValueError`` with a stable ``agent_manifest_invalid:`` prefix for
    every rejection.  The returned manifest only contains declarative,
    redacted fields safe to persist and render.
    """
    if not isinstance(manifest, dict):
        raise ValueError("agent_manifest_invalid: manifest must be a JSON object")
    if str(manifest.get("manifestApi") or manifest.get("apiVersion") or "") != AGENT_MANIFEST_API:
        raise ValueError(f"agent_manifest_invalid: manifestApi must be {AGENT_MANIFEST_API}")
    agent_id = _clean_text(manifest.get("agentId"), max_len=64)
    if not re.fullmatch(_AGENT_ID_PATTERN, agent_id):
        raise ValueError("agent_manifest_invalid: agentId must match [a-z0-9][a-z0-9._-]*")
    display_name = _clean_text(manifest.get("displayName"), max_len=80)
    if not display_name:
        raise ValueError("agent_manifest_invalid: displayName is required (max 80 chars)")
    version = _clean_text(manifest.get("version"), max_len=64)
    if not version or re.search(r"\s", version) or "/" in version or "\\" in version:
        raise ValueError("agent_manifest_invalid: version must be a compact version string")
    driver = _clean_text(manifest.get("driver"), max_len=32)
    if driver not in ALLOWED_DRIVERS:
        raise ValueError(f"agent_manifest_invalid: unsupported driver {driver!r}; supported drivers: {sorted(ALLOWED_DRIVERS)}")
    command = _clean_text(manifest.get("command"), max_len=64)
    if not re.fullmatch(_COMMAND_PATTERN, command):
        raise ValueError("agent_manifest_invalid: command must be a bare executable name (no path or arguments)")
    try:
        protocol_version = int(manifest.get("protocolVersion", 1))
    except (TypeError, ValueError):
        raise ValueError("agent_manifest_invalid: protocolVersion must be an integer")
    if protocol_version < 1:
        raise ValueError("agent_manifest_invalid: protocolVersion must be >= 1")
    for key in ("repository", "homepage"):
        value = _clean_text(manifest.get(key), max_len=512)
        if value:
            _validate_https_url(value, f"agent_manifest_invalid: {key} must be an HTTPS URL")
    model_access = manifest.get("modelAccess") if isinstance(manifest.get("modelAccess"), dict) else {}
    mode = str(model_access.get("mode") or "").strip()
    if mode not in ALLOWED_MODEL_ACCESS:
        mode = "cyrene_managed"
    normalized = {
        "manifestApi": AGENT_MANIFEST_API,
        "agentId": agent_id,
        "displayName": display_name,
        "version": version,
        "driver": driver,
        "command": command,
        "protocolVersion": protocol_version,
        "publisher": _clean_text(manifest.get("publisher"), max_len=80),
        "description": _clean_text(manifest.get("description"), max_len=500),
        "repository": _clean_text(manifest.get("repository"), max_len=512),
        "homepage": _clean_text(manifest.get("homepage"), max_len=512),
        "capabilities": _sanitize_capabilities(manifest.get("capabilities")),
        "modelAccess": {"mode": mode} if mode == "agent_managed" else {"mode": mode, "profileId": _clean_text(model_access.get("profileId"), max_len=64)},
        "auth": _sanitize_auth(manifest.get("auth")),
    }
    if normalized["modelAccess"].get("mode") == "cyrene_managed":
        profile_id = str(normalized["modelAccess"].get("profileId") or "")
        if profile_id and not re.fullmatch(_PROFILE_ID_PATTERN, profile_id):
            normalized["modelAccess"] = {"mode": "cyrene_managed", "profileId": "primary"}
        elif not profile_id:
            normalized["modelAccess"] = {"mode": "cyrene_managed", "profileId": "primary"}
    return normalized


def recommended_manifest(agent_id: str) -> dict[str, Any]:
    """Declarative manifest built from the Cyrene-recommended catalog profile."""
    profile = RECOMMENDED_AGENTS.get(agent_id)
    if not profile:
        raise ValueError(f"unknown recommended agent: {agent_id}")
    return validate_agent_manifest({
        "manifestApi": AGENT_MANIFEST_API,
        "agentId": profile["agentId"],
        "displayName": profile["displayName"],
        "version": profile["recommended_version"],
        "driver": profile["driver"],
        "command": profile["command"],
        "protocolVersion": profile.get("protocol_version", 1),
        "publisher": profile.get("publisher", "Cyrene"),
        "description": profile.get("description", ""),
        "repository": profile.get("repository", ""),
        "capabilities": profile.get("capabilities", {}),
        "modelAccess": {"mode": profile.get("default_model_access", "cyrene_managed"), "profileId": "primary"},
    })


def validate_proposal_source(source: Any) -> dict[str, Any]:
    """Validate the proposal source and return a normalized, redacted copy."""
    if not isinstance(source, dict):
        raise ValueError("proposal_source_invalid: source must be an object")
    source_type = str(source.get("type") or "").strip()
    if source_type == "manifest_url":
        url = _clean_text(source.get("url"), max_len=1024)
        if not url:
            raise ValueError("proposal_source_invalid: manifest_url requires a url")
        _validate_https_url(url, "proposal_source_invalid: manifest URL must use HTTPS")
        return {"type": "manifest_url", "url": url}
    if source_type == "inline":
        if not isinstance(source.get("manifest"), dict):
            raise ValueError("proposal_source_invalid: inline source requires a manifest object")
        return {"type": "inline"}
    raise ValueError("proposal_source_invalid: source.type must be manifest_url or inline")


async def fetch_manifest_from_source(source: dict[str, Any], inline_manifest: Any = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch (for manifest_url) and normalize the proposal source payload.

    ``inline_manifest`` carries the raw manifest for ``inline`` sources after
    source normalization (which intentionally drops the payload from the
    persisted source descriptor).
    """
    if source.get("type") == "manifest_url":
        url = str(source.get("url") or "")
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, read=30), follow_redirects=False) as client:
            current_url = url
            content = bytearray()
            for _redirect in range(6):
                # Re-validate every hop (including redirect targets) so a
                # manifest source can never reach a private/local address.
                await _validate_manifest_fetch_url(current_url)
                async with client.stream("GET", current_url) as response:
                    if bool(getattr(response, "is_redirect", False)):
                        location = response.headers.get("location")
                        if not location:
                            raise ValueError("agent_manifest_invalid: manifest redirect omitted Location")
                        current_url = urllib.parse.urljoin(current_url, location)
                        continue
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > _MAX_MANIFEST_BYTES:
                            raise ValueError("agent_manifest_invalid: manifest exceeds the 1 MiB size limit")
                    break
            else:
                raise ValueError("agent_manifest_invalid: manifest URL redirected too many times")
        try:
            manifest = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("agent_manifest_invalid: manifest URL did not return valid JSON") from exc
        return manifest, {"type": "manifest_url", "url": url}
    return inline_manifest, {"type": "inline"}


# ---------------------------------------------------------------------------
# Proposals
# ---------------------------------------------------------------------------


def list_agent_proposals() -> list[dict[str, Any]]:
    value = get_setting(PROPOSALS_KEY, [])
    return value if isinstance(value, list) else []


def _save_agent_proposals(proposals: list[dict[str, Any]]) -> None:
    set_setting(PROPOSALS_KEY, proposals)


def get_agent_proposal(proposal_id: str) -> dict[str, Any] | None:
    return next((proposal for proposal in list_agent_proposals() if str(proposal.get("proposalId")) == proposal_id), None)


async def create_agent_install_proposal(source: Any, requested_version: str = "", *, actor: str = "user") -> dict[str, Any]:
    """Validate a manifest source and create a pending install proposal.

    Idempotent for the same source/agent/version: a pending proposal is
    returned as-is, and an already installed Agent returns the existing
    installation instead of creating a duplicate proposal.
    """
    normalized_source = validate_proposal_source(source)
    inline_manifest = source.get("manifest") if normalized_source.get("type") == "inline" else None
    raw_manifest, persist_source = await fetch_manifest_from_source(normalized_source, inline_manifest=inline_manifest)
    manifest = validate_agent_manifest(raw_manifest)
    agent_id = manifest["agentId"]
    version = manifest["version"]
    if persist_source.get("type") == "manifest_url":
        source_key = f"url:{agent_id}:{version}:" + str(persist_source.get("url") or "")
    else:
        digest = hashlib.sha256(json.dumps(_redact(manifest), sort_keys=True).encode("utf-8")).hexdigest()
        source_key = f"inline:{agent_id}:{version}:{digest}"

    existing_install = find_installation_by_agent_id(agent_id)
    if existing_install and str(existing_install.get("version")) == version:
        return {
            "ok": True,
            "already_installed": True,
            "installation": agent_card(existing_install),
            "requiresConfirmation": False,
        }

    for proposal in list_agent_proposals():
        if proposal.get("sourceKey") == source_key:
            return proposal_response(proposal, already_pending=True)

    proposal = {
        "proposalId": "agent_prop_" + uuid.uuid4().hex[:12],
        "agentId": agent_id,
        "displayName": manifest["displayName"],
        "version": version,
        "source": persist_source,
        "sourceKey": source_key,
        # Only the catalog-controlled recommended install path may grant
        # cyrene_recommended trust. An external manifest never inherits trust
        # merely by claiming a recommended Agent id.
        "sourceTrust": "external_unverified",
        "requiresConfirmation": True,
        "manifest": manifest,
        "requestedVersion": _clean_text(requested_version, max_len=64),
        "status": "pending",
        "actor": actor,
        "createdAt": _now(),
        "updatedAt": _now(),
    }
    proposals = list_agent_proposals()
    proposals.append(proposal)
    _save_agent_proposals(proposals)
    audit(actor, "agent.proposal.create", f"agent:{agent_id}", {"proposalId": proposal["proposalId"], "sourceTrust": proposal["sourceTrust"]})
    return proposal_response(proposal)


def proposal_response(proposal: dict[str, Any], *, already_pending: bool = False) -> dict[str, Any]:
    manifest = proposal.get("manifest") or {}
    return {
        "ok": True,
        "proposalId": proposal["proposalId"],
        "agentId": proposal["agentId"],
        "displayName": proposal["displayName"],
        "version": proposal["version"],
        "sourceTrust": proposal["sourceTrust"],
        "requiresConfirmation": True,
        "status": proposal.get("status", "pending"),
        "alreadyPending": already_pending,
        "source": dict(proposal.get("source") or {}),
        "inspect": {
            "driver": manifest.get("driver", ""),
            "command": manifest.get("command", ""),
            "version": manifest.get("version", ""),
            "protocolVersion": manifest.get("protocolVersion", 1),
            "checksums": {},
            "publisher": manifest.get("publisher", ""),
            "capabilities": manifest.get("capabilities", {}),
        },
    }


def mark_proposal_confirmed(proposal_id: str) -> bool:
    proposals = list_agent_proposals()
    changed = False
    for proposal in proposals:
        if str(proposal.get("proposalId")) == proposal_id and proposal.get("status") == "pending":
            proposal["status"] = "confirmed"
            proposal["updatedAt"] = _now()
            changed = True
    if changed:
        _save_agent_proposals(proposals)
    return changed


# ---------------------------------------------------------------------------
# Installation records
# ---------------------------------------------------------------------------


def list_agent_installations() -> list[dict[str, Any]]:
    value = get_setting(INSTALLATIONS_KEY, [])
    if not isinstance(value, list):
        return []
    # ``pending_transport`` was written by the UI-only prototype before the
    # ACP stdio runtime landed.  Keeping it makes working installations look
    # permanently unavailable, so migrate old records on first read.
    changed = False
    for record in value:
        if isinstance(record, dict) and record.get("runtime_state") in {None, "", "pending_transport"}:
            record["runtime_state"] = "not_started"
            changed = True
    if changed:
        _save_agent_installations(value)
    return value


def _save_agent_installations(records: list[dict[str, Any]]) -> None:
    set_setting(INSTALLATIONS_KEY, records)


def get_agent_installation(installation_id: str) -> dict[str, Any] | None:
    return next((record for record in list_agent_installations() if str(record.get("installation_id")) == installation_id), None)


def find_installation_by_agent_id(agent_id: str) -> dict[str, Any] | None:
    return next((record for record in list_agent_installations() if str(record.get("agent_id")) == agent_id), None)


def update_installation_record(record: dict[str, Any]) -> None:
    records = list_agent_installations()
    for index, item in enumerate(records):
        if str(item.get("installation_id")) == record.get("installation_id"):
            records[index] = record
            break
    else:
        records.append(record)
    _save_agent_installations(records)


def register_agent_installation(
    *,
    agent_id: str,
    manifest: dict[str, Any],
    source: dict[str, Any],
    source_trust: str,
    recommended: bool,
    proposal_id: str = "",
    actor: str = "user",
) -> dict[str, Any]:
    """Persist or upgrade an installation record (idempotent by version)."""
    existing = find_installation_by_agent_id(agent_id)
    if existing:
        if str(existing.get("version") or "") == str(manifest.get("version") or ""):
            return existing
        existing.update({
            "display_name": manifest["displayName"],
            "version": manifest["version"],
            "driver": manifest["driver"],
            "protocol_version": manifest.get("protocolVersion", 1),
            "publisher": manifest.get("publisher", ""),
            "description": manifest.get("description", ""),
            "command": manifest["command"],
            "source": _redact(source),
            "source_trust": source_trust,
            "recommended": bool(recommended),
            "manifest": manifest,
            "capabilities": manifest.get("capabilities", {}),
            "model_access": dict(manifest.get("modelAccess") or existing.get("model_access") or {"mode": "cyrene_managed", "profileId": "primary"}),
            "proposal_id": proposal_id or "",
            "updated_at": _now(),
            "install_state": "installed",
            "runtime_state": "not_started",
            "last_error": "",
        })
        update_installation_record(existing)
        audit(actor, "agent.upgrade", f"agent:{agent_id}", {"installation_id": existing["installation_id"], "version": existing["version"]})
        return existing
    record = {
        "installation_id": f"agent_{agent_id}_default",
        "agent_id": agent_id,
        "display_name": manifest["displayName"],
        "version": manifest["version"],
        "driver": manifest["driver"],
        "protocol_version": manifest.get("protocolVersion", 1),
        "publisher": manifest.get("publisher", ""),
        "description": manifest.get("description", ""),
        "command": manifest["command"],
        "source": _redact(source),
        "source_trust": source_trust,
        "recommended": bool(recommended),
        "manifest": manifest,
        "capabilities": manifest.get("capabilities", {}),
        "model_access": dict(manifest.get("modelAccess") or {"mode": "cyrene_managed", "profileId": "primary"}),
        "proposal_id": proposal_id or "",
        "installed_at": _now(),
        "updated_at": _now(),
        "install_state": "installed",
        "runtime_state": "not_started",
        "enabled": True,
        "auth_state": "not_configured",
        "health": "unknown",
        "last_error": "",
        "last_started_at": "",
    }
    records = list_agent_installations()
    records.append(record)
    _save_agent_installations(records)
    audit(actor, "agent.install", f"agent:{agent_id}", {"installation_id": record["installation_id"], "version": record["version"], "source_trust": source_trust})
    return record


def delete_agent_installation(installation_id: str) -> bool:
    records = list_agent_installations()
    remaining = [record for record in records if str(record.get("installation_id")) != installation_id]
    if len(remaining) == len(records):
        return False
    _save_agent_installations(remaining)
    return True


def _capability_summary(capabilities: dict[str, Any]) -> dict[str, Any]:
    supported = 0
    unknown = 0
    total = 0
    for entries in capabilities.values():
        if not isinstance(entries, dict):
            continue
        for state in entries.values():
            if isinstance(state, str) and state in ALLOWED_CAPABILITY_STATES:
                total += 1
                if state == "supported":
                    supported += 1
                elif state == "unknown":
                    unknown += 1
            elif isinstance(state, list):
                total += 1
                supported += 1
    return {"source": "declared_profile", "supported": supported, "unknown": unknown, "total": total}


def agent_card(record: dict[str, Any]) -> dict[str, Any]:
    source_trust = str(record.get("source_trust") or "external_unverified")
    return {
        "installationId": record.get("installation_id", ""),
        "agentId": record.get("agent_id", ""),
        "displayName": record.get("display_name", ""),
        "version": record.get("version", ""),
        "recommended": bool(record.get("recommended")),
        "sourceTrust": source_trust,
        "installState": "installed",
        "enabled": bool(record.get("enabled", True)),
        "runtimeState": record.get("runtime_state") or "not_started",
        "authState": record.get("auth_state", "not_configured"),
        "health": record.get("health", "unknown"),
        "driver": record.get("driver", "acp_stdio"),
        "protocolVersion": record.get("protocol_version", 1),
        "modelAccess": dict(record.get("model_access") or {"mode": "cyrene_managed", "profileId": "primary"}),
        "capabilities": dict(record.get("capabilities") or {}),
        "capabilitiesRevision": int(record.get("capabilities_revision") or 1),
        "negotiatedCapabilities": dict(record.get("negotiated_capabilities") or {}),
        "publisher": record.get("publisher", ""),
        "repository": (record.get("manifest") or {}).get("repository", ""),
        "source": dict(record.get("source") or {}),
        "verified": source_trust in {"cyrene_recommended", "external_verified"},
        "checksums": {"sha256": record.get("checksum")} if record.get("checksum") else {},
        "capabilitySummary": _capability_summary(record.get("capabilities") or {}),
    }


def recommended_agent_cards() -> list[dict[str, Any]]:
    installed = {str(record.get("agent_id")): record for record in list_agent_installations()}
    cards = []
    for agent_id in RECOMMENDED_AGENT_ORDER:
        spec = RECOMMENDED_AGENTS.get(agent_id)
        if not spec:
            continue
        record = installed.get(agent_id)
        if record:
            install_state = "installed" if str(record.get("version")) == str(spec.get("recommended_version") or "") else "upgrade_available"
        else:
            install_state = "available"
        cards.append({
            "agentId": agent_id,
            "displayName": spec["displayName"],
            "description": spec.get("description", ""),
            "recommended": True,
            "driver": spec["driver"],
            "protocolVersion": spec.get("protocol_version", 1),
            "version": str(spec.get("recommended_version") or ""),
            "versionSource": spec.get("version_source", "declared_profile"),
            "publisher": spec.get("publisher", ""),
            "installState": install_state,
            "installationId": record.get("installation_id", "") if record else "",
            "enabled": bool(record.get("enabled", True)) if record else False,
            "runtimeState": record.get("runtime_state", "") if record else "",
            "authState": record.get("auth_state", "not_configured") if record else "",
            "capabilities": spec.get("capabilities", {}),
            "defaultModelAccess": spec.get("default_model_access", "cyrene_managed"),
            "checksums": {
                "sha256": ((spec.get("distribution") or {}).get("platforms") or {}).get(
                    _agent_platform_key(), {}
                ).get("sha256")
            } if str((spec.get("distribution") or {}).get("kind") or "") == "binary" else {},
            "verified": True,
        })
    return cards


def get_agent_detail(installation_id: str) -> dict[str, Any]:
    record = get_agent_installation(installation_id)
    if record is None:
        raise KeyError("Agent installation not found")
    return {
        **agent_card(record),
        "state": record.get("install_state", "installed"),
        "installedAt": record.get("installed_at", ""),
        "command": record.get("command", ""),
        "capabilities": record.get("capabilities", {}),
        "negotiatedCapabilities": record.get("negotiated_capabilities", {}),
        "defaultModelAccess": dict(record.get("model_access") or {"mode": "cyrene_managed", "profileId": "primary"}),
        "auth": {
            "state": record.get("auth_state", "not_configured"),
            "method": (record.get("manifest") or {}).get("auth", {}),
        },
        "runtime": {
            "transport": record.get("driver", "acp_stdio"),
            "state": record.get("runtime_state") or "not_started",
            "started": False,
            "pid": None,
            "lastStartedAt": record.get("last_started_at", ""),
            "reason": "starts_on_demand",
        },
        "diagnostics": {
            "lastErrors": [record["last_error"]] if record.get("last_error") else [],
            "probe": None,
            "noteCode": "starts_on_demand",
        },
    }


def update_agent_settings(installation_id: str, changes: dict[str, Any], *, actor: str = "user") -> dict[str, Any]:
    record = get_agent_installation(installation_id)
    if record is None:
        raise KeyError("Agent installation not found")
    if "enabled" in changes:
        if not isinstance(changes["enabled"], bool):
            raise ValueError("agent_settings_invalid: enabled must be a boolean")
        record["enabled"] = changes["enabled"]
    if "modelAccess" in changes:
        model_access = changes["modelAccess"]
        if not isinstance(model_access, dict):
            raise ValueError("agent_settings_invalid: modelAccess must be an object")
        mode = str(model_access.get("mode") or "").strip()
        if mode not in ALLOWED_MODEL_ACCESS:
            raise ValueError("agent_settings_invalid: modelAccess.mode must be cyrene_managed or agent_managed")
        if mode == "cyrene_managed":
            profile_id = str(model_access.get("profileId") or "primary").strip()
            if not re.fullmatch(_PROFILE_ID_PATTERN, profile_id):
                raise ValueError("agent_settings_invalid: modelAccess.profileId is invalid")
            if profile_id != "primary":
                raise ValueError("agent_settings_invalid: only the primary Cyrene model profile is currently supported")
            record["model_access"] = {"mode": mode, "profileId": profile_id}
        else:
            record["model_access"] = {"mode": mode}
    record["updated_at"] = _now()
    update_installation_record(record)
    audit(actor, "agent.settings", f"agent:{installation_id}", {
        "enabled": record.get("enabled"),
        "model_access": {key: value for key, value in record.get("model_access", {}).items()},
    })
    return get_agent_detail(installation_id)


# ---------------------------------------------------------------------------
# Runtime-facing status helpers
# ---------------------------------------------------------------------------


def _require_installation(installation_id: str) -> dict[str, Any]:
    record = get_agent_installation(installation_id)
    if record is None:
        raise KeyError("Agent installation not found")
    return record


async def auth_agent(installation_id: str, action: str) -> dict[str, Any]:
    record = _require_installation(installation_id)
    from cyrene.agent_runtime import AgentRuntimeError, AgentStartRequest, get_acp_runtime_service

    recovery_record = dict(record)
    # Authentication is the recovery path for expired/failed credentials, so
    # it must be able to start the Agent even when normal prompts are gated.
    recovery_record["auth_state"] = "not_configured"
    connection = await get_acp_runtime_service().driver().connect(AgentStartRequest(
        installation_id=installation_id,
        settings={"installation": recovery_record},
        model_access=record.get("model_access"),
    ))
    try:
        await connection._ensure_initialized()
        if action == "logout":
            auth_caps = connection.transport.agent_capabilities.get("auth")
            if not isinstance(auth_caps, dict) or "logout" not in auth_caps:
                raise AgentRuntimeError("capability_missing", "Agent did not advertise ACP logout support")
            await connection.transport.request("logout", {})
            state = "not_configured"
        else:
            method = next(
                (item for item in connection.transport.auth_methods if str(item.get("type") or "agent") != "terminal"),
                None,
            )
            method_id = str((method or {}).get("id") or "").strip()
            if not method_id:
                raise AgentRuntimeError("capability_missing", "Agent did not advertise an in-protocol login method")
            await connection.transport.request("authenticate", {"methodId": method_id})
            state = "connected"
        record["auth_state"] = state
        record["updated_at"] = _now()
        update_installation_record(record)
        return {"ok": True, "agentId": record.get("agent_id", ""), "installationId": installation_id, "authState": state}
    finally:
        await connection.close()


async def probe_agent(installation_id: str) -> dict[str, Any]:
    record = _require_installation(installation_id)
    try:
        from cyrene.agent_runtime import AgentStartRequest, get_acp_runtime_service

        connection = await get_acp_runtime_service().driver().connect(AgentStartRequest(
            installation_id=installation_id,
            settings={"installation": record},
            model_access=record.get("model_access"),
        ))
        try:
            handshake = await connection.transport.initialize()
        finally:
            await connection.close()
    except Exception as exc:
        kind = str(getattr(exc, "kind", "") or "agent_crashed")
        record["health"] = "unhealthy"
        record["last_error"] = str(exc)
        record["updated_at"] = _now()
        update_installation_record(record)
        return {
            "ok": False,
            "error": str(exc),
            "failureKind": kind,
            "agentId": record.get("agent_id", ""),
            "installationId": installation_id,
            "probe": None,
            "runtimeState": record.get("runtime_state", "unknown"),
        }
    from cyrene.agent_runtime.capabilities import merge_capabilities

    raw_capabilities = handshake.get("agentCapabilities", {})
    session_capabilities = raw_capabilities.get("sessionCapabilities") if isinstance(raw_capabilities, dict) else {}
    prompt_capabilities = raw_capabilities.get("promptCapabilities") if isinstance(raw_capabilities, dict) else {}
    probed_capabilities = {
        "session": {
            "load": "supported" if raw_capabilities.get("loadSession") is True else "unsupported",
            "fork": "supported" if isinstance(session_capabilities, dict) and "fork" in session_capabilities else "unsupported",
            "close": "supported" if isinstance(session_capabilities, dict) and "close" in session_capabilities else "unsupported",
        },
        "input": {
            "text": "supported",
            "image": "supported" if isinstance(prompt_capabilities, dict) and prompt_capabilities.get("image") is True else "unsupported",
        },
    }
    record["health"] = "healthy"
    record["runtime_state"] = "not_started"
    record["negotiated_capabilities"] = raw_capabilities
    record["capabilities"] = merge_capabilities(record.get("capabilities"), raw_capabilities, probed_capabilities)
    record["capabilities_revision"] = int(record.get("capabilities_revision") or 0) + 1
    record["last_error"] = ""
    record["updated_at"] = _now()
    update_installation_record(record)
    return {
        "ok": True,
        "agentId": record.get("agent_id", ""),
        "installationId": installation_id,
        "probe": {"driver": record.get("driver", ""), "command": record.get("command", ""), "handshake": handshake},
        "runtimeState": record.get("runtime_state", "not_started"),
        "agent": get_agent_detail(installation_id),
    }


async def restart_agent(installation_id: str) -> dict[str, Any]:
    record = _require_installation(installation_id)
    from cyrene.agent_runtime import get_process_manager

    await get_process_manager().release(installation_id)
    record["runtime_state"] = "not_started"
    record["health"] = "unknown"
    record["updated_at"] = _now()
    update_installation_record(record)
    return {"ok": True, "agentId": record.get("agent_id", ""), "installationId": installation_id, "runtimeState": "not_started"}


def diagnostics_placeholder(installation_id: str) -> dict[str, Any]:
    detail = get_agent_detail(installation_id)
    return {
        "ok": True,
        "installationId": installation_id,
        "agentId": detail["agentId"],
        "runtimeState": detail["runtime"]["state"],
        "authState": detail["authState"],
        "lastErrors": detail["diagnostics"]["lastErrors"],
        "probe": None,
        "noteCode": "starts_on_demand",
    }


def audit(actor: str, action: str, target: str, detail: dict[str, Any]) -> None:
    from cyrene.extensions.service import audit_extension_event

    audit_extension_event(actor, action, target, detail)


__all__ = [
    "agent_card", "auth_agent", "create_agent_install_proposal", "delete_agent_installation",
    "diagnostics_placeholder", "find_installation_by_agent_id", "get_agent_detail",
    "get_agent_installation", "get_agent_proposal", "list_agent_installations",
    "list_agent_proposals", "probe_agent", "recommended_agent_cards",
    "recommended_manifest", "register_agent_installation", "restart_agent",
    "update_agent_settings", "update_installation_record", "validate_agent_manifest",
]
