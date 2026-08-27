"""
Cyrene CLI — thin HTTP client for the Cyrene daemon.

Usage:
    cyrene start                         Start daemon (background)
    cyrene stop                          Stop daemon
    cyrene chat [text]                   Interactive streaming conversation
    cyrene do <text> --session <id>      Send message to agent
    cyrene session list                  List sessions
    cyrene session status --session <id> Session details
    cyrene session delete --session <id> Delete session
    cyrene flow --session <id>           List rounds
    cyrene flow --session <id> --round <r>  Round timeline
    cyrene flow --session <id> --round <r> --id <e>  Event details
    cyrene memory soul [--edit <path>]   View/edit SOUL.md
    cyrene memory short-term             Short-term memory
    cyrene memory context                Context window
    cyrene status                        System status
    cyrene mcp list/add/remove/toggle    MCP servers
"""

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from cyrene.localization import app_language, normalize_language

DAEMON_URL = "http://localhost:4242"
DAEMON_TOKEN = ""
DAEMON_IS_DESKTOP = False
_PROTECTED_DAEMON_PRESENT = False
CLIENT_TIMEOUT = 300.0  # 5 min default for long tasks
_CLI_PORT_RANGE = range(4242, 4263)
_CLI_CONNECTION_FILENAME = "cli-connection.json"
_CLI_LANGUAGE = app_language()


def _t(en: str, zh: str, /, **values: object) -> str:
    """Render a compact CLI message in the effective presentation language."""
    template = zh if _CLI_LANGUAGE == "zh" else en
    return template.format(**values)


def _tp(
    en_one: str,
    en_other: str,
    zh: str,
    /,
    *,
    count: int | float,
    **values: object,
) -> str:
    if _CLI_LANGUAGE == "zh":
        template = zh
    else:
        template = en_one if count == 1 else en_other
    return template.format(count=count, **values)


def _set_cli_language(value: object = None) -> str:
    """Set and return the canonical language used by every CLI command."""
    global _CLI_LANGUAGE
    _CLI_LANGUAGE = app_language(value)
    return _CLI_LANGUAGE


def _display_state(value: object) -> str:
    raw = str(value or "?")
    if _CLI_LANGUAGE != "zh":
        return raw
    return {
        "active": "活跃",
        "running": "运行中",
        "idle": "空闲",
        "pending": "等待中",
        "completed": "已完成",
        "complete": "已完成",
        "failed": "失败",
        "stopped": "已停止",
        "connected": "已连接",
        "connecting": "连接中",
        "disconnected": "未连接",
        "enabled": "已启用",
        "disabled": "已禁用",
        "error": "错误",
    }.get(raw.lower(), raw)


def _requested_language(argv: list[str]) -> str:
    """Find ``--lang`` before parsing so help and parser errors are localized."""
    for index, value in enumerate(argv):
        if value.startswith("--lang="):
            return value.partition("=")[2]
        if value == "--lang" and index + 1 < len(argv):
            return argv[index + 1]
    return ""


def _daemon_language(
    explicit: object = None,
    *,
    daemon_url: str = "",
    auth_token: str = "",
) -> str:
    """Resolve explicit -> daemon setting -> shared local/OS fallback."""
    normalized = normalize_language(explicit)
    if normalized:
        return normalized
    target_url = str(daemon_url or DAEMON_URL).rstrip("/")
    if target_url:
        target_token = str(auth_token or "").strip()
        if not target_token and target_url == str(DAEMON_URL).rstrip("/"):
            target_token = str(DAEMON_TOKEN or "").strip()
        if not target_token:
            target_token = str(os.environ.get("CYRENE_AUTH_TOKEN") or "").strip()
        try:
            response = httpx.get(
                f"{target_url}/api/settings/config",
                timeout=1.0,
                trust_env=False,
                headers={"X-Cyrene-Token": target_token} if target_token else {},
            )
            if response.status_code == 200:
                payload = response.json()
                if isinstance(payload, dict):
                    normalized = normalize_language(payload.get("app_language"))
                    if normalized:
                        return normalized
        except (httpx.HTTPError, OSError, ValueError, TypeError):
            pass
    return app_language()


