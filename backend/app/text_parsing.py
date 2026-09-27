"""Parsers for non-PDF course material: Markdown/plain text and Excel workbooks."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from io import BytesIO

from app.parsed import ParsedDocument, ParsedPage, Segment

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")


class TextParsingError(RuntimeError):
    """The file could not be decoded or read."""


def _plain_heading(text: str) -> str:
    text = re.sub(r"[*_`]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_markdown(data: bytes) -> ParsedDocument:
    """Split Markdown (or plain text) into one segment per heading, keeping heading paths.

    A single leading ``#`` heading is the document title and is left out of the paths.
    Tables, lists, and formulas are kept verbatim; horizontal rules are dropped.
    """

    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    lines = text.split("\n")

    headings: list[tuple[int, int, str]] = []  # (line index, level, text)
    in_fence = False
    for index, line in enumerate(lines):
        if _FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence and (match := _HEADING.match(line)):
            headings.append((index, len(match.group(1)), _plain_heading(match.group(2))))

    top_level = [heading for heading in headings if heading[1] == 1]
    title: str | None = None
    if len(top_level) == 1 and headings and headings[0] == top_level[0]:
        title = top_level[0][2]
        headings = headings[1:]
        body_start = top_level[0][0] + 1
    else:
        body_start = 0

    def body(start: int, end: int) -> str:
        kept = [line.rstrip() for line in lines[start:end] if not _RULE.match(line)]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()

    segments: list[Segment] = []
    first_heading_line = headings[0][0] if headings else len(lines)
    preamble = body(body_start, first_heading_line)
    if preamble:
        segments.append(Segment(heading_path=(), text=preamble))
    stack: list[tuple[int, str]] = []
    for position, (index, level, heading) in enumerate(headings):
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, heading))
        end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        segments.append(
            Segment(heading_path=tuple(item for _, item in stack), text=body(index + 1, end))
        )

    pages = (ParsedPage(number=1, segments=tuple(segments)),) if segments else ()
    return ParsedDocument(
        title=title,
        page_count=1,
        pages=pages,
        empty_pages=() if pages else (1,),
        paginated=False,
    )


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, datetime | date):
        return value.isoformat()
    return re.sub(r"\s+", " ", str(value)).strip()


def parse_xlsx(data: bytes) -> ParsedDocument:
    """Keep each sheet's explanatory rows (any row containing text) and drop pure data rows.

    Worked-example workbooks mix raw numbers with the parts worth retrieving: hypotheses,
    decision rules, and labeled results ("p = 0.012"). Rows made only of numbers carry no
    meaning on their own, so they are summarized by count instead of embedded.
    """

    try:
        from openpyxl import load_workbook

        workbook = load_workbook(BytesIO(data), read_only=True, data_only=True)
    except Exception as error:
        raise TextParsingError(str(error) or "unreadable workbook") from error

    pages: list[ParsedPage] = []
    empty: list[int] = []
    try:
        for number, sheet in enumerate(workbook.worksheets, start=1):
            kept_rows: list[str] = []
            data_rows = 0
            for row in sheet.iter_rows(values_only=True):
                cells = [_cell_text(value) for value in row]
                cells = [cell for cell in cells if cell]
                if not cells:
                    continue
                if any(re.search(r"[^\W\d_]", cell) for cell in cells):
                    kept_rows.append(" | ".join(cells))
                else:
                    data_rows += 1
            if not kept_rows:
                empty.append(number)
                continue
            if data_rows:
                kept_rows.append(f"({data_rows} lignes de données numériques non reproduites)")
            pages.append(
                ParsedPage(
                    number=number,
                    segments=(
                        Segment(
                            heading_path=(f"Feuille {sheet.title}",), text="\n".join(kept_rows)
                        ),
                    ),
                )
            )
        sheet_count = len(workbook.worksheets)
    finally:
        workbook.close()

    return ParsedDocument(
        title=None,
        page_count=sheet_count,
        pages=tuple(pages),
        empty_pages=tuple(empty),
        paginated=False,
    )
