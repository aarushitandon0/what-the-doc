# Local HTTP API

`api.py` is a thin FastAPI adapter over the existing pipeline. It contains no repair
logic: every diagnosis, bound, validator and preservation invariant is the one
`pipeline.run`, `engine.validate` and `workflow.check_invariants` already apply when
you drive them from the command line. If a rule is not in those modules, the API does
not have it either.

It binds to `127.0.0.1`. There is **no authentication, no rate limiting, no database
and no sandbox around LibreOffice**. Do not expose it.

---

## Starting it

```powershell
# from the repository root, with the venv active
pip install -r requirements.txt            # lxml, pypdfium2, fastapi, uvicorn, python-multipart
$env:GEMMA_MODEL = "gemma4:e2b"            # the installed Ollama tag to diagnose with
python api.py --cors http://localhost:5173 # Vite's default dev origin
```

| flag | default | meaning |
| --- | --- | --- |
| `--host` | `127.0.0.1` | anything else logs a warning; the service has no auth |
| `--port` | `8000` | |
| `--root` | `work/api` | where documents, candidates and previews are stored |
| `--cors` | none | repeatable allowed origin for a dev frontend |
| `--allow-fixture-mode` | off | permit injected diagnoses, always labelled `fixture` |

Interactive docs are at `http://127.0.0.1:8000/docs`.

State lives in memory. Restarting forgets the job list; the files under `--root`
remain, and the uploaded originals were never written to in the first place.

### Check it can actually work before you rely on it

```
GET /api/health
```

```json
{
  "service": "what-the-doc",
  "model_runtime": {
    "endpoint": "http://localhost:11434/api/chat",
    "configured_model": "gemma4:e2b",
    "available": true,
    "model_installed": true,
    "installed_gemma_models": ["gemma4:e2b", "gemma4:e4b"],
    "reason": null
  },
  "live_diagnosis_available": true,
  "renderer": {"available": true, "binary": "...\\soffice.exe", "note": "..."},
  "fixture_mode_allowed": false
}
```

When `live_diagnosis_available` is `false`, `POST /api/jobs` returns **503
`model_unavailable`** with the runtime's own reason. There is no mock to fall back to
and no canned answer is ever substituted. Show the reason; do not retry in a loop.

---

## Errors

Every error body has the same shape, and the frontend should branch on `error_code`,
never on the prose:

```json
{"error_code": "model_unavailable", "detail": "GEMMA_MODEL 'gemma4:e2b' is not installed on this runtime"}
```

| code | HTTP | meaning |
| --- | --- | --- |
| `unsupported_upload` | 415 | not a `.docx`, or not a usable one |
| `upload_too_large` | 413 | over 20 MiB |
| `not_found` | 404 | unknown document, job or preview page |
| `invalid_selection` | 400/404 | no such paragraph, or an empty complaint |
| `model_unavailable` | 503 | no Gemma runtime, or the configured tag is not installed |
| `fixture_mode_disabled` | 403 | an injected diagnosis was sent to a normal server |
| `invalid_state` | 409 | the action does not apply in this state |
| `candidate_integrity_failed` | 409 | the candidate on disk no longer checks out |
| `invalid_response` | 400 | an injected diagnosis broke the schema |
| `internal_error` | 500 | an unexpected server fault. The body never echoes the exception message, which can carry a stored path or document text. Before 23 October 2026 this was Starlette's plain-text `Internal Server Error` with no `error_code` |

Codes that describe a *job's* failure (`model_timeout`, `invalid_response`,
`unsupported_formatting`, `invalid_operation`, `render_failure`) arrive as the job's
`error_code` field, not as an HTTP error, because the request to start the job
succeeded.

An entirely ineffective proposal fails with `invalid_operation`, while
`diagnosis.status` remains the model's raw `proposed` status. This is a deterministic
refusal, not evidence that the model correctly abstained. Equal values include equal
inherited values, whose direct override would unnecessarily pin formatting. Zero and
`False` remain different from unset values.

