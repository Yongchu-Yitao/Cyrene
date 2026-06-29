import os

from cyrene.app_paths import cleanup_temporary_artifacts, resolve_app_paths


def test_packaged_macos_paths_are_separated():
    paths = resolve_app_paths(
        platform="darwin",
        home="/Users/alice",
        env={},
        bundled=True,
        install_resources="/Applications/Cyrene.app/Contents/Resources",
    )

    assert str(paths.install_resources) == "/Applications/Cyrene.app/Contents/Resources"
    assert str(paths.user_data) == "/Users/alice/Library/Application Support/Cyrene"
    assert str(paths.runtime_base) == "/Users/alice/Library/Application Support/Cyrene"
    assert str(paths.workspace) == "/Users/alice/Library/Application Support/Cyrene/workspace"
    assert str(paths.store) == "/Users/alice/Library/Application Support/Cyrene/store"
    assert str(paths.data) == "/Users/alice/Library/Application Support/Cyrene/data"
    assert str(paths.cache) == "/Users/alice/Library/Caches/Cyrene"
    assert str(paths.temp) == "/Users/alice/Library/Caches/Cyrene/tmp"
    assert paths.install_resources != paths.user_data
    assert paths.cache != paths.data
    assert paths.temp != paths.cache


def test_packaged_windows_paths_use_roaming_data_and_local_cache():
    env = {
        "APPDATA": r"C:\Users\Alice\AppData\Roaming",
        "LOCALAPPDATA": r"C:\Users\Alice\AppData\Local",
    }
    paths = resolve_app_paths(
        platform="win32",
        home=r"C:\Users\Alice",
        env=env,
        bundled=True,
        install_resources=r"C:\Program Files\Cyrene\resources",
    )

    assert str(paths.user_data) == os.path.join(env["APPDATA"], "Cyrene")
    assert str(paths.workspace) == os.path.join(env["APPDATA"], "Cyrene", "workspace")
    assert str(paths.cache) == os.path.join(env["LOCALAPPDATA"], "Cyrene", "Cache")
    assert str(paths.temp) == os.path.join(env["LOCALAPPDATA"], "Cyrene", "Cache", "tmp")
    assert paths.install_resources != paths.user_data


def test_packaged_linux_paths_honor_xdg_directories():
    env = {
        "XDG_DATA_HOME": "/home/alice/.local/stateful-data",
        "XDG_CACHE_HOME": "/home/alice/.runtime-cache",
    }
    paths = resolve_app_paths(
        platform="linux",
        home="/home/alice",
        env=env,
        bundled=True,
        install_resources="/opt/Cyrene/resources",
    )

    assert str(paths.user_data) == "/home/alice/.local/stateful-data/Cyrene"
    assert str(paths.workspace) == "/home/alice/.local/stateful-data/Cyrene/workspace"
    assert str(paths.cache) == "/home/alice/.runtime-cache/Cyrene"
    assert str(paths.temp) == "/home/alice/.runtime-cache/Cyrene/tmp"
    assert paths.install_resources != paths.user_data


def test_cleanup_temporary_artifacts_removes_only_expired_children(tmp_path):
    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()
    old_file = temp_dir / "old.txt"
    new_file = temp_dir / "new.txt"
    old_dir = temp_dir / "old-dir"
    old_dir.mkdir()
    (old_dir / "nested.txt").write_text("stale", encoding="utf-8")
    old_file.write_text("stale", encoding="utf-8")
    new_file.write_text("fresh", encoding="utf-8")

    now = 2_000.0
    os.utime(old_file, (now - 200, now - 200))
    os.utime(old_dir, (now - 200, now - 200))
    os.utime(new_file, (now - 10, now - 10))

    removed = cleanup_temporary_artifacts(temp_dir, ttl_seconds=60, now=now)

    assert set(removed) == {old_file, old_dir}
    assert not old_file.exists()
    assert not old_dir.exists()
    assert new_file.exists()
