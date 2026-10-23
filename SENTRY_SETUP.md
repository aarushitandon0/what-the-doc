# Sentry setup

**Current status: not verified against a hosted Sentry project.** No real DSN is
configured on this machine, so nothing has ever been sent to sentry.io from this
code, and no event has been seen in a Sentry dashboard. Those claims are kept apart
from the ones below everywhere in this repository.

What *is* verified, as of 23 October 2026:

- **Envelope tests.** `test_observability`, `test_observability_breaks`,
  `test_observability_review`, `test_api_observability` and
  `test_observability_errors` (backend), and `frontend/src/telemetry.test.jsx`, capture
  the envelope at the transport boundary and assert that the forbidden content is
  absent.
- **The real HTTP transports, end to end, against a local stand-in.** Both SDKs were
  pointed at `work/session3/sentry_capture_server.py` with a DSN of the form
  `http://publickey@127.0.0.1:8099/<project>`, so the bytes that would leave the
  machine were captured off the wire. Backend: a real `api.py` run with a failing
  renderer, an accepted job and a rejected job posted **1 error event and 6
  transactions** (gzip, `/api/1/envelope/`). Browser: real Chromium on a production
  bundle posted **2 error events** (an uncaught `TypeError` and an unhandled
  rejection). Every sentinel planted in a file name, complaint, model output,
  exception message, renderer stderr and URL query was absent from what arrived, as
  were the checkout path and the Windows user name. Results:
  `work/session3/sentry-wire-backend.json`, `work/session3/sentry-wire-frontend.json`.

That wire test found a real defect, which the unit tests had passed: the browser
filter mapped the SDK's handler names as `onerror`, but `@sentry/browser` 11 calls
them `auto.browser.global_handlers.onerror`, so every uncaught browser error was
silently dropped. It is fixed, and a unit test now drives the handler the SDK
actually installs. See **[OBSERVABILITY.md](OBSERVABILITY.md)** for what is traced.

**Still unverified:** that a hosted Sentry project accepts these envelopes, groups
them sensibly, and shows them. That needs a real DSN and a person looking at the
project.

---

## The variables this code reads

### Backend (`api.py`)

Both are read **once**, by `observability.initialize()`, when the app is created.

| Variable | Read at | Effect |
| --- | --- | --- |
| `SENTRY_DSN` | `observability.py` → `initialize()` | The project DSN. **Empty or absent means telemetry is off**, and the application runs normally with no client at all. |
| `WTD_SENTRY_TRACES_SAMPLE_RATE` | `observability.py` → `initialize()` | Float in `[0, 1]`, default `1.0`, for transactions only. A value outside that range disables telemetry rather than being clamped. Error events are not sampled: each one is sent. |

### Frontend (Vite build)

Read **at build time** by `npm run build` / `npm run dev`. They are compiled into the
bundle, so changing one means rebuilding.

| Variable | Read at | Effect |
| --- | --- | --- |
| `VITE_SENTRY_DSN` | `frontend/src/telemetry.js` → `initTelemetry()` | A **browser** project's DSN. Empty or absent: no SDK is downloaded, nothing is initialised, and the page never requests the `sentry-sdk-*.js` chunk (checked in the browser run). |
| `VITE_SENTRY_ENVIRONMENT` | `frontend/src/telemetry.js` | Environment label, default `local`. |

**A browser DSN is public by construction.** Every `VITE_*` value is in the
JavaScript anyone can download, which is how browser error reporting works: the DSN
only lets a client *submit* events. Use a **separate Sentry project for the browser**
— never the backend's DSN — and set its allowed domains and rate limits in Sentry.

There is no config file, no CLI flag and no API field for either. Telemetry cannot be
turned on by a request.

### Read once per process

`initialize()` sets an `_attempted` flag under a lock and returns the cached client
afterwards. **Setting or changing either variable has no effect until `api.py` is
restarted.** This is the single most common reason a correctly-set DSN appears to do
nothing.

---

## Turning it on

```powershell
# In the SAME PowerShell window you will start api.py in.
$env:SENTRY_DSN = "<your project DSN from Sentry>"
$env:WTD_SENTRY_TRACES_SAMPLE_RATE = "1.0"

cd <path-to-your-clone>\what-the-doc
.\.venv\Scripts\python.exe api.py --cors http://localhost:5173
```

An environment variable set in one window does not reach another, and a variable set
after the server started does not reach the server. If you are not sure:

```powershell
# in the window the server is about to run in
$env:SENTRY_DSN.Length    # non-zero means set; this does not print the secret
```

**Do not paste a DSN into a chat window or a commit, and never put the backend's DSN
in a `VITE_*` variable.** The backend DSN stays server-side. The browser gets its own
project's DSN through `VITE_SENTRY_DSN`, set in the shell that runs the build:

```powershell
cd <path-to-your-clone>\what-the-doc\frontend
$env:VITE_SENTRY_DSN = "<your BROWSER project DSN>"
npm run build
```

### Confirming it took effect

```powershell
.\.venv\Scripts\python.exe -c "import observability; print('telemetry on:', observability.initialize())"
```

`True` means a client was constructed from the DSN in *this* process. It does not yet
mean a trace reached Sentry.

### Producing a synthetic trace

