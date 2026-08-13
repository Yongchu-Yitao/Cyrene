"""One-shot x64 OCR sidecar for the native Windows ARM64 backend."""

from __future__ import annotations

import json
import platform
import sys
from pathlib import Path


def main() -> int:
    try:
        if "--smoke-test" in sys.argv:
            import onnxruntime as ort
            import rapidocr

            print(json.dumps({
                "ok": True,
                "marker": "CYRENE_OCR_SIDECAR_SMOKE=ok",
                "architecture": platform.machine(),
                "providers": ort.get_available_providers(),
                "rapidocr": getattr(rapidocr, "__version__", "ok"),
            }))
            return 0
        request = json.loads(sys.stdin.read())
        image = str(Path(request["image"]).resolve())
        root = Path(request["model_root"]).resolve()
        from rapidocr import EngineType, ModelType, OCRVersion, RapidOCR

        def run(use_dml: bool):
            engine = RapidOCR(params={
                "EngineConfig.onnxruntime.use_dml": use_dml,
                "EngineConfig.onnxruntime.intra_op_num_threads": 2,
                "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                "Det.engine_type": EngineType.ONNXRUNTIME,
                "Det.model_type": ModelType.MEDIUM,
                "Det.ocr_version": OCRVersion.PPOCRV6,
                "Det.model_path": str(root / "det.onnx"),
                "Rec.engine_type": EngineType.ONNXRUNTIME,
                "Rec.model_type": ModelType.MEDIUM,
                "Rec.ocr_version": OCRVersion.PPOCRV6,
                "Rec.model_path": str(root / "rec.onnx"),
                "Rec.rec_keys_path": str(root / "ppocrv6_dict.txt"),
            })
            return engine(image)

        try:
            result = run(True)
        except Exception:
            result = run(False)
        text = "\n".join(
            str(value).strip()
            for value in (getattr(result, "txts", None) or [])
            if str(value).strip()
        )
        print(json.dumps({"ok": True, "text": text}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
