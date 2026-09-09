"""Resolve desktop dependencies for a CPU-only Linux guest, without CUDA."""
import sys
import tomllib
import json
import re
from pathlib import Path


def requirements(project):
    for dependency in project["project"]["dependencies"]:
        name = dependency.split(";", 1)[0].strip()
        if name.startswith(("onnxruntime", "mlx-lm")):
            continue
        yield dependency
    yield "onnxruntime>=1.27,<1.28"
    yield from project["project"]["optional-dependencies"]["browser"]


if __name__ == "__main__":
    with open(sys.argv[1], "rb") as stream:
        selected = list(requirements(tomllib.load(stream)))
    if "--apply" in sys.argv[2:]:
        path = Path(sys.argv[1])
        replacement = "dependencies = [\n" + "".join("    " + json.dumps(item) + ",\n" for item in selected) + "]"
        updated, count = re.subn(r"(?ms)^dependencies = \[.*?^\]", lambda _: replacement, path.read_text(), count=1)
        if count != 1:
            raise ValueError("Unable to locate project dependency array")
        path.write_text(updated)
    print("\n".join(selected))
