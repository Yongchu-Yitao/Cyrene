"""Driver protocol and registry for the unified Agent Runtime.

Routes and UI never branch on ``opencode`` / ``codex`` / ``pi`` product names;
they ask the registry for a driver by name and speak the internal protocol
below (handoff §5/§20).  Phase 1 ships the ACP stdio driver in a later step;
this module defines the stable internal SPI, a typed registry, and a uniform
error boundary (unknown driver → ``protocol_mismatch``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Protocol, runtime_checkable

from cyrene.agent_runtime.errors import AgentRuntimeError
from cyrene.agent_runtime.models import AgentDescriptor


@dataclass(frozen=True)
class AgentStartRequest:
    """Normalized start context handed to a driver's ``connect``.

    Never carries long-lived credentials; gateway short-term tokens and
    Agent-owned credentials are injected by the model binders separately.
    """

    installation_id: str
    settings: dict[str, Any] = field(default_factory=dict)
    model_access: dict[str, Any] | None = None
    chat_id: str = ""
    run_id: str = ""
    workspace_path: str = ""


@runtime_checkable
class AgentDriver(Protocol):
    """Driver side: inspect an installation and create a connection."""

    async def inspect(self, installation: dict[str, Any]) -> AgentDescriptor: ...

    async def connect(self, request: AgentStartRequest) -> "AgentConnection": ...


@runtime_checkable
class AgentConnection(Protocol):
    """Connection side: session lifecycle, prompt, interaction, events.

    Every optional operation must be gated by a capability check first;
    unsupported operations return the stable ``capability_missing`` failure
    kind instead of failing silently (handoff §5).
    """

    async def authenticate(self, request: dict[str, Any]) -> dict[str, Any]: ...

    async def open_session(self, request: dict[str, Any]) -> dict[str, Any]: ...

    async def load_session(self, external_session_id: str) -> dict[str, Any]: ...

    async def prompt(self, request: dict[str, Any]) -> None: ...

    async def respond_permission(self, request_id: str, option_id: str) -> None: ...

    async def respond_elicitation(self, request_id: str, value: object) -> None: ...

    async def steer(self, request: dict[str, Any]) -> None: ...

    async def cancel(self, run_id: str) -> None: ...

    async def close(self) -> None: ...

    def events(self) -> AsyncIterator[dict[str, Any]]: ...


@dataclass(frozen=True)
class DriverInfo:
    name: str
    protocol_version: int = 1
    description: str = ""


class DriverRegistry:
    """Named driver factories; connection creation never branches on product."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], Any]] = {}
        self._info: dict[str, DriverInfo] = {}

    def register(
        self,
        name: str,
        factory: Callable[[], Any],
        *,
        protocol_version: int = 1,
        description: str = "",
    ) -> None:
        if not name or not callable(factory):
            raise ValueError("driver name and factory are required")
        self._factories[name] = factory
        self._info[name] = DriverInfo(
            name=name,
            protocol_version=protocol_version,
            description=description,
        )

    def create(self, name: str) -> Any:
        factory = self._factories.get(name)
        if factory is None:
            raise AgentRuntimeError(
                kind="protocol_mismatch",
                message=f"no agent driver registered for {name!r}",
            )
        return factory()

    def get(self, name: str) -> Any:
        return self.create(name)

    def names(self) -> list[str]:
        return sorted(self._factories)

    def info(self, name: str) -> DriverInfo | None:
        return self._info.get(name)

    def contains(self, name: str) -> bool:
        return name in self._factories


_DEFAULT_REGISTRY = DriverRegistry()


def default_registry() -> DriverRegistry:
    return _DEFAULT_REGISTRY


def register_driver(
    name: str,
    factory: Callable[[], Any],
    *,
    protocol_version: int = 1,
    description: str = "",
) -> None:
    _DEFAULT_REGISTRY.register(
        name,
        factory,
        protocol_version=protocol_version,
        description=description,
    )


def get_driver(name: str) -> Any:
    return _DEFAULT_REGISTRY.create(name)


def driver_names() -> list[str]:
    return _DEFAULT_REGISTRY.names()
