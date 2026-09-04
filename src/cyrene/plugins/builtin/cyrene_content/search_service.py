"""SearXNG subprocess manager — launches SimpleXNG as a managed child process.

No Docker required. SimpleXNG is a standalone pip-installable package that
vendors SearXNG and runs it via waitress on a configurable port.
"""

import asyncio
import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import getproxies

import httpx

from cyrene.config import DATA_DIR, INSTALL_RESOURCES_DIR, TEMP_DIR

from .runtime_config import (
    SEARCH_PROXY,
    SEARXNG_AUTO_START,
    SEARXNG_HOST,
    SEARXNG_PORT,
    SEARXNG_URL,
)

logger = logging.getLogger(__name__)

_HEALTH_CHECK_TIMEOUT = 30.0
_HEALTH_CHECK_INTERVAL = 0.5
_SIMPLEXNG_SETTINGS_PATH = DATA_DIR / "simplexng_settings.yml"

# Keep the built-in global engines, but also enable search engines whose public
# web endpoints are normally reachable from mainland China without an API key.
# These entries are merged into SimpleXNG's vendored SearXNG defaults by name.
_CHINA_SEARCH_ENGINE_OVERRIDES: tuple[dict[str, object], ...] = (
    {
        "name": "baidu",
        "disabled": False,
        "timeout": 10.0,
    },
    {
        "name": "sogou",
        "disabled": False,
        "timeout": 10.0,
    },
    {
        "name": "bing",
        "disabled": False,
        "timeout": 10.0,
        # SimpleXNG 0.1.x vendors a Bing engine whose base_url is the complete
        # search endpoint rather than only the origin.
        "base_url": "https://cn.bing.com/search",
    },
)


def _is_windows_arm() -> bool:
    return sys.platform == "win32" and platform_machine() in {"arm64", "aarch64"}


def _woa_simplexng_sidecar() -> Path | None:
    if not _is_windows_arm():
        return None
    override = os.environ.get("CYRENE_X64_SIMPLEXNG_SIDECAR", "").strip()
    candidate = (
        Path(override)
        if override
        else Path(INSTALL_RESOURCES_DIR) / "x64-sidecars" / "simplexng" / "CyreneSimpleXNG.exe"
    )
    return candidate if candidate.is_file() else None


