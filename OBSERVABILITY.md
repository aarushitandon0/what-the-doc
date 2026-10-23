# Optional local-pipeline tracing

What-the-Doc uses `sentry-sdk==2.72.0` with manual instrumentation of its existing
Ollama/Gemma pipeline, and — since 23 October 2026 — content-free **error events**
from the backend (`observability.capture_error`) and the browser
(`frontend/src/telemetry.js`, `@sentry/browser` 11.6.0, loaded only when
`VITE_SENTRY_DSN` is set). What each sends and never sends is in
[SENTRY_SETUP.md](SENTRY_SETUP.md). No Sentry configuration is required to repair a document.
Missing SDK/configuration, initialization failures, span failures and exporter
failures leave the repair result unchanged.

## Configuration

Install the normal dependencies, then set these variables **before** starting a
fresh backend process. Supply your own project DSN; none is checked into the app.

```powershell
Set-Location '<path-to-your-clone>\what-the-doc'
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:GEMMA_MODEL = 'gemma4:e2b'
$env:SENTRY_DSN = '<your Sentry project DSN>'
$env:WTD_SENTRY_TRACES_SAMPLE_RATE = '1.0'
.\.venv\Scripts\python.exe api.py --host 127.0.0.1 --port 8012 --root work/api-8012 --cors http://127.0.0.1:5177
```

Use a confirmed free port, and point Vite's `VITE_API_TARGET` at that backend
as described in `frontend/docs/VERIFICATION.md`. Ctrl+C in that terminal stops only
the server you started. Do not kill a historical PID from a report.

`WTD_SENTRY_TRACES_SAMPLE_RATE` accepts 0 through 1; it defaults to 1 when a DSN is
present. A missing DSN disables telemetry. To run offline, remove the DSN before a
fresh startup:

```powershell
Remove-Item Env:SENTRY_DSN -ErrorAction SilentlyContinue
```

Configuration is read once per process. Malformed configuration or initialization
failure disables that exporter until restart. The Sentry client uses fixed
`environment=local` and `release=what-the-doc`; the exact source revision is a
separate hash. No document path or environment-variable dump is exported.

## Trace boundaries and interpretation

| Transaction/span | What it measures |
| --- | --- |
| `repair.execute` | Actual asynchronous worker execution, or one CLI pipeline run |
| `structure.inspect` | Explicit-break and section inspection: the HTTP inspection endpoints, the break-selection check at job creation, and the evidence built for the prompt |
| `diagnosis.execute` | Diagnoser call, including the adapter around inference |
| `model.inference` (`gen_ai.chat`) | Ollama HTTP inference and runtime-envelope extraction |
| `diagnosis.validate` | Parsing and schema validation of the model diagnosis |
| `proposal.prepare` / `proposal.validate` | Full raw-plan validation, ineffective-write refusal, then preparation |
| `candidate.create` | Apply the prepared plan or verify/reuse the candidate |
| `render.original` / `render.candidate` | PDF conversion, page rasterization, and candidate comparison |
| `preservation.check` | Independent checks of the original and candidate bytes |
| `review.accept` / `review.reject` | A finite, later human-review request |
| `review.integrity` | Revalidation before acceptance |
| `download.execute` / `download.integrity` | A later download request and integrity checks |

`repair.execute` begins inside the executor worker, not around the short HTTP
enqueue request. Nested pipeline calls reuse it. Spans share the real parent trace
and opaque `wtd.job_id`. Review and download have their own transactions and trace
IDs, correlated by that same opaque job ID. No transaction remains open while
waiting for a person. No incoming browser trace headers or user identity are needed.

Only stages actually executed have spans. Fixture diagnosers bypass the model and
its schema parser; disabled/missing rendering has no render span. `awaiting_review`
remains unaccepted, and rendering has no authority to approve a document.

The model span uses Sentry's current manual AI conventions:
`op=gen_ai.chat`, `gen_ai.operation.name=chat`,
`gen_ai.operation.type=ai_client`, `gen_ai.provider.name=ollama`, the requested
model and the runtime's response-model identifier when present. Its fixed span name
is `model.inference`. The repair root is a custom `task`, not an asserted autonomous
agent invocation. Local AI-span emission does **not** establish that a hosted
Sentry Agents dashboard accepts or displays this integration.

Two closed-set labels describe the kind of work rather than its content.
`wtd.operation_kind` is one of `paragraph_property`, `explicit_break_removal`,
`section_start_change`, `mixed` or `none`, and `wtd.break_type` is `page` or
`column`. Both are validated against those sets, so an unrecognised value is dropped
rather than exported and no free-form string can ride out on either key. They label
work that was already validated elsewhere and grant nothing. `wtd.break_type` is set
only when every break target in a plan is the same type; a mixed plan leaves it
absent rather than picking one.

`section_start_change` was added to the closed set alongside the operation itself.
`wtd.break_type` was deliberately **not** widened to carry a section start type:
`nextPage` or `continuous` would be a new fact about the user's document rather than
a label on work the service already validated, and the rule here is that telemetry
describes the work, never the document. A section change therefore exports its kind
and its outcome, and nothing about which section or what it was.

A break's `target_id` is **not** exported. It is a digest of the document revision and
a position rather than content, but it is not in the allowlist either, so it cannot
be used to correlate telemetry with a particular place in a particular document.

Allowed metadata comprises model identifiers, live/fixture mode, known outcomes and
error codes, opaque job IDs, the operation kind and break type above, the SHA-256 of
the actual static system prompt, and an aggregate SHA-256 of the current backend
implementation files. No invented schema
version is assigned. Stage durations come from SDK timestamps. Only nonnegative
integer `prompt_eval_count` / `eval_count` reported by Ollama become
`gen_ai.usage.input_tokens` / `output_tokens`; total is recorded only when both are
known. Missing/invalid counts remain absent. No monetary or total-compute-cost
estimate is produced for local inference.