def _api(path: str, method: str = "GET", **kwargs) -> httpx.Response:
    """Make an API call to the daemon."""
    url = f"{DAEMON_URL}{path}"
    kwargs.setdefault("timeout", CLIENT_TIMEOUT)
    kwargs.setdefault("headers", _daemon_auth_headers())
    try:
        # A loopback daemon request must never inherit HTTP(S) proxy settings.
        # System-level proxy discovery can otherwise turn localhost calls into
        # remote 502 responses even when no proxy variables are exported.
        with httpx.Client(trust_env=False) as client:
            if method == "GET":
                resp = client.get(url, **kwargs)
            elif method == "POST":
                resp = client.post(url, **kwargs)
            elif method == "PUT":
                resp = client.put(url, **kwargs)
            elif method == "DELETE":
                resp = client.delete(url, **kwargs)
            else:
                raise ValueError(f"Unsupported method: {method}")
        resp.raise_for_status()
        return resp
    except httpx.ConnectError:
        print(_t(
            "Error: Cannot connect to the Cyrene daemon at {url}",
            "错误：无法连接到位于 {url} 的 Cyrene Daemon",
            url=DAEMON_URL,
        ), file=sys.stderr)
        print(_t(
            "Start it with: cyrene start",
            "请运行以下命令启动：cyrene start",
        ), file=sys.stderr)
        sys.exit(1)
    except httpx.TimeoutException:
        print(_t(
            "Error: The request to the Cyrene daemon timed out.",
            "错误：向 Cyrene Daemon 发出的请求已超时。",
        ), file=sys.stderr)
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        try:
            payload = e.response.json()
            detail = (
                payload.get("error") or payload.get("detail") or str(payload)
                if isinstance(payload, dict)
                else str(payload)
            )
        except Exception:
            detail = str(e)
        print(_t(
            "Error ({status}): {detail}",
            "错误（{status}）：{detail}",
            status=e.response.status_code,
            detail=detail,
        ), file=sys.stderr)
        sys.exit(1)
    except httpx.RequestError as exc:
        print(_t(
            "Error: The Cyrene daemon request failed: {error}",
            "错误：Cyrene Daemon 请求失败：{error}",
            error=exc,
        ), file=sys.stderr)
        sys.exit(1)


def _api_json(path: str, method: str = "GET", **kwargs) -> dict | list:
    try:
        return _api(path, method, **kwargs).json()
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        print(_t(
            "Error: The daemon returned invalid JSON: {error}",
            "错误：Daemon 返回了无效 JSON：{error}",
            error=exc,
        ), file=sys.stderr)
        raise SystemExit(1) from exc


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------


def _daemon_auth_headers(token: str = "") -> dict[str, str]:
    token = str(token or DAEMON_TOKEN or os.environ.get("CYRENE_AUTH_TOKEN") or "").strip()
    return {"X-Cyrene-Token": token} if token else {}


def _desktop_connection_path() -> Path:
    from cyrene.runtime.paths import app_temp_dir

    return app_temp_dir() / _CLI_CONNECTION_FILENAME


def _read_desktop_connection() -> tuple[str, str] | None:
    """Read Electron's current same-user connection capability."""
    path = _desktop_connection_path()
    try:
        file_stat = path.stat()
        if os.name != "nt":
            if file_stat.st_uid != os.getuid() or file_stat.st_mode & 0o077:
                return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or int(payload.get("version") or 0) != 1:
        return None
    url = str(payload.get("url") or "").rstrip("/")
    token = str(payload.get("token") or "").strip()
    parsed = urlparse(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or not parsed.port
        or not token
    ):
        return None
    try:
        response = httpx.get(
            f"{url}/api/status",
            timeout=0.75,
            trust_env=False,
            headers=_daemon_auth_headers(token),
        )
    except Exception:
        return None
    return (url, token) if response.status_code == 200 else None


def _daemon_url(port: int) -> str:
    return f"http://127.0.0.1:{int(port)}"


def _port_is_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.05):
            return True
    except OSError:
        return False


def _discover_daemon_url() -> str:
    """Find a CLI-accessible Cyrene daemon without touching Electron's token."""
    global DAEMON_IS_DESKTOP, DAEMON_TOKEN, _PROTECTED_DAEMON_PRESENT
    DAEMON_IS_DESKTOP = False
    _PROTECTED_DAEMON_PRESENT = False
    desktop = _read_desktop_connection()
    if desktop is not None:
        url, token = desktop
        DAEMON_TOKEN = token
        DAEMON_IS_DESKTOP = True
        return url
    DAEMON_TOKEN = str(os.environ.get("CYRENE_AUTH_TOKEN") or "").strip()
    headers = _daemon_auth_headers()
    for port in _CLI_PORT_RANGE:
        if not _port_is_open(port):
            continue
        url = _daemon_url(port)
        try:
            response = httpx.get(
                f"{url}/api/status",
                timeout=0.5,
                trust_env=False,
                headers=headers,
            )
        except Exception:
            continue
        if response.status_code == 200:
            return url
        if response.status_code == 401:
            try:
                identity = httpx.get(
                    f"{url}/api/instance-id",
                    timeout=0.5,
                    trust_env=False,
                )
                if identity.status_code == 200 and identity.json().get("instance_id"):
                    _PROTECTED_DAEMON_PRESENT = True
            except Exception:
                pass
    return ""


def _allocate_daemon_port() -> int:
    """Choose a deterministic nearby port, falling back to an ephemeral port."""
    for port in _CLI_PORT_RANGE:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
                candidate.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


