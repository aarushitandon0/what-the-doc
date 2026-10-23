# Tinker: an unfinished training experiment

**Status: not started. Nothing was trained, nothing was evaluated, and no result in
this repository comes from a fine-tuned model.** This page exists so the intended
Tinker work is recorded as unfinished rather than quietly dropped, and so that
whoever picks it up inherits the constraints rather than rediscovering them.

No placeholder, stub or configuration file anywhere in this repository should be read
as integration. There is none.

---

## Why it is worth doing

The live measurement taken this session is the argument. Asked to diagnose an
explicit page break, `gemma4:e2b` returned:

```json
{"operation": "remove_break", "break_id": "brk-7925c71d4abd3fc5"}
```

That is an invented schema. The required envelope has five keys
(`status`, `explanation`, `evidence`, `source_sha256`, `operations`) and the
operation is named `remove_explicit_break` with a `target_id`. The validator rejected
it as `invalid_response`, which is the system behaving correctly — but a diagnosis
that never passes validation is a diagnosis the user never sees.

A controlled comparison was run: the **previous** system prompt, without this
session's section-operation block, produced `{"operation": "remove_break"}` — the
same failure, with less information. So the problem is not prompt length and not this
session's change. It is that a 4.6 B model does not reliably hold an eleven-thousand
character instruction and emit a fixed five-key envelope.

That is the specific, narrow thing a fine-tune would fix: **envelope conformance**,
not judgement.

> **Update, 23 October 2026 — the premise above no longer holds.** Envelope
> conformance was fixed without training. `gemma_adapter.response_schema` now sends
> Ollama a per-request JSON Schema (the supplied ids, the exact source hash, the
> closed operation shapes and the status/operations rules) in place of
> `format: "json"`, so decoding cannot produce the invented shape above, and decodes
> `status` only after the explanation, evidence and operations. On the five live
> structural cases the final schema produced both correct proposals and the correct
> odd-page abstention (`TEST_RESULTS.md` section 17.3). What remains is narrower:
> the ambiguous break gets `no_change` where `needs_clarification` is right, and the
> model intermittently repeats an evidence entry, which the validator refuses (seen
> twice). A fine-tune, if one is ever attempted, should target *clarification* and
> clean citations, with constrained decoding kept on — and a proper multi-case
> re-measurement should come first, since these are single runs. Nothing below has
> been started.

## 1. Train against the now-stable schema

The schema stopped moving this session. Version 3 of the plan schema is the target:

- **Response envelope** — exactly `status`, `explanation`, `evidence`,
  `source_sha256`, `operations`, with optional `question`. No extra key, and a
  self-assigned confidence score is rejected rather than ignored.
- **Statuses** — `proposed`, `no_change`, `unsupported`, `needs_clarification`. Only
  `proposed` may carry operations, and `proposed` with an empty list is invalid.
- **Three operation shapes** —
  `{paragraph_id, field, value}`;
  `{operation: "remove_explicit_break", target_id, expected_break_type}`;
  `{operation: "change_section_start", target_id, expected_start_type, new_start_type}`.
- **Three evidence shapes** — `{paragraph_id, field}`, `{paragraph_id, target_id}`,
  `{section_id}`.

Both validators (`diagnosis.check`, then `engine.validate` against the real XML) are
the ground truth. A training target is a response those two accept; a correctly
*abstaining* response is as valuable a target as a correct repair, and should be well
represented or the model will learn to always propose something.

## 2. Split the data by document family, not by example

The fixtures in this repository come from a small number of generators
(`fixtures.py`, `eval_fixtures.py`, `break_fixtures.py`, `section_fixtures.py`), and
examples from one generator share prose, styles and structure. A random split would
put near-duplicates on both sides and report a score that means nothing.

**Split by generator and by document, so no document family appears in more than one
of train, validation and test.** Hold out at least one entire fault category — the
section slice is the obvious candidate, since it is newest — so there is a genuine
measurement of generalisation rather than of memorisation.

## 3. Check supported models and pricing before writing any code

Two gates, both before training, both recorded with the date they were checked:

- **Is the base model supported?** The baseline here is `gemma4:e2b` running locally
  through Ollama. Whether Tinker supports that exact base, and in what format, has
  not been checked. If it does not, a fine-tune of a *different* base is a different
  experiment and the before/after comparison below is void.
- **What does it cost?** Training, storage and inference, with real numbers from the
  current pricing page, not from memory.

## 4. Evaluate before and after on the same base model

The only comparison that means anything is the same base model, same prompts, same
documents, same validators, before and after. Specifically:

- **Envelope conformance rate** — the share of responses `diagnosis.check` accepts.
  This is the primary metric, because it is the measured failure.
- **Operation validity** — of those, the share `engine.validate` accepts against the
  real XML.
- **Abstention correctness** — on fixtures where the right answer is `no_change`,
  `unsupported` or `needs_clarification`, the share that abstains. A model that
  learns to always propose has got worse, however good its conformance.
- **Target fidelity** — never naming a `target_id` it was not shown. A fabricated id
  is already refused by the server, so this measures the model, not safety.

Do **not** compare a fine-tuned model against `gemma4:e4b`, or against results from
an earlier prompt scope. The historical 8/10 figure in `TEST_RESULTS.md` belongs to
its original prompt and capability scope and is not a baseline for this. The current
pre-training measurement is section 18 (three runs per case, the two sets scored
separately); compare against that, under the same prompt hash and schema revision.

## 5. An explicit spending cap, agreed before training starts

A number, written down and approved, before the first job is submitted — not a
budget alert after the fact. Training runs that are cheap in isolation are not cheap
when repeated across a hyperparameter sweep, and an unsupervised sweep is the usual
way a small experiment becomes an expensive one. No training without a cap.

## 6. Trained, evaluated and improved are three different milestones

They are routinely reported as one, and they are not:

| Milestone | What it means | What it does not mean |
| --- | --- | --- |
| **Trained** | A job finished and produced weights. | Nothing about quality. |
| **Evaluated** | Section 4's metrics were measured on the held-out set. | Nothing about whether it is better. |
| **Improved** | Those metrics beat the same-base-model baseline on data it never saw. | Nothing about production readiness. |

Each is claimable only on its own evidence. "We fine-tuned a model" is a statement
about milestone one and should never be presented as milestone three.

---

## Not done in this session, on purpose

No training was started, no data was exported, no account was created and no money
was spent. The schema this would train against only became stable at the end of this
session, and training against a moving target is wasted money.