For a mixed proposal, `proposal.operations` contains only the effective writes used
to create the candidate. `ineffective_operations` lists removed writes with their
paragraph id, field, current/proposed values, current origin and reason. The private
`pipeline.json` retains the complete model diagnosis. If validation fails before
preparation, `proposal` can still contain the refused raw plan; it is never a
downloadable candidate.

---

## 1. Upload a document

```
POST /api/documents        multipart/form-data, field name "file"
```

```bash
curl -F "file=@report.docx" http://127.0.0.1:8000/api/documents
```

```json
{
  "document_id": "9f1c3a7e4b2d08516c7a9e0f33b1d245",
  "source_sha256": "ca278f1a80650a47112774905a4a54b61736d76dda6d9f3178faab904276350f",
  "paragraph_count": 21,
  "has_styles_part": true,
  "default_paragraph_style": "Normal",
  "size_bytes": 10244,
  "uploaded_at": 1792531200.0,
  "origin": "upload"
}
```

`document_id` is 32 random hex characters. It is the **only** thing that selects a
stored file; the name you uploaded is used to reject the upload and is then discarded,
so it never becomes a path component. The file is validated by `engine.open_package`
on the way in, which is what rejects tracked changes, content controls, VBA, signed
packages, duplicate ZIP entries and ZIP bombs — not just the extension.

`POST /api/demo/document` builds the synthetic sample from `fixtures.py` instead, with
no upload. It is returned with `"is_demo_document": true`, `"origin":
"synthetic_demo_fixture"` and the planted faults listed, so a demo document is never
indistinguishable from a user's. Diagnoses of it are still live model answers.

`GET /api/documents/{document_id}` returns that same record again.

## 2. Read the evidence

```
GET /api/documents/{document_id}/paragraphs
```

```json
{
  "document_id": "9f1c…",
  "source_sha256": "ca278f…",
  "paragraphs": [
    {
      "paragraph_id": 1,
      "text": "Introduction",
      "style_id": "Heading1",
      "style_chain": ["Heading1"],
      "effective_properties": {
        "space_before_pt": {"value": 48.0, "origin": "direct", "overridden_by": []},
        "space_after_pt":  {"value": 12.0, "origin": "style:Heading1", "overridden_by": []},
        "alignment":       {"value": null, "origin": "application_default", "overridden_by": []}
      },
      "resolution_caveats": []
    }
  ],
  "warning": "… a null value with origin \"application_default\" means no layer set it, not zero …"
}
```

**Render `application_default` as "not set", never as `0`.** That distinction is the
point of the resolver and the one thing a UI can most easily get wrong.

```
GET /api/documents/{document_id}/paragraphs/{paragraph_id}/peers
```

returns what paragraphs sharing this style resolve to, most common first. Its own
`warning` says it plainly: differing from peers is evidence of a fault, not proof.

### Explicit breaks

```
GET /api/documents/{document_id}/breaks
```

Read-only. Every `w:br` in a top-level body paragraph, plus every
`w:lastRenderedPageBreak`, each with the eligibility the server derived from the
actual structure.

```json
{
  "source_sha256": "ca278f…",
  "break_count": 6, "removable_count": 3,
  "last_rendered_page_break_count": 1,
  "removable_break_types": ["page", "column"],
  "breaks": [
    {
      "kind": "explicit_break", "break_type": "page", "break_type_raw": "page",
      "target_id": "brk-3f9c21a0b7d4e5f6",
      "paragraph_id": 2, "style_id": null,
      "run_index": 1, "break_index_in_run": 0, "ordinal_in_paragraph": 1,
      "edit_eligible": true, "ineligible_reason": null,
      "text_before": "…before the break", "text_after": "after the break…",
      "run_has_properties": true, "runs_in_paragraph": 4
    },
    {
      "kind": "explicit_break", "break_type": "page",
      "target_id": "brk-7a1e4c8d2b0f9e33",
      "paragraph_id": 5, "edit_eligible": false,
      "ineligible_reason": "The break sits inside <w:hyperlink> rather than directly in a top-level body paragraph, so this version will not edit it."
    },
    {
      "kind": "last_rendered_page_break", "break_type": null, "target_id": null,
      "paragraph_id": 4, "edit_eligible": false,
      "ineligible_reason": "w:lastRenderedPageBreak records where the application that last paginated this document placed a page break. It is not a user-inserted break, so removing it would repair nothing."
    }
  ],
  "warning": "A target_id is issued by this server for this source revision only … A deliberate break is not a fault: identification is not a recommendation to remove."
}
```

