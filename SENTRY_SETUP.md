# Sentry setup

**Current status: not verified against a hosted Sentry project.** No DSN is
configured on this machine, so nothing has ever been sent to sentry.io from this
code. The privacy boundary has been verified locally, against the envelope the
transport would have handed to the network — which is a real test of *what would be
sent*, and is not the same thing as a hosted trace appearing in a dashboard. Those
two claims are kept apart everywhere in this repository, and this page does not
blur them.

What is verified: `test_observability`, `test_observability_breaks`,
`test_observability_review` and `test_api_observability` capture the envelope at the
transport boundary and assert that the forbidden content is absent. See
**[OBSERVABILITY.md](OBSERVABILITY.md)** for what is traced and why.

---

## The two variables this code reads

These are the only two. Both are read **once**, by `observability.initialize()`, the
first time a trace is started in the process.

| Variable | Read at | Effect |
| --- | --- | --- |
| `SENTRY_DSN` | `observability.py` → `initialize()` | The project DSN. **Empty or absent means telemetry is off**, and the application runs normally with no client at all. |
| `WTD_SENTRY_TRACES_SAMPLE_RATE` | `observability.py` → `initialize()` | Float in `[0, 1]`, default `1.0`. A value outside that range disables telemetry rather than being clamped. |

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

cd C:\Users\Aarushi\Downloads\What-the-Doc-Starter\what-the-doc
.\.venv\Scripts\python.exe api.py --cors http://localhost:5173
```

An environment variable set in one window does not reach another, and a variable set
after the server started does not reach the server. If you are not sure:

```powershell
# in the window the server is about to run in
$env:SENTRY_DSN.Length    # non-zero means set; this does not print the secret
```

**Do not paste a DSN into a chat window, a commit, or a `VITE_*` variable.** A DSN is
a write credential for your Sentry project, and every `VITE_*` value is compiled into
the public JavaScript bundle. This project's frontend has no Sentry integration at
all, deliberately.

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
`repair.execute`, `job.accept`, `job.reject` and `job.download`, with spans named
`structure.inspect`, `model.inference`, `candidate.create`, `render.original`,
`render.candidate` and `preservation.check`.

If nothing arrives: the DSN was wrong, the process was started before the variable was
set, the sample rate was outside `[0, 1]`, or egress is blocked. The client is built
to fail silently rather than take the application down with it, so **absence of
traces is not an application error** and nothing in the logs will say so.

---

## What is never sent

This is enforced in three places — an allowlist of tag keys, a transaction sanitiser,
and a transport that rebuilds the envelope from scratch — not by remembering to be
careful at each call site.

Never sent, under any configuration:

- document text, or any fragment of it
- the user's complaint
- the prompt, the model's explanation, its question, or any model output
- file names, document titles, or any local path
- rendered pages, screenshots or attachments
- secrets, headers, cookies, query strings, request bodies
- user identity, IP address, server name, local variables, stack frames

The transport rebuilds each envelope keeping only `event_id` and `sent_at` from the
headers, copies across only transaction items, and re-sanitises every one. An item
type it does not recognise — including an attachment — is dropped rather than
forwarded.

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
span becomes a no-op. Telemetry being off is the default and needs no action.
