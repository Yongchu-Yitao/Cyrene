import importlib.util
from pathlib import Path
import tempfile
import unittest


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HERE = Path(__file__).parent
build = load("desktop_build", HERE / "build.py")
dependencies = load("desktop_requirements", HERE / "guest/requirements.py")


class BuildTests(unittest.TestCase):
    def test_context_excludes_private_runtime_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            (source / "src/cyrene/workbench/webui/static/app").mkdir(parents=True)
            for name in ("pyproject.toml", "LICENSE", "SOUL.default.md", ".env", "db.sqlite3"):
                (source / name).write_text("private" if name.startswith((".", "db")) else "source")
            for name in (".env", "runtime.db", "cache.sqlite3-wal", "module.py"):
                (source / "src/cyrene" / name).write_text(name)
            target = root / "context"
            build.copy_source(source, target)
            self.assertTrue((target / "src/cyrene/module.py").exists())
            self.assertFalse((target / ".env").exists())
            self.assertFalse((target / "db.sqlite3").exists())
            for name in (".env", "runtime.db", "cache.sqlite3-wal"):
                self.assertFalse((target / "src/cyrene" / name).exists())

    def test_cpu_profile_keeps_tools_and_platform_markers(self):
        project = {"project": {"dependencies": ["httpx>=0.28", "onnxruntime-gpu>=1.26; sys_platform != 'darwin'",
                     "onnxruntime>=1.27", "mlx-lm>=0.31; sys_platform == 'darwin'", "pywinpty; sys_platform == 'win32'"],
                     "optional-dependencies": {"browser": ["playwright>=1.40"]}}}
        result = list(dependencies.requirements(project))
        self.assertIn("playwright>=1.40", result)
        self.assertIn("pywinpty; sys_platform == 'win32'", result)
        self.assertEqual([item for item in result if item.startswith("onnx")], ["onnxruntime>=1.27,<1.28"])
        self.assertFalse(any(item.startswith("mlx") for item in result))


if __name__ == "__main__":
    unittest.main()
