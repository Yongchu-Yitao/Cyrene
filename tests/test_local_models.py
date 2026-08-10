import hashlib
from pathlib import Path

import pytest

from cyrene.knowledge import local_models


def test_both_local_models_prefer_domestic_mirror_and_keep_fallbacks():
    for model in local_models.MODEL_CATALOG.values():
        for item in model["files"]:
            sources = item["sources"]
            assert any(
                host in sources[0]["url"]
                for host in ("modelscope.cn", "hf-mirror.com")
            )
            assert len(sources) >= 2


@pytest.mark.asyncio
async def test_download_switches_mirror_after_failure(tmp_path, monkeypatch):
    payload = b"valid mirrored model"
    digest = hashlib.sha256(payload).hexdigest()
    item = {
        "path": "model.onnx",
        "min_bytes": len(payload),
        "sha256": digest,
        "sources": [
            {"url": "https://domestic.example/model", "resume_key": "same"},
            {"url": "https://fallback.example/model", "resume_key": "same"},
        ],
    }

    class Response:
        status_code = 200
        headers = {"content-length": str(len(payload))}

        def raise_for_status(self):
            return None

        async def aiter_bytes(self, _size):
            yield payload

    class Stream:
        def __init__(self, fail):
            self.fail = fail

        async def __aenter__(self):
            if self.fail:
                raise RuntimeError("mirror unavailable")
            return Response()

        async def __aexit__(self, *_args):
            return False

    class Client:
        def stream(self, _method, url, **_kwargs):
            return Stream("domestic.example" in url)

    local_models._PROGRESS["test-model"] = {
        "downloaded_bytes": 0, "total_bytes": 0, "error": "",
    }
    destination = Path(tmp_path) / "model.onnx"

    await local_models._download_file(Client(), item, destination, "test-model")

    assert destination.read_bytes() == payload
    assert local_models._PROGRESS["test-model"]["source"] == "fallback.example"
