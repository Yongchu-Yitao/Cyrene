import hashlib
import io
import sys
import tarfile
from types import SimpleNamespace
from pathlib import Path

import pytest

from cyrene.knowledge import local_models


def test_sherpa_provider_prefers_cuda_then_apple_coreml(monkeypatch):
    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(
        get_available_providers=lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
    ))
    assert local_models.sherpa_provider("fireredasr2-aed-int8") == "cuda"

    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(
        get_available_providers=lambda: ["CoreMLExecutionProvider", "CPUExecutionProvider"],
    ))
    monkeypatch.setattr(local_models.sys, "platform", "darwin")
    monkeypatch.setattr(local_models.platform, "machine", lambda: "arm64")
    assert local_models.sherpa_provider("zipvoice-zh-en") == "coreml"
    assert local_models.sherpa_provider("fireredasr2-aed-int8") == "cpu"


def test_onnx_provider_order_prefers_directml_before_cpu(monkeypatch):
    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(
        get_available_providers=lambda: ["DmlExecutionProvider", "CPUExecutionProvider"],
    ))

    assert local_models.onnx_execution_providers() == [
        "DmlExecutionProvider", "CPUExecutionProvider",
    ]


def test_windows_arm_registers_qnn_npu_session(monkeypatch):
    class Options:
        def __init__(self):
            self.calls = []

        def add_provider_for_devices(self, devices, options):
            self.calls.append((devices, options))

    npu = SimpleNamespace(
        ep_name="QNNExecutionProvider",
        device=SimpleNamespace(type="NPU"),
    )
    ort = SimpleNamespace(get_ep_devices=lambda: [npu])
    qnn = SimpleNamespace(get_qnn_htp_path=lambda: "QnnHtp.dll")
    monkeypatch.setattr(local_models.sys, "platform", "win32")
    monkeypatch.setattr(local_models.platform, "machine", lambda: "ARM64")
    monkeypatch.setattr(local_models, "_register_qnn_plugin", lambda _ort: True)
    monkeypatch.setitem(sys.modules, "onnxruntime_qnn", qnn)
    options = Options()

    assert local_models.configure_qnn_session_options(options, ort)
    assert options.calls == [([npu], {
        "backend_path": "QnnHtp.dll",
        "enable_htp_fp16_precision": "1",
    })]


def test_windows_arm_ocr_uses_only_x64_sidecar(monkeypatch, tmp_path):
    from cyrene.knowledge import ocr

    sidecar = tmp_path / "CyreneOcr.exe"
    sidecar.touch()
    monkeypatch.setattr(ocr.sys, "platform", "win32")
    monkeypatch.setattr(ocr.platform, "machine", lambda: "ARM64")
    monkeypatch.setenv("CYRENE_X64_OCR_SIDECAR", str(sidecar))
    monkeypatch.setattr(
        ocr,
        "_recognize_with_sidecar",
        lambda image, executable: f"{image}:{executable.name}",
    )
    monkeypatch.setattr(
        ocr,
        "_load_engine",
        lambda: (_ for _ in ()).throw(AssertionError("must not load RapidOCR in ARM core")),
    )

    assert ocr._recognize_sync("page.png") == "page.png:CyreneOcr.exe"


def test_windows_arm_ocr_does_not_fall_back_to_x64_modules(monkeypatch):
    from cyrene.knowledge import ocr

    monkeypatch.setattr(ocr.sys, "platform", "win32")
    monkeypatch.setattr(ocr.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(ocr, "_woa_x64_sidecar", lambda: None)

    with pytest.raises(RuntimeError, match="x64 OCR sidecar"):
        ocr._recognize_sync("page.png")


def test_local_models_prefer_domestic_mirror_and_keep_fallbacks():
    for model in local_models.MODEL_CATALOG.values():
        for item in model["files"]:
            sources = item["sources"]
            assert any(
                host in sources[0]["url"]
                for host in ("modelscope.cn", "hf-mirror.com", "ghfast.top", "gh-proxy.com")
            )
            assert len(sources) >= 2


def test_zipvoice_uses_fp32_distill_pack_within_one_gib_budget():
    model = local_models.MODEL_CATALOG["zipvoice-zh-en"]

    assert model["download_bytes"] < 1024 ** 3
    archive = model["files"][0]
    assert "distill-fp32" in archive["path"]
    assert archive["sha256"] == "3b6729d03bf4ba64deeec113048bdbbe55dbc30580b609be76342e0099fd23a8"
    outputs = archive["extract"]["outputs"]
    assert any(item["path"] == "encoder.onnx" for item in outputs)
    assert any(item["path"] == "decoder.onnx" for item in outputs)


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


def test_archive_model_publishes_only_declared_outputs(tmp_path):
    archive = tmp_path / "model.tar.bz2"
    payload = b"declared model payload"
    with tarfile.open(archive, "w:bz2") as bundle:
        info = tarfile.TarInfo("bundle/model.onnx")
        info.size = len(payload)
        bundle.addfile(info, io.BytesIO(payload))
        ignored = tarfile.TarInfo("bundle/ignored.txt")
        ignored.size = 7
        bundle.addfile(ignored, io.BytesIO(b"ignored"))

    item = {
        "extract": {
            "root": "bundle",
            "outputs": [
                {"source": "model.onnx", "path": "runtime/model.onnx", "min_bytes": len(payload)},
            ],
        }
    }
    local_models._extract_archive(archive, tmp_path, item)

    assert (tmp_path / "runtime" / "model.onnx").read_bytes() == payload
    assert not (tmp_path / "ignored.txt").exists()
    assert not list(tmp_path.glob(".extract-*"))


@pytest.mark.asyncio
async def test_download_reuses_valid_archive_after_extraction_failure(tmp_path, monkeypatch):
    model_id = "test-archive-model"
    root = tmp_path / model_id
    archive = root / ".downloads" / "model.tar.bz2"
    archive.parent.mkdir(parents=True)
    payload = b"reusable model payload"
    with tarfile.open(archive, "w:bz2") as bundle:
        info = tarfile.TarInfo("bundle/model.onnx")
        info.size = len(payload)
        bundle.addfile(info, io.BytesIO(payload))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    monkeypatch.setattr(local_models, "MODEL_ROOT", tmp_path)
    monkeypatch.setitem(local_models.MODEL_CATALOG, model_id, {
        "name": "Test archive",
        "kind": "test",
        "description": "",
        "runtime": "test",
        "download_bytes": archive.stat().st_size,
        "files": [{
            "path": ".downloads/model.tar.bz2",
            "min_bytes": archive.stat().st_size,
            "download_bytes": archive.stat().st_size,
            "sha256": digest,
            "sources": [{"url": "https://invalid.example/model.tar.bz2"}],
            "extract": {
                "root": "bundle",
                "outputs": [
                    {"source": "model.onnx", "path": "model.onnx", "min_bytes": len(payload)},
                ],
            },
        }],
    })

    await local_models._download(model_id)

    assert (root / "model.onnx").read_bytes() == payload
    assert (root / ".ready.json").is_file()
    assert not archive.exists()
