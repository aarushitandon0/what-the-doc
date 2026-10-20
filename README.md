# What the Doc?!

Point at the formatting problem. Get a targeted, reviewable repair.

## Current status

Four engineering milestones are in place: a constrained DOCX inspection and patching
core; effective paragraph-style resolution with a before/after rendering workflow; a
live local Gemma diagnosis layer that turns a plain-language complaint into a
validated, previewable proposal; and a local HTTP adapter over that pipeline for a
frontend to drive.

The React interface is in `frontend/` and connects to the local API for upload,
diagnosis, preview, explicit acceptance and download. Real Chromium verification and
the latest automated results are recorded in `TEST_RESULTS.md` section 12 and
`frontend/docs/VERIFICATION.md`. Optional Sentry instrumentation covers the custom
Ollama pipeline, asynchronous jobs and later review/download integrity checks. Actual
SDK payloads are verified locally; hosted Sentry verification needs project access.
See `OBSERVABILITY.md` for configuration, privacy and limits. No MongoDB connection
or Tinker training has been performed, and the service has no authentication. No
real user document has been repaired: samples are synthetic and generated here.

Rendering is done by local LibreOffice. Microsoft Word was used to verify the sample:
Word agrees with the resolver on 15 of 15 comparable fields, 11 of them inherited from
a style, and reports the same page counts as LibreOffice on this fixture. That is a
measurement on one synthetic document and one Word build, not a general claim that the
two applications paginate alike, so the rendered pages remain an approximation.

See `IDEATION.md` for the complete product and implementation plan, and
`TEST_RESULTS.md` for what the test run actually establishes.

## Repository layout

Every module is small and single-purpose, and the dependency direction only ever runs
downwards in this table: the HTTP adapter calls the pipeline, the pipeline calls the
diagnosis and workflow layers, and those call the engine. Nothing lower ever reaches
back up, which is why the CLI keeps working with no model, no renderer and no server.
`styles.py`, `render.py` and `observability.py` import nothing else from the project:
the first two are the bottom of the graph, and tracing is a leaf that several layers
call and none depend on.

### The repair core, no model required

| file | what it does |
| --- | --- |
| `engine.py` | The constrained DOCX patcher and the `inspect` / `peers` / `apply` CLI. Only `word/document.xml` is ever rewritten; every other ZIP member keeps its original bytes. |
| `styles.py` | Effective paragraph-property resolution through `docDefaults`, the `w:basedOn` style chain and direct formatting, labelling the layer that supplied each value. |
| `workflow.py` | The before/after run: patch a copy, render both, compare pages, re-check the preservation invariants against the files on disk, stop at `awaiting_review`. |
| `render.py` | DOCX → PDF → page images through headless LibreOffice in a throwaway profile, plus the page-level visual comparison. |

### The diagnosis layer

| file | what it does |
| --- | --- |
| `diagnosis.py` | The strict response schema and the named failure codes. It decides what a model answer is *not* allowed to be. |
| `gemma_adapter.py` | The prompt builder and the Ollama-compatible client for a local Gemma. No file path, raw XML, tool or shell is ever sent. |
| `pipeline.py` | The vertical slice: complaint → diagnosis → validated plan → rendered candidate → review status. Adds no powers the engine does not already have. |

### The local service and its observability

| file | what it does |
| --- | --- |
| `api.py` | The FastAPI adapter: upload, paragraph evidence, asynchronous jobs, preview images, explicit acceptance, guarded download. An adapter, not a second engine. |
| `observability.py` | The only module that talks to the Sentry SDK. Content-free spans, an allowlist, no automatic integrations and a hard no-op without `SENTRY_DSN`. |

### Fixtures, demos and evaluation

| file | what it does |
| --- | --- |
| `fixtures.py` | Builds the Word-openable four-page sample carrying three labelled faults, each recording the repair a correct diagnosis should reach. |
| `eval_fixtures.py` | The second sample, carrying the cases a *diagnosis* must get right — including the ones it should decline. |
| `demo.py` | The Milestone B walkthrough: evidence, the single expected repair, before/after render, and what changed. |
| `live_eval.py` | The ten-case live grading run. Deliberately not a unittest module, because the deterministic suite must pass with no model installed. |
| `word_check.py`, `word_check.ps1` | Optional, Windows-only cross-check of the resolver against Microsoft Word's own effective formatting. |

