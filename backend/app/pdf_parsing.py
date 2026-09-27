"""Layout-aware PDF text extraction tuned for lecture slides, exercise sheets, and QCMs.

Each physical page is read with its font sizes, positions, and annotations so that we can:

* find headings (text noticeably larger than the body text) anywhere on the page, so a
  slide title or an "Exercice 3" heading starts its own segment,
* drop running headers/footers and page numbers repeated across pages,
* keep math readable: superscripts become ``^`` (``4^21``) and subscripts ``_`` (``c_1``),
* rejoin lines that a text box wrapped, mend hyphenated words and ligatures (``ﬁ``),
* keep bullets as separate lines instead of flattening a slide into one sentence,
* drop Beamer overlay steps (the same slide revealed bullet by bullet),
* split QCM pages into one segment per question and mark highlighted answers.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any

import pymupdf

from app.parsed import ParsedDocument, ParsedPage, Segment

# Text at least this much larger than the document's body text is a heading.
HEADING_SIZE_RATIO = 1.2
# A heading starting in this top part of the page is the page (slide) title.
TITLE_TOP_FRACTION = 0.35
# Running headers/footers live in these top/bottom bands of the page.
MARGIN_BAND_FRACTION = 0.12
# A margin line repeated on at least this share of pages (and at least twice) is boilerplate.
REPEATED_PAGE_SHARE = 0.5
MINIMUM_REPEATS = 2
# A page with at least this many numbered questions followed by lettered options is a QCM.
MINIMUM_QCM_QUESTIONS = 3

# Symbol bullets (including Wingdings private-use glyphs from PowerPoint) may touch the
# text; ASCII-like dashes need a following space so "-5" stays a number.
_BULLET = re.compile(
    r"^(?:[•‣⁃∙▪▫■□●○◦▶▷"
    r"▸►◆♦➢➤➔✓✔☐"
    r"·]\s*|[*–—>-]\s+)"
)
_PAGE_NUMBER = re.compile(r"(page\s*)?\d{1,4}(\s*(/|sur|of)\s*\d{1,4})?")
_SENTENCE_END = re.compile(r"[.!?:;]$")
_QUESTION = re.compile(r"^(\d{1,3})\s*[.)]\s+\S")
_OPTION = re.compile(r"^[A-Ha-h]\s*[.)]\s+\S")
_LIGATURES = str.maketrans(
    {
        "ﬀ": "ff",
        "ﬁ": "fi",
        "ﬂ": "fl",
        "ﬃ": "ffi",
        "ﬄ": "ffl",
        "ﬅ": "st",
        "ﬆ": "st",
    }
)
# pymupdf.PDF_ANNOT_HIGHLIGHT, which PyMuPDF's type hints do not declare.
_HIGHLIGHT_ANNOTATION: int = getattr(pymupdf, "PDF_ANNOT_HIGHLIGHT", 8)
CORRECT_ANSWER_MARK = " ✔ (réponse surlignée)"
HIGHLIGHT_MARK = " [surligné]"


class PdfParsingError(RuntimeError):
    """The PDF could not be opened or read."""


class EncryptedPdfError(PdfParsingError):
    """The PDF requires a password."""


@dataclass(frozen=True)
class _Line:
    text: str
    size: float
    top: float
    bottom: float
    block: int
    highlighted: bool = False


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFC", text.translate(_LIGATURES)).replace("\x00", "")
    return re.sub(r"\s+", " ", text).strip()


def _line_text(spans: list[dict[str, Any]], size: float, baseline: float) -> str:
    """Join spans, writing raised text as ``^x`` and lowered small text as ``_x``."""

    parts: list[str] = []
    for span in spans:
        text = span.get("text", "")
        stripped = text.strip()
        if not stripped:
            parts.append(text)
            continue
        trailing = " " if text.endswith(" ") else ""
        span_size = float(span["size"])
        origin_y = float(span.get("origin", (0.0, baseline))[1])
        raised = span_size < size * 0.85 and origin_y < baseline - size * 0.1
        lowered = span_size < size * 0.85 and origin_y > baseline + size * 0.1
        if int(span.get("flags", 0)) & 1 or raised or lowered:
            # Attach to the base ("4 ^21" -> "4^21"): gaps before small raised or lowered
            # text are layout, not word breaks.
            joined = "".join(parts).rstrip()
            parts = [joined, f"{'_' if lowered and not raised else '^'}{stripped}{trailing}"]
        else:
            parts.append(text)
    return "".join(parts)


def _page_lines(page: Any) -> list[_Line]:
    highlights = [
        annotation.rect for annotation in page.annots(types=[_HIGHLIGHT_ANNOTATION])
    ]
    lines: list[_Line] = []
    content = page.get_text("dict", sort=True)
    for block_index, block in enumerate(content.get("blocks", [])):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = [span for span in line.get("spans", []) if span.get("text", "").strip()]
            if not spans:
                continue
            # The dominant size is the one covering the most characters in the line.
            sizes: Counter[float] = Counter()
            for span in spans:
                sizes[round(float(span["size"]), 1)] += len(span["text"].strip())
            size = sizes.most_common(1)[0][0]
            dominant = next(span for span in spans if round(float(span["size"]), 1) == size)
            baseline = float(dominant.get("origin", (0.0, line["bbox"][3]))[1])
            text = _clean(_line_text(line["spans"], size, baseline))
            if not text:
                continue
            rect = pymupdf.Rect(line["bbox"])
            highlighted = False
            for highlight in highlights:
                overlap = rect & highlight
                if overlap.height >= rect.height * 0.5 and overlap.width >= rect.width * 0.3:
                    highlighted = True
                    break
            lines.append(
                _Line(
                    text=text,
                    size=size,
                    top=float(rect.y0),
                    bottom=float(rect.y1),
                    block=block_index,
                    highlighted=highlighted,
                )
            )
    return lines


def _image_share(page: Any) -> float:
    area = float(page.rect.width) * float(page.rect.height)
    if area <= 0:
        return 0.0
    covered = 0.0
    for image in page.get_images():
        for rect in page.get_image_rects(image[0]):
            covered += float((rect & page.rect).width) * float((rect & page.rect).height)
    return min(1.0, covered / area)


def _boilerplate_key(line: _Line, body_size: float) -> str:
    """Key for spotting repeated margin text.

    Small print (footers such as "EPF 2025 - 3/40") matches regardless of numbers.
    Body-sized text must repeat exactly, so titles like "Exercice 1", "Exercice 2" survive.
    """

    text = line.text.casefold()
    return re.sub(r"\d+", "#", text) if line.size < body_size else text


def _in_margin(line: _Line, height: float) -> bool:
    band = height * MARGIN_BAND_FRACTION
    return line.top >= height - band or line.bottom <= band


def _body_size(pages: list[list[_Line]]) -> float:
    """The font size covering the most characters across the document.

    Each page's topmost line (usually the slide title) is left out, so short decks whose
    titles hold as much text as their bodies still get the body size right.
    """

    sizes: Counter[float] = Counter()
    fallback: Counter[float] = Counter()
    for lines in pages:
        if not lines:
            continue
        first_top = min(line.top for line in lines)
        for line in lines:
            fallback[line.size] += len(line.text)
            if line.top > first_top:
                sizes[line.size] += len(line.text)
    chosen = sizes or fallback
    return chosen.most_common(1)[0][0] if chosen else 0.0


def _marked(line: _Line) -> str:
    if not line.highlighted:
        return line.text
    return line.text + (CORRECT_ANSWER_MARK if _OPTION.match(line.text) else HIGHLIGHT_MARK)


def _join_lines(lines: list[_Line]) -> str:
    """Rejoin wrapped lines inside each text block while keeping bullets and blocks apart."""

    items: list[str] = []
    previous_block: int | None = None
    for line in lines:
        text = _marked(line)
        bullet = _BULLET.match(text)
        if bullet:
            text = ("- " + text[bullet.end() :].strip()).rstrip()
        if items and items[-1] == "-":
            items[-1] = "- " + text  # a bullet glyph extracted as its own line
        elif (
            not items
            or line.block != previous_block
            or bullet is not None
            or _SENTENCE_END.search(items[-1]) is not None
            or _QUESTION.match(text)
            or _OPTION.match(text)
            or items[-1].endswith((HIGHLIGHT_MARK, CORRECT_ANSWER_MARK))
        ):
            items.append(text)
        elif re.search(r"[^\W\d_]-$", items[-1]) and text[:1].islower():
            items[-1] = items[-1][:-1] + text  # "struc-" + "ture" -> "structure"
        else:
            items[-1] = f"{items[-1]} {text}"
        previous_block = line.block
    return "\n".join(item for item in items if item.strip(" -"))


def _join_heading(lines: list[_Line]) -> str:
    heading = ""
    for line in lines:
        if re.search(r"[^\W\d_]-$", heading) and line.text[:1].islower():
            heading = heading[:-1] + line.text  # "arith-" + "métique"
        else:
            heading = f"{heading} {line.text}" if heading else line.text
    return _clean(heading)


def _slide_segments(lines: list[_Line], body_size: float) -> tuple[list[Segment], bool]:
    """A slide has one title: its largest heading-sized text (with the lines of the same
    text box at that size), wherever it sits. Everything else is body, since slide decks
    size bullets and diagram labels freely."""

    candidates = [
        line for line in lines if body_size > 0 and line.size >= body_size * HEADING_SIZE_RATIO
    ]
    if not candidates:
        text = _join_lines(lines)
        return ([Segment(heading_path=(), text=text)] if text else []), False
    largest = max(line.size for line in candidates)
    title_block = next(line.block for line in candidates if line.size >= largest - 0.5)
    title_lines = [
        line for line in lines if line.block == title_block and line.size >= largest - 0.5
    ]
    body = [line for line in lines if line not in title_lines]
    return [Segment(heading_path=(_join_heading(title_lines),), text=_join_lines(body))], True


def _segments(
    lines: list[_Line], height: float, body_size: float, *, slide: bool
) -> tuple[list[Segment], bool]:
    """Split a page at heading lines; report whether it starts with a title.

    Slides (landscape pages) get a single title. On portrait pages (exercise sheets,
    notes) headings such as "Exercice 3" can appear anywhere and each starts a segment.
    """

    if slide:
        return _slide_segments(lines, body_size)

    def is_heading(line: _Line) -> bool:
        return body_size > 0 and line.size >= body_size * HEADING_SIZE_RATIO

    groups: list[tuple[list[_Line], list[_Line]]] = []  # (heading lines, body lines)
    for line in lines:
        if is_heading(line):
            previous = groups[-1] if groups else None
            if (
                previous is not None
                and previous[0]
                and not previous[1]
                and previous[0][-1].block == line.block
                and abs(previous[0][-1].size - line.size) <= 0.5
            ):
                previous[0].append(line)  # a heading wrapped onto several lines
            else:
                groups.append(([line], []))
        elif groups:
            groups[-1][1].append(line)
        else:
            groups.append(([], [line]))

    segments = [
        Segment(
            heading_path=(_join_heading(heading),) if heading else (),
            text=_join_lines(body),
        )
        for heading, body in groups
    ]
    titled = bool(
        groups and groups[0][0] and groups[0][0][0].top <= height * TITLE_TOP_FRACTION
    )
    return [segment for segment in segments if segment.heading_path or segment.text], titled


def _split_questions(segments: list[Segment]) -> list[Segment]:
    """Give each QCM question (a numbered line followed by lettered options) its own segment."""

    lines = [
        (index, line)
        for index, segment in enumerate(segments)
        for line in segment.text.splitlines()
    ]
    starts = [
        position
        for position, (_, line) in enumerate(lines)
        if _QUESTION.match(line)
        and any(_OPTION.match(following) for _, following in lines[position + 1 : position + 4])
    ]
    if len(starts) < MINIMUM_QCM_QUESTIONS:
        return segments

    owner = lines[starts[0]][0]
    result = list(segments[:owner])
    leading = [line for index, line in lines[: starts[0]] if index == owner]
    if segments[owner].heading_path or leading:
        result.append(replace(segments[owner], text="\n".join(leading)))
    for number, start in enumerate(starts):
        end = starts[number + 1] if number + 1 < len(starts) else len(lines)
        question_lines = [line for _, line in lines[start:end]]
        match = _QUESTION.match(question_lines[0])
        label = f"Question {match.group(1)}" if match else f"Question {number + 1}"
        result.append(Segment(heading_path=(label,), text="\n".join(question_lines)))
    return result


def _alphanumeric_count(text: str) -> int:
    return sum(character.isalnum() for character in text)


def _drop_repeated_pages(pages: list[ParsedPage]) -> list[ParsedPage]:
    """Drop Beamer overlay steps and pages repeated verbatim (such as a recurring outline).

    An overlay step is a page followed by one with the same title whose content only grows,
    or whose own body is nearly empty (a diagram being built up letter by letter).
    """

    kept: list[ParsedPage] = []
    seen_texts: set[str] = set()
    followers: list[ParsedPage | None] = [*pages[1:], None] if pages else []
    for page, following in zip(pages, followers, strict=True):
        if (
            following is not None
            and page.title
            and page.title == following.title
            and following.number == page.number + 1
            and (
                set(page.text.splitlines()) <= set(following.text.splitlines())
                or _alphanumeric_count(page.body) < 20 <= _alphanumeric_count(following.body)
            )
        ):
            continue
        if page.text in seen_texts:
            continue
        seen_texts.add(page.text)
        kept.append(page)
    return kept


def parse_pdf(data: bytes) -> ParsedDocument:
    """Extract cleaned per-page segments, headings, and slide titles from PDF bytes."""

    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as error:  # PyMuPDF raises several unrelated types for bad input.
        raise PdfParsingError(str(error) or "unreadable PDF") from error
    with document:
        if document.needs_pass:
            raise EncryptedPdfError("PDF is encrypted")
        try:
            pages = [document[index] for index in range(document.page_count)]
            heights = [float(page.rect.height) for page in pages]
            slides = [float(page.rect.width) > float(page.rect.height) for page in pages]
            image_shares = [_image_share(page) for page in pages]
            page_lines = [_page_lines(page) for page in pages]
        except Exception as error:
            raise PdfParsingError(str(error) or "unreadable PDF page") from error

    page_count = len(page_lines)
    body_size = _body_size(page_lines)

    # Running headers/footers: margin text no larger than body text, repeated across pages.
    repeats: Counter[str] = Counter()
    for lines, height in zip(page_lines, heights, strict=True):
        repeats.update(
            {
                _boilerplate_key(line, body_size)
                for line in lines
                if _in_margin(line, height) and line.size <= body_size
            }
        )
    threshold = max(MINIMUM_REPEATS, math.ceil(page_count * REPEATED_PAGE_SHARE))
    boilerplate = {key for key, count in repeats.items() if count >= threshold}

    parsed: list[ParsedPage] = []
    empty_pages: list[int] = []
    for number, (lines, height, slide, image_share) in enumerate(
        zip(page_lines, heights, slides, image_shares, strict=True), start=1
    ):
        kept = [
            line
            for line in lines
            if not (
                _in_margin(line, height)
                and line.size <= body_size
                and (
                    _boilerplate_key(line, body_size) in boilerplate
                    or _PAGE_NUMBER.fullmatch(line.text.casefold())
                )
            )
        ]
        segments, titled = _segments(kept, height, body_size, slide=slide)
        segments = _split_questions(segments)
        if segments:
            parsed.append(
                ParsedPage(
                    number=number,
                    segments=tuple(segments),
                    titled=titled,
                    image_share=image_share,
                )
            )
        else:
            empty_pages.append(number)

    parsed = _drop_repeated_pages(parsed)
    first_title = parsed[0].title if parsed and parsed[0].number == 1 else None
    return ParsedDocument(
        title=first_title,
        page_count=page_count,
        pages=tuple(parsed),
        empty_pages=tuple(empty_pages),
    )