Four rules a client has to respect here:

- **`target_id` is the only way to name a break.** It is minted by the server from the
  source revision and the break's location and type. The API accepts no XML, XPath,
  element index or path from a caller. An id from an earlier revision of the same
  document simply does not resolve, which is how a stale target is refused.
- **`edit_eligible: false` means refused, and `ineligible_reason` is the server's own
  words.** Show the row and the reason rather than hiding it, or "why can't I pick
  that one?" has no answer.
- **An untyped `w:br` is a line break**, not a page break: `textWrapping` is the
  schema default. `break_type_raw` is `null` in that case and `break_type` says
  `textWrapping`.
- **Use `ordinal_in_paragraph` together with `text_before` / `text_after` to label a
  row.** Two breaks in one paragraph must never render identically; an id is not
  something a person can tell apart. The ordinal counts every explicit break of that
  type in the paragraph, editable or not.
- **A break outside the top-level paragraphs has `paragraph_id: null`.** A break in a
  body-level table (`w:tbl`) or content control (`w:sdt`) carries `"container":
  "tbl"` (or `"sdt"`), `target_id: null`, `edit_eligible: false`, null run and
  ordinal fields, and a reason naming the container. It counts in `break_count`, never
  in `removable_count`, and cannot be selected or sent. Until 23 October 2026 such
  breaks were omitted entirely.

### Sections — inspection, and one convertible start type

```
GET /api/documents/{document_id}/sections
```

```json
{
  "source_sha256": "ca278f…",
  "section_count": 2,
  "has_final_body_section_properties": true,
  "sections": [
    {
      "section_index": 0,
      "defined_in": "paragraph_properties", "defining_paragraph_id": 1,
      "governs": "the section that ends with paragraph 1",
      "start_type": "continuous", "start_type_raw": "continuous",
      "start_type_is_default": false, "start_type_recognised": true,
      "is_continuous": true, "starts_new_column": false,
      "columns": {"declared": true, "count": 2, "equal_width": true,
                  "column_definitions": 0, "raw_num": "2"},
      "has_page_size": true, "has_page_margins": false,
      "first_paragraph_id": 0, "last_paragraph_id": 1,
      "is_final_section": false,
      "target_id": "sec-0000000000000000",
      "editable": false, "edit_eligible": false,
      "supported_start_changes": [],
      "ineligible_reason": "This is the first section of the document. A section start describes how a section follows the previous one, and there is no previous section here, so changing it would repair nothing.",
      "differing_page_properties": null
    },
    {
      "section_index": 1,
      "defined_in": "body", "defining_paragraph_id": null,
      "governs": "the final section of the document",
      "start_type": "nextPage", "start_type_raw": null,
      "start_type_is_default": true, "start_type_recognised": true,
      "is_continuous": false, "starts_new_column": false,
      "columns": {"declared": false, "count": 1},
      "has_page_size": true, "has_page_margins": true,
      "first_paragraph_id": 2, "last_paragraph_id": 5,
      "is_final_section": true,
      "target_id": "sec-7ae31e1d47034432",
      "editable": true, "edit_eligible": true,
      "supported_start_changes": ["continuous"],
      "ineligible_reason": null,
      "differing_page_properties": []
    }
  ],
  "editable": true,
  "editable_target_ids": ["sec-7ae31e1d47034432"],
  "supported_start_changes": [{"from": "nextPage", "to": "continuous"}],
  "warning": "… A paragraph-level w:sectPr governs the section ending with its paragraph; a body-level w:sectPr governs the final section. Paragraph ranges are body-paragraph ids, not page numbers: no visual page number can be derived from this XML. Continuous does not guarantee that a visible page break disappears."
}
```

**Exactly one section change exists: `nextPage` → `continuous`.** Nothing else about
a section can be altered through this API — not its columns, page size, margins,
orientation, numbering, headers or footers, and not the number or position of the
sections themselves.

