# Deployment

Three things are described here and they are **not** three stages of the same thing.
Only A runs today. B is prepared and can be deployed. C is not built, and this page
says what it would actually take rather than implying it is nearly done.

| | What it is | Status |
| --- | --- | --- |
| **A** | Local live application — Python API + Ollama + LibreOffice + React | **Working.** This is the real product. |
| **B** | Public static demo — the React frontend only, in a labelled sample mode | **Prepared, not deployed.** Config in this repo; nothing has been published. |
| **C** | Hosted live backend | **Not built.** See the honest list below. |

The one-line version: **B is a picture of the product, not the product.** It cannot
repair a document, because the things that repair a document — Python, a model and a
renderer — are not in a static site.

---

# A. Local live application

## What has to be running

| Component | How | Port | Required for |
| --- | --- | --- | --- |
| Python API (`api.py`, FastAPI + Uvicorn) | `.\.venv\Scripts\python.exe api.py` | `127.0.0.1:8000` | everything |
| Ollama | `ollama serve` | `127.0.0.1:11434` | live diagnosis only |
| LibreOffice | discovered, not started | — | previews; **required** for break and section repairs |
| Vite dev server | `npm run dev` | `127.0.0.1:5173` | the interface |

### Confirmed configuration on this machine

- **Model:** `gemma4:e2b`. Set `GEMMA_MODEL` deliberately before starting `api.py`.
  `gemma4:e4b` is also installed here; a larger model being present in Ollama does
  **not** make it the configured baseline, and the recorded evaluation results in
  `TEST_RESULTS.md` belong to `e2b` on the prompt scope they were measured against.
- **Renderer:** `.tools\lo\program\soffice.exe`, found by `render.find_soffice()`.
  Override with `SOFFICE_PATH`.
- **Ports:** API `8000`, frontend `5173`. Both configurable; see below.

### Start

```powershell
# backend
cd C:\Users\Aarushi\Downloads\What-the-Doc-Starter\what-the-doc
$env:GEMMA_MODEL = "gemma4:e2b"
.\.venv\Scripts\python.exe api.py --cors http://localhost:5173

# frontend, separate window
cd C:\Users\Aarushi\Downloads\What-the-Doc-Starter\what-the-doc\frontend
npm ci
npm run dev
```

`api.py` flags, as they actually are:

| Flag | Default | Notes |
| --- | --- | --- |
| `--host` | `127.0.0.1` | Anything else logs a warning. There is no authentication. |
| `--port` | `8000` | |
| `--root` | `work/api` | Storage for uploaded documents and job artifacts. |
| `--allow-fixture-mode` | off | Permits injected diagnoses. Every such response is labelled `mode: fixture`. **Development only.** |
| `--cors` | none | Repeatable allowed origin, e.g. `http://localhost:5173`. |

### The proxy, and why it exists

`frontend/vite.config.js` proxies `/api` to `http://127.0.0.1:8000`, so the browser
makes **same-origin** requests and no CORS allowance is strictly needed in dev.
Override the target when the backend is elsewhere:

```powershell
$env:VITE_API_TARGET = "http://127.0.0.1:8010"
npm run dev
```

**This proxy is a development-server feature. It does not exist in the production
build.** `npm run build` emits static files; nothing in `dist/` can proxy anything.
Any deployment that serves `dist/` must either put the API behind the same origin
with its own reverse proxy, or the frontend has no backend at all — which is exactly
case B.

### Keep it on localhost

The API has **no authentication, no rate limiting, no per-user ownership of
documents, and no renderer sandbox.** Anyone who can reach the port can upload a
document, read every stored document, and make the machine run LibreOffice. Binding
it to `0.0.0.0` on a shared network exposes all of that. The default is `127.0.0.1`
for that reason and should stay there.

### Shutdown

`Ctrl+C` in each window. `api.py` flushes telemetry on the way out. To find a
straggler:

```powershell
Get-NetTCPConnection -LocalPort 8000,5173,11434 -State Listen -ErrorAction SilentlyContinue |
  Select-Object LocalPort, OwningProcess
Get-Process -Id <PID> | Select-Object Id, ProcessName, Path
Stop-Process -Id <PID>
```

Look at the process before stopping it. These ports get reused, and older listeners
from earlier sessions may still be around. Never hard-code a PID into a script or a
document — it is meaningless by the time anyone reads it.

Ollama is a background service and usually keeps running; stop it from the tray icon
or `Get-Process ollama | Stop-Process` if you want the memory back.

---

# B. Public static demo on Render

## What this is and is not

**Is:** the React interface, built to static files, in an explicitly labelled sample
mode, so someone can see the flow and the design without installing anything.

**Is not:** a working repair service. There is no Python, no Gemma and no LibreOffice
behind it. It cannot open a real document, cannot render a page and cannot produce a
.docx.

### Three rules this deployment must keep

1. **Demo mode is deliberate and obvious.** The interface carries a `DEMO DOCUMENT`
   badge and a banner. A visitor must never be able to mistake a sample for an
   analysis of their own file.
2. **A failed live request never becomes a sample success.** The frontend already
   separates these: when the backend is unreachable it shows a connection banner and
   *asks the visitor to choose* between retrying and opening the sample. It does not
   silently fall back. Keep it that way.
