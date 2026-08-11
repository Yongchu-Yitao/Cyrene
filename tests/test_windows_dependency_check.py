import importlib.util
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "build" / "check_windows_dependencies.py"


def _module():
    spec = importlib.util.spec_from_file_location("check_windows_dependencies", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_known_winloop_substitution_is_allowed(monkeypatch):
    module = _module()
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            1,
            "simplexng 0.1.3 requires uvloop, which is not installed.\n",
            "",
        ),
    )

    assert module.main() == 0


def test_every_other_dependency_conflict_still_blocks_release(monkeypatch):
    module = _module()
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            1,
            "cyrene 0.7.3 requires numpy, which is not installed.\n",
            "",
        ),
    )

    assert module.main() == 1