def cmd_start(args: argparse.Namespace, *, quiet: bool = False) -> str:
    """Start the Cyrene daemon in background."""
    headers = _daemon_auth_headers()
    running_url = _discover_daemon_url()
    if running_url:
        if not quiet:
            print(_t(
                "Cyrene is already running at {url}",
                "Cyrene 已在 {url} 运行",
                url=running_url,
            ))
        return running_url
    if _PROTECTED_DAEMON_PRESENT:
        print(
            _t(
                "Error: Cyrene Desktop is running but has not published its CLI "
                "connection yet. Restart Electron once, then run `cyrene` again.",
                "错误：Cyrene Desktop 正在运行，但尚未发布 CLI 连接。请重启一次 "
                "Electron，然后再次运行 `cyrene`。",
            ),
            file=sys.stderr,
        )
        raise SystemExit(1)

    selected_port = _allocate_daemon_port()
    selected_url = _daemon_url(selected_port)

    # Launch daemon as subprocess.
    # In PyInstaller frozen builds, sys.executable is the app binary and "-m"
    # does not work — use the trampoline flag that run_cyrene.py understands.
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--launch-web", "--port", str(selected_port)]
    else:
        cmd = [sys.executable, "-m", "cyrene", "--port", str(selected_port)]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            start_new_session=sys.platform != "win32",
        )
    except OSError as exc:
        print(_t(
            "Error: Failed to launch the Cyrene daemon: {error}",
            "错误：无法启动 Cyrene Daemon：{error}",
            error=exc,
        ), file=sys.stderr)
        raise SystemExit(1) from exc

    # Wait for it to be ready
    for _ in range(30):
        try:
            resp = httpx.get(
                f"{selected_url}/api/ui-data",
                timeout=3.0,
                trust_env=False,
                headers=headers,
            )
            if resp.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(1)
    else:
        print(_t(
            "Error: The daemon failed to start within 30 seconds.",
            "错误：Daemon 未能在 30 秒内启动。",
        ), file=sys.stderr)
        proc.kill()
        sys.exit(1)

    try:
        data = resp.json()
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        print(_t(
            "Error: The daemon returned invalid startup data: {error}",
            "错误：Daemon 返回了无效的启动数据：{error}",
            error=exc,
        ), file=sys.stderr)
        raise SystemExit(1) from exc
    if quiet:
        return selected_url
    sessions = data.get("sessions", [])

    print(_t(
        "Cyrene started at {url}",
        "Cyrene 已启动：{url}",
        url=selected_url,
    ))
    print()
    print(_t("Available sessions:", "可用 Session："))
    for s in sessions[:5]:
        sid = s.get("id", "?")
        title = s.get("title", "?")
        status = _display_state(s.get("status", "?"))
        n = s.get("summary", {}).get("tokens", "—")
        print(f"  {sid}  ({title}, {status}, {n})")
    if len(sessions) > 5:
        print(_tp(
            "  ... and {count} more",
            "  ... and {count} more",
            "  ……另有 {count} 个",
            count=len(sessions) - 5,
        ))
    print()
    print(_t("Available commands:", "可用命令："))
    print("  cyrene chat")
    print(_t(
        '  cyrene chat "your question"',
        '  cyrene chat "你的问题"',
    ))
    print("  cyrene status")
    print("  cyrene --help")
    print()
    print(_t("Extra notes:", "补充说明："))
    print(_t(
        "  The daemon is running in the background.",
        "  Daemon 正在后台运行。",
    ))
    print(_t(
        "  Use 'cyrene stop' when you want to stop it.",
        "  需要停止时，请运行 'cyrene stop'。",
    ))
    return selected_url


def cmd_stop(args: argparse.Namespace) -> None:
    """Stop the Cyrene daemon."""
    if DAEMON_IS_DESKTOP:
        print(_t(
            "Cyrene Desktop owns this backend; quit Electron to stop it.",
            "此后端由 Cyrene Desktop 管理；请退出 Electron 以停止它。",
        ))
        return
    try:
        _api("/api/shutdown", method="POST")
    except Exception as exc:
        print(_t(
            "Warning: shutdown request failed ({error}); the daemon may still be running.",
            "警告：关闭请求失败（{error}）；Daemon 可能仍在运行。",
            error=exc,
        ), file=sys.stderr)
    print(_t("Cyrene stopped.", "Cyrene 已停止。"))


def cmd_chat(args: argparse.Namespace) -> None:
    """Run the interactive streaming daemon client."""
    from cyrene.cli_chat import run_chat

    raise SystemExit(asyncio.run(run_chat(args)))


# ---------------------------------------------------------------------------
# session
# ---------------------------------------------------------------------------