### Tests

`python -m unittest` discovers all of these and contacts no model and no network. The
live measurements are separate commands, and their results are recorded as such.

| file | covers |
| --- | --- |
| `test_engine.py`, `test_workflow.py` | Patching bounds, the preservation invariants, and rendering cases that are *skipped* rather than faked when LibreOffice is absent. |
| `test_render.py` | Injected subprocess failures at the real boundary: a helper executable that exits non-zero, times out, or writes no output. |
| `test_diagnosis.py`, `test_gemma_adapter.py` | What the schema refuses, and the prompt and client behaviour that needs no live model. |
| `test_pipeline.py` | The slice with an injected diagnoser returning a fixed answer, so the validators are what is under test. |
| `test_api.py` | The HTTP contract, the state table and the acceptance boundary, with the model unreachable by construction. |
| `test_api_noop.py` | Effective no-op rejection through the review boundary, inspecting actual candidate DOCX bytes. |
| `test_api_render.py` | Renderer failures staying failed jobs rather than becoming approvable repairs. |
| `test_observability.py`, `test_api_observability.py`, `test_observability_review.py` | Real SDK envelopes captured by a test transport, checked for span shape and for the absence of document content. |
| `test_eval_fixtures.py` | That the evaluation's cases really are the situations its manifest claims. |

### The interface

| path | what it is |
| --- | --- |
| `frontend/src/` | The React app: `App.jsx` (routes and flow), `components.jsx`, `api.js` (the typed client for `API.md`), `styles.css`, `data.js`. |
| `frontend/public/assets/` | The interface's own artwork. Committed deliberately, against the blanket image rule below. |
| `frontend/docs/DESIGN_SYSTEM.md` | The visual language and why it is what it is. |
| `frontend/docs/VERIFICATION.md` | What was verified in a real browser, what is blocked, and the manual checklist. |
| `frontend/docs/API_CONTRACT.md` | The frontend's *proposed* contract, kept for history. `API.md` at the root is authoritative where the two differ. |

### Documents

| file | what it is for |
| --- | --- |
| `README.md` | This file: what exists, how to run it, and what it does and does not promise. |
| `API.md` | The complete HTTP contract: requests, responses, the state table and the full frontend flow. |
| `OBSERVABILITY.md` | Sentry configuration, the span map, the privacy rules and what hosted verification is still missing. |
| `TEST_RESULTS.md` | What each run actually established, live results kept apart from deterministic ones. |
| `IDEATION.md` | The product and implementation plan, including the hardening this prototype does not implement. |

## Run locally