With the server running and the DSN set, do one complete run — upload a document from
`test-documents\`, diagnose, and reject it. Then:

```powershell
.\.venv\Scripts\python.exe -c "import observability; observability.flush()"
```

`api.py` also flushes on shutdown, so stopping it with `Ctrl+C` flushes too.

In Sentry, look under **Performance / Traces** for transactions named
`repair.execute`, `review.accept`, `review.reject` and `download.execute`, with spans
named `structure.inspect`, `model.inference`, `candidate.create`, `render.original`,
`render.candidate` and `preservation.check`. (An earlier version of this page gave
`job.*` names; the code and the wire capture both use these.)

Under **Issues**, errors are grouped by where they were caught, the error code and
the exception class:

| `wtd.site` | Raised when |
| --- | --- |
| `job.execute` | a repair job crashed outside the pipeline's own error handling (`internal_error`) |
| `render.failure` | an *available* renderer failed (`render_failure`). An absent renderer is a configuration, shown by `/api/health`, and is not reported |
| `request.unhandled` | an API request raised an unexpected exception. The client now receives a coded `500 {"error_code": "internal_error"}` instead of Starlette's plain-text page |
| `window.error`, `window.unhandledrejection` | browser: an uncaught error or rejected promise |
| `react.uncaught`, `react.caught` | browser: a render error. The page now shows "This screen failed to draw" instead of going blank |

Expected outcomes are **not** errors: a model abstaining, `invalid_response`,
`model_timeout` and a refused repair are outcomes on the transaction, not issues.

If nothing arrives: the DSN was wrong, the process was started before the variable was
set, the sample rate was outside `[0, 1]`, or egress is blocked. The client is built
to fail silently rather than take the application down with it, so **absence of
traces is not an application error** and nothing in the logs will say so.

---

## What is never sent

This is enforced in layers — an allowlist of tag keys, a sanitiser for each event
type, and (backend) a transport that rebuilds the envelope from scratch — not by
remembering to be careful at each call site. Each sanitiser *rebuilds* an event from
a small schema rather than redacting what the SDK collected, so a field nobody
thought of cannot leak.

Never sent, under any configuration:

- document text, or any fragment of it
- the user's complaint
- the prompt, the model's explanation, its question, or any model output
- file names, document titles, or any local path
- rendered pages, screenshots, replays or attachments
- **exception messages** — an error is identified by its class name and code; its
  message can quote a path, renderer output or the model, any of which can quote the
  document, so it is never read
- secrets, headers, cookies, page URLs, query strings, request bodies
- breadcrumbs, user identity, IP address, server name, local variables, source lines
- stack frames outside this project's own code, and absolute paths for any frame

The backend transport keeps only `event_id` and `sent_at` from the envelope headers,
copies across only transaction and error-event items, and re-sanitises each one. Any
other item type — an attachment, a session, a log — is dropped rather than forwarded.
An error event that `capture_error` did not build (no recognised `wtd.site`) is
dropped too, so an ambient `sentry_sdk.capture_exception` elsewhere cannot use this
client.

In the browser there is no replay, tracing, session, feedback, breadcrumb or
HTTP-context integration installed at all — only the two window handlers and
de-duplication — and `sendDefaultPii` is off. The SDK adds its own `sdk` block (its
name, version and integration names) after filtering; that is the one field in a
browser event that the sanitiser does not produce.

## What is sent

Typed, closed-set metadata about work the application already validated:

| Tag | Values |
| --- | --- |
| `wtd.operation_kind` | `paragraph_property`, `explicit_break_removal`, `section_start_change`, `mixed`, `none` |
| `wtd.break_type` | `page`, `column` |
| `wtd.outcome` | a fixed set including `awaiting_review`, `accepted`, `rejected`, `failed`, `no_change`, `unsupported`, `needs_clarification` |
| `wtd.error_code` | a fixed set matching the API's error codes |
| `wtd.mode` | `live`, `fixture` |
| `wtd.job_id` | 32 hex characters |
| `wtd.prompt_sha256`, `wtd.source_revision` | 64 hex characters |
| `gen_ai.request.model`, `gen_ai.response.model` | a model tag matching a conservative pattern |
| `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` | integers |

Each is checked against its own rule before it is attached. A value that does not
match — a free-form string arriving where a closed set is expected — is dropped, not
truncated. `section_start_change` was added to the closed set in this session
alongside the operation itself; `wtd.break_type` was deliberately **not** extended to
carry a section start type, because a start type would be a new fact about the
document rather than a label on work already validated.

Error events add only:

| Field | Values |
| --- | --- |
| `wtd.site` | the closed set in the Issues table above |
| `wtd.route` (browser) | the route name (`upload`, `review`, …) or `other`; never the URL |
| exception `type` | the class name (`RenderFailed`, `TypeError`), if it is a plain identifier; otherwise `Error` |
| exception `value` | the error code or site, **not** the message |
| frames (backend) | `module`, `function`, `lineno` for frames in this project's own top-level `.py` files only |
| frames (browser) | path within this site's own origin, `function`, `lineno`, `colno`; frames from any other origin (extensions, CDNs) are dropped |

Note what the tags do *not* let you reconstruct: `wtd.source_revision` hashes the
application's own source files, not the user's document. There is no document hash in
telemetry at all.

---

## Turning it off

Unset the variable and restart:

```powershell
Remove-Item Env:\SENTRY_DSN -ErrorAction SilentlyContinue
```

With no DSN, `initialize()` returns `False` immediately, no SDK is imported, and every
span and `capture_error` call becomes a no-op. For the browser, build without
`VITE_SENTRY_DSN`; the SDK chunk is then never requested. Telemetry being off is the
default and needs no action — the browser run in `TEST_RESULTS.md` section 17 was
made that way.