class SearXNGManager:
    """Manage a SimpleXNG subprocess lifecycle."""

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._url: str = ""
        self._external = False

    @property
    def url(self) -> str:
        """The base URL of the running SimpleXNG instance, e.g. http://127.0.0.1:8888."""
        return self._url

    @property
    def is_running(self) -> bool:
        if self._external:
            return bool(self._url)
        return self._process is not None and self._process.poll() is None

    def start(self, port: int = 8888, host: str = "127.0.0.1") -> str:
        """Launch SimpleXNG and wait until it is ready to serve requests.

        Returns the base URL on success.  Raises RuntimeError if the process
        fails to start or doesn't become healthy within the timeout.
        """
        if self.is_running:
            logger.info("SimpleXNG already running at %s", self._url)
            return self._url

        external_url = str(SEARXNG_URL or "").strip().rstrip("/")
        if external_url:
            self._external = True
            self._url = external_url
            logger.info("Using external SearXNG at %s", self._url)
            return self._url

        self._external = False
        requested_port = port
        if not _is_port_available(host, port):
            port = _find_available_port(host)
            logger.warning(
                "SimpleXNG port %d is already occupied; using fallback port %d",
                requested_port,
                port,
            )
        self._url = f"http://{host}:{port}"

        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        fd, log_path = tempfile.mkstemp(prefix="simplexng_", suffix=".log", dir=TEMP_DIR)
        os.close(fd)
        self._stderr_path = log_path

        try:
            settings_path = _write_simplexng_settings(port, host)
            launch_cmd = _build_simplexng_launch_cmd(port, host, settings_path=settings_path)
            env = _build_simplexng_env(settings_path)
            with open(log_path, "w") as stderr_file:
                self._process = subprocess.Popen(
                    launch_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=stderr_file,
                    env=env,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
                )
        except FileNotFoundError:
            raise RuntimeError(
                "SimpleXNG runtime is unavailable (the WoA x64 sidecar may be missing)"
            ) from None
        except Exception as exc:
            raise RuntimeError(
                f"Failed to launch SimpleXNG: {exc}"
            ) from exc

        logger.info("SimpleXNG launching on %s (pid=%d) ...", self._url, self._process.pid)

        if not self._wait_ready():
            self._dump_stderr()
            self.stop()
            raise RuntimeError(
                f"SimpleXNG did not become healthy within {_HEALTH_CHECK_TIMEOUT}s"
            )

        logger.info("SimpleXNG ready at %s", self._url)
        return self._url

    def stop(self) -> None:
        """Terminate the SimpleXNG subprocess gracefully, then force-kill."""
        if self._external:
            self._external = False
            self._url = ""
            return
        if self._process is None:
            return
        proc, self._process = self._process, None
        self._url = ""

        if proc.poll() is not None:
            logger.info("SimpleXNG process already exited (rc=%d)", proc.returncode)
            return

        logger.info("Stopping SimpleXNG (pid=%d)...", proc.pid)
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("SimpleXNG did not exit gracefully, force-killing")
                proc.kill()
                proc.wait(timeout=3)
        except Exception as exc:
            logger.warning("Error stopping SimpleXNG: %s", exc)

    def _dump_stderr(self) -> None:
        """Log the contents of the stderr capture file."""
        path = getattr(self, "_stderr_path", None)
        if not path:
            return
        try:
            text = open(path).read()
            if text.strip():
                logger.error("SimpleXNG stderr (%s):\n%s", path, text[-4000:])
        except Exception:
            pass

    def _wait_ready(self) -> bool:
        """Poll the local HTTP endpoint until the server responds 200."""
        deadline = time.monotonic() + _HEALTH_CHECK_TIMEOUT
        url = f"{self._url}/"

        while time.monotonic() < deadline:
            if self._process and self._process.poll() is not None:
                logger.error("SimpleXNG exited prematurely (rc=%d)", self._process.returncode)
                return False
            try:
                r = httpx.get(url, timeout=3.0, trust_env=False)
                if r.status_code == 200:
                    # A different process may have answered on the target port.
                    # Confirm our child is still alive before accepting readiness.
                    time.sleep(0.1)
                    return self._process is not None and self._process.poll() is None
            except Exception:
                pass
            time.sleep(_HEALTH_CHECK_INTERVAL)

        return False


_manager: SearXNGManager | None = None


def get_manager() -> SearXNGManager:
    """Return the module-level singleton SearXNGManager."""
    global _manager
    if _manager is None:
        _manager = SearXNGManager()
    return _manager


def _enable_china_search_engines(settings: dict[str, object]) -> None:
    """Enable the no-key mainland search pool in a SearXNG settings mapping."""
    configured = settings.setdefault("engines", [])
    if not isinstance(configured, list):
        configured = []
        settings["engines"] = configured

    by_name = {
        str(item.get("name") or ""): item
        for item in configured
        if isinstance(item, dict)
    }
    for override in _CHINA_SEARCH_ENGINE_OVERRIDES:
        name = str(override["name"])
        existing = by_name.get(name)
        if existing is None:
            existing = {}
            configured.append(existing)
            by_name[name] = existing
        existing.update(override)


