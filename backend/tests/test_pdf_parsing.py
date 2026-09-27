"""Tests for slide-aware PDF parsing and the chunks and context built from it."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pymupdf
from test_ingestion import _FakeSessionFactory, _run_ingestion, _slides_pdf

from app.ingestion import contextual_embedding_text, preview_document, title_from_filename
from app.pdf_parsing import CORRECT_ANSWER_MARK, _clean, parse_pdf


def test_slides_keep_titles_and_bullets_and_drop_footers_and_page_numbers() -> None:
    slides = [
        ("Algorithmique avancée", "Prof. Martin"),
        (
            "Les listes chaînées",
            "- Une liste chaînée est une struc-\nture de données\n"
            "- Insertion en tête : O(1).\n-5 reste un nombre",
        ),
        ("Les piles", "- LIFO : dernier entré, premier sorti"),
        ("Les files", "- FIFO : premier entré, premier sorti"),
    ]
    parsed = parse_pdf(_slides_pdf(slides, footer="EPF - Algorithmique 2025"))

    assert parsed.title == "Algorithmique avancée"
    assert parsed.page_count == 4
    assert [page.title for page in parsed.pages] == [title for title, _ in slides]
    lists = parsed.pages[1]
    assert lists.body.splitlines() == [
        "- Une liste chaînée est une structure de données",
        "- Insertion en tête : O(1).",
        "-5 reste un nombre",
    ]
    assert all("EPF" not in page.text for page in parsed.pages)
    assert all(" / 4" not in page.text for page in parsed.pages)


def test_repeated_body_sized_titles_with_different_numbers_are_kept() -> None:
    document = pymupdf.open()
    for number in range(1, 5):
        page = document.new_page(width=960, height=540)
        page.insert_text((40, 40), f"Exercice {number}", fontsize=16)
        page.insert_textbox(
            pymupdf.Rect(40, 120, 920, 500), f"Calculer la somme des {number} termes.", fontsize=16
        )
    parsed = parse_pdf(bytes(document.tobytes()))

    assert [page.text.splitlines()[0] for page in parsed.pages] == [
        f"Exercice {number}" for number in range(1, 5)
    ]


def test_blank_slides_are_reported_as_empty_pages() -> None:
    parsed = parse_pdf(_slides_pdf([("Titre", "Contenu"), (None, ""), (None, "Suite")]))

    assert parsed.empty_pages == (2,)
    assert [page.number for page in parsed.pages] == [1, 3]


def test_ingestion_tracks_sections_and_embeds_chunks_with_their_course_context(
    tmp_path: Path,
) -> None:
    course = tmp_path / "Informatique" / "Algo"
    course.mkdir(parents=True)
    (course / "cours_3.pdf").write_bytes(
        _slides_pdf(
            [
                ("Structures de données", "Semestre 1"),
                ("Chapitre 2 : Les arbres", ""),
                ("Parcours en profondeur", "- Visite récursive des fils"),
            ]
        )
    )
    embedded: list[str] = []

    class RecordingProvider:
        provider_id = "deterministic"

        async def embed_batch(self, texts: list[str]) -> list[list[float]]:
            embedded.extend(texts)
            return [[0.5] * 768 for _ in texts]

    database = _FakeSessionFactory()
    asyncio.run(
        _run_ingestion(
            tmp_path,
            "Informatique/Algo/cours_3.pdf",
            "Algo",
            database,
            provider=RecordingProvider(),
            subject="Informatique",
        )
    )

    document = database.documents[0]
    assert (document.subject, document.title) == ("Informatique", "Structures de données")
    last = database.chunks[-1]
    assert last.section_title == "Chapitre 2 : Les arbres > Parcours en profondeur"
    assert last.text == "Parcours en profondeur\n- Visite récursive des fils"
    assert embedded[-1] == (
        "Informatique > Algo > Structures de données > "
        "Chapitre 2 : Les arbres > Parcours en profondeur\n\n"
        "Parcours en profondeur\n- Visite récursive des fils"
    )
    # The first slide is the deck's title page, not a section divider.
    assert database.chunks[0].section_title == "Structures de données"


def test_document_title_falls_back_to_the_filename() -> None:
    assert title_from_filename("week-2/03_listes__chainees.pdf") == "03 listes chainees"
    assert contextual_embedding_text(
        subject="Maths", course_id="Analyse", document_title="Suites", section_title=None,
        text="Une suite converge.",
    ) == "Maths > Analyse > Suites\n\nUne suite converge."


def test_only_real_section_dividers_start_a_section() -> None:
    from app.ingestion import _divider_kind
    from app.parsed import ParsedPage, Segment

    def page(number: int, title: str, body: str, image_share: float = 0.0) -> ParsedPage:
        return ParsedPage(
            number=number,
            segments=(Segment(heading_path=(title,), text=body),),
            titled=True,
            image_share=image_share,
        )

    assert _divider_kind(page(2, "Les arbres", "")) == "plain"
    assert _divider_kind(page(2, "Chapitre 3 : Graphes", "Parcours et plus courts chemins")) == (
        "named"
    )
    assert _divider_kind(page(2, "II. Les graphes", "")) == "named"
    assert _divider_kind(page(1, "Cours d'algorithmique", "")) is None
    assert _divider_kind(page(3, "Parcours en profondeur", "- Visite des fils")) is None
    assert _divider_kind(page(3, "Chapitre 3 : Graphes", "- Un graphe G = (V, E)")) is None
    # A title over a large picture is a diagram slide, not a divider.
    assert _divider_kind(page(4, "Cipher Feedback Block (CFB)", "", image_share=0.3)) is None


def _portrait_pdf(draw: Callable[[pymupdf.Page, int], None], pages: int = 1) -> bytes:
    document = pymupdf.open()
    for number in range(pages):
        draw(document.new_page(width=595, height=842), number)
    return bytes(document.tobytes())


def test_exponents_subscripts_and_ligatures_stay_readable() -> None:
    def draw(page: pymupdf.Page, _number: int) -> None:
        page.insert_text((60, 100), "Calculer 4", fontsize=11)
        page.insert_text((113.5, 95), "21", fontsize=7)
        page.insert_text((123, 100), "mod 493 et c", fontsize=11)
        page.insert_text((191, 103), "1", fontsize=7)

    parsed = parse_pdf(_portrait_pdf(draw))

    assert parsed.pages[0].text == "Calculer 4^21 mod 493 et c_1"
    # LaTeX PDFs extract "ff"/"fi" ligatures as single characters.
    assert _clean("Le chi\ufb00rement est d\u00e9\ufb01ni.") == "Le chiffrement est défini."


def test_exercise_headings_split_segments_and_continue_across_pages(tmp_path: Path) -> None:
    def draw(page: pymupdf.Page, number: int) -> None:
        if number == 0:
            page.insert_text((60, 80), "Exercice 1 : RSA", fontsize=14)
            page.insert_text((60, 110), "1. Calculer n = pq.", fontsize=10)
            page.insert_text((60, 150), "Exercice 2 : Elgamal", fontsize=14)
            page.insert_text((60, 180), "1. Quelle est la clef publique ?", fontsize=10)
        else:
            page.insert_text((60, 100), "2. Montrer que D(E(m)) = m.", fontsize=10)

    (tmp_path / "Crypto").mkdir()
    (tmp_path / "Crypto" / "td.pdf").write_bytes(_portrait_pdf(draw, pages=2))
    preview = preview_document(
        source_directory=tmp_path, selected_file="Crypto/td.pdf", subject="S", course_id="Crypto"
    )

    assert [(page, section) for page, section, _ in preview.chunks] == [
        (1, "Exercice 1 : RSA"),
        (1, "Exercice 2 : Elgamal"),
        (2, "Exercice 2 : Elgamal"),
    ]
    assert preview.chunks[2][2] == "2. Montrer que D(E(m)) = m."


def test_qcm_pages_split_per_question_and_mark_highlighted_answers() -> None:
    def draw(page: pymupdf.Page, _number: int) -> None:
        y = 80.0
        for question in range(1, 4):
            page.insert_text((60, y), f"{question}. Question numéro {question} ?", fontsize=10)
            for letter in "ABCD":
                y += 16
                page.insert_text((70, y), f"{letter}. Réponse {letter}", fontsize=10)
                if letter == "B":
                    page.add_highlight_annot(pymupdf.Rect(68, y - 10, 150, y + 3))
            y += 28

    parsed = parse_pdf(_portrait_pdf(draw))

    segments = parsed.pages[0].segments
    assert [segment.heading for segment in segments] == ["Question 1", "Question 2", "Question 3"]
    assert segments[0].text.splitlines() == [
        "1. Question numéro 1 ?",
        "A. Réponse A",
        "B. Réponse B" + CORRECT_ANSWER_MARK,
        "C. Réponse C",
        "D. Réponse D",
    ]


def test_beamer_overlay_steps_and_repeated_outlines_are_dropped() -> None:
    slides = [
        ("Cours", "Intro"),
        ("Sommaire", "1 RSA\n2 AES"),
        ("RSA", "- Choisir p et q"),
        ("RSA", "- Choisir p et q\n- Calculer n = pq"),
        ("Sommaire", "1 RSA\n2 AES"),
        ("AES", "- Chiffrement par blocs de 128 bits"),
    ]
    parsed = parse_pdf(_slides_pdf(slides))

    assert [(page.number, page.title) for page in parsed.pages] == [
        (1, "Cours"),
        (2, "Sommaire"),
        (4, "RSA"),
        (6, "AES"),
    ]


def test_slide_title_is_the_largest_text_even_below_large_bullets() -> None:
    document = pymupdf.open()
    for title in ("Agenda", "Suite"):
        page = document.new_page(width=960, height=540)
        page.insert_text((40, 60), title, fontsize=40)
        page.insert_text((40, 140), "- Statistiques descriptives", fontsize=24)
        page.insert_text((40, 180), "- Tests d'hypothèse", fontsize=24)
        page.insert_textbox(
            pymupdf.Rect(40, 220, 900, 500), "Texte courant " * 30, fontsize=18
        )
    parsed = parse_pdf(bytes(document.tobytes()))

    agenda = parsed.pages[0]
    assert agenda.title == "Agenda"
    assert agenda.body.splitlines()[:2] == ["- Statistiques descriptives", "- Tests d'hypothèse"]
    assert len(agenda.segments) == 1


def test_a_single_plain_divider_does_not_label_the_rest_of_the_deck(tmp_path: Path) -> None:
    (tmp_path / "Stats").mkdir()
    (tmp_path / "Stats" / "deck.pdf").write_bytes(
        _slides_pdf(
            [
                ("Statistics", "Cours"),
                ("Practice", ""),
                ("Exercise", "- Identify the data type"),
                ("Normal law", "- Bell-shaped distribution"),
            ]
        )
    )
    preview = preview_document(
        source_directory=tmp_path, selected_file="Stats/deck.pdf", subject="S", course_id="Stats"
    )

    assert [section for _, section, _ in preview.chunks][-1] == "Normal law"