`edit_eligible` is computed from the document, and every `false` carries the rule it
hit in `ineligible_reason`: the first section of a document is never eligible (its
start type describes a relationship to a previous section that does not exist), only
`nextPage` converts, and a section declaring page setup, numbering or header/footer
references different from the previous section's is refused with the differing
element named in `differing_page_properties`.

`start_type_is_default: true` means `w:type` was absent and `nextPage` is the
documented schema default — report it as a default, not as something the author
wrote. `first_paragraph_id` / `last_paragraph_id` are body paragraph ids, not page
numbers. A `target_id` is bound to the source revision **and** the current start
type, so one from an earlier revision will not resolve.

**Eligible is not a recommendation.** A section break before an appendix is a
decision, not a defect. Do not build a control that presents an eligible section as
a problem.

## 3. Start a diagnosis

```
POST /api/jobs
```

```json
{"document_id": "9f1c…", "paragraph_id": 1,
 "complaint": "Too much space above this heading.", "render": true}
```

→ **202 Accepted**, with the job in state `queued`. Optional fields: `"model"` to
override the tag for one run, `"timeout"` in seconds, and
`"expected_source_sha256"` — the revision the caller was looking at, refused with 409
if it does not match, as IDEATION section 7 proposes.

`"break_target_id"` is also optional: the break the person pointed at in the
interface, as a `target_id` from `GET /breaks`. It is checked **before** a model is
called, so a stale or invented id costs nothing:

| condition | response |
| --- | --- |
| not a string | 400 `invalid_selection` |
| no such break in this revision | 409 `invalid_state` — re-read `GET /breaks` |
| `edit_eligible: false` | 400 `unsupported_formatting`, with the server's reason |
| in a different paragraph than `paragraph_id` | 400 `invalid_selection` |

Sending it says *which* break is meant. It is not a request to remove one: the
diagnosis may still abstain, and a break the writer inserted on purpose is not a
fault. Omitting it when the paragraph holds more than one break is what produces
`needs_clarification` with a question rather than a guess.

`"section_target_id"` is the equivalent for a section, as a `target_id` from
`GET /sections`, and is checked the same way before a model is called:

| condition | response |
| --- | --- |
| not a string | 400 `invalid_selection` |
| sent together with `break_target_id` | 400 `invalid_selection` — they are different repairs, approved separately |
| no such section in this revision | 409 `invalid_state` — re-read `GET /sections` |
| `edit_eligible: false` | 400 `unsupported_formatting`, with the server's reason |

A section is **not** required to be in the selected paragraph: the final body
`w:sectPr` belongs to no paragraph at all. Like a break id, sending one says which
section is meant and is not a request to change it.

Only the selected section's id is shown to the model. A diagnosis naming any other
section — even a real, eligible one — is refused as a fabrication, exactly as an
unsupplied break id is.

CPU inference on this machine can take **several minutes per call**, so this is
asynchronous by necessity rather than by taste. One job runs at a
time; a second request genuinely waits in `queued`.

## 4. Poll it

```
GET /api/jobs/{job_id}
```

**Poll every 2–3 seconds** and keep polling while `state` is `queued` or `running`.
There is no websocket and no long-poll. Give the request a client timeout well above
the inference time, or none at all — the poll itself returns immediately; it is the
job that is slow. Show elapsed time rather than a progress bar: the runtime reports no
progress, and inventing one would be a lie about something the frontend cannot see.

`GET /api/jobs` returns `{"jobs": [...]}`, every job this process knows about in the
same shape, for a history panel. It is in-memory, so it is empty after a restart.

### The eight states

| state | meaning | what the UI should offer |
| --- | --- | --- |
| `queued` | accepted, waiting for the worker | elapsed time |
| `running` | the pipeline is executing | elapsed time |
| `needs_clarification` | diagnosis status `needs_clarification` | show `diagnosis.question`, let them start a new job with a better complaint |
| `no_supported_repair` | diagnosis status `no_change` **or** `unsupported` | show `diagnosis.status` and `explanation`; there is nothing to accept |
| `failed` | the run failed | show `error_code` and `error_detail` |
| `awaiting_review` | a candidate exists and is **not** accepted | previews, diff, Accept and Reject |
| `accepted` | a human accepted it | download |
| `rejected` | a human rejected it | nothing; the candidate stays on disk |