3. **The hosted site must not call a visitor's localhost.** A page served from
   `onrender.com` asking the browser for `http://127.0.0.1:8000` is pointing at the
   *visitor's* machine, not yours. It will fail for everyone and, for the handful of
   people who happen to run this project, it would reach into their machine. Set no
   API base at all in the hosted build; the connection check simply fails and the
   banner appears, which is the honest state.

### No secrets in `VITE_*`

Every `VITE_*` variable is **substituted into the public JavaScript bundle at build
time**. Anyone can read it with View Source. No API key, no token, and never the
backend's Sentry DSN. The one exception is `VITE_SENTRY_DSN`, which is optional and
must be a *separate browser project's* DSN: a browser DSN only permits submitting
events and is public by design, so restrict it with that project's allowed domains
and rate limits. Leave it unset and the hosted page loads no Sentry code at all. See
[SENTRY_SETUP.md](SENTRY_SETUP.md).

### Verified facts about this repository's layout

```
what-the-doc/
  frontend/
    package.json       scripts: dev, build, preview, test
    vite.config.js     base: './'   (no build.outDir override)
    dist/              build output  <- publish directory
```

- Root directory: **`frontend`** (the repository root is the Python service).
- Build command: **`npm ci && npm run build`**.
- Publish directory: **`dist`** — Vite's default, and `vite.config.js` does not
  change `build.outDir`.
- `base: './'` means relative asset URLs, so the build works at a subpath as well as
  at a domain root.

Verified locally this session: `npm run build` succeeds and writes
`frontend/dist/index.html` plus hashed assets.

### `render.yaml`

A Blueprint is committed at the repository root as **[`render.yaml`](render.yaml)**.
It declares a single static site, the `frontend` root directory, the build command
and publish path above, and an SPA rewrite so a refresh on a client-side route serves
`index.html` instead of a 404. It declares **no environment variables**, because the
demo needs none and none may carry a secret.

### Deploying it

Nothing has been published, and this repository has **not** been pushed anywhere. The
steps, for when that is wanted:

1. Check the licence situation first. There is no `LICENSE` file in this repository
   today. Publishing source with no licence statement leaves its terms undefined —
   add one, or decide deliberately not to.
2. Check what would go public: `.gitignore` already excludes `work/`, `.venv/`,
   `.tools/` and model files. Confirm before pushing:
   ```powershell
   git status --short
   git ls-files | Select-String -Pattern '\.docx$|\.pdf$|\.msi$|\.gguf$'
   ```
   The only `.docx` files that should appear are the synthetic ones in
   `test-documents\`. No private document, no model weight, no `.env` and no DSN.
3. Push to a Git host, then in Render: **New → Blueprint**, point at the repository,
   and it reads `render.yaml`. Or **New → Static Site** and type the three values by
   hand.
4. Open the URL and confirm the demo banner appears and no request goes to
   `127.0.0.1` (check the browser network panel).

**Not done in this session, deliberately:** no hosting was purchased, no repository
was initialised or published, and the unauthenticated document-processing API was not
exposed.

---

# C. Full hosted live backend — not yet deployed

This section exists so nobody reads B as "nearly C". Making the real service public
is not a deployment task; it is a product that does not exist yet. What it needs:

**Compute for inference.** A 4.6 B model on CPU takes **minutes** per diagnosis here.
That is tolerable for one person on their own machine and unusable for a shared
service. Hosted inference means either a GPU instance or a paid model API, and the
second changes the privacy story completely: documents would leave the machine. A
free static site provides no inference of any kind.

**Persistent artifact storage.** Documents, candidates and rendered PDFs currently
live on local disk under `work/api`, in memory-backed job records that vanish on
restart. A hosted service needs durable object storage, a real job store, and a
retention and deletion policy it can actually honour.

**Job execution.** Diagnosis and rendering are minutes-long. They cannot run inside a
request. That means a queue, workers, progress reporting that survives a dropped
connection, and a story for what happens to a job when a worker dies mid-render.

**Authentication and ownership.** There is none. Today any caller can read any
`document_id` or `job_id` it can name. A hosted service needs accounts, per-user
ownership on every object, and authorisation checks on every endpoint — not an
API key in front of the whole thing.

**Resource limits.** Upload size is capped, but there is no rate limiting, no
concurrency cap, no per-user quota and no cost ceiling. Rendering is the expensive
operation and it is reachable by anyone who can upload.

**Renderer isolation.** This is the one to be most careful about. The service shells
out to LibreOffice to convert untrusted documents. Today's isolation is **a unique
profile directory and a timeout** — that is all, and `render.py` says so in its own
comments. A document format parser processing hostile input is a serious attack
surface.

> **A container is not a sandbox.** Running LibreOffice in Docker does not by itself
> contain it: by default a container shares the kernel, and an escape or a
> resource-exhaustion attack is not addressed by packaging. Real isolation means a
> dedicated, network-denied, read-only, non-root execution environment with strict
> CPU, memory, process and wall-clock limits, ideally with a stronger boundary than a
> shared kernel. Claiming "it runs in Docker, so it is sandboxed" would be false.

None of the above is started. The honest summary is: **the local application works
and is the thing to demonstrate; the static demo can be published; the hosted service
is a plan, not a deliverable.**
