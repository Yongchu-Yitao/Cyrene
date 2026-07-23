"""Stable tool-result serialization."""

from __future__ import annotations

import json
from typing import Any

from cyrene.tooling.types import ToolResult


class ToolProtocolError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


def serialize_result(result: ToolResult | dict[str, Any]) -> str:
    payload = result if isinstance(result, dict) else {
        "status": result.status,
        "summary": result.summary,
        "data": result.data,
        "error_type": result.error_type,
        "next_valid_actions": result.next_valid_actions,
        "evidence_refs": result.evidence_refs,
        "truncated": result.truncated,
    }
    return json.dumps(payload, ensure_ascii=False)


def serialize_error(error: ToolProtocolError) -> str:
    return serialize_result({
        "status": "error",
        "error": {
            "type": error.code,
            "message": error.message,
        },
    })
