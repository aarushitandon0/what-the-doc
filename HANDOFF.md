# Handoff — session of 24 October 2026, ~01:50–02:15

Written for: the next Claude Code session on this repository (and Aarushi reading it first).

> **Update, 24 October 2026 morning — section 5 is decided.** Aarushi chose **option D**
> (ship safely now). Done in the working tree, still uncommitted:
> - `gemma_adapter.DIAGNOSIS_ATTEMPTS = 1` (re-sampling off; the loop and its tests are
>   kept, the loop's tests patch the budget to 3, and a new test pins the shipped 1).
> - `diagnosis.py`'s duplicate rule is **unchanged**.
> - `TEST_RESULTS.md` §18.12 records the measurement (§18.10 already existed as
>   "Limitations"). `API.md` updated to `max_attempts: 1`. The `invalid_response` UI text
>   no longer says "every time it was asked".
> - The three draws in `work/verify-retry/raw-draws.txt` have identical content when parsed
>   as JSON. That file is a reformatted record, so "byte-identical" below is too strong.
> - Corrections to this file: 67 s → 136 s is about double, not triple. The
>   `work/backend-suite-after-retry.log` run was cut off mid-run and is not evidence; the
>   final full-suite log is `work/backend-suite-final.log`.
> - Option A (schema v5) is still the real fix, and it needs a full re-evaluation.
>
> **Update, 24 October 2026, late morning — option A is not "nothing loosened".**
> The failing request matches eval case `clean_paragraph_needs_no_change` (same complaint,
> a heading that inherits its spacing; it expects `no_change` and forbids
> `space_before_pt`). Live raw capture of that case (`work/raw-clean-case.txt`) shows the
> same pattern: a wrong `proposed` answer with a repeated citation. The duplicate
> refusal is stopping wrong proposals here. Making repeats impossible would likely let
> the 12 → 6 pt proposal reach review. See `TEST_RESULTS.md` §18.12. Also fixed: the
> failure panel no longer says "The validator refused it" for every failure code (it
> says "Reason recorded"), and no longer says "Nothing was written" (a run can fail after
> a candidate is built). It now says "Your original document is untouched." Full-suite
> log: `work/backend-suite-final-2.log`.

Read this file top to bottom before touching anything. Section 5 contains an unresolved
decision that is **yours to make, not the agent's** — it is a safety rule the repository
measured and chose deliberately.

---

## 1. Where things stand

- Branch `main`, HEAD `df60d0241058b366a10bd4a3a8f48e3a2e3b21c6` ("update").
- **Nothing was committed this session.** All work is uncommitted in the working tree.
- `SUBMISSION_CHECKLIST.md`, `submission/EVIDENCE_TABLE.md` and `submission/POST_DRAFT.md`
  were *already modified* before this session began. **They are not mine — do not
  attribute them to this work and do not revert them.**

---

## 2. What was asked

Two things, in two messages:

1. A screenshot of the running app: a live run against `gemma4:e2b` failed with
   `invalid_response` / **`duplicate evidence entry`**, showing "That run failed." and a
   "Try again" button. Verbatim: *"LMAO PLS FIXXXX"*.
2. *"AND ON THE WEBSITE, SHOW A PREVIEW OF THE WORD DOC.... AND THEN BEFORE AND AFTER DOC"*.

Item 2 is **done and verified**. Item 1 is **not fixed** — see section 4.

---

## 3. Root-cause investigation (completed, with evidence)

The failing job is on disk: `work/api/jobs/fce11ea9616ca14df6413bc514409e99/pipeline.json`.
Document `544a8a3c811bcfc5c4e9da9a7b68fd65`, paragraph 0, complaint
"There's too much space above this heading.", 67.0 s, `outcome: failed`.

**`diagnosis.py:139` is correct and was deliberately left alone.** `TEST_RESULTS.md`
section 18.5 records that dropping repeated citations from *every* status turned
refusals into reviewable proposals and let **three wrong proposals reach review in a
single run**. Refusing a duplicate on a `proposed` answer is a safety rule backed by
measurement, not a bug. Do not "fix" it casually.

The defect was taken to be one level up: `gemma_adapter.diagnose` sampled the model
**exactly once**. The reasoning was that Ollama's grammar path does not enforce JSON
Schema `uniqueItems` (the repo's own docstring says so), and section 18.2 records that
*"Temperature 0 is not deterministic here"* — so a re-draw should produce a different,
valid answer.

**That reasoning turned out not to hold for this input.** See section 4.

---

## 4. THE UNRESOLVED PROBLEM — read this carefully

### 4.1 What was measured, live, on this machine

Re-ran the exact failing job against the real `gemma4:e2b`
(`work/verify-retry/pipeline.json`):

```
attempts: 3,  max_attempts: 3,  latency: 136.2 s
discarded_attempts: 3 × invalid_response / "duplicate evidence entry"
outcome: failed
```

Then captured the model's **raw answers before validation**. All **three draws were
byte-identical**:

```json
{
  "source_sha256": "fb4fca34…abb01e",
  "explanation": "The user complained about too much space above the heading. The selected
                  paragraph (ID 0, 'Quarterly Register', style Heading1) has a
                  `space_before_pt` of 12.0 points. Since there are no peer comparisons,
                  this value is taken as the current state.",
  "evidence": [{"paragraph_id": 0, "field": "space_before_pt"},
               {"paragraph_id": 0, "field": "space_before_pt"}],
  "operations": [{"paragraph_id": 0, "field": "space_before_pt", "value": 6.0}],
  "status": "proposed",
  "question": "Should the space before the heading be reduced?"
}
```

### 4.2 What this proves

1. **For this input, temperature-0 decoding IS reproducible.** Section 18.2's
   non-determinism note does not generalise to every case. Re-sampling an *identical*
   request therefore **cannot** rescue this class of failure, by construction.
2. The duplicate is pure noise: the same citation emitted twice.
3. **The citation rule is already satisfied** — the answer cites `space_before_pt` and
   changes `space_before_pt`. If repeats were dropped for `proposed`, this answer would
   pass every other check and reach human review as a normal proposal.
4. The proposal itself is defensible but arguably pointless: ¶0 inherits
   `space_before_pt: 12.0` from `style:Heading1`, i.e. **there is no fault here**, and ¶0
   is the first paragraph in the document (space "above" it is the page's top margin).
   `no_change` or `needs_clarification` would be the better answer. The model also
   attaches a `question` to a `proposed` status (harmless — `check` drops it).

### 4.3 Honest status of the retry change

The re-sampling machinery works exactly as designed and is fully tested, but it is
**unproven to help**, and on a persistent failure it **doubles-to-triples the user's
wait** (67 s → 136 s) for no benefit. Treat "keep it, revise it, or revert it" as an
open decision — see 5.3.

---

## 5. The decision for tomorrow (Aarushi's call, not the agent's)

The duplicate-citation rule is the blocker. Four options, with what each costs:

### 5.1 Option A — make duplicates structurally impossible (schema v5)
`evidence` is currently `{"type": "array", "minItems": …, "maxItems": 8,
"items": {"anyOf": […]}}` (`gemma_adapter.py` ~line 820). Arrays cannot express
uniqueness in a grammar. Re-shaping evidence as an **object** with one boolean property
per citable `(paragraph, field)` pair would make a repeat impossible, because an object
property is emitted at most once.
- **Cost:** schema revision v5, prompt change, `PROMPT_SHA256` update, and a **full
  45-run re-evaluation** to compare against `eval-final2`. Hours of CPU.
- **Risk:** changes the measured configuration; `TEST_RESULTS.md` section 18 would need
  a new section.
- **Upside:** the cleanest real fix. Nothing is loosened.

### 5.2 Option B — revisit the 18.5 decision, with fresh evidence
Section 18.5's rejection of "drop repeats everywhere" was measured on
`eval-intermediate`, which **did not have the citation rule**. Section 18.5 itself says
the citation rule *"narrows the injection path; it does not close it."* A configuration
of **drop-repeats-for-proposed + citation-rule has never been measured.**
- **Cost:** a fresh 45-run eval (`live_eval.py … --structural --repeat 3`), ~34 min per
  suite plus model time; section 18.9 records 2030 s for the backend suite alone.
- **Risk:** this is the safety rule that was already caught letting bad proposals
  through once. **Do not ship it on reasoning alone — it needs the numbers.**

### 5.3 Option C — decide what to do with the re-sampling change
Keep it (it is harmless on success: one draw, `attempts: 1`), set
`gemma_adapter.DIAGNOSIS_ATTEMPTS = 1` to disable it without removing the plumbing, or
revert it entirely. Given 4.2.1, a retry that varies nothing is of limited value; a
retry that *varied* the draw (seed/temperature) would change the measured generation
options and needs its own evaluation.

### 5.4 Option D — ship as-is
The failure is now *explained* in the UI rather than mysterious (section 6.3), the
document is visible, and nothing unsafe reaches review. The user still cannot get a
repair on this particular paragraph — but on this paragraph there is arguably nothing
to repair.

**Recommendation if forced to pick one for a deadline:** D for the submission, A as the
real fix afterwards. B only with a full eval behind it.

---

## 6. What was actually changed (all uncommitted)

### 6.1 Bounded re-sampling — `gemma_adapter.py`, `diagnosis.py`, `pipeline.py`, `api.py`
- `gemma_adapter.DIAGNOSIS_ATTEMPTS = 3` (new module constant, heavily commented).
- `diagnose()` refactored: the request/decode/validate body is now an inner `draw(attempt)`
  closure called in a bounded loop. The request is rebuilt per attempt (`urlopen`
  consumes it) and is otherwise byte-identical — prompt, schema and generation options
  are untouched, so provenance still describes every draw.
- **Retries only on `INVALID_RESPONSE`.** `MODEL_UNAVAILABLE`, `MODEL_TIMEOUT` and
  `INVALID_SELECTION` are stable conditions and still fail on the first try (there is a
  test pinning this).
- When the budget is spent the **last refusal is re-raised with its own reason intact**,
  carrying `attempts`, `max_attempts`, `discarded_attempts` as attributes.
- `diagnosis.DiagnosisError` gained class-level defaults (`attempts = 1`,
  `max_attempts = 1`, `discarded_attempts = ()`) so callers can read them unconditionally.
- `pipeline.py` writes those three fields into `diagnosis_metadata` on the failure path.
- `metadata['latency_seconds']` now covers **all** draws (wall clock), so a retried run
  never looks faster than it was.
- `api.py::_public_job` exposes `attempts`, `max_attempts`, `discarded_attempts`.

### 6.2 Document preview — `api.py`, `render.py` (unchanged), frontend
The before/after comparison **already existed** for jobs; it was simply unreachable,
because `job.preview` only exists after a *successful* run. Before that the canvas showed
extracted paragraph text — precisely the layer a formatting fault does not live in.

- `Store.document_preview_dir(document_id)` → `<documents>/<id>/preview`.
- `GET /api/documents/{id}/preview` → `{rendered, page_count, urls, renderer_available,
  unavailable_reason, note}`.
- `GET /api/documents/{id}/preview/{index}` → `image/png`.
- Rendering is **lazy and cached**, guarded by a per-document `threading.Lock` kept in a
  `_preview_locks` dict; the original is immutable so one render lasts its lifetime.
- No LibreOffice, or a renderer that fails → **HTTP 200** with `rendered: false` and an
  `unavailable_reason`. It is a configuration, not an error: a diagnosis reads the
  package itself and is unaffected. Path containment is enforced exactly as the job
  previews do.

Frontend:
- `api.js`: `getDocumentPreview`; `ERROR_TEXT.invalid_response` reworded.
- `App.jsx`: `docPreview` / `docView` state; `loadDocPreview` fired **without await** on
  `openDocument` so the workspace is not blocked on LibreOffice; `docPages` prefers a
  finished job's `before_urls` over the standalone render so the workspace picture is
  literally the one the review compares against; canvas gained a **Pages / Text** toggle
  (`role="group"`, `aria-label="Document view"`). Text stays reachable because it is the
  only view carrying the paragraph ids the selection needs.
- `styles.css`: `.canvas-controls`, `.segmented.small`, `.canvas .preview-pages`,
  `.text-button.inline` appended at the end of the file (the file's main rules are one
  minified line — append, do not try to edit in place).

### 6.3 Failure UI — `App.jsx`
The `failed` panel now shows the catalogue text, then the validator's own words verbatim
(*The validator refused it: "duplicate evidence entry"*), then — when `attempts > 1` —
"Gemma was asked 3 times of 3 and every answer was refused", then "Your document is
untouched. Nothing was written."

### 6.4 Docs — `API.md`
New "The document as uploaded" subsection under §5 Previews, and an
`attempts` / `max_attempts` / `discarded_attempts` subsection under §4 explaining that
re-sampling **loosens nothing**.

### 6.5 New/changed test files
- `test_observability.py` — `fake_runtime` is now a **factory** used as `side_effect`
  rather than a single `return_value`. See 7.1.2 for why.
- **`test_api_document_preview.py` (new, untracked)** — 4 tests: missing renderer
  reported not raised; unknown document and traversal → 404; a real LibreOffice render
  served as PNG; the render is cached (second call must not re-render).
- `test_gemma_adapter.py` — new `ResampleTests` (5 tests): a repeat is re-drawn; a sound
  answer is still drawn once; a persistent repeat spends the budget and fails with its
  own reason; an unsafe proposal is **not** re-drawn until it passes; an unreachable
  runtime is **not** retried.
- `frontend/src/api.test.jsx` — `DOCUMENT_PREVIEW` fixture + the preview route added to
  the default mock; new `describe('the document itself')` with 4 tests.

---

## 7. Test status — exact, no rounding

| Suite | Result |
| --- | --- |
| `test_gemma_adapter test_diagnosis test_pipeline test_api` | **213 passed, OK** (11.3 s) |
| `test_api_document_preview` | **4 passed, OK** (19.9 s, real LibreOffice render) |
| `npm test` | **46 passed** (3 files) — was 42 before |
| `npm run build` | **succeeded**, 2.66 s |
| `test_observability` ×4 modules | **27 passed, OK** (after the fixes in 7.1) |
| Full backend suite | **see 7.2 — check the log before believing anything** |

### 7.1 Two bugs my own change introduced, found by the full suite and fixed

The full suite (504 tests, 647 s) initially came back **`FAILED (errors=2)`**, both in
`test_observability.py`, both caused by the re-sampling change. Targeted suites had not
caught either.

1. **`attempt=attempt` passed to `observability.span`.** `Trace.set` is a deliberate
   keyword allowlist — its docstring says *"Safe facade; callers cannot add arbitrary
   span attributes."* An unknown keyword raises `TypeError`, which `span()` swallows in
   its `except Exception: trace.raw = None`, **silently stripping the span's `gen_ai.*`
   attributes and its Sentry span**. Broke
   `test_actual_adapter_boundaries_emit_ai_conventions_tokens_and_source_hash`.
   **Fixed** by removing the tag from both spans; the attempt count travels in
   `diagnosis_metadata` instead. *If you ever want it on the span, add an allowlisted
   parameter to `Trace.set` — do not pass an arbitrary keyword.*
2. **`patch.object(..., 'urlopen', return_value=<single BytesIO>)`.** A response stream
   is consumed and closed by the first read, so the second draw raised
   `ValueError: I/O operation on closed file`. Broke
   `test_invalid_schema_preserves_real_error_code_without_model_content`.
   **Fixed** by turning `ObservabilityTests.fake_runtime` into a **factory** used as
   `side_effect`, yielding a fresh `BytesIO` per call, with a comment explaining why.

**Generalise this:** any test fixture that assumes `diagnose` makes exactly one HTTP
call is now wrong whenever the canned answer is one the validator refuses. If you add
such a fixture, use `side_effect` with a factory, never `return_value`.

### 7.2 Full backend suite

Section 18.9 records the pre-existing baseline as 493 tests / ~2030 s; this run reports
**504 tests / ~647 s** (the count grew with the new tests; the wall clock differs from
18.9's figure, which is worth a glance but was not investigated).

A final foreground run was started at the very end of the session, writing to
**`work/backend-suite-after-retry.log`**. **Its result was not seen before the session
ended.** Read the last ~10 lines of that log first thing:

```bash
tail -20 work/backend-suite-after-retry.log
```

If it is not `OK`, fix that before anything in section 5. If the log is missing or
truncated, re-run:

```bash
cd "C:/Users/Aarushi/Downloads/What-the-Doc-Starter/what-the-doc"
SOFFICE_PATH="C:/Users/Aarushi/Downloads/What-the-Doc-Starter/what-the-doc/.tools/lo/program/soffice.exe" \
  .venv/Scripts/python.exe -m unittest discover -p "test_*.py" > work/backend-suite-after-retry.log 2>&1; echo "exit=$?"
```

**Note:** background runs of this suite in this session produced *empty* output files
despite exiting 0. Redirect to a file inside the command (as above) rather than relying
on the harness to capture it.

---

## 8. Environment facts worth not rediscovering

- Python is `.venv/Scripts/python.exe`. **`pytest` is not installed** — use
  `python -m unittest`. Plain `python` lacks `lxml`; the venv has it.
- LibreOffice is vendored at
  `.tools/lo/program/soffice.exe`; export `SOFFICE_PATH` to it or renderer tests skip.
- Ollama is on `http://localhost:11434`; `gemma4:e2b` and `gemma4:e4b` are installed.
  `GEMMA_MODEL=gemma4:e2b` must be set for any live call.
- One live diagnosis draw is roughly **45–70 s** on this CPU; three draws ≈ **136 s**.
- The failing document is still on disk at
  `work/api/documents/544a8a3c811bcfc5c4e9da9a7b68fd65/original.docx` — reproduce with:
  ```bash
  GEMMA_MODEL=gemma4:e2b .venv/Scripts/python.exe pipeline.py \
    "work/api/documents/544a8a3c811bcfc5c4e9da9a7b68fd65/original.docx" \
    0 "There's too much space above this heading." work/verify-retry --no-render
  ```
- To dump raw model answers before validation, monkeypatch `diagnosis.parse`. A working
  script was kept at **`work/raw_answer.py`**; run it with
  `GEMMA_MODEL=gemma4:e2b .venv/Scripts/python.exe -I work/raw_answer.py`. It carries an
  explicit `sys.path.insert` because `-I` drops the working directory from `sys.path`.
- The three byte-identical draws from 4.1 are kept verbatim at
  **`work/verify-retry/raw-draws.txt`**, beside that run's
  `work/verify-retry/pipeline.json`.

---

## 9. Suggested order for tomorrow

1. `tail -20 work/backend-suite-after-retry.log` — confirm the full suite is `OK`
   (section 7.2). Nothing else matters until it is.
2. Read section 4 and decide between options A–D (section 5). **Do not let an agent make
   this call unilaterally** — it is the repository's central safety rule.
3. If the submission deadline is close: take option D, and make sure
   `TEST_RESULTS.md` gets a short, honest note that re-sampling was added, was measured,
   and does **not** fix the persistent-duplicate case (with the byte-identical three-draw
   evidence from 4.1).
4. Decide the fate of `DIAGNOSIS_ATTEMPTS` (section 5.3).
5. Commit. Nothing is committed yet.

---

## 10. Things the agent should NOT do

- Do not relax `diagnosis.py`'s duplicate rule without a fresh 45-run evaluation.
- Do not describe the re-sampling change as "fixing the duplicate evidence bug". It
  demonstrably does not fix the reported case.
- Do not attribute `SUBMISSION_CHECKLIST.md` / `submission/*` changes to this work.
- Do not claim the full backend suite passes until a captured log says so.
