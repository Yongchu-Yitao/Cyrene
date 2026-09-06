from pathlib import Path

import pytest

from cyrene.platform import version


@pytest.mark.parametrize(("metadata", "label"), [
    ("0.9.0b10", "0.9.0-beta10"),
    ("0.9.0a1", "0.9.0-alpha1"),
    ("0.9.0rc2", "0.9.0-rc2"),
    ("0.9.0b10+fix", "0.9.0-beta10-fix"),
    ("0.9.0-beta10", "0.9.0-beta10"),
    ("0.9.0+fix", "0.9.0-fix"),
    ("0.9.0", "0.9.0"),
])
def test_package_metadata_keeps_public_release_label(monkeypatch, metadata, label):
    monkeypatch.setattr(version, "_pyproject_candidates", lambda: [])
    monkeypatch.setattr(version.importlib.metadata, "version", lambda _name: metadata)
    assert version.get_version.__wrapped__() == label


def test_source_checkout_reads_its_project_version_before_installed_metadata(monkeypatch, tmp_path):
    module = tmp_path / "src" / "cyrene" / "platform" / "version.py"
    module.parent.mkdir(parents=True)
    module.touch()
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.9.0-beta10"\n')
    monkeypatch.setattr(version, "__file__", str(module))
    monkeypatch.setattr(version.sys, "frozen", False, raising=False)
    monkeypatch.setattr(version, "_bundle_contents_dir", lambda: None)
    monkeypatch.setattr(version.importlib.metadata, "version", lambda _name: "0.8.0")
    assert version._pyproject_candidates() == [Path(tmp_path) / "pyproject.toml"]
    assert version.get_version.__wrapped__() == "0.9.0-beta10"
