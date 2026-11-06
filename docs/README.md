# `docs/`

Where each kind of document belongs, and why there is more than one kind. The division is
by **what makes a document wrong**, because that decides who may change it and when.

| Directory | What is in it | What makes one wrong |
|---|---|---|
| [`architecture.md`](architecture.md) | the architecture, transcribed from the PDF in `Plan/` | nothing here — it is a transcription, and the PDF is the original. A disagreement with the code is the code's business, or a new decision |
| [`plans/`](plans) | a piece of work with a beginning and an end, plus its progress file | it stops being followed. A plan nobody is working from is history, not a plan |
| [`specs/`](specs) | one file per feature: goal, non-goals, acceptance criteria, the tests that prove them | the thing was built differently. Then the spec is amended, or it is not the spec |
| [`decisions/`](decisions) | one file per decision: context, options, choice, why, consequences | **a decision never goes stale.** It records what was known at the time. Superseded, not edited |
| [`findings/`](findings) | a research result: data, method, result, caveats | its data or configuration is misstated. A result that turned out wrong is still a finding — what changes is the next one |
| [`research/`](research) | `plan.md` — which question is next and why — and benchmark reports | the plan does not say what would make us abandon a question |
| [`ideas/`](ideas) | `backlog.md`: candidate directions with their scores | a score with no evidence behind it |
| [`feedback/`](feedback) | observations from real use. **Written by the human, read by agents** | an agent writes one. It is the one input nothing in this repository can generate |
| [`radar/`](radar) | weekly scout digests, plus `seen.md` and `ignore.md` | an item with no answer to "what does this mean for this project" |

Each directory has its own `README.md` with the naming scheme and the shape of an entry.

## Two things that hold across all of them

**A document that contradicts the code is a bug in the document.** `tools/check_docs.py`
runs the Python in `README.md`, and `tools/check_doc_claims.py` checks stated claims
against the code, but neither can read prose. The `doc-keeper` agent
(`.claude/agents/doc-keeper.md`) exists for the rest, and the rule it works by is that the
document is corrected and never the code — a document cannot be evidence about the code.

**Everything here is in English**, like the rest of the project, and
`tools/check_language.py` fails otherwise. For documents the reason is the same as for
code: a section cited by two names is a section the citation stops finding.

## What is not here

- `Plan/` — the three original PDFs, and their transcriptions. The originals, not working
  documents.
- `requirements.md`, `conventions.md`, `research-questions.md`, `README.md` at the root.
  They are the project's own standing documents, cited by the guards, and moving them would
  break every pointer in the code for nothing.
- `/research/` at the repository root — the local research **record** (run history, trade
  logs), which is not version controlled and is not documentation. Not to be confused with
  `docs/research/`, which is the plan, or with `backtest/research/`, which is code.
