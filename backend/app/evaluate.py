"""Measure retrieval (and optionally answers) on questions with known source passages.

    uv run --project backend python -m app.evaluate            # retrieval only, no LLM
    uv run --project backend python -m app.evaluate --answers  # also generate answers

Retrieval compares vector search alone, hybrid search (vector + keyword), and hybrid search
followed by reranking: where the right passage ranks, and how relevant and off-topic
questions score, which is what the relevance cutoffs must separate.
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
    answer_question,
    hybrid_candidates,
)
from app.config import get_settings
from app.db import create_database_engine, create_session_factory
from app.providers import (
    close_provider,
    create_embedding_provider,
    create_generation_provider,
    create_rerank_provider,
)

QUESTIONS = Path(__file__).resolve().parents[1] / "eval" / "questions.json"
DEPTH = 8


def _matches(row: Any, targets: list[dict[str, str]]) -> bool:
    blob = f"{row.section_title or ''}\n{row.excerpt}"
    return any(t["source"] in row.source_filename and t["match"] in blob for t in targets)


def _report(name: str, ranks: list[int | None]) -> None:
    total = len(ranks)

    def share(limit: int) -> str:
        hits = sum(1 for rank in ranks if rank is not None and rank < limit)
        return f"{hits}/{total} ({hits / total:.0%})"

    mrr = sum(1 / (rank + 1) for rank in ranks if rank is not None) / total
    print(
        f"  {name:<20} hit@1 {share(1)}   hit@3 {share(3)}   hit@{DEPTH} {share(DEPTH)}"
        f"   MRR {mrr:.3f}"
    )


def _gap(label: str, answerable: list[float], off_topic: list[float], cutoff: float) -> None:
    if not answerable:
        return
    low, high = max(off_topic), min(answerable)
    verdict = "clean gap" if low < high else "OVERLAP"
    print(
        f"  {label}: answerable min {high:.4f} (median {statistics.median(answerable):.3f}), "
        f"off-topic max {low:.4f} -> {verdict}; cutoff in use {cutoff:g}"
    )


async def run(with_answers: bool) -> int:
    settings = get_settings()
    data = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    engine = create_database_engine(settings)
    sessions = create_session_factory(engine)
    if not data["questions"] or not data["off_topic"]:
        raise SystemExit(f"{QUESTIONS} needs both answerable and off-topic questions")
    embedder = create_embedding_provider(settings)
    reranker = create_rerank_provider(settings)
    modes: dict[str, tuple[Any, Any]] = {
        "vector": (settings.model_copy(update={"keyword_search": False}), None),
        "hybrid": (settings.model_copy(update={"keyword_search": True}), None),
    }
    if reranker is not None:
        modes["hybrid + reranking"] = (
            settings.model_copy(update={"keyword_search": True}),
            reranker,
        )
    question_sets = {
        "paraphrases": data["questions"],
        "exact terms": data.get("exact_terms", []),
    }
    cosine_targets: list[float] = []
    best_rerank: list[float] = []
    try:
        print(f"Retrieval ({settings.embedding_model}, reranker {settings.reranker}):")
        for set_name, items in question_sets.items():
            if not items:
                continue
            ranks: dict[str, list[int | None]] = {name: [] for name in modes}
            for item in items:
                vector = await embedder.embed(item["question"])
                for name, (mode_settings, mode_reranker) in modes.items():
                    rows = await hybrid_candidates(
                        session_factory=sessions,
                        scope=Scope(),
                        query=item["question"],
                        query_embedding=vector,
                        settings=mode_settings,
                        reranker=mode_reranker,
                    )
                    rank = next(
                        (i for i, row in enumerate(rows) if _matches(row, item["targets"])), None
                    )
                    ranks[name].append(rank)
                    if rank is None or rank >= DEPTH:
                        top = rows[0] if rows else None
                        where = f"{top.source_filename} [{top.section_title}]" if top else "nothing"
                        print(f"  MISS ({name})  {item['question'][:50]!r} -> top: {where}")
                    if name == "vector" and rank is not None:
                        cosine_targets.append(rows[rank].score)
                    if mode_reranker is not None:
                        best_rerank.append(max(row.rerank_score or 0.0 for row in rows))
            print(f" {set_name} ({len(items)} questions)")
            for name, mode_ranks in ranks.items():
                _report(name, mode_ranks)

        cosine_off: list[float] = []
        rerank_off: list[float] = []
        for question in data["off_topic"]:
            vector = await embedder.embed(question)
            rows = await hybrid_candidates(
                session_factory=sessions,
                scope=Scope(),
                query=question,
                query_embedding=vector,
                settings=settings,
                reranker=reranker,
            )
            cosine_off.append(max((row.score for row in rows), default=0.0))
            rerank_off.append(max((row.rerank_score or 0.0 for row in rows), default=0.0))
        print(f" relevance cutoffs ({len(data['off_topic'])} off-topic questions)")
        _gap("cosine, right passage", cosine_targets, cosine_off, settings.minimum_score)
        if reranker is not None:
            _gap("reranker, best candidate", best_rerank, rerank_off, settings.rerank_cutoff)

        if with_answers:
            await _answers(data, settings, sessions, embedder, reranker)
    finally:
        await close_provider(embedder)
        await close_provider(reranker)
        engine.dispose()
    return 0


async def _answers(
    data: dict[str, Any], settings: Any, sessions: Any, embedder: Any, reranker: Any
) -> None:
    generator = create_generation_provider(settings)
    answered = refused = failed = quotes = verified = 0
    print(f"\nAnswers ({settings.answer_provider}, {settings.answer_model}):")
    try:
        answerable = data["questions"] + data.get("exact_terms", [])
        cases = [(item["question"], True) for item in answerable]
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
                    rerank_provider=reranker,
                )
            except (NoEvidenceError, UnsupportedQuestionError):
                if answerable:
                    print(f"  ABSTAINED  {question[:60]!r}")
                else:
                    refused += 1
                continue
            except AnsweringError as error:
                failed += 1
                print(f"  ERROR      {question[:60]!r}: {error}")
                continue
            if not answerable:
                print(f"  ANSWERED OFF-TOPIC  {question[:60]!r}")
                continue
            answered += 1
            for citation in answer.citations:
                quotes += 1
                verified += bool(citation.quotes)
    finally:
        await close_provider(generator)
    print(
        f"  answerable questions answered {answered}/{len(answerable)}   "
        f"off-topic refused {refused}/{len(data['off_topic'])}   errors {failed}\n"
        f"  citations of answers with a verified quote {verified}/{quotes}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.evaluate", description=__doc__)
    parser.add_argument(
        "--answers", action="store_true", help="also generate answers (ANSWER_PROVIDER)"
    )
    args = parser.parse_args()
    return asyncio.run(run(args.answers))


if __name__ == "__main__":
    raise SystemExit(main())
