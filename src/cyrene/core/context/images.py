"""Path-backed user images; pixels exist only at the provider boundary."""
from __future__ import annotations

import base64
import io
from copy import deepcopy
from pathlib import Path

IMAGE_TYPE = "cyrene_local_image"


def user_content(value):
    text = str(value.get("content") or "")
    images = (value.get("metadata") or {}).get("input_images") or []
    if not images:
        return text
    blocks = [{"type": "text", "text": text}]
    blocks.extend({"type": IMAGE_TYPE, **image} for image in images)
    return blocks


def image_references(message):
    """Retire pixels permanently in a saved model transcript, retaining paths."""
    result = deepcopy(message)
    content = result.get("content")
    if isinstance(content, list):
        result["content"] = [
            {"type": "text", "text": f'[Historical image: {block["path"]}]'}
            if isinstance(block, dict) and block.get("type") == IMAGE_TYPE else block
            for block in content
        ]
    return result


def has_local_images(messages):
    return any(isinstance(block, dict) and block.get("type") == IMAGE_TYPE
               for message in messages if isinstance(message.get("content"), list)
               for block in message["content"])


def materialize_images(messages):
    """Convert owned path blocks to standard multimodal protocol blocks."""
    result = deepcopy(messages)
    for message in result:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for index, block in enumerate(content):
            if not isinstance(block, dict) or block.get("type") != IMAGE_TYPE:
                continue
            from PIL import Image
            path = Path(block["path"])
            with Image.open(path) as source:
                if source.format in {"PNG", "JPEG", "GIF", "WEBP"}:
                    mime = Image.MIME[source.format]
                    data = path.read_bytes()
                else:
                    output = io.BytesIO()
                    source.convert("RGB").save(output, format="PNG")
                    mime, data = "image/png", output.getvalue()
            encoded = base64.b64encode(data).decode("ascii")
            content[index] = {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}
    return result


def retire_historical_images(path, retired_ids=()):
    """Projection-only eviction at successful context transitions/compaction.

    The current request stays visible while the assistant routes it to a task.
    Earlier images never reappear when that task is later reloaded.
    """
    from dataclasses import replace
    retired_ids = set(retired_ids)
    boundary = -1
    current_user = None
    for index, node in enumerate(path):
        value = node.value
        if value.get("role") == "user":
            current_user = getattr(node, "id", None)
        if value.get("role") in {"context_compaction", "context_reflection"}:
            boundary = index
        if value.get("role") == "tool_results" and any(
            result.get("success") and result.get("name") in {"switch_context", "unload_context", "load_context"}
            for result in value.get("results", [])
        ):
            boundary = index
    result = []
    for index, node in enumerate(path):
        value = node.value
        if (
            value.get("role") == "user"
            and (value.get("metadata") or {}).get("input_images")
            and node.id != current_user
            and (index < boundary or node.id in retired_ids)
        ):
            value = deepcopy(value)
            value["content"] = image_references({"content": user_content(value)})["content"]
            value["content"] = "\n".join(block["text"] for block in value["content"])
            value["metadata"].pop("input_images", None)
            node = replace(node, value=value)
        result.append(node)
    return result


def retire_task_images(state, path):
    state["retired_image_nodes"] = list(dict.fromkeys([
        *state.get("retired_image_nodes", []),
        *(node.id for node in path if node.value.get("role") == "user"
          and (node.value.get("metadata") or {}).get("input_images")),
    ]))