def cmd_session_list(args: argparse.Namespace) -> None:
    """List all sessions."""
    data = _api_json("/api/workbench/sessions")
    sessions = data.get("sessions", [])

    if args.json:
        print(json.dumps(sessions, ensure_ascii=False, indent=2))
        return

    print(
        f"{'ID':<45} "
        f"{_t('Title', '标题'):<24} "
        f"{_t('Status', '状态'):<10} "
        f"{_t('Messages', '消息'):<10} "
        f"{_t('Duration', '时长'):<10}"
    )
    print(f"{'-'*45} {'-'*24} {'-'*10} {'-'*10} {'-'*10}")
    for s in sessions:
        sid = s.get("id", "?")
        title = (s.get("title") or "?")[:24]
        status = _display_state(s.get("status", "?"))
        summary = s.get("summary", {})
        tokens = summary.get("tokens", "—")
        dur = s.get("dur", "—")
        print(f"{sid:<45} {title:<24} {status:<10} {tokens:<10} {dur:<10}")


def cmd_session_status(args: argparse.Namespace) -> None:
    """Show detailed session status."""
    session_id = args.session

    data = _api_json("/api/workbench/sessions")
    sessions = data.get("sessions", [])
    session = next((s for s in sessions if s.get("id") == session_id), None)

    if session is None:
        # Try API
        print(_t(
            "Session '{session}' was not found.",
            "未找到 Session '{session}'。",
            session=session_id,
        ), file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(session, ensure_ascii=False, indent=2))
        return

    summary = session.get("summary", {})

    print(f"Session: {session_id}")
    print(f"  {_t('title', '标题')}: {session.get('title', '?')}")
    print(f"  {_t('status', '状态')}: {_display_state(session.get('status', '?'))}")
    print(f"  {_t('messages', '消息')}: {session.get('messageCount', 0)}")
    print(f"  token: {summary.get('tokens', '—')}")
    print(f"  {_t('started', '开始时间')}: {session.get('started', '—')}")
    print(f"  {_t('duration', '时长')}: {session.get('dur', '—')}")
    print()

    subagents = session.get("subagents", [])
    if subagents:
        print(_t(
            "\nSubagents ({count}):",
            "\n子代理（{count}）：",
            count=len(subagents),
        ))
        for sa in subagents:
            name = sa.get("name", "?")
            status = _display_state(sa.get("status", "?"))
            task = (sa.get("task") or "")[:40]
            print(f"  {name:<16} {status:<10} \"{task}\"")


def cmd_session_delete(args: argparse.Namespace) -> None:
    """Delete a session."""
    session_id = args.session
    _api(f"/api/workbench/sessions/{session_id}", method="DELETE")
    print(_t(
        "Session {session} deleted.",
        "Session {session} 已删除。",
        session=session_id,
    ))


# ---------------------------------------------------------------------------
# memory
# ---------------------------------------------------------------------------


def cmd_memory_soul(args: argparse.Namespace) -> None:
    """View or edit SOUL.md."""
    if args.edit:
        edit_path = Path(args.edit)
        if not edit_path.exists():
            print(_t(
                "Error: file not found: {path}",
                "错误：未找到文件：{path}",
                path=edit_path,
            ), file=sys.stderr)
            sys.exit(1)
        try:
            content = edit_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            print(_t(
                "Error: Cannot read {path}: {error}",
                "错误：无法读取 {path}：{error}",
                path=edit_path,
                error=exc,
            ), file=sys.stderr)
            raise SystemExit(1) from exc
        _api("/api/settings/soul", method="PUT", json={"content": content})
        sections = content.count("## ")
        section_unit = "section" if sections == 1 else "sections"
        char_unit = "character" if len(content) == 1 else "characters"
        print(_t(
            "✅ SOUL.md updated ({sections} {section_unit}, {chars} {char_unit}).",
            "✅ SOUL.md 已更新（{sections} 节，{chars} 个字符）。",
            sections=sections,
            section_unit=section_unit,
            chars=len(content),
            char_unit=char_unit,
        ))
        return

    data = _api_json("/api/settings/soul")
    print(data.get("content", ""))


def cmd_memory_short_term(args: argparse.Namespace) -> None:
    """View short-term memory."""
    data = _api_json("/api/memory")
    st = data.get("short_term", {})
    entries = st.get("entries", [])

    if args.json:
        print(json.dumps(entries, ensure_ascii=False, indent=2))
        return

    print(
        f"{_t('Type', '类型'):<12} "
        f"{_t('Content', '内容'):<48} "
        f"{_t('Count', '次数'):<6} "
        f"{_t('Valence', '情感值'):<8} "
        f"{_t('First', '首次'):<10} "
        f"{_t('Last', '最近'):<10}"
    )
    print(f"{'-'*12} {'-'*48} {'-'*6} {'-'*8} {'-'*10} {'-'*10}")
    for e in entries:
        etype = e.get("type", "?")
        content = (e.get("content", "") or "")[:48]
        count = e.get("mention_count", 0)
        valence = e.get("emotional_valence", 0)
        valence_str = f"+{valence}" if valence > 0 else str(valence)
        first = (e.get("first_seen") or "—")
        last = (e.get("last_mentioned") or "—")
        print(f"{etype:<12} {content:<48} {count:<6} {valence_str:<8} {first:<10} {last:<10}")


