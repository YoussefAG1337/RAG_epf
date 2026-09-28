"""Measure retrieval (and optionally answers) on questions with known source passages.

    uv run --project backend python -m app.evaluate            # retrieval only, no Gemini
    uv run --project backend python -m app.evaluate --answers  # also generate answers

Retrieval reports where the right passage ranks and how relevant and off-topic questions
score, which is what EVIDENCE_MINIMUM_SCORE must separate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from pathlib import Path
from typing import Any

from app.answering import (
    AnsweringError,
    NoEvidenceError,
    Scope,
    UnsupportedQuestionError,
    _retrieve,
    answer_question,
)
from app.config import get_settings
from app.db import create_database_engine, create_session_factory
from app.providers.deterministic import DeterministicEmbeddingProvider
from app.providers.gemini import GeminiGenerationProvider
from app.providers.local import LocalEmbeddingProvider

QUESTIONS = Path(__file__).resolve().parents[1] / "eval" / "questions.json"
DEPTH = 8


def _matches(row: Any, targets: list[dict[str, str]]) -> bool:
    blob = f"{row.section_title or ''}\n{row.excerpt}"
    return any(t["source"] in row.source_filename and t["match"] in blob for t in targets)


async def run(with_answers: bool) -> int:
    settings = get_settings()
    data = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    engine = create_database_engine(settings)
    sessions = create_session_factory(engine)
    embedder: Any = (
        LocalEmbeddingProvider(
            settings.embedding_url,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
        )
        if settings.embedding_provider == "local"
        else DeterministicEmbeddingProvider(settings.embedding_dimensions)
    )
    ranks: list[int | None] = []
    target_scores: list[float] = []
    try:
        print(f"Retrieval ({settings.embedding_model}, top {DEPTH}):")
        for item in data["questions"]:
            vector = await embedder.embed(item["question"])
            rows = _retrieve(
                session_factory=sessions,
                scope=Scope(),
                query_embedding=vector,
                limit=DEPTH,
                minimum_score=-1.0,
            )
            rank = next((i for i, row in enumerate(rows) if _matches(row, item["targets"])), None)
            ranks.append(rank)
            if rank is not None:
                target_scores.append(rows[rank].score)
            else:
                top = rows[0] if rows else None
                where = f"{top.source_filename} [{top.section_title}]" if top else "nothing"
                print(f"  MISS  {item['question'][:55]!r} -> top: {where}")
        off_scores = []
        for question in data["off_topic"]:
            vector = await embedder.embed(question)
            rows = _retrieve(
                session_factory=sessions,
                scope=Scope(),
                query_embedding=vector,
                limit=1,
                minimum_score=-1.0,
            )
            off_scores.append(rows[0].score if rows else 0.0)

        total = len(ranks)

        def share(limit: int) -> str:
            hits = sum(1 for rank in ranks if rank is not None and rank < limit)
            return f"{hits}/{total} ({hits / total:.0%})"

        mrr = sum(1 / (rank + 1) for rank in ranks if rank is not None) / total
        print(f"  hit@1 {share(1)}   hit@3 {share(3)}   hit@{DEPTH} {share(DEPTH)}   MRR {mrr:.3f}")
        if target_scores:
            print(
                f"  right passage score: min {min(target_scores):.3f}  "
                f"median {statistics.median(target_scores):.3f}"
            )
        print(f"  off-topic best score: max {max(off_scores):.3f}  ({len(off_scores)} questions)")
        if target_scores:
            gap_low, gap_high = max(off_scores), min(target_scores)
            verdict = "clean gap" if gap_low < gap_high else "OVERLAP"
            print(
                f"  cutoff in use {settings.minimum_score:.2f}; separating range "
                f"{gap_low:.3f}..{gap_high:.3f} ({verdict})"
            )

        if with_answers:
            await _answers(data, settings, sessions, embedder)
    finally:
        aclose = getattr(embedder, "aclose", None)
        if aclose is not None:
            await aclose()
        engine.dispose()
    return 0


async def _answers(data: dict[str, Any], settings: Any, sessions: Any, embedder: Any) -> None:
    generator = GeminiGenerationProvider(
        settings.gemini_api_key,
        model=settings.answer_model,
        fallback_models=settings.fallback_answer_models,
    )
    answered = abstained = failed = quotes = verified = 0
    print("\nAnswers:")
    try:
        cases = [(item["question"], True) for item in data["questions"]]
        cases += [(question, False) for question in data["off_topic"]]
        for question, answerable in cases:
            try:
                answer = await answer_question(
                    question=question,
                    scope=Scope(),
                    session_factory=sessions,
                    embedding_provider=embedder,
                    generation_provider=generator,
                    settings=settings,
                )
            except (NoEvidenceError, UnsupportedQuestionError):
                abstained += 1
                if answerable:
                    print(f"  ABSTAINED  {question[:60]!r}")
                continue
            except AnsweringError as error:
                failed += 1
                print(f"  ERROR      {question[:60]!r}: {error}")
                continue
            answered += 1
            if not answerable:
                print(f"  ANSWERED OFF-TOPIC  {question[:60]!r}")
            for citation in answer.citations:
                quotes += 1
                verified += bool(citation.quotes)
    finally:
        generator.close()
    print(
        f"  answered {answered}, abstained {abstained}, errors {failed}; "
        f"citations with a verified quote {verified}/{quotes}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.evaluate", description=__doc__)
    parser.add_argument("--answers", action="store_true", help="also generate answers (Gemini)")
    args = parser.parse_args()
    return asyncio.run(run(args.answers))


if __name__ == "__main__":
    raise SystemExit(main())