`no_change` and `unsupported` share a state because the UI does the same thing for
both, but the real status is always reported in `diagnosis.status` and is never
replaced.

### A finished job awaiting review

```json
{
  "job_id": "41ab…", "document_id": "9f1c…",
  "state": "awaiting_review",
  "mode": "live", "is_live_model_answer": true,
  "paragraph_id": 1,
  "accepted": false,
  "error_code": null, "error_detail": null,
  "diagnosis": {
    "status": "proposed",
    "explanation": "This heading has a direct 48pt space above it where every other Heading1 inherits 12pt.",
    "evidence": [{"paragraph_id": 1, "field": "space_before_pt"}],
    "question": null
  },
  "proposal": {
    "source_sha256": "ca278f…",
    "operations": [{"paragraph_id": 1, "field": "space_before_pt", "value": 12.0}]
  },
  "preview": {
    "rendered": true,
    "before_page_count": 4, "after_page_count": 4,
    "before_urls": ["/api/jobs/41ab…/preview/before/0", "…/before/1", "…"],
    "after_urls":  ["/api/jobs/41ab…/preview/after/0",  "…/after/1",  "…"],
    "renderer_available": true,
    "note": "Rendered by LibreOffice. It approximates Word; it is not Word."
  },
  "visual_comparison": {
    "before_pages": 4, "after_pages": 4, "page_count_delta": 0,
    "pages": [{"page": 1, "status": "compared", "changed_ratio": 0.26551},
              {"page": 2, "status": "compared", "changed_ratio": 0.0}],
    "note": "Changed-pixel ratios are review signals. …"
  },
  "candidate": {
    "candidate_sha256": "7d3e…",
    "reused": false,
    "accept_requires_candidate_sha256": "7d3e…",
    "download_available": false
  },
  "invariants": {
    "original_unmodified": {"passed": true, "detail": "sha256 of the input file is unchanged by the run"},
    "text_preserved": {"passed": true, "detail": "…"},
    "styles_part_unmodified": {"passed": true, "detail": "…"}
  },
  "invariant_failures": [],
  "operation_kind": "paragraph_property",
  "plan_schema_version": 1,
  "break_target_id": null,
  "break_targets": null,
  "rendering_required": false,
  "rendering_satisfied": true,
  "model": "gemma4:e2b",
  "latency_seconds": 73.637,
  "decision": null,
  "note": "A candidate exists for review and is NOT accepted. …"
}
```

### A job that removes an explicit break

The same shape, with the break-specific fields populated. `proposal.operations` holds
a differently-shaped operation, so **branch on the presence of `operation`** rather
than assuming `field` and `value` exist:

```json
{
  "state": "awaiting_review",
  "operation_kind": "explicit_break_removal",
  "plan_schema_version": 2,
  "break_target_id": "brk-3f9c21a0b7d4e5f6",
  "rendering_required": true,
  "rendering_satisfied": true,
  "diagnosis": {
    "status": "proposed",
    "explanation": "The complaint says this page break is unwanted; removing it leaves the text and every other break untouched.",
    "evidence": [{"paragraph_id": 2, "target_id": "brk-3f9c21a0b7d4e5f6"}],
    "question": null
  },
  "proposal": {
    "source_sha256": "ca278f…",
    "schema_version": 2,
    "operations": [{"operation": "remove_explicit_break",
                    "target_id": "brk-3f9c21a0b7d4e5f6",
                    "expected_break_type": "page"}]
  },
  "break_targets": [
    {"target_id": "brk-3f9c21a0b7d4e5f6", "break_type": "page",
     "paragraph_id": 2, "run_index": 1, "ordinal_in_paragraph": 1,
     "text_before": "…before the break", "text_after": "after the break…"}
  ]
}
```

`operation_kind` is one of `paragraph_property`, `explicit_break_removal`, `mixed` or
`none`. It is a label on a plan that was already validated elsewhere; it grants
nothing.

