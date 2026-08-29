import re
from pathlib import Path


def test_macos_traffic_lights_are_centered_in_workbench_topbar():
    root = Path(__file__).resolve().parent.parent
    main_source = (root / "electron" / "main.js").read_text(encoding="utf-8")
    css_source = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    topbar = re.search(
        r"\.workbench-topbar\s*\{[^}]*height:\s*var\(--wb-topbar-height\)",
        css_source,
    )
    topbar_height_value = re.search(
        r"--wb-topbar-height:\s*(\d+)px",
        css_source,
    )
    traffic_lights = re.search(
        r"trafficLightPosition\s*=\s*\{\s*x:\s*\d+,\s*y:\s*(\d+)\s*\}",
        main_source,
    )
    brand_wordmark = re.search(
        r"\.workbench-brand strong\s*\{([^}]*)\}", css_source
    )

    assert topbar is not None
    assert topbar_height_value is not None
    assert traffic_lights is not None
    assert brand_wordmark is not None
    topbar_height = int(topbar_height_value.group(1))
    traffic_light_y = int(traffic_lights.group(1))
    # Electron's rendered traffic-light image needs a 1px optical correction.
    assert traffic_light_y == (topbar_height - 14) // 2 - 1
    assert "transform: translateY(-1px)" in brand_wordmark.group(1)
    topbar_styles = css_source.split(".workbench-topbar {", 1)[1].split("}", 1)[0]
    assert "grid-template-columns: 174px" in topbar_styles
    darwin_topbar_styles = css_source.split(
        'html[data-platform="darwin"] .workbench-topbar {', 1
    )[1].split("}", 1)[0]
    assert "grid-template-columns: 236px" in darwin_topbar_styles