def cmd_memory_context(args: argparse.Namespace) -> None:
    """View context window status."""
    data = _api_json("/api/memory")
    cw = data.get("context_window", {})
    current = cw.get("messages", 0)
    max_msgs = cw.get("max", 40)
    threshold = max_msgs + 5

    if args.json:
        print(json.dumps(cw, ensure_ascii=False, indent=2))
        return

    print(_tp(
        "Context window: {current} / {maximum} message",
        "Context window: {current} / {maximum} messages",
        "上下文窗口：{current} / {maximum} 条消息",
        count=max_msgs,
        current=current,
        maximum=max_msgs,
    ))
    print(_tp(
        "  Compression trigger: {count} message",
        "  Compression trigger: {count} messages",
        "  压缩触发点：{count} 条消息",
        count=threshold,
    ))
    need_compress = _t("yes", "是") if current >= threshold else _t(
        "— (below threshold)",
        "—（低于阈值）",
    )
    print(_t(
        "  Next action: {action}",
        "  下一步：{action}",
        action=need_compress,
    ))


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> None:
    """Show system status."""
    data = _api_json("/api/status")

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    model = data.get("model", "?")
    base_url = data.get("base_url", "?")
    print(_t("Model: {model}", "模型：{model}", model=model))
    print(_t("Endpoint: {url}", "端点：{url}", url=base_url))
    print()

    workers = data.get("workers", [])
    if workers:
        print(
            f"{_t('Worker', 'Worker'):<20} "
            f"{_t('Role', '角色'):<16} "
            f"{_t('Status', '状态'):<10} "
            f"{_t('Uptime', '运行时间'):<12} "
            f"{_t('Tokens', 'Token'):<12}"
        )
        print(f"{'-'*20} {'-'*16} {'-'*10} {'-'*12} {'-'*12}")
        for w in workers:
            wid = w.get("id", "?")
            role = w.get("role", "?")
            status = _display_state(w.get("status", "?"))
            uptime = w.get("uptime", "—")
            tokens = w.get("tokens", "—")
            print(f"{wid:<20} {role:<16} {status:<10} {uptime:<12} {tokens:<12}")

    metrics = data.get("metrics", [])
    if metrics:
        print(_t("\nMetrics:", "\n指标："))
        for m in metrics:
            label = m.get("label", "?")
            value = m.get("value", "?")
            unit = m.get("unit", "")
            sub = m.get("sub", "")
            print(f"  {label}: {value}{unit} ({sub})")

    services = data.get("services", [])
    if services:
        print(_t("\nServices:", "\n服务："))
        for svc in services:
            name = svc.get("name", "?")
            status = _display_state(svc.get("status", "?"))
            latency = svc.get("latency", "—")
            note = svc.get("note", "")
            note_str = f" — {note}" if note else ""
            print(f"  {name:<40} {status:<6} {latency:<10}{note_str}")


# ---------------------------------------------------------------------------
# mcp (reuse existing implementation via HTTP)
# ---------------------------------------------------------------------------


def _mcp_call(method: str = "GET", **kwargs) -> dict | list:
    return _api_json("/api/settings/mcp", method=method, **kwargs)


def cmd_mcp_list(args: argparse.Namespace) -> None:
    """List MCP servers."""
    data = _api_json("/api/settings/mcp")
    servers = data.get("servers", [])

    if args.json:
        print(json.dumps(servers, ensure_ascii=False, indent=2))
        return

    if not servers:
        print(_t(
            "No MCP servers are configured.",
            "尚未配置 MCP Server。",
        ))
        return

    print(
        f"{_t('Name', '名称'):<20} "
        f"{_t('Transport', '传输'):<10} "
        f"{_t('Status', '状态'):<14} "
        f"{_t('Tools', '工具'):<6} "
        f"{_t('Endpoint', '端点')}"
    )
    print(f"{'-'*20} {'-'*10} {'-'*14} {'-'*6} {'-'*40}")
    for s in servers:
        name = s.get("name", "?")
        transport = s.get("transport", "?")
        status = _display_state(s.get("status", "disconnected"))
        tools = s.get("tool_count", 0)
        endpoint = s.get("command", "") if transport == "stdio" else s.get("url", "")
        print(f"{name:<20} {transport:<10} {status:<14} {tools:<6} {endpoint}")