def _write_simplexng_settings(port: int, host: str) -> Path:
    """Write the SimpleXNG settings file managed by Cyrene."""
    proxy_url = _get_effective_search_proxy()
    sidecar = _woa_simplexng_sidecar()
    if sidecar is not None:
        _SIMPLEXNG_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            [str(sidecar), "--cyrene-prepare-settings"],
            input=json.dumps({
                "path": str(_SIMPLEXNG_SETTINGS_PATH),
                "port": port,
                "host": host,
                "secret_key": secrets.token_hex(16),
                "proxy_url": proxy_url,
                "engine_overrides": list(_CHINA_SEARCH_ENGINE_OVERRIDES),
            }),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0 or not _SIMPLEXNG_SETTINGS_PATH.is_file():
            raise RuntimeError(
                (completed.stderr or completed.stdout or "SimpleXNG sidecar could not prepare settings").strip()
            )
        return _SIMPLEXNG_SETTINGS_PATH
    if _is_windows_arm():
        raise FileNotFoundError("Windows ARM x64 SimpleXNG sidecar is missing")
    try:
        import yaml
        from simplexng.settings import get_bundled_template

        template_path = get_bundled_template()
    except Exception as exc:
        raise RuntimeError(f"Could not locate SimpleXNG settings template: {exc}") from exc

    settings = yaml.safe_load(Path(template_path).read_text(encoding="utf-8"))
    settings["server"]["port"] = port
    settings["server"]["bind_address"] = host
    settings["server"]["secret_key"] = secrets.token_hex(16)

    _enable_china_search_engines(settings)

    formats = settings.setdefault("search", {}).setdefault("formats", [])
    if "json" not in formats:
        formats.append("json")

    outgoing = settings.setdefault("outgoing", {})
    if proxy_url:
        outgoing["proxies"] = {"all://": [proxy_url]}
        outgoing["extra_proxy_timeout"] = 10
        outgoing["request_timeout"] = max(float(outgoing.get("request_timeout") or 3.0), 15.0)
    else:
        outgoing.pop("proxies", None)
        outgoing.pop("extra_proxy_timeout", None)

    _SIMPLEXNG_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "# Generated by Cyrene. Do not edit while Cyrene is running.\n"
        f"# Port: {port}, Host: {host}\n"
        f"# Proxy: {'configured' if proxy_url else 'not configured'}\n\n"
        f"{yaml.dump(settings, default_flow_style=False, sort_keys=False)}"
    )
    _SIMPLEXNG_SETTINGS_PATH.write_text(content, encoding="utf-8")
    return _SIMPLEXNG_SETTINGS_PATH


def _build_simplexng_env(settings_path: Path) -> dict[str, str]:
    """Build environment for the SimpleXNG child process."""
    env = os.environ.copy()
    env["SEARXNG_SETTINGS_PATH"] = str(settings_path)
    env["CYRENE_SIMPLEXNG_PARENT_PID"] = str(os.getpid())
    proxy_url = _get_effective_search_proxy()
    proxy_keys = (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "all_proxy",
    )
    if proxy_url:
        for key in proxy_keys:
            env[key] = proxy_url
    else:
        # getproxies() may discover an inherited desktop proxy that is no
        # longer reachable.  Once it has been rejected above, do not let the
        # child process accidentally inherit and keep using it.
        for key in proxy_keys:
            env.pop(key, None)
    env["NO_PROXY"] = _merge_no_proxy(env.get("NO_PROXY") or env.get("no_proxy") or "")
    env["no_proxy"] = env["NO_PROXY"]
    return env


def _get_effective_search_proxy() -> str:
    """Return the configured or system proxy if it is reachable."""
    from cyrene.platform.network_proxy import scoped_proxy_url

    scoped_proxy = scoped_proxy_url("search")
    if scoped_proxy:
        # The user explicitly selected this proxy for search. Fail closed when
        # it is unavailable instead of silently leaking the request to a
        # direct/system route.
        return scoped_proxy
    proxy_url = (SEARCH_PROXY or "").strip()
    if not proxy_url:
        proxies = getproxies()
        proxy_url = (
            proxies.get("https")
            or proxies.get("http")
            or proxies.get("all")
            or proxies.get("all://")
            or ""
        )
    proxy_url = str(proxy_url or "").strip()
    if not proxy_url:
        return ""
    if not _is_proxy_reachable(proxy_url):
        logger.warning("Ignoring unreachable search proxy: %s", proxy_url)
        return ""
    return proxy_url


def get_effective_search_proxy() -> str:
    """Public network boundary shared by every web-search provider."""
    return _get_effective_search_proxy()


def _is_proxy_reachable(proxy_url: str, timeout: float = 1.5) -> bool:
    parsed = urlparse(proxy_url)
    host = parsed.hostname
    port = parsed.port
    if not host:
        return False
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _merge_no_proxy(existing: str) -> str:
    entries = [item.strip() for item in existing.split(",") if item.strip()]
    required = ["127.0.0.1", "localhost", "::1"]
    lowered = {item.lower() for item in entries}
    for item in required:
        if item.lower() not in lowered:
            entries.append(item)
    return ",".join(entries)


def _is_port_available(host: str, port: int) -> bool:
    """Return whether a TCP listener can bind to ``host:port``."""
    try:
        with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
        return True
    except OSError:
        return False