`break_targets` is what the run is about to delete, with the text either side, so a
reviewer sees the actual break instead of a hash. Render it before the Accept
control, not after.

**An evidence entry has either `field` or `target_id`, never both.** The second form
cites one identified break.

**`rendering_required` with `rendering_satisfied: false` cannot be accepted or
downloaded.** An explicit-break removal changes document structure, so both the
original and the candidate must have rendered successfully first. The job reports
`failed` with `error_code: "render_failure"` in that case, and acceptance and
download are refused with 409 — see §6 and §7. Paragraph-property repairs are
unchanged and may still be accepted after a structural-only review when no renderer
is installed.

`mode` is `live` or `fixture`, and `is_live_model_answer` says the same thing as a
boolean. A `fixture` job's answer was injected by a developer, not produced by a
model. **Surface this in the UI.** A fixture job can only exist on a server started
with `--allow-fixture-mode`.

`visual_comparison` is a **review signal, not approval**. A correct repair legitimately
reflows later text, so a large `changed_ratio` means "look at this", not "this is
wrong".

## 5. Previews

```
GET /api/jobs/{job_id}/preview/{before|after}/{index}
```

Returns `image/png`, one rendered page. Use the `before_urls` / `after_urls` arrays
rather than building these yourself; the index is server-side and a page is only ever
served from inside that job's own directory. They are plain `<img src>` targets, and
there are no signed URLs or expiry — anyone who can reach the port can read them,
which is the main reason the port is on localhost only.

When the job ran with `"render": false`, or LibreOffice is not installed, `rendered`
is `false` and both arrays are empty. That is reported as *not rendered*, never as
"no visible change".

An available renderer that fails to launch, exits with a nonzero code, times out or
produces missing, empty or invalid output instead makes the job `failed` with
`error_code: "render_failure"`. No candidate or partial previews are offered by the
API; accept and download return `409 invalid_state`. Partial files can remain in the
private job directory. Each conversion validates fresh staging output, preventing an
old PDF from masquerading as the result of a failed retry.

## 6. Accept or reject — the one step the backend will not take for you

A job reaching `awaiting_review` is **not** an approval. Completing a job is not
accepting it, and no changed-pixel ratio, status or explanation is treated as consent.

The current policy permits explicit acceptance without previews when rendering was
disabled or the renderer is unavailable, provided structural checks pass. The UI must
state that visual comparison was unavailable; the reviewer should inspect the DOCX
in Word. A renderer failure is a failed job and cannot be accepted.

**That permission does not extend to an explicit-break removal.** When
`rendering_required` is true and `rendering_satisfied` is false, accept is refused
with 409 `render_failure` and the job stays where it is. This is enforced in the
server for every caller: a disabled button in one interface is not a rule.

```
POST /api/jobs/{job_id}/accept
{"candidate_sha256": "7d3e…", "reviewer": "optional label"}
```

The hash is mandatory and must equal `candidate.accept_requires_candidate_sha256`, so
a candidate nobody looked at cannot be accepted by a stray POST. Before the state
changes, the preservation invariants are **re-run against the files on disk**: a
tampered, replaced or missing candidate is refused with 409
`candidate_integrity_failed` and the job stays `awaiting_review`.

```
POST /api/jobs/{job_id}/reject
{"reason": "the heading should stand out", "reviewer": "optional label"}
```

The candidate file is left in place for inspection. A rejected job cannot then be
accepted.

## 7. Download

```
GET /api/jobs/{job_id}/download
```

Only in state `accepted`; anything else is 409 `invalid_state`. The integrity check
runs **again** here, immediately before the bytes are served, because "it passed at
acceptance" is not evidence about the file now. The rendering requirement is
re-checked here too: a candidate carrying a structural change that was never rendered
is refused with 409 `render_failure` and not served, even if it somehow reached
`accepted`. The filename is server-chosen
(`repaired-<job id prefix>.docx`); the uploaded name never round-trips.

The original is never modified and is always the undo path. There is no endpoint that
overwrites it, because `engine.apply` refuses to.

---

## A complete frontend flow