def cmd_mcp_add(args: argparse.Namespace) -> None:
    """Add an MCP server."""
    data = _api_json("/api/settings/mcp")
    configs = data.get("configs", [])

    name = args.name
    transport = args.transport
    if transport == "stdio":
        cmd_parts = list(args.rest) if args.rest else []
        command = cmd_parts[0] if cmd_parts else ""
        extra_args = cmd_parts[1:]
        server = {"name": name, "transport": "stdio", "command": command, "args": extra_args, "enabled": True}
    elif transport == "sse":
        url = args.rest[0] if args.rest else ""
        server = {"name": name, "transport": "sse", "url": url, "enabled": True}
    else:
        print(_t(
            "Unknown transport: {transport}",
            "未知传输方式：{transport}",
            transport=transport,
        ), file=sys.stderr)
        sys.exit(1)

    configs = [s for s in configs if s.get("name") != name]
    configs.append(server)
    _api("/api/settings/mcp", method="PUT", json={"servers": configs})

    # Refresh to get status
    data = _api_json("/api/settings/mcp")
    live = next((s for s in data.get("servers", []) if s.get("name") == name), {})
    tools = live.get("tool_count", 0)
    print(_tp(
        "✅ MCP server '{name}' added ({count} tool available).",
        "✅ MCP server '{name}' added ({count} tools available).",
        "✅ MCP Server '{name}' 已添加（{tools} 个工具可用）。",
        count=tools,
        name=name,
        tools=tools,
    ))


def cmd_mcp_remove(args: argparse.Namespace) -> None:
    """Remove an MCP server."""
    data = _api_json("/api/settings/mcp")
    configs = data.get("configs", [])
    name = args.name
    configs = [s for s in configs if s.get("name") != name]
    _api("/api/settings/mcp", method="PUT", json={"servers": configs})
    print(_t(
        "✅ MCP server '{name}' deleted.",
        "✅ MCP Server '{name}' 已删除。",
        name=name,
    ))


def cmd_mcp_toggle(args: argparse.Namespace) -> None:
    """Toggle an MCP server on/off."""
    data = _api_json("/api/settings/mcp")
    configs = data.get("configs", [])
    name = args.name
    for s in configs:
        if s.get("name") == name:
            s["enabled"] = not s.get("enabled", True)
            break
    _api("/api/settings/mcp", method="PUT", json={"servers": configs})
    # Refresh
    data = _api_json("/api/settings/mcp")
    live = next((s for s in data.get("servers", []) if s.get("name") == name), {})
    enabled = bool(live.get("enabled", True))
    status = _t("enabled", "已启用") if enabled else _t("disabled", "已禁用")
    print(_t(
        "✅ MCP server '{name}' {status}.",
        "✅ MCP Server '{name}' {status}。",
        name=name,
        status=status,
    ))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


class _LocalizedArgumentParser(argparse.ArgumentParser):
    """ArgumentParser with localized section labels, usage, and error prefix."""

    def __init__(self, *args: object, language: object = None, **kwargs: object) -> None:
        self.cli_language = app_language(language)
        super().__init__(*args, **kwargs)
        self._positionals.title = self._pick("positional arguments", "位置参数")
        self._optionals.title = self._pick("options", "选项")

    def _pick(self, en: str, zh: str) -> str:
        return zh if self.cli_language == "zh" else en

    def format_usage(self) -> str:
        value = super().format_usage()
        return value.replace("usage:", self._pick("usage:", "用法："), 1)

    def format_help(self) -> str:
        value = super().format_help()
        return value.replace("usage:", self._pick("usage:", "用法："), 1)

    def error(self, message: str) -> None:
        if self.cli_language == "zh":
            replacements = (
                ("the following arguments are required:", "缺少必需参数："),
                ("unrecognized arguments:", "无法识别的参数："),
                ("invalid choice:", "无效选项："),
                ("choose from", "可选值："),
                ("invalid int value:", "整数值无效："),
                ("invalid float value:", "数值无效："),
                ("expected one argument", "需要一个参数"),
            )
            for source, target in replacements:
                message = message.replace(source, target)
        self.print_usage(sys.stderr)
        prefix = self._pick("error", "错误")
        self.exit(2, f"{self.prog}: {prefix}: {message}\n")


def _subparsers(
    parser: argparse.ArgumentParser,
    *,
    dest: str,
    language: str,
) -> argparse._SubParsersAction:
    return parser.add_subparsers(
        dest=dest,
        required=True,
        parser_class=lambda **kwargs: _LocalizedArgumentParser(
            language=language,
            **kwargs,
        ),
    )