Python 3.10 or later:

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS / Linux instead:
# source .venv/bin/activate
pip install -r requirements.txt
python -m unittest -v
python engine.py inspect your-document.docx
```

On the recorded machine, with the full manifest installed and both LibreOffice and
Microsoft Word available, `python -m unittest` runs **259 tests, 0 skipped, 0 failed**,
and `npm test` in `frontend/` runs **24 tests**. Neither suite contacts a model, a
network service or Sentry; the live measurements are separate commands, recorded
separately in `TEST_RESULTS.md`. Without LibreOffice the rendering cases report
themselves as skipped instead of passing.

`requirements.txt` holds the core `lxml` and `pypdfium2` dependencies, FastAPI extras
and the tested Sentry SDK version. Install the manifest for the complete test suite.
Telemetry is disabled without `SENTRY_DSN`; the core CLI also works without the SDK.

Rendering additionally needs LibreOffice. It is found automatically in the usual
install locations, on `PATH`, and at `.tools/lo/program/soffice.exe` beside the
project; otherwise set `SOFFICE_PATH` to the `soffice` binary. Without it, every
rendering step is skipped and reported as skipped rather than passed, and the test
suite reports 5 skips instead of silently passing.

On a machine where LibreOffice cannot be installed per-machine (no administrator
rights), its payload can be unpacked beside the project instead, which needs no
elevation and is what the recorded test run used:

```bash
msiexec /a LibreOffice_<version>_Win_x86-64.msi /qn TARGETDIR=<repo>\.tools\lo
```

## Walkthrough on a sample document

`fixtures.py` builds a real, Word-openable four-page report carrying three labelled
faults: an excessive 48pt space above a heading, a page break that strands the closing
section alone on a final page, and one centred body paragraph among justified ones.
Each fault records the repair a correct diagnosis should reach, so a run can be checked
against a known answer rather than an impression.

`demo.py` shows the evidence for each, applies only the expected repair, renders before
and after, and reports what changed:

```bash
python demo.py work/demo                # add --no-render to skip LibreOffice
python demo.py work/demo --word         # also cross-check against Microsoft Word
```

## Verifying against Microsoft Word

Word is the authority on what a paragraph's formatting resolves to, and it is what the
user actually opens. On Windows with Word installed, `word_check.py` opens a document
read-only and compares Word's own effective paragraph formatting with this project's
resolver:

```bash
python word_check.py work/demo/sample-faulty.docx 1 10 19
```

Word is opened with macros force-disabled and alerts off, is always quit without
saving, and the script **refuses to run if Word is already open**, so it cannot close
a document you have in front of you. A field is only compared where both sides state a
value: where this project reports `application_default` Word still reports its own
built-in number, and that pair is recorded as not comparable rather than counted as
agreement or disagreement.

This check is optional and Windows-only. An unavailable result means *not verified*,
never verified.

## Inspection and effective properties

`python engine.py inspect file.docx` reports, per top-level body paragraph, both the
direct formatting as written and the **effective** value of each supported property,
resolved through three layers and labelled with the one that supplied it:

| origin | meaning |
| --- | --- |
| `direct` | set on this paragraph |
| `style:<id>` | inherited from that style in the `w:basedOn` chain |
| `doc_defaults` | from `w:docDefaults/w:pPrDefault` |
| `application_default` | nothing set it, so the value is `null` |

`application_default` means unset. It is not zero, and Word's own built-in fallback is
deliberately not restated here as a number.

`python engine.py peers file.docx 6` compares one paragraph against the others sharing
its style, so a diagnosis can see that a heading resolves to 48pt where its peers
inherit 12pt. Differing from peers is evidence of a fault, never proof: a deliberate
chapter break or a one-off centred line is allowed to differ.

Resolution is reported honestly where it is incomplete. Numbering (`w:numPr`)
indentation, table-style conditional formatting and theme effects are not resolved,
and a paragraph affected by them carries a caveat instead of a confident number. An
unparsable `word/styles.xml` is a hard error rather than being treated as absent,
which would silently misreport style-supplied values as application defaults.

## Applying a repair

Make a plan using the exact `source_sha256` and `paragraph_id` from inspection:

```json
{
  "source_sha256": "COPY_THE_REAL_HASH_FROM_INSPECTION",
  "operations": [
    {"paragraph_id": 0, "field": "space_before_pt", "value": 12}
  ]
}
```

Save it as `plan.json`, then either patch alone:

```bash
python engine.py apply your-document.docx plan.json repaired.docx
```

or run the full before/after workflow, which patches a copy, renders both, compares
the pages and writes a report:

```bash
python workflow.py your-document.docx plan.json work/run
```

The workflow ends at `awaiting_review`. It never accepts a repair for you, and no
changed-pixel ratio is treated as approval. No overwrite is allowed; undo is returning
to the untouched source.

## What the workflow guarantees

Checked against the files on disk after every run, independently of the patcher:

- the original file's sha256 is unchanged
- every `w:t` character survives, in order
- run count and run properties (bold, italic, fonts) are identical
- no body paragraph is added or removed
- `word/styles.xml` is unmodified: a repair overrides formatting on the paragraph
  rather than editing styles shared with the rest of the document
- the ZIP member list matches, and only `word/document.xml` differs
- only the targeted paragraphs changed

A candidate is named from its parent's hash plus the proposal hash, so re-running a
proposal reuses that candidate instead of patching an already patched copy.

Page comparison reports per-page changed-pixel ratios and any page-count change as
**review signals**. A correct repair legitimately reflows later text, so a large
difference is something to look at, not an automatic failure.

## Supported prototype operations

Direct paragraph spacing before/after, left indentation, alignment, page-break-before,
and keep-with-next. Top-level body paragraphs only. Existing styles are preserved.
Raw length fields are in twips; effective values are in points.

A repair is refused when an alternative measurement anywhere in the resolved chain
would defeat it — `beforeAutospacing`, `afterAutospacing`, `beforeLines`, `afterLines`,
or character-unit indentation — because writing a value that the layout then ignores
would look like a successful repair and change nothing.

Numbered list geometry, tables, headers, footers, text boxes and explicit run-level
page breaks are not editable through this prototype.

## Live Gemma diagnosis

The diagnosis layer runs against a real local Gemma. One command takes a document, a
paragraph id and a complaint, and produces a diagnosis, a validated proposal, rendered
before/after previews and a review status:

```bash
python pipeline.py your-document.docx 1 "Too much space above this heading." work/run
```

Add `--no-render` to skip LibreOffice, `--json` for the full report, `--model` to
override the model for one run, and `--timeout` to bound the wait. The command exits
non-zero only when the run *failed*; an abstention is a successful run that proposes
nothing. Every run writes `work/run/pipeline.json`.

### Windows setup

Gemma runs locally through [Ollama](https://ollama.com). No account, API key or paid
service is involved, and the document never leaves the machine.

```powershell
winget install Ollama.Ollama        # or the installer from ollama.com
ollama pull gemma4:e2b              # the tag the recorded results used
$env:GEMMA_MODEL = "gemma4:e2b"
python fixtures.py work\run\sample-faulty.docx
python pipeline.py work\run\sample-faulty.docx 1 "Too much space above this heading." work\run
```

The diagnosis layer talks to the runtime through the standard library. The optional
Sentry dependency instruments that call without changing its request or model settings.

The recorded runs used **`gemma4:e2b`** (4.6B parameters, Q4_K_M, llama.cpp runner)
served by Ollama 0.40.6. `gemma4:e4b` (7.5B) is also installed on the recorded machine
but was not used for the headline numbers. Any instruction-tuned Gemma tag works; the
tag that actually ran is recorded in every report, so a result can never be attributed
to a model that did not produce it.

`GEMMA_MODEL` names the installed tag; `GEMMA_URL` overrides the endpoint and defaults
to `http://localhost:11434/api/chat`. `python -c "import gemma_adapter, json; print(json.dumps(gemma_adapter.runtime_status(), indent=2))"`
reports whether the runtime is reachable, which Gemma tags are installed and, if the
configured one is missing, says so rather than falling back to something else.

