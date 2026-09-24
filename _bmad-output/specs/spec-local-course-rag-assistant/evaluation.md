# Evaluation Contract

## Dataset shape

Store a small, reviewable dataset in a text diff-friendly format. Each case contains:

- stable case identifier and category;
- `course_id` and question;
- expected behavior: supported answer, abstention, or clarification;
- expected source filename/page pairs for supported cases;
- concise reference facts or required evidence points, not a single exact answer string;
- forbidden claims when a likely model-prior answer must not appear;
- notes explaining ambiguity or insufficiency where applicable.

The dataset must not contain embeddings or provider-generated expected prose.

## Required coverage

| Category | Minimum intent | Pass condition |
|---|---|---|
| Factual | A fact supported by a localized passage | Required fact is present, no contradictory fact appears, and the expected page is cited |
| Multi-passage | An answer requiring evidence from at least two passages or pages | Required evidence points are combined and every contributing source is cited |
| Ambiguous | A question with multiple materially different interpretations in the materials | The response asks for clarification or abstains without selecting an unsupported interpretation |
| Unsupported | A plausible question whose answer is absent from the collection | The response states that the materials are insufficient, supplies no guessed answer, and emits no misleading citation |

Include at least two cases in each category so one example cannot define the category's behavior.

## Evaluation runner

- Runs locally against a specified course and dataset.
- Records retrieved evidence, final answer or abstention, citations, latency metadata, and individual check results.
- Separates deterministic structural checks from model-judged semantic checks.
- Structural checks include course isolation, required citation fields, citation resolution to retrieved chunks, and correct abstention shape.
- Every deterministic check must pass for the evaluation run to be deterministically acceptable; any deterministic failure makes the run fail.
- Semantic checks assess required evidence points and forbidden claims using a documented rubric. Their results remain visible case by case and require manual acceptance.
- V1 must not reduce semantic results to a single score or apply an aggregate numerical pass threshold.
- Produces machine-readable results plus a concise human-readable summary.
- Does not make live Gemini calls unless explicitly enabled, so routine tests remain deterministic and inexpensive.
