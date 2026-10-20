# Optional local-pipeline tracing

What-the-Doc uses `sentry-sdk==2.72.0` with manual instrumentation of its existing
Ollama/Gemma pipeline. No Sentry configuration is required to repair a document.
Missing SDK/configuration, initialization failures, span failures and exporter
failures leave the repair result unchanged.

## Configuration

Install the normal dependencies, then set these variables **before** starting a
fresh backend process. Supply your own project DSN; none is checked into the app.

```powershell
Set-Location 'C:\Users\Aarushi\Downloads\What-the-Doc-Starter\what-the-doc'
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

Allowed metadata comprises model identifiers, live/fixture mode, known outcomes and
error codes, opaque job IDs, the SHA-256 of the actual static system prompt, and an
aggregate SHA-256 of the current backend implementation files. No invented schema
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
enter the telemetry API. Existing local diagnostic/evaluation artifacts keep their
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