## Privacy and failure isolation

The client disables all default and automatically enabled integrations. There is no
FastAPI, logging, HTTP, subprocess or model integration capturing content behind
these manual spans. Every SDK 2.72 `data_collection` category is explicitly disabled:
user information, cookies, request headers, HTTP bodies, query parameters, GraphQL,
AI inputs/outputs, database query data, queues, stack variables and source context.
Logs, breadcrumbs, sessions, profiles and trace-header propagation are disabled.
No replay or browser SDK is installed by this change.

Error events are dropped. Failures are represented by allowlisted error codes and
span status; raw exception messages, tracebacks and frame locals are not exported.
The transaction hook reconstructs the payload from the allowlist, dropping request
data, users, arbitrary tags/extras/contexts, SDK/server metadata and unknown spans.
The final transport reconstructs the envelope too: only sanitized transaction items
and event-ID/send-time headers survive. Attachments, logs, sessions, profiles and
standalone spans are refused. `stream_gen_ai_spans=False` keeps AI spans inside the
transaction privacy boundary. Client binding covers both SDK current and isolation
scopes and restores ambient scopes afterward.

Document/complaint text, full prompts, raw model outputs, filenames, filesystem
paths, credentials, DOCX/PDF/image attachments and local evaluation snapshots never
enter the telemetry API. The text either side of an identified break is in the same
category: it is how a person tells two breaks in one paragraph apart, so it exists in
the HTTP response and in the on-disk job report, and it must never reach telemetry.
`test_observability_breaks.py` asserts its absence from complete exported envelopes
using sentinels planted in the fixture document. Existing local diagnostic/evaluation artifacts keep their
existing behavior; they are not automatically uploaded or attached.

SDK initialization, span entry/exit, transport submission and flush are best effort.
Telemetry exception handling surrounds SDK work only; application exceptions retain
their existing behavior. Successful local emission or `flush()` is not proof of
network delivery, retention or hosted visibility.

## Verification and debugging evidence

`test_observability.py` inspects actual serialized SDK envelopes through an in-memory
transport with a fake DSN. Eight tests cover real adapter boundaries with an injected
HTTP response, AI metadata and actual response-envelope counts, unknown/invalid token
usage, schema refusal, an injected renderer failure, ineffective proposal refusal,
disabled collection, and missing/configuration/export/span failures. Sensitive
complaint, model-output, path and exception sentinels are absent from complete
exported payloads. These tests do not contact Ollama or Sentry.

`test_observability_breaks.py` covers the explicit-break slice against the same
in-memory transport: that `structure.inspect` is traced as its own stage, that the
operation kind appears on the stages that have one, that a column break is labelled a
column break, that a refused target is traced as `invalid_operation`, that an
unrendered break removal is traced as a `render_failure`, that a rendered removal
traces every stage with durations, that an unknown operation kind or break type is
dropped instead of exported, and that no document text, nearby break text, target id
or path appears in any payload.

`test_api_observability.py` separately exercises the actual asynchronous API worker,
finite review/download correlation and sensitive request values.
`test_observability_review.py` seeds global, isolation and current SDK scopes with
sensitive user/extra/attachment content and an unrelated ambient client. The private
client is selected only within our scope, the unrelated client receives nothing,
and the complete exported envelope contains a sanitized transaction only.

One controlled debugging observation is the existing ineffective-plan guard: a
fixture proposes 48pt where the paragraph already resolves to 48pt. The raw model
status remains `proposed`; `proposal.validate` and `proposal.prepare` fail with
`invalid_operation`, the root finishes `failed`, and there is no `candidate.create`
span or candidate artifact. This is an observed deterministic refusal, not a newly
discovered engine bug or a live-model observation. Another test injects a renderer
failure and shows `render.original` plus the root retaining `render_failure` without
exporting the exception message. Full-run counts and browser/live timing evidence
belong in the latest section of `TEST_RESULTS.md`.

Hosted verification is **still pending after the explicit-break session**. No
`SENTRY_DSN` is set in this environment, no project DSN or account access exists in
the workspace, and none was requested: a secret must not be pasted into a working
session. The remaining requirement is therefore access, not code — the instrumentation
for the new stages is written and locally verified against real serialized envelopes.

What a hosted check still has to establish, and what no local test can substitute for:
that the sanitized transaction is accepted and displayed, that `structure.inspect`,
the model span and the render spans appear with their durations under one trace, that
review and download transactions correlate by the opaque job id across the
asynchronous worker boundary, and that nothing beyond the allowlist arrives.

Hosted verification is pending: no usable project DSN/account access was available
in this workspace. Supply a project DSN and access to that Sentry project's trace
interface, restart with the settings above, and run a synthetic live repair plus a
clearly labelled controlled failure. Inspect those sanitized events in the actual
hosted interface, including the AI model span, correlation, timings and error codes.
Do not substitute the memory transport, a local JSONL export or an SDK flush for
that hosted check.

## Official references consulted

- [Manual AI instrumentation](https://docs.sentry.io/platforms/python/agent-tracing/manual-instrumentation/)
- [Custom transactions and spans](https://docs.sentry.io/platforms/python/tracing/instrumentation/custom-instrumentation/)
- [Python SDK options](https://docs.sentry.io/platforms/python/configuration/options/)
- [Data collected and explicit category controls](https://docs.sentry.io/platforms/python/data-management/data-collected/)
- [Sentry AI attribute conventions](https://getsentry.github.io/sentry-conventions/attributes/gen_ai/)

The installed SDK options, transport and scope implementation were inspected as
well as current official documentation. Recheck collection categories, AI streaming
behavior and envelope tests before changing the pinned SDK version.
