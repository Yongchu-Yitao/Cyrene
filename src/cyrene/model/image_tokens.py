"""Local image-token estimates, independent of encoded transport size.

These are budgeting heuristics, not provider usage or billing measurements.
Remote images are never fetched just to estimate a request.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from io import BytesIO

from PIL import Image


def estimate_image_tokens(block: Mapping[str, object], *, model: str = "") -> int:
    image = block.get("image_url")
    url = image.get("url") if isinstance(image, Mapping) else image
    if not isinstance(url, str) or not url.startswith("data:image/"):
        return 16_386
    # Decode only a bounded header. Pillow reads dimensions without decoding
    # pixels; malformed/unsupported images remain the provider's responsibility.
    header, separator, payload = url.partition(",")
    if not separator or not header.endswith(";base64"):
        return 16_386
    try:
        data = base64.b64decode(payload[:262_144], validate=True)
        with Image.open(BytesIO(data)) as image_file:
            width, height = image_file.size
    except (ValueError, OSError, binascii.Error, Image.DecompressionBombError):
        return 16_386
    # Qwen3 vision uses 32x32 pixel units. Use a finer 28px heuristic for
    # other models until their provider-specific budget is available.
    qwen3 = model.lower().startswith(("qwen3", "qwen-3"))
    patch = 32 if qwen3 else 28
    tokens = ((width + patch - 1) // patch) * ((height + patch - 1) // patch) + 2
    return min(tokens, 16_386) if qwen3 else tokens
