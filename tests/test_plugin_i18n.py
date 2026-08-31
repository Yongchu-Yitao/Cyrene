"""Tests for the Agent package, kept outside the shipped source tree."""

from __future__ import annotations

import json
import re
from pathlib import Path

from cyrene.core.plugin import (
    Plugin,
    PluginCustomizationState,
    PluginPack,
    PluginRegistry,
)
from cyrene.plugins.native_tools import seed_builtin_plugin_directory


_HAN_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_CATALOG_PATH = (
    Path(__file__).parents[1]
    / "src" / "cyrene" / "plugins" / "builtin"
    / "i18n.json"
)


def _write_catalog(path, *, suffix: str = "") -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "packs": {
                    "core": {
                        "en": {
                            "name": f"Core catalog{suffix}",
                            "description": "Core catalog description",
                        },
                        "zh": {
                            "name": f"核心目录{suffix}",
                            "description": "核心目录描述",
                        },
                    },
                    "sample_pack": {
                        "en": {
                            "name": f"Catalog pack{suffix}",
                            "description": f"Catalog pack description{suffix}",
                        },
                        "zh": {
                            "name": f"目录工具包{suffix}",
                            "description": f"目录工具包描述{suffix}",
                        },
                    },
                },
                "plugins": {
                    "SampleTool": {
                        "en": {
                            "name": f"Catalog tool{suffix}",
                            "description": f"Catalog tool description{suffix}",
                        },
                        "zh": {
                            "name": f"目录工具{suffix}",
                            "description": f"目录工具描述{suffix}",
                        },
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_registry_merges_editable_catalog_with_authored_i18n_and_fallbacks(
    tmp_path,
) -> None:
    root = tmp_path / "plugin_impl"
    pack = root / "sample_pack"
    pack.mkdir(parents=True)
    (pack / "__init__.py").write_text(
        '''
from cyrene.core.plugin import Plugin, PluginPack

plugin_pack = PluginPack(
    id="sample_pack",
    description="Source pack description",
    metadata={"i18n": {"en": {"name": "Authored pack"}}},
    plugins=(Plugin(
        name="SampleTool",
        description="Source tool description",
        input_schema={"type": "object"},
        handler=lambda _arguments, _context: "ok",
        metadata={"i18n": {"zh": {"name": "贡献工具"}}},
    ),),
)
''',
        encoding="utf-8",
    )
    (root / "odd_tool.py").write_text(
        '''
from cyrene.core.plugin import Plugin

plugin = Plugin(
    name="odd.customTool",
    description="Source-only English description",
    input_schema={"type": "object"},
    handler=lambda _arguments, _context: "ok",
    metadata={"i18n": {"zh": {"name": "自定义工具"}}},
)
''',
        encoding="utf-8",
    )
    catalog = root / "i18n.json"
    _write_catalog(catalog)

    registry = PluginRegistry()
    assert registry.load_directory(root) == ()

    loaded_pack = next(item for item in registry.list_packs() if item.id == "sample_pack")
    assert loaded_pack.localized("en") == (
        "Authored pack",
        "Catalog pack description",
    )
    assert loaded_pack.localized("zh") == ("目录工具包", "目录工具包描述")
    loaded_tool = registry.resolve("SampleTool")
    assert loaded_tool.localized("en") == (
        "Catalog tool",
        "Catalog tool description",
    )
    assert loaded_tool.localized("zh") == ("贡献工具", "目录工具描述")
    assert loaded_tool.localized("en-US") == loaded_tool.localized("en")
    assert loaded_tool.localized("zh-CN") == loaded_tool.localized("zh")
    assert loaded_pack.localized("en_US") == loaded_pack.localized("en")
    assert loaded_pack.localized("zh_CN") == loaded_pack.localized("zh")
    fallback = registry.resolve("odd.customTool")
    assert fallback.localized("en") == (
        "Odd Custom Tool",
        "Source-only English description",
    )
    assert fallback.localized("zh") == (
        "自定义工具",
        "Source-only English description",
    )
    assert next(item for item in registry.list_packs() if item.id == "core").localized(
        "zh"
    ) == ("核心目录", "核心目录描述")

    _write_catalog(catalog, suffix=" v2")
    assert registry.refresh_directory(root) == ()
    assert registry.resolve("SampleTool").localized("en") == (
        "Catalog tool v2",
        "Catalog tool description v2",
    )
    assert next(item for item in registry.list_packs() if item.id == "core").localized(
        "en"
    )[0] == "Core catalog v2"

    assert not _HAN_CHARACTER.search(fallback.localized("zh")[1])


def test_custom_description_has_priority_over_catalog_i18n() -> None:
    customizations = PluginCustomizationState(
        {"LocalizedTool": {"description": "Description written by the user"}}
    )
    registry = PluginRegistry(include_core=False, customizations=customizations)
    registry.register_pack(
        PluginPack(
            id="localized_pack",
            description="Localized pack",
            plugins=(
                Plugin(
                    name="LocalizedTool",
                    description="Source description",
                    input_schema={"type": "object"},
                    handler=lambda _arguments, _context: "ok",
                    metadata={
                        "i18n": {
                            "en": {"description": "Old English translation"},
                            "zh": {"description": "旧的中文翻译"},
                        }
                    },
                ),
            ),
        ),
        source="user-test",
    )

    plugin = registry.resolve("LocalizedTool")
    assert plugin.localized("en-US")[1] == "Description written by the user"
    assert plugin.localized("zh-CN")[1] == "Description written by the user"
    assert plugin.metadata["customized_description"] is True


def test_builtin_i18n_catalog_is_seeded_and_user_edits_are_preserved(tmp_path) -> None:
    root = tmp_path / "plugin_impl"
    seeded = seed_builtin_plugin_directory(root)
    catalog_path = root / "i18n.json"

    assert catalog_path in seeded.created
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert catalog["packs"]["cyrene_application"]["zh"]["name"]
    assert catalog["plugins"]["Edit"]["en"]["name"] == "Edit"
    assert catalog["plugins"]["Edit"]["zh"]["description"] == (
        "替换文本文件中的精确字符串。"
    )

    edited = b'{"version": 1, "packs": {}, "plugins": {}}\n'
    catalog_path.write_bytes(edited)
    reseeded = seed_builtin_plugin_directory(root)

    assert catalog_path in reseeded.existing
    assert catalog_path.read_bytes() == edited


def test_builtin_catalog_explicitly_covers_every_plugin_description() -> None:
    catalog = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    registry = PluginRegistry()
    assert registry.load_directory(_CATALOG_PATH.parent) == ()

    # Keep historical/compatibility translations in the catalog even after a
    # Plugin is removed; the currently registered identities must nevertheless
    # all have explicit entries.
    assert {pack.id for pack in registry.list_packs()} <= set(catalog["packs"])
    assert {
        registered.plugin.canonical_name
        for registered in registry.list_plugins()
    } <= set(catalog["plugins"])

    for section in ("packs", "plugins"):
        for identity, translations in catalog[section].items():
            assert set(translations) == {"en", "zh"}, identity
            for locale in ("en", "zh"):
                assert set(translations[locale]) == {"name", "description"}, (
                    identity,
                    locale,
                )
                assert translations[locale]["name"].strip(), (identity, locale)
                assert translations[locale]["description"].strip(), (
                    identity,
                    locale,
                )
            assert _HAN_CHARACTER.search(
                translations["zh"]["description"]
            ), identity
            assert (
                translations["zh"]["description"]
                != translations["en"]["description"]
            ), identity


def test_workbench_tool_name_catalog_covers_every_registered_builtin() -> None:
    root = Path(__file__).parents[1]
    i18n_root = (
        root / "src" / "cyrene" / "workbench" / "webui" / "frontend"
        / "shared" / "i18n"
    )
    key_sets = []
    for filename in ("catalog-en.jsx", "catalog-zh.jsx"):
        source = (i18n_root / filename).read_text(encoding="utf-8")
        key_sets.append(set(re.findall(r'"toolName\.([^"\\]+)"\s*:', source)))
    assert key_sets[0] == key_sets[1]

    alias_source = (i18n_root / "tool-name-aliases.jsx").read_text(encoding="utf-8")
    aliases = dict(re.findall(r'^\s*"([^"]+)":\s*"([^"]+)"', alias_source, re.M))
    assert set(aliases.values()) <= key_sets[0]

    registry = PluginRegistry()
    assert registry.load_directory(_CATALOG_PATH.parent) == ()
    missing = {
        registered.plugin.canonical_name
        for registered in registry.list_plugins()
        if registered.plugin.canonical_name not in key_sets[0]
        and aliases.get(registered.plugin.canonical_name) not in key_sets[0]
    }
    assert missing == set()


def test_seeded_plugin_english_descriptions_match_english_source(tmp_path) -> None:
    root = tmp_path / "plugin_impl"
    seed_builtin_plugin_directory(root)
    registry = PluginRegistry()

    assert registry.load_directory(root) == ()
    catalog = json.loads((root / "i18n.json").read_text(encoding="utf-8"))
    compared = []
    for registered in registry.list_plugins():
        plugin = registered.plugin
        identity = plugin.canonical_name
        if identity not in catalog["plugins"]:
            continue
        source_description = str(
            plugin.metadata.get("source_description") or plugin.description
        )
        if _HAN_CHARACTER.search(source_description):
            continue
        assert catalog["plugins"][identity]["en"]["description"] == (
            source_description
        ), identity
        compared.append(identity)
    assert len(compared) >= 150
