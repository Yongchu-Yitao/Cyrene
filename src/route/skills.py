"""Installed-skill routes."""

# ruff: noqa: F403,F405

from cyrene.workbench_runtime import *


def register_skill_routes(router: APIRouter, bot: Any, db_path: str) -> None:
    global _bot, _db_path
    _bot = bot
    _db_path = db_path

    # ---- Skills install API ----

    @router.get("/api/skills/installed")
    async def api_installed_skills():
        return {"skills": _build_skills()}

    @router.post("/api/skills/scan")
    async def api_scan_existing_skills():
        """Register valid skill folders/files already present in installed_skills/ but
        missing from the settings registry (e.g. after a data reset or manual copy).
        """
        return _register_existing_skills()

    @router.post("/api/skills/install")
    async def api_install_skill(request: Request):
        body = await request.json()
        source_path = Path(str(body.get("path") or "")).expanduser()
        if not source_path.exists():
            return JSONResponse({"ok": False, "error": "invalid skill source path"}, status_code=400)
        result = install_skill_from_path(source_path)
        if not result.get("ok", False):
            return JSONResponse(result, status_code=400)
        return result

    @router.post("/api/skills/install-upload")
    async def api_install_skill_upload(request: Request):
        """Install a skill from an uploaded file (browser file picker path)."""
        import tempfile

        try:
            form = await request.form()
            file = form.get("file")
            if not file:
                return JSONResponse({"ok": False, "error": "No file provided"}, status_code=400)
            content = await file.read()
            if len(content) > 8 * 1024 * 1024:  # 8 MB (matches _MAX_SKILL_ARCHIVE_BYTES)
                return JSONResponse({"ok": False, "error": "File too large (max 8 MB)"}, status_code=400)
            suffix = Path(file.filename or "skill.tmp").suffix or ".tmp"
            TEMP_DIR.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=TEMP_DIR) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            try:
                result = install_skill_from_path(Path(tmp_path))
                if not result.get("ok", False):
                    return JSONResponse(result, status_code=400)
                return result
            finally:
                Path(tmp_path).unlink(missing_ok=True)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @router.post("/api/skills/install-picker")
    async def api_install_skill_picker():
        import platform
        import subprocess

        system = platform.system()
        if system != "Darwin":
            return JSONResponse({"ok": False, "error": f"Skill picker not supported on {system}"}, status_code=400)

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["osascript", "-e",
                 'POSIX path of (choose folder with prompt "Select skill folder containing SKILL.md")'],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            return JSONResponse({"ok": False, "error": "Picker timed out — please try again"}, status_code=400)
        except Exception as e:
            return JSONResponse({"ok": False, "error": f"Picker error: {e}"}, status_code=400)

        stderr = (result.stderr or "").strip()
        if stderr and "User cancelled" not in stderr:
            return JSONResponse({"ok": False, "error": f"Picker error: {stderr}"}, status_code=400)

        selected = result.stdout.strip()
        if not selected:
            return {"ok": False, "cancelled": True}

        source_path = Path(selected).expanduser()
        if not source_path.exists():
            return JSONResponse({"ok": False, "error": "selected skill source is invalid"}, status_code=400)

        result = install_skill_from_path(source_path)
        if not result.get("ok", False):
            return JSONResponse(result, status_code=400)
        return result

    @router.post("/api/skills/{skill_id}/toggle")
    async def api_toggle_skill(skill_id: str):
        if not _toggle_skill(skill_id):
            return JSONResponse({"ok": False, "error": "skill not found"}, status_code=404)
        return {"ok": True}

    @router.post("/api/skills/{skill_id}/uninstall")
    async def api_uninstall_skill(skill_id: str):
        if not _uninstall_skill(skill_id):
            return JSONResponse({"ok": False, "error": "skill not found"}, status_code=404)
        return {"ok": True}


    # ---- Skills API ----

    @router.get("/api/skills")
    async def api_skills():
        return {"skills": _build_skills()}
