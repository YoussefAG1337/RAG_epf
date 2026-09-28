"""Tests for mapping chunk text to source lines and quotes to highlights."""

from app.locating import find_quote, highlights, locate_lines, skeleton
from app.parsed import LineRef


def test_skeleton_ignores_case_accents_and_punctuation() -> None:
    text, offsets = skeleton("Été : n = pq !")
    assert text == "etenpq"
    assert offsets == [0, 1, 2, 6, 10, 11]


def test_lines_are_located_through_rejoined_bullets_and_mended_hyphens() -> None:
    chunk = "Définition\n- Une liste chaînée est une structure de données\n- Insertion : O(1)."
    lines = [
        LineRef(text="Définition", page=3, box=(0.1, 0.1, 0.4, 0.15)),
        LineRef(text="• Une liste chaînée est une struc-", page=3, box=(0.1, 0.2, 0.8, 0.25)),
        LineRef(text="ture de données", page=3, box=(0.1, 0.25, 0.5, 0.3)),
        LineRef(text="m", page=3, box=(0.9, 0.9, 0.95, 0.95)),  # a diagram label: too short
        LineRef(text="• Insertion : O(1).", page=3, box=(0.1, 0.3, 0.5, 0.35)),
    ]

    spans = locate_lines(chunk, lines)

    assert [chunk[span["s"] : span["e"]] for span in spans] == [
        "Définition",
        "Une liste chaînée est une struc",
        "ture de données",
        "Insertion : O(1",
    ]
    assert all(span["p"] == 3 for span in spans)
    assert spans[0]["b"] == [0.1, 0.1, 0.4, 0.15]


def test_text_file_lines_keep_their_line_numbers() -> None:
    spans = locate_lines(
        "| Mean | Moyenne |\n| Median | Médiane |",
        [
            LineRef(text="| **Mean** | Moyenne |", page=1, line=3),
            LineRef(text="| **Median** | Médiane |", page=1, line=4),
        ],
    )
    assert [span["l"] for span in spans] == [3, 4]


def test_quotes_are_found_exactly_or_with_small_differences() -> None:
    chunk = "RSA repose sur le problème de la factorisation : étant donné n = pq, retrouver p et q."

    exact = find_quote(chunk, "le problème de la factorisation")
    assert exact is not None
    assert chunk[exact[0] : exact[1]] == "le problème de la factorisation"

    reworded = find_quote(chunk, "étant donne n=pq retrouver p et q")
    assert reworded is not None
    assert "retrouver p et q" in chunk[reworded[0] : reworded[1]]

    assert find_quote(chunk, "la courbe elliptique est définie sur un corps fini") is None
    assert find_quote(chunk, "p") is None


def test_highlights_group_overlapping_lines_by_page() -> None:
    locations = [
        {"s": 0, "e": 10, "p": 4, "b": [0.1, 0.1, 0.5, 0.15]},
        {"s": 11, "e": 30, "p": 4, "b": [0.1, 0.2, 0.9, 0.25]},
        {"s": 31, "e": 50, "p": 5, "b": [0.1, 0.1, 0.9, 0.15]},
    ]

    assert highlights(locations, (12, 20)) == [
        {"page": 4, "boxes": [[0.1, 0.2, 0.9, 0.25]], "lines": []}
    ]
    assert [item["page"] for item in highlights(locations, None)] == [4, 5]
