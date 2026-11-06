"""Provider-plugin boundary for textual tool protocol leaking into answers.

Text is never promoted to executable calls here. Reject malformed responses via
the session's bounded model-response retry path; only structured calls execute.
"""

from cyrene.model.error_details import ModelCallError, classify_model_error


_PREFIXES = ("<||DSML", "</||DSML")


def _canonical(text):
    return "".join(text.replace("｜", "|").split()).upper()


def _invalid():
    return ModelCallError(
        classify_model_error(ValueError("Invalid response: textual tool protocol")),
        diagnostics={"termination_reason": "textual_tool_protocol"},
    )


class _TextGuard:
    def __init__(self):
        self.pending = ""

    def feed(self, text):
        text = self.pending + text
        self.pending = ""
        # Hold only a possible marker suffix so arbitrary SSE chunk boundaries
        # cannot expose even the start of an opening or orphan closing marker.
        start = 0
        while (index := text.find("<", start)) >= 0:
            tail = _canonical(text[index:])
            if any(tail.startswith(prefix) for prefix in _PREFIXES):
                raise _invalid()
            if any(prefix.startswith(tail) for prefix in _PREFIXES):
                self.pending = text[index:]
                return text[:index]
            start = index + 1
        return text

    def finish(self):
        tail, self.pending = self.pending, ""
        if "|" in _canonical(tail):
            raise _invalid()
        return tail


def validate_response_text(text):
    if isinstance(text, str):
        guard = _TextGuard()
        guard.feed(text)
        guard.finish()


class ResponseStreamGuard:
    def __init__(self, callback):
        self.callback = callback
        self.text = _TextGuard()

    async def __call__(self, event):
        kind = event.get("type")
        try:
            if kind in {"reply_start", "stream_cancelled"}:
                self.text = _TextGuard()
            elif kind == "reply_delta":
                delta = self.text.feed(str(event.get("delta") or ""))
                if not delta:
                    return
                event = {**event, "delta": delta}
            elif kind == "reply_done":
                validate_response_text(event.get("response"))
                tail = self.text.finish()
                if tail:
                    await self.callback({"type": "reply_delta", "delta": tail})
        except ModelCallError:
            self.text = _TextGuard()
            # Retract already streamed prose/history; this attempt is not an
            # answer and must not leave an apparently successful partial reply.
            await self.callback({"type": "stream_cancelled"})
            raise
        await self.callback(event)