def _find_available_port(host: str) -> int:
    """Ask the OS for an unused TCP port on the requested interface."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _build_simplexng_launch_cmd(port: int, host: str, *, settings_path: Path | None = None) -> list[str]:
    """Build a launch command compatible with different SimpleXNG package layouts."""
    args = ["-p", str(port), "-H", host]
    if settings_path is not None:
        args.extend(["--settings", str(settings_path)])

    # WoA keeps the main backend native ARM64. SimpleXNG still depends on a
    # handful of x64-only wheels (Brotli/fastText), so only this child service
    # crosses the Windows compatibility boundary.
    if _is_windows_arm():
        sidecar = _woa_simplexng_sidecar()
        if sidecar is not None:
            return [str(sidecar), *args]
        raise FileNotFoundError("Windows ARM x64 SimpleXNG sidecar is missing")

    child_entrypoint = Path(__file__).with_name("simplexng_child.py").resolve()

    # In a PyInstaller frozen build, sys.executable is the app binary itself.
    # Pass the editable Plugin child entrypoint through the trampoline so the
    # subprocess still runs the user's seeded implementation.
    if getattr(sys, "frozen", False):
        return [
            sys.executable,
            "--launch-simplexng",
            str(child_entrypoint),
            *args,
        ]

    if child_entrypoint.is_file():
        return [sys.executable, str(child_entrypoint), *args]

    script_path = Path(sys.executable).resolve().parent / "simplexng"
    if script_path.exists():
        return [str(script_path), *args]

    raise FileNotFoundError("Could not locate a runnable SimpleXNG entrypoint")


def platform_machine() -> str:
    import platform

    return platform.machine().lower()


async def start_searxng(port: int = 8888, host: str = "127.0.0.1") -> str:
    """Convenience: start SimpleXNG in a thread and return the URL."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_manager().start, port, host)


def stop_searxng() -> None:
    """Convenience: stop the SimpleXNG subprocess."""
    get_manager().stop()


class WebSearchService:
    """Plugin-owned web-search facade and local backend lifecycle."""

    def __init__(self) -> None:
        from .search_backend import ProviderHealthRegistry

        self._provider_health = ProviderHealthRegistry()
        self._startup_error = ""

    @property
    def manager(self) -> SearXNGManager:
        return get_manager()

    async def search(self, topic: str, **options: object) -> str:
        from .search_backend import deep_search

        return await deep_search(
            str(topic),
            provider_health=self._provider_health,
            **options,
        )

    def provider_health(self, providers: tuple[str, ...]) -> tuple[dict[str, object], ...]:
        return self._provider_health.snapshots(providers)

    def reset_provider_health(self) -> None:
        self._provider_health.reset()

    @property
    def startup_error(self) -> str:
        return self._startup_error

    async def startup(
        self,
        port: int | None = None,
        host: str | None = None,
    ) -> str:
        if not SEARXNG_AUTO_START:
            return ""
        url = await start_searxng(
            int(SEARXNG_PORT if port is None else port),
            str(SEARXNG_HOST if host is None else host),
        )
        self.reset_provider_health()
        return url

    async def startup_best_effort(self) -> str:
        """Start local search without disabling unrelated content capabilities."""
        try:
            url = await self.startup()
        except Exception as exc:
            self._startup_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "SimpleXNG startup failed; continuing without local search: %s",
                exc,
                exc_info=True,
            )
            return ""
        self._startup_error = ""
        return url

    async def restart(self, port: int, host: str) -> str:
        await asyncio.to_thread(self.manager.stop)
        url = await start_searxng(int(port), str(host))
        self.reset_provider_health()
        return url

    async def settings_changed(
        self,
        _namespace: str,
        changed: tuple[str, ...],
    ) -> None:
        # Configuration revisions are the recovery boundary for credential and
        # provider-selection circuits. Never carry stale health across them.
        self.reset_provider_health()
        proxy_keys = {
            "external_agent_proxy_enabled",
            "external_agent_proxy_url",
            "external_agent_proxy_port",
            "proxy_search_enabled",
        }
        if not proxy_keys.intersection(changed) or not self.manager.is_running:
            return
        await self.restart(int(SEARXNG_PORT), str(SEARXNG_HOST))

    def shutdown(self) -> None:
        stop_searxng()


_search_service: WebSearchService | None = None


def get_search_service() -> WebSearchService:
    global _search_service
    if _search_service is None:
        _search_service = WebSearchService()
    return _search_service


__all__ = [
    "SearXNGManager",
    "WebSearchService",
    "get_effective_search_proxy",
    "get_manager",
    "get_search_service",
    "start_searxng",
    "stop_searxng",
]
