"""Map chunk text back to where it sits in the source file, and quotes back to that text.

Chunk text is cleaned and re-flowed during parsing (bullets rewritten, hyphenated words
mended, lines joined), so it cannot be sliced by offsets from the source. Instead, both
sides are reduced to a "skeleton" (lowercase letters and digits, accents removed) and
matched there; skeleton positions map back to character offsets in the chunk text.
"""

from __future__ import annotations

import unicodedata
from typing import Any

from rapidfuzz import fuzz

from app.parsed import LineRef

# A quote is accepted if its skeleton matches the chunk this closely (0-100).
QUOTE_MINIMUM_SIMILARITY = 85.0
# Lines this short ("m", "1") match almost anywhere, so they are not located on their own.
MINIMUM_LINE_SKELETON = 3


def skeleton(text: str) -> tuple[str, list[int]]:
    """Lowercase alphanumerics without accents, with each one's offset in ``text``."""

    characters: list[str] = []
    offsets: list[int] = []
    for offset, character in enumerate(text):
        for part in unicodedata.normalize("NFKD", character.casefold()):
            if part.isalnum() and not unicodedata.combining(part):
                characters.append(part)
                offsets.append(offset)
    return "".join(characters), offsets


def locate_lines(chunk_text: str, lines: list[LineRef]) -> list[dict[str, Any]]:
    """Find each source line inside the chunk text, in order.

    Returns JSON-ready spans ``{"s": start, "e": end, "p": page, "b": box | "l": line}``
    with character offsets into ``chunk_text``. Lines that cannot be found are skipped.
    """

    chunk_skeleton, offsets = skeleton(chunk_text)
    cursor = 0
    spans: list[dict[str, Any]] = []
    for line in lines:
        line_skeleton, _ = skeleton(line.text)
        if len(line_skeleton) < MINIMUM_LINE_SKELETON:
            continue
        found = chunk_skeleton.find(line_skeleton, cursor)
        if found < 0:
            continue
        end = found + len(line_skeleton)
        span: dict[str, Any] = {"s": offsets[found], "e": offsets[end - 1] + 1, "p": line.page}
        if line.box is not None:
            span["b"] = [round(value, 5) for value in line.box]
        if line.line is not None:
            span["l"] = line.line
        spans.append(span)
        cursor = end
    return spans


def find_quote(chunk_text: str, quote: str) -> tuple[int, int] | None:
    """Character span of ``quote`` in ``chunk_text``, tolerating small differences."""

    quote_skeleton, _ = skeleton(quote)
    if len(quote_skeleton) < MINIMUM_LINE_SKELETON:
        return None
    chunk_skeleton, offsets = skeleton(chunk_text)
    if not chunk_skeleton:
        return None
    start = chunk_skeleton.find(quote_skeleton)
    if start >= 0:
        end = start + len(quote_skeleton)
    else:
        alignment = fuzz.partial_ratio_alignment(quote_skeleton, chunk_skeleton)
        if alignment is None or alignment.score < QUOTE_MINIMUM_SIMILARITY:
            return None
        start, end = alignment.dest_start, alignment.dest_end
        if end <= start:
            return None
    return offsets[start], offsets[end - 1] + 1


def highlights(
    locations: list[dict[str, Any]], span: tuple[int, int] | None
) -> list[dict[str, Any]]:
    """Group the located lines overlapping ``span`` (or all lines) by page.

    Returns ``[{"page": n, "boxes": [[x0, y0, x1, y1], ...], "lines": [n, ...]}]``.
    """

    selected = [
        location
        for location in locations
        if span is None or (location["s"] < span[1] and location["e"] > span[0])
    ]
    pages: dict[int, dict[str, Any]] = {}
    for location in selected:
        page = pages.setdefault(location["p"], {"page": location["p"], "boxes": [], "lines": []})
        if "b" in location:
            page["boxes"].append(location["b"])
        if "l" in location:
            page["lines"].append(location["l"])
    return sorted(pages.values(), key=lambda item: int(item["page"]))
