"""Structure-aware text splitter with stable source offsets."""

from __future__ import annotations

import re


_BREAKS = [
    (re.compile(r"(?m)^#{1,6}\s+"), 100),
    (re.compile(r"(?m)^```"), 80),
    (re.compile(r"(?m)^\s*(?:---+|===+)\s*$"), 60),
    (re.compile(r"\n\n+"), 20),
    (re.compile(r"(?m)^\s*(?:[-*+] |\d+[.)]\s+)"), 5),
    (re.compile(r"\n"), 1),
]


def _inside_fence(text: str, position: int) -> bool:
    return text.count("```", 0, position) % 2 == 1


def _best_break(text: str, start: int, target: int, radius: int) -> int:
    left = max(start + 1, target - radius)
    right = min(len(text), target + radius)
    best = (float("-inf"), min(target, len(text)))
    for pattern, weight in _BREAKS:
        for match in pattern.finditer(text, left, right):
            position = match.start()
            if _inside_fence(text, position) and pattern.pattern != r"(?m)^```":
                continue
            distance = abs(position - target) / max(1, radius)
            score = weight - (distance * weight * 0.7)
            if position > start and score > best[0]:
                best = (score, match.end() if weight <= 20 else position)
    return max(start + 1, best[1])


def split_text(text: str, target_chars: int = 800, overlap: int = 120) -> list[tuple[str, int, int]]:
    if not text or not text.strip():
        return []
    chunks: list[tuple[str, int, int]] = []
    start = 0
    radius = max(80, int(target_chars * 0.22))
    while start < len(text):
        target = min(start + target_chars, len(text))
        end = len(text) if target >= len(text) else _best_break(text, start, target, radius)
        raw = text[start:end]
        left_trim = len(raw) - len(raw.lstrip())
        right_trimmed = raw.rstrip()
        if right_trimmed:
            piece_start = start + left_trim
            piece_end = start + len(right_trimmed)
            chunks.append((text[piece_start:piece_end], piece_start, piece_end))
        if end >= len(text):
            break
        start = max(start + 1, end - max(0, overlap))
    return chunks
