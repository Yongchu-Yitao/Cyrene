"""Local file selection adapter for the existing Plugin installer."""
from pathlib import Path

from cyrene.core.plugin import application_plugin_scope
from cyrene.plugins.installation import install_source
from cyrene.plugins.package_source import package_source


async def install_plugin_file(raw_path: str, *, host=None) -> dict:
    if not raw_path.strip():
        raise ValueError('Choose an existing Plugin folder, Python file, or ZIP archive.')
    with package_source(Path(raw_path).expanduser().resolve()) as source:
        return await install_source(host if host is not None else application_plugin_scope(), source)