Gemma is required, not preferred: a model tag from another family is **refused** with
`model_unavailable` rather than silently substituted. Set `WTD_ALLOW_NON_GEMMA=1` only
to run a deliberate non-Gemma baseline, and the result records that it was not Gemma.

Inference is CPU-only on the recorded machine and can take several minutes per call;
the default timeout is 600s. A GPU is not required and was not used.

### What the model is asked, and what it may answer

The model receives the complaint, the selected paragraph id, the document hash, and
each nearby paragraph's **effective** properties in points with the layer that supplied
each one, plus the same-style peer distribution and any resolution caveats. It never
receives a file path, raw XML, a tool or a shell. No hidden chain of thought is
requested: an unverifiable reasoning trace is not what the validator checks.

It must answer with exactly this structure, which wraps the plan the engine already
understands rather than replacing it:

| key | meaning |
| --- | --- |
| `status` | `proposed`, `no_change`, `unsupported` or `needs_clarification` |
| `explanation` | one or two plain sentences for the user |
| `evidence` | the `{paragraph_id, field}` pairs it relied on |
| `source_sha256` | the document hash, copied exactly |
| `operations` | the bounded edits, only on the selected paragraph |
| `question` | one question, only with `needs_clarification` |

`diagnosis.py` refuses a response that invents a target, cites a paragraph it was never
shown, names an unsupported field, carries a stale hash, edits outside the selected
paragraph, or includes any extra key — a self-assigned `confidence` among them, because
model confidence must never become an approval condition. A valid proposal is then
re-checked by `engine.validate` before a byte is written.

**`status: proposed` with an empty operations list is rejected outright.** Abstention is
a valid and useful answer, but it is reported as `no_change`, `unsupported` or
`needs_clarification`, so nothing can describe "I changed nothing" as a repair.

One deliberate leniency: a small model often fills every key in the template, so a
`question` returned alongside a proposal is *dropped* rather than rejected. The
user-visible rule is unchanged — a question is only ever surfaced with
`needs_clarification` — and no safety check was relaxed to allow it.