def build_parser(language: object = None) -> argparse.ArgumentParser:
    resolved_language = _set_cli_language(language)

    def text(en: str, zh: str) -> str:
        return zh if resolved_language == "zh" else en

    parser = _LocalizedArgumentParser(
        prog="cyrene",
        description=text("Cyrene AI Agent CLI", "Cyrene AI Agent 命令行工具"),
        language=resolved_language,
    )
    parser.add_argument(
        "--lang",
        choices=["en", "zh"],
        default=None,
        help=text(
            "CLI language; defaults to app_language, then the operating system",
            "CLI 语言；默认依次使用 app_language 和操作系统语言",
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help=text("Output raw JSON", "输出原始 JSON"),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help=text("Verbose output", "显示详细输出"),
    )

    sub = _subparsers(parser, dest="command", language=resolved_language)

    # start
    sub.add_parser(
        "start",
        help=text("Start the Cyrene daemon", "启动 Cyrene Daemon"),
    )

    # stop
    sub.add_parser(
        "stop",
        help=text("Stop the Cyrene daemon", "停止 Cyrene Daemon"),
    )

    # chat
    chat_parser = sub.add_parser(
        "chat",
        help=text("Interactive streaming conversation", "交互式流式对话"),
    )
    chat_parser.add_argument(
        "text",
        nargs="?",
        help=text("Send one message and exit", "发送一条消息后退出"),
    )
    chat_parser.add_argument(
        "--chat",
        dest="chat_id",
        metavar="CHAT_ID",
        help=text("Resume a Workbench conversation", "继续 Workbench 对话"),
    )
    chat_parser.add_argument(
        "--list",
        dest="list_chats",
        action="store_true",
        help=text("List conversations and exit", "列出对话后退出"),
    )
    chat_parser.add_argument(
        "--resume",
        action="store_true",
        help=text(
            "Reconnect to the selected conversation's latest run",
            "重新连接所选对话的最新运行",
        ),
    )
    chat_parser.add_argument(
        "--cursor",
        type=int,
        default=0,
        help=text("Resume after this event sequence", "从此事件序号后恢复"),
    )
    chat_parser.add_argument(
        "--mode",
        choices=["default", "plan", "auto"],
        default="default",
        help=text("Permission mode", "权限模式"),
    )
    chat_parser.add_argument(
        "--lang",
        choices=["en", "zh"],
        default=argparse.SUPPRESS,
        help=text(
            "CLI language (compatible chat-level form)",
            "CLI 语言（兼容原有 chat 级写法）",
        ),
    )
    chat_parser.add_argument(
        "--url",
        default=DAEMON_URL,
        help=text("Cyrene daemon URL", "Cyrene Daemon 地址"),
    )
    chat_parser.add_argument(
        "--timeout",
        type=float,
        default=CLIENT_TIMEOUT,
        help=text("Request timeout in seconds", "请求超时秒数"),
    )
    chat_parser.add_argument(
        "--history-file",
        help=text(
            "Optional plaintext prompt history path",
            "可选的纯文本提示历史文件路径",
        ),
    )
    chat_parser.add_argument(
        "--no-color",
        action="store_true",
        help=text("Disable colored output", "禁用彩色输出"),
    )
    chat_parser.add_argument(
        "--json",
        action="store_true",
        help=text(
            "Output NDJSON events (one-shot only)",
            "输出 NDJSON 事件（仅限单次运行）",
        ),
    )
    chat_parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help=text("Show unknown public events", "显示未知的公开事件"),
    )

    # session
    session_parser = sub.add_parser(
        "session",
        help=text("Session management", "Session 管理"),
    )
    session_sub = _subparsers(
        session_parser,
        dest="subcommand",
        language=resolved_language,
    )

    session_list = session_sub.add_parser(
        "list",
        help=text("List all sessions", "列出所有 Session"),
    )
    session_list.add_argument(
        "--json",
        action="store_true",
        help=text("Output raw JSON", "输出原始 JSON"),
    )

    session_status = session_sub.add_parser(
        "status",
        help=text("Session details", "查看 Session 详情"),
    )
    session_status.add_argument(
        "--session",
        "-s",
        required=True,
        metavar="SESSION_ID",
        help=text("Session ID", "Session ID"),
    )
    session_status.add_argument(
        "--json",
        action="store_true",
        help=text("Output raw JSON", "输出原始 JSON"),
    )

    session_delete = session_sub.add_parser(
        "delete",
        help=text("Delete a session", "删除 Session"),
    )
    session_delete.add_argument(
        "--session",
        "-s",
        required=True,
        metavar="SESSION_ID",
        help=text("Session ID", "Session ID"),
    )
    session_delete.add_argument(
        "--json",
        action="store_true",
        help=text("Output raw JSON", "输出原始 JSON"),
    )

    # memory
    memory_parser = sub.add_parser(
        "memory",
        help=text("Memory system", "记忆系统"),
    )
    memory_sub = _subparsers(
        memory_parser,
        dest="subcommand",
        language=resolved_language,
    )

    mem_soul = memory_sub.add_parser(
        "soul",
        help=text("View or edit SOUL.md", "查看或编辑 SOUL.md"),
    )
    mem_soul.add_argument(
        "--edit",
        metavar="PATH",
        help=text(
            "Path to a file to write as SOUL.md",
            "要写入为 SOUL.md 的文件路径",
        ),
    )
    mem_soul.add_argument(
        "--json",
        action="store_true",
        help=text("Output raw JSON", "输出原始 JSON"),
    )

    mem_st = memory_sub.add_parser(
        "short-term",
        help=text("View short-term memory", "查看短期记忆"),
    )
    mem_st.add_argument(
        "--json",
        action="store_true",
        help=text("Output raw JSON", "输出原始 JSON"),
    )

    mem_ctx = memory_sub.add_parser(
        "context",
        help=text("View context window", "查看上下文窗口"),
    )
    mem_ctx.add_argument(
        "--json",
        action="store_true",
        help=text("Output raw JSON", "输出原始 JSON"),
    )

    # status
    status_parser = sub.add_parser(
        "status",
        help=text("System status", "系统状态"),
    )
    status_parser.add_argument(
        "--json",
        action="store_true",
        help=text("Output raw JSON", "输出原始 JSON"),
    )

    # mcp
    mcp_parser = sub.add_parser(
        "mcp",
        help=text("MCP server management", "MCP Server 管理"),
    )
    mcp_sub = _subparsers(
        mcp_parser,
        dest="subcommand",
        language=resolved_language,
    )

    mcp_list = mcp_sub.add_parser(
        "list",
        help=text("List MCP servers", "列出 MCP Server"),
    )
    mcp_list.add_argument(
        "--json",
        action="store_true",
        help=text("Output raw JSON", "输出原始 JSON"),
    )

    mcp_add = mcp_sub.add_parser(
        "add",
        help=text("Add an MCP server", "添加 MCP Server"),
    )
    mcp_add.add_argument("name", help=text("Server name", "Server 名称"))
    mcp_add.add_argument(
        "transport",
        choices=["stdio", "sse"],
        help=text("Transport type", "传输类型"),
    )
    mcp_add.add_argument(
        "rest",
        nargs=argparse.REMAINDER,
        default=[],
        help=text(
            "Command and arguments (stdio), or URL (sse)",
            "命令及参数（stdio），或 URL（sse）",
        ),
    )

    mcp_remove = mcp_sub.add_parser(
        "remove",
        help=text("Remove an MCP server", "移除 MCP Server"),
    )
    mcp_remove.add_argument("name", help=text("Server name", "Server 名称"))

    mcp_toggle = mcp_sub.add_parser(
        "toggle",
        help=text("Enable or disable an MCP server", "启用或禁用 MCP Server"),
    )
    mcp_toggle.add_argument("name", help=text("Server name", "Server 名称"))

    return parser


