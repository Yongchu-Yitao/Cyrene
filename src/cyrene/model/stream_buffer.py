"""Bounded-fragment text accumulation for a single provider stream."""


class TextBuffer:
    """Copy each fragment a bounded number of times before final materialization."""

    def __init__(self, initial: str = "") -> None:
        self._blocks: list[str] = []
        self._parts: list[str] = []
        self._pending_chars = 0
        self._length = 0
        self._value: str | None = None
        self.append(initial)

    def append(self, text: str) -> None:
        if not text:
            return
        self._value = None
        self._length += len(text)
        self._parts.append(text)
        self._pending_chars += len(text)
        if len(self._parts) >= 256 or self._pending_chars >= 65536:
            self._blocks.append("".join(self._parts))
            self._parts.clear()
            self._pending_chars = 0

    def __bool__(self) -> bool:
        return bool(self._length)

    def __str__(self) -> str:
        if self._value is None:
            self._value = "".join((*self._blocks, *self._parts))
            # Release fragment objects once a snapshot is requested. Later
            # appends remain supported without duplicating this prefix.
            self._blocks = [self._value] if self._value else []
            self._parts.clear()
            self._pending_chars = 0
        return self._value


def append_argument(call: dict, fragment: str) -> None:
    value = call.get("arguments")
    if not isinstance(value, TextBuffer):
        value = TextBuffer(str(value or ""))
        call["arguments"] = value
    value.append(fragment)