### Failures are named

| code | meaning |
| --- | --- |
| `model_unavailable` | runtime unreachable, model not installed, or not a Gemma |
| `model_timeout` | the model did not answer within the timeout |
| `invalid_response` | not JSON, or it broke the schema |
| `invalid_selection` | no such paragraph, or an empty complaint |
| `unsupported_formatting` | an alternative measurement would defeat the repair |
| `invalid_operation` | out-of-bounds value, or a preservation invariant failed |
| `render_failure` | LibreOffice produced no usable PDF |

### Measured results

`live_eval.py` grades ten labelled cases, most of which the system should decline to
repair. The model is never shown a label: each case passes only the document, a
paragraph id and a complaint into the same pipeline a user would drive.

```bash
python live_eval.py work/live-eval            # add --render for before/after images
```

With `gemma4:e2b` on CPU, **8 of 10 cases passed**. The prompt has since been revised
twice and re-measured on the same ten cases; all three revisions score 8/10, with a
different two failing. The current code sends revision 4: schema valid 10/10, status
correct 8/10, operation and target correct 9/10, forbidden-field *proposals* 1/10,
preservation failures 0/10, median latency 119s. (Revision 2's figures, for comparison:
schema 9/10, status 9/10, operation 9/10, forbidden 0/10, median 84s.) Latency is
dominated by the machine, not the prompt. Full per-case output and the revision-by-
revision table are in `TEST_RESULTS.md` sections 5 and 10.

If no Gemma runtime is reachable, the cases that need one are recorded `not_run` and are
never graded. A mocked answer is never counted as a pass.

### What the pipeline will not do

Ordinary model proposals cannot turn an inherited value into an equal direct override
and call it a repair. The proposal validator rejects an entirely ineffective plan as
`invalid_operation`; the model's raw `proposed` diagnosis remains in the report rather
than being relabelled as a correct abstention. Mixed plans retain actual changes and
remove redundant writes before candidate creation. Lengths are compared at whole-twip
resolution; unset values remain distinct from zero and `False`. The low-level engine
still permits a deliberate hand-written plan that pins inheritance.

It never accepts a repair. The best outcome is `awaiting_review`: a candidate exists,
built from the immutable parent plus the proposal hash, with the preservation invariants
re-checked against the files on disk and the original's hash unchanged. Acceptance stays
a separate, human step, and the untouched original is the undo path. Re-running the same
proposal reuses the existing candidate instead of patching a patched copy.

A missing renderer still permits structural review with `visual_comparison: null`.
Acceptance is permitted without previews, so the reviewer must inspect the DOCX in
Word. An available renderer that cannot launch, exits unsuccessfully, times out or
produces missing, empty or invalid PDF output fails the job as `render_failure`;
acceptance and download are refused. Each conversion uses fresh staging output so a
failed retry cannot reuse an older PDF.

## Local HTTP API

`api.py` is a small FastAPI adapter over the pipeline above, for a React frontend to
drive. It is an adapter, not a second repair engine: every diagnosis, bound, validator
and preservation invariant is the one `pipeline.run`, `engine.validate` and
`workflow.check_invariants` already apply from the command line.

```powershell
pip install -r requirements.txt
$env:GEMMA_MODEL = "gemma4:e2b"
.\.venv\Scripts\python.exe api.py --host 127.0.0.1 --port 8012 --root work/api-browser-8012 --cors http://127.0.0.1:5173

# In a second PowerShell terminal, starting at the project root:
Set-Location frontend
$env:VITE_API_TARGET = "http://127.0.0.1:8012"
npm run dev -- --port 5173 --strictPort
```

Check those ports are free before starting. Open `http://127.0.0.1:5173` and stop
only these two terminal sessions with Ctrl+C. Existing listeners on other ports may
belong to earlier work; their historical PIDs are never shutdown instructions.
For Sentry, set `SENTRY_DSN` and optionally `WTD_SENTRY_TRACES_SAMPLE_RATE` before
starting the backend, following [`OBSERVABILITY.md`](OBSERVABILITY.md). The frontend
does not load a telemetry SDK, session replay or DOM capture.

