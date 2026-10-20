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
  "model": "gemma4:e2b",
  "latency_seconds": 73.637,
  "decision": null,
  "note": "A candidate exists for review and is NOT accepted. …"
}
```

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
acceptance" is not evidence about the file now. The filename is server-chosen
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

Optional Sentry tracing starts inside the actual worker and finishes when execution
ends. Later acceptance, rejection and download use separate finite transactions
correlated by the opaque job ID. No request bodies, document content, filenames,
paths, prompts, model response text or reviewer/rejection text are exported. See
[`OBSERVABILITY.md`](OBSERVABILITY.md) for configuration and verification limits.

Routine logs carry job and document ids, states, codes, byte counts and latencies
only — no complaint text, paragraph text, uploaded filename or stored path. Note that
`pipeline.json` inside each job directory **does** record the user's complaint, as it
does for the CLI; that is a file under `--root`, not a log line.
