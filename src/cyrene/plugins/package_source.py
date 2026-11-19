"""Resolve a selected plugin folder, standalone file, or ZIP for validation."""
from contextlib import contextmanager
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


@contextmanager
def package_source(source: Path):
    if not source.exists():
        raise ValueError('Choose an existing Plugin folder, Python file, or ZIP archive.')
    with tempfile.TemporaryDirectory(prefix='cyrene-plugin-import-') as temporary:
        if source.is_file() and source.suffix.lower() == '.zip':
            root = Path(temporary)
            with zipfile.ZipFile(source) as archive:
                entries = archive.infolist()
                if len(entries) > 5000 or sum(item.file_size for item in entries) > 100 * 1024 * 1024:
                    raise ValueError('Plugin archive exceeds the 100 MB / 5000 file limit.')
                for item in entries:
                    path = PurePosixPath(item.filename)
                    if path.is_absolute() or '..' in path.parts or '\\' in item.filename or stat.S_ISLNK(item.external_attr >> 16):
                        raise ValueError('Plugin archive contains an unsafe path or symbolic link.')
                archive.extractall(root)
            candidates = [p for p in root.iterdir() if p.name != '__MACOSX' and not p.name.startswith('.')]
            source = root if (root / '__init__.py').is_file() else candidates[0] if len(candidates) == 1 else root
        if source.is_dir() and any(p.is_symlink() for p in source.rglob('*')):
            raise ValueError('Plugin folders must not contain symbolic links.')
        yield source