It covers upload, per-paragraph effective-style evidence, an asynchronous job with
eight explicit states, before/after preview images, and an **explicit** acceptance.
Reaching `awaiting_review` is not acceptance: `accepted` is reachable only through
`POST /api/jobs/{id}/accept` carrying the candidate hash the reviewer saw, the
preservation invariants are re-run against the files on disk both at acceptance and
again before a download, and download is refused in any other state.

Document and job ids are 32 random hex characters, every path is server-chosen, and
an uploaded file's own name is used only to reject the upload. It binds to
`127.0.0.1` and has **no authentication, no rate limiting, no database and no sandbox
around LibreOffice**. Do not expose it.

Fixture and live answers are kept apart: an injected diagnosis is refused unless the
server was started with `--allow-fixture-mode`, every job reports `mode` and
`is_live_model_answer`, and when no Gemma runtime is reachable a job is refused with
`model_unavailable` rather than answered from a mock.

The full contract, with request/response examples, the state table, polling guidance
for CPU inference and a complete frontend flow, is in **[`API.md`](API.md)**.

## What is not in the repository, and why

`.gitignore` is deliberately broad, on three rules.

**A secret is never committed.** `SENTRY_DSN`, `GEMMA_MODEL`, `GEMMA_URL` and
`SOFFICE_PATH` are read from the environment at runtime, never from a tracked file, so
`.env*`, keys and certificates are all excluded.

**Anything derived from a document is private.** `work/` holds every run's uploaded
copy, patched candidate, rendered page image, `pipeline.json` and log. Any of those can
reveal the contents of someone's file, so the directory is ignored as a whole rather
than file by file, and `*.docx`, `*.pdf` and the image extensions are ignored
repository-wide as a second line of defence. A screenshot of the *synthetic* fixture
that is genuinely worth keeping therefore has to be added on purpose with
`git add -f` — which is the intended friction, because it means no sweep of untracked
files can ever commit a page rendered from a real document. The one standing exception
is the interface's own static assets: the artwork in `frontend/public/` is drawn for
this project and the bundled Rubik fonts in `frontend/src/assets/` carry their OFL
licence beside them. Neither contains document content, and both are re-included
explicitly.

**Anything large and not ours stays out.** `.venv/` and `node_modules/` are
reproducible from `requirements.txt` and `package-lock.json`; `.tools/` holds the
unpacked LibreOffice payload, which is hundreds of megabytes and belongs to its own
project; `frontend/dist/` is build output, and the recorded production build is a
result in `TEST_RESULTS.md` rather than an artefact to commit. Model weights are
covered too, although they belong in the runtime's own store and have never been
placed in this tree.

## Limits

Input cap 20 MiB; expanded ZIP cap 80 MiB; 2000 entries; 12 changes per plan.
Rejects tracked changes/content controls, signed packages, VBA, duplicate ZIP entries,
invalid targets and stale hashes. These are initial guards, not a complete upload
security boundary. Public uploads need isolated conversion workers, timeouts, rate
limits, relationship checks and authentication before deployment.

Rendering runs LibreOffice with a throwaway user profile and a timeout, and passes the
document as an argument vector, never through a shell. The container, dropped
privileges, blocked outbound network and resource caps described in `IDEATION.md`
section 9 are **not** implemented, so this is for local development only.

Non-document ZIP members retain their original uncompressed bytes. `document.xml` is
serialized again, so XML byte identity is not promised.

## Next milestone

The recorded revision-4 baseline remains `gemma4:e2b`: schema 10/10, status 8/10,
operation/target 9/10, one forbidden-field proposal, and zero preservation failures.
These are observations on ten synthetic cases, not general accuracy rates. The
earlier e4b comparison used revision 2 and scored 6/10; it is not a revision-4
comparison. Full results and prompt history remain in `TEST_RESULTS.md` sections 5–10.

The deterministic no-op guard is separate from raw model quality. It prevents an
ineffective proposal from becoming a reviewable repair without changing the model's
recorded answer or relabelling the historical evaluation as improved.

Next: verify sanitized traces in a configured hosted Sentry project and expand browser
coverage to other engines and assistive technology. Then exercise the workflow on a
consented real-world document, where numbered lists, tables and theme effects will
test the resolution caveats beyond the synthetic fixtures. The ambiguous complaint still does not reach
`needs_clarification` on e2b in the recorded revision-4 run.

No model weights, credentials, user documents or service subscriptions are bundled.