```js
const API = "http://127.0.0.1:8000";

const { live_diagnosis_available, model_runtime } = await (await fetch(`${API}/api/health`)).json();
if (!live_diagnosis_available) throw new Error(model_runtime.reason);

const form = new FormData();
form.append("file", file);                       // a File from <input type=file>
const doc = await (await fetch(`${API}/api/documents`, { method: "POST", body: form })).json();

const { paragraphs } = await (await fetch(`${API}/api/documents/${doc.document_id}/paragraphs`)).json();
// let the user click a paragraph and type what looks wrong …

const started = await fetch(`${API}/api/jobs`, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ document_id: doc.document_id, paragraph_id: 1,
                         complaint: "Too much space above this heading.", render: true }),
});
if (started.status === 503) { /* show error_code model_unavailable; do not retry blindly */ }
let job = await started.json();

while (job.state === "queued" || job.state === "running") {
  await new Promise(r => setTimeout(r, 2500));   // CPU inference: 70-180s is normal
  job = await (await fetch(`${API}/api/jobs/${job.job_id}`)).json();
}

if (job.state === "awaiting_review") {
  // render job.preview.before_urls / after_urls side by side, show job.diagnosis
  // and job.proposal.operations, and only then offer the buttons:
  await fetch(`${API}/api/jobs/${job.job_id}/accept`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ candidate_sha256: job.candidate.candidate_sha256 }),
  });
  window.location = `${API}/api/jobs/${job.job_id}/download`;
}
```

---

## How this differs from the plan in IDEATION section 7

The planned surface named proposals and versions separately. This one collapses
`POST /documents/{id}/proposals` and `GET /proposals/{id}` into a single **job**,
because one complaint produces exactly one diagnosis and at most one candidate, and a
second id for the same thing is a second thing to get wrong. The planned state flow is
preserved, including `awaiting_review` before `accepted`, the immutable parent plus
proposal hash naming a candidate, and the refusal to patch an already patched copy.

Not implemented from that section: `DELETE /documents/{id}`, and "authenticated
private download" — the download is unauthenticated, like the rest of the service.

## What this API does not do

- No authentication, authorisation, accounts, rate limiting or quotas.
- No database. The job table is in memory and is lost on restart.
- No sandbox around LibreOffice. The throwaway profile and timeout in `render.py` are
  all there is; the container, dropped privileges and blocked egress described in
  `IDEATION.md` section 9 are **not** implemented.
- No cancellation. A running job finishes or times out.
- No Word. Previews come from LibreOffice, which approximates Word's layout; on the
  one synthetic fixture measured they agreed on page counts, which is not a general
  claim. **No candidate produced through this API has been opened in Word.**
- No deletion endpoint. Stored documents, candidates and previews accumulate under
  `--root` until you delete the directory yourself.
- **One section change only.** A supported `nextPage` start may become
  `continuous`. Nothing in this API creates, merges, splits or deletes a section, or
  alters a column layout, page size, margin, orientation, numbering, header or
  footer. Every other start type, `evenPage` and `oddPage` included, is inspected
  and refused.
- **No more than one section change per diagnosis, and never alongside another
  operation.** A section plan carries exactly one operation.
- **Continuous is not a promise that a page disappears.** The response reports the
  real before and after page counts, per page, and claims nothing beyond them.
- No removal of a break outside a top-level body paragraph, of a line break, or of a
  `w:lastRenderedPageBreak`. Those are reported with a reason and refused.
- No more than one break removal per diagnosis, and no bulk "remove every break that
  looks wrong" operation. Each break is approved separately.

Optional Sentry tracing starts inside the actual worker and finishes when execution
ends. Later acceptance, rejection and download use separate finite transactions
correlated by the opaque job ID. No request bodies, document content, filenames,
paths, prompts, model response text or reviewer/rejection text are exported. See
[`OBSERVABILITY.md`](OBSERVABILITY.md) for configuration and verification limits.

Routine logs carry job and document ids, states, codes, byte counts and latencies
only — no complaint text, paragraph text, uploaded filename or stored path. Note that
`pipeline.json` inside each job directory **does** record the user's complaint, as it
does for the CLI; that is a file under `--root`, not a log line.
