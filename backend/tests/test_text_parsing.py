"""Tests for Markdown/text and Excel parsing, line-based chunking, and the dry run."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from openpyxl import Workbook

from app import ingest
from app.config import Settings
from app.ingestion import chunk_lines, preview_document
from app.text_parsing import parse_markdown, parse_xlsx


def test_markdown_sections_keep_their_heading_path_and_drop_rules() -> None:
    parsed = parse_markdown(
        (
            "# Fiche de synthèse\n> Cours de Mme Gal\n\n---\n"
            "## 1. Fondamentaux\n### Définitions\n- **Moyenne** : somme / n\n"
            "## 2. Probabilité\nP(A) = cas favorables / cas possibles\n"
            "```\n# pas un titre\n```\n"
        ).encode()
    )

    assert parsed.title == "Fiche de synthèse"
    assert not parsed.paginated
    assert [(segment.heading_path, segment.text) for segment in parsed.pages[0].segments] == [
        ((), "> Cours de Mme Gal"),
        (("1. Fondamentaux",), ""),
        (("1. Fondamentaux", "Définitions"), "- **Moyenne** : somme / n"),
        (
            ("2. Probabilité",),
            "P(A) = cas favorables / cas possibles\n```\n# pas un titre\n```",
        ),
    ]


def test_plain_text_without_headings_is_one_untitled_segment() -> None:
    parsed = parse_markdown("Mean : moyenne\r\nMedian : médiane\r\n".encode("utf-8-sig"))

    assert parsed.title is None
    assert [segment.text for segment in parsed.pages[0].segments] == [
        "Mean : moyenne\nMedian : médiane"
    ]


def test_long_tables_are_split_by_row_and_repeat_their_header() -> None:
    rows = "\n".join(f"| {index} | Exemple numéro {index} | **A** |" for index in range(40))
    text = f"Associer chaque exemple :\n| # | Exemple | Réponse |\n|---|---|---|\n{rows}"

    chunks = chunk_lines(text, chunk_size=400, overlap=100)

    assert len(chunks) > 2
    for chunk in chunks[1:]:
        assert chunk.splitlines()[:2] == ["| # | Exemple | Réponse |", "|---|---|---|"]
    body_rows = [
        line for chunk in chunks for line in chunk.splitlines() if "Exemple numéro" in line
    ]
    assert len(body_rows) == 40  # every row appears exactly once, never cut in half


def test_bullets_are_never_cut_and_short_trailing_lines_overlap() -> None:
    bullets = [(f"- Point numéro {index} " + "détail " * 8).strip() for index in range(12)]

    chunks = chunk_lines("\n".join(bullets), chunk_size=300, overlap=80)

    assert all(line in bullets for chunk in chunks for line in chunk.splitlines())
    assert all(len(chunk) <= 300 for chunk in chunks)


def _workbook_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "T-test"
    sheet.append(["Paired", "H0: training has no effect", "If p>0,05 do not reject H0"])
    sheet.append([130, 115])
    sheet.append([112, 111])
    sheet.append(["p =", 0.00180427])
    workbook.create_sheet("Vide").append([1, 2, 3])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_workbooks_keep_explanatory_rows_and_summarize_numeric_data() -> None:
    parsed = parse_xlsx(_workbook_bytes())

    assert parsed.page_count == 2
    assert parsed.empty_pages == (2,)
    segment = parsed.pages[0].segments[0]
    assert segment.heading_path == ("Feuille T-test",)
    assert segment.text.splitlines() == [
        "Paired | H0: training has no effect | If p>0,05 do not reject H0",
        "p = | 0.00180427",
        "(2 lignes de données numériques non reproduites)",
    ]


def test_preview_handles_every_supported_format(tmp_path: Path) -> None:
    course = tmp_path / "Stats"
    course.mkdir()
    (course / "fiche.md").write_text("# Fiche\n## Variance\nMoyenne des carrés des écarts.")
    (course / "glossaire.txt").write_text("Mean : moyenne")
    (course / "exercices.xlsx").write_bytes(_workbook_bytes())

    previews = {
        name: preview_document(
            source_directory=tmp_path, selected_file=f"Stats/{name}", subject="S", course_id="Stats"
        )
        for name in ("fiche.md", "glossaire.txt", "exercices.xlsx")
    }

    assert previews["fiche.md"].chunks == (
        (1, "Variance", "Variance\nMoyenne des carrés des écarts."),
    )
    assert previews["fiche.md"].title == "Fiche"
    assert previews["glossaire.txt"].title == "glossaire"
    assert previews["exercices.xlsx"].chunks[0][1] == "Feuille T-test"
    assert previews["fiche.md"].embedding_inputs[0].startswith("S > Stats > Fiche > Variance\n\n")


def test_dry_run_prints_chunks_without_touching_the_database(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    (tmp_path / "Stats").mkdir()
    (tmp_path / "Stats" / "fiche.md").write_text("# Fiche\n## Variance\nMoyenne des carrés.")
    (tmp_path / "Stats" / "photo.jpeg").write_bytes(b"jpeg")
    monkeypatch.setattr(ingest, "get_settings", lambda: Settings(pdf_source_dir=tmp_path))
    monkeypatch.setattr(
        ingest,
        "create_database_engine",
        lambda _settings: (_ for _ in ()).throw(AssertionError("no database in dry run")),
    )

    assert ingest.main(["--all", "--dry-run"]) == 0

    output = capsys.readouterr()
    assert "== Stats/fiche.md" in output.out
    assert "subject=Général course=Stats title='Fiche'" in output.out
    assert "[Variance]" in output.out
    assert "1 of 1 files would be ingested" in output.out
    assert "skipped Stats/photo.jpeg: unsupported format .jpeg" in output.err