def main() -> None:
    global DAEMON_URL
    raw_args = sys.argv[1:]
    default_chat = not raw_args
    requested_language = _requested_language(raw_args)
    parser = build_parser(requested_language or None)
    args = parser.parse_args(["chat"] if default_chat else raw_args)
    explicit_language = normalize_language(
        getattr(args, "lang", None) or requested_language
    )
    _set_cli_language(explicit_language or None)

    cmd = args.command

    if cmd == "chat" and str(getattr(args, "url", "") or "") == DAEMON_URL:
        args.url = cmd_start(args, quiet=True)
        DAEMON_URL = str(args.url)
        args.auth_token = DAEMON_TOKEN
    elif cmd != "start":
        discovered_url = _discover_daemon_url()
        if discovered_url:
            DAEMON_URL = discovered_url

    if cmd != "start":
        language_url = (
            str(getattr(args, "url", "") or "")
            if cmd == "chat"
            else DAEMON_URL
        )
        resolved_language = _set_cli_language(_daemon_language(
            explicit_language,
            daemon_url=language_url,
            auth_token=str(getattr(args, "auth_token", "") or ""),
        ))
    else:
        resolved_language = _CLI_LANGUAGE
    args.lang = resolved_language
    args._lang_explicit = explicit_language or None
    args._lang_resolved = True

    if cmd == "start":
        cmd_start(args)
    elif cmd == "stop":
        cmd_stop(args)
    elif cmd == "chat":
        cmd_chat(args)
    elif cmd == "session":
        sub = args.subcommand
        if sub == "list":
            cmd_session_list(args)
        elif sub == "status":
            cmd_session_status(args)
        elif sub == "delete":
            cmd_session_delete(args)
    elif cmd == "memory":
        sub = args.subcommand
        if sub == "soul":
            cmd_memory_soul(args)
        elif sub == "short-term":
            cmd_memory_short_term(args)
        elif sub == "context":
            cmd_memory_context(args)
    elif cmd == "status":
        cmd_status(args)
    elif cmd == "mcp":
        sub = args.subcommand
        if sub == "list":
            cmd_mcp_list(args)
        elif sub == "add":
            cmd_mcp_add(args)
        elif sub == "remove":
            cmd_mcp_remove(args)
        elif sub == "toggle":
            cmd_mcp_toggle(args)


if __name__ == "__main__":
    main()
