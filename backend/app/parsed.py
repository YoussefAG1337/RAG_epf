"""Format-independent structure produced by every document parser."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LineRef:
    """One source line: a PDF text line (normalized box), a text-file line, or a sheet row."""

    text: str
    page: int
    box: tuple[float, float, float, float] | None = None  # x0, y0, x1, y1 as page fractions
    line: int | None = None  # 1-based line (text files) or row (spreadsheets)


@dataclass(frozen=True)
class Segment:
    """A run of text under one heading.

    ``heading_path`` is the heading (or nested headings, outermost first) that starts this
    segment. An empty path means the text continues the previous segment's heading, for
    example an exercise that runs onto the next page.
    """

    heading_path: tuple[str, ...]
    text: str
    # Indices into the page's ``lines`` that this segment was built from.
    line_indices: tuple[int, ...] = ()

    @property
    def heading(self) -> str | None:
        return self.heading_path[-1] if self.heading_path else None


@dataclass(frozen=True)
class ParsedPage:
    """One citable unit: a PDF page or slide, a spreadsheet sheet, or a whole text file."""

    number: int
    segments: tuple[Segment, ...]
    # True when the first segment's heading is the page (slide) title.
    titled: bool = False
    # Share of the page covered by images; a title-only "slide" that is mostly a picture
    # (a diagram) is content, not a section divider.
    image_share: float = 0.0
    # Source lines of the page, used to locate chunks in the original file.
    lines: tuple[LineRef, ...] = ()

    @property
    def title(self) -> str | None:
        return self.segments[0].heading if self.titled and self.segments else None

    @property
    def body(self) -> str:
        parts = [segment.text for segment in self.segments[:1] if segment.text]
        for segment in self.segments[1:]:
            parts.extend(part for part in (segment.heading, segment.text) if part)
        return "\n".join(parts) if self.titled else self.text

    @property
    def text(self) -> str:
        parts: list[str] = []
        for segment in self.segments:
            parts.extend(part for part in (segment.heading, segment.text) if part)
        return "\n".join(parts)


@dataclass(frozen=True)
class ParsedDocument:
    """All citable units that contain text, plus the ones that had none."""

    title: str | None
    page_count: int
    pages: tuple[ParsedPage, ...]
    empty_pages: tuple[int, ...]
    # PDFs are paginated (citations show a page); text files and spreadsheets are not.
    paginated: bool = True
