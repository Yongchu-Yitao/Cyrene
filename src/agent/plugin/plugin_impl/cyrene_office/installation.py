"""User-facing installation lifecycle for the Cyrene PowerPoint add-in."""

from __future__ import annotations

import hashlib
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes

from .gateway import OfficeGatewayFiles, get_office_gateway_runtime
from .service import get_office_bridge


class OfficeInstallationError(RuntimeError):
    pass


def powerpoint_manifest_target(*, system: str | None = None, home: Path | None = None) -> Path | None:
    selected = system or platform.system()
    user_home = Path(home or Path.home())
    if selected == "Darwin":
        return user_home / "Library/Containers/com.microsoft.Powerpoint/Data/Documents/wef/cyrene-powerpoint-addin.xml"
    return None


def powerpoint_available(*, system: str | None = None, home: Path | None = None) -> bool:
    selected = system or platform.system()
    user_home = Path(home or Path.home())
    if selected == "Darwin":
        return any(path.exists() for path in (
            Path("/Applications/Microsoft PowerPoint.app"),
            user_home / "Applications/Microsoft PowerPoint.app",
        ))
    if selected == "Windows":
        candidates = (
            user_home / "AppData/Local/Microsoft/WindowsApps/POWERPNT.EXE",
            Path("C:/Program Files/Microsoft Office/root/Office16/POWERPNT.EXE"),
            Path("C:/Program Files (x86)/Microsoft Office/root/Office16/POWERPNT.EXE"),
        )
        return any(path.exists() for path in candidates)
    return False


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def powerpoint_addin_installed(
    files: OfficeGatewayFiles | None = None,
    *,
    system: str | None = None,
    home: Path | None = None,
) -> bool:
    material = files or get_office_gateway_runtime().files
    material.ensure()
    target = powerpoint_manifest_target(system=system, home=home)
    manifest_matches = bool(target and target.is_file() and _file_digest(target) == _file_digest(material.manifest_path))
    # Windows sideload catalogs don't expose a reliable per-user manifest path.
    # A live authenticated add-in session is definitive evidence of installation.
    return manifest_matches or bool(get_office_bridge().list_sessions("powerpoint"))


def certificate_trusted(files: OfficeGatewayFiles, *, system: str | None = None) -> bool:
    files.ensure()
    selected = system or platform.system()
    try:
        if selected == "Darwin":
            result = subprocess.run(
                ["security", "verify-cert", "-c", str(files.cert_path), "-p", "ssl", "-s", "localhost"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            return result.returncode == 0
        if selected == "Windows":
            certificate = x509.load_pem_x509_certificate(files.cert_path.read_bytes())
            fingerprint = certificate.fingerprint(hashes.SHA1()).hex().upper()
            result = subprocess.run(
                ["certutil", "-user", "-store", "Root"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            normalized = "".join(result.stdout.upper().split())
            return result.returncode == 0 and fingerprint in normalized
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return False


def trust_certificate(files: OfficeGatewayFiles, *, system: str | None = None, home: Path | None = None) -> None:
    files.ensure()
    selected = system or platform.system()
    user_home = Path(home or Path.home())
    try:
        if selected == "Darwin":
            subprocess.run([
                "security", "add-trusted-cert", "-d", "-r", "trustRoot",
                "-k", str(user_home / "Library/Keychains/login.keychain-db"),
                str(files.cert_path),
            ], check=True, timeout=60)
            return
        if selected == "Windows":
            subprocess.run(
                ["certutil", "-user", "-addstore", "Root", str(files.cert_path)],
                check=True,
                timeout=60,
            )
            return
    except subprocess.TimeoutExpired as exc:
        raise OfficeInstallationError("Certificate trust confirmation timed out.") from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        raise OfficeInstallationError("The local Office certificate could not be added to the current user's trust store.") from exc
    raise OfficeInstallationError("Automatic certificate trust is supported on macOS and Windows only.")


def integration_status(
    files: OfficeGatewayFiles | None = None,
    *,
    system: str | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    material = files or get_office_gateway_runtime().files
    material.ensure()
    selected = system or platform.system()
    target = powerpoint_manifest_target(system=selected, home=home)
    installed = powerpoint_addin_installed(material, system=selected, home=home)
    sessions = get_office_bridge().list_sessions("powerpoint")
    return {
        **material.public_info(running=get_office_gateway_runtime().running),
        "platform": selected.lower(),
        "powerpoint_available": powerpoint_available(system=selected, home=home),
        "certificate_trusted": certificate_trusted(material, system=selected),
        "addin_installed": installed,
        "installed_manifest_path": str(target.resolve()) if target is not None else "",
        "one_click_install": selected == "Darwin",
        "connected_presentations": len(sessions),
        "sessions": sessions,
        "manual_step_required": selected == "Windows",
    }


def install_powerpoint_addin(
    files: OfficeGatewayFiles | None = None,
    *,
    trust: bool = True,
    system: str | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    material = files or get_office_gateway_runtime().files
    material.ensure()
    selected = system or platform.system()
    if trust and not certificate_trusted(material, system=selected):
        trust_certificate(material, system=selected, home=home)
    target = powerpoint_manifest_target(system=selected, home=home)
    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(material.manifest_path, target)
    result = integration_status(material, system=selected, home=home)
    result.update({
        "ok": True,
        "restart_powerpoint": True,
        "message_code": "installed" if result["addin_installed"] else "prepared_manual",
    })
    return result


def remove_powerpoint_addin(
    files: OfficeGatewayFiles | None = None,
    *,
    system: str | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    selected = system or platform.system()
    target = powerpoint_manifest_target(system=selected, home=home)
    if target is None:
        raise OfficeInstallationError("Automatic add-in removal is supported on macOS only.")
    if target.is_file():
        target.unlink()
    result = integration_status(files, system=selected, home=home)
    result.update({"ok": True, "restart_powerpoint": True, "message_code": "removed"})
    return result


__all__ = [
    "OfficeInstallationError",
    "certificate_trusted",
    "install_powerpoint_addin",
    "integration_status",
    "powerpoint_manifest_target",
    "powerpoint_addin_installed",
    "remove_powerpoint_addin",
    "trust_certificate",
]
