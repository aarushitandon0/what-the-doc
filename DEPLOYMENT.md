# Deployment

Two ways to run the real application. Both are the same code; neither is a mock.

| | What it is | Status |
| --- | --- | --- |
| **A** | Local — Python API + Ollama + LibreOffice + React on your own machine | **Working.** |
| **B** | Hosted on Render — the same four pieces in one Docker container, in public mode | **Configured** in `Dockerfile` + `render.yaml`. |
| **C** | A multi-user production service | **Not built.** What it would still need is listed below. |

B is a real deployment of the real product, sized for a judged demo: a handful of
people at once, no accounts, documents deleted after an hour. It is not C.

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
cd <path-to-your-clone>\what-the-doc
$env:GEMMA_MODEL = "gemma4:e2b"
.\.venv\Scripts\python.exe api.py --cors http://localhost:5173

# frontend, separate window
cd <path-to-your-clone>\what-the-doc\frontend
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

# B. Hosted on Render — the full application

## What runs

One Docker container ([`Dockerfile`](Dockerfile), started by
[`deploy/start.sh`](deploy/start.sh)):

| Piece | How |
| --- | --- |
| Ollama `0.40.10`, CPU only | GPU libraries are stripped from the release at build time |
| `gemma4:e2b` | pulled at build time and baked into the image; re-pulled at start if ever missing |
| LibreOffice Writer (headless) | Debian `libreoffice-writer-nogui`, plus metric-compatible fonts (Carlito for Calibri, Caladea for Cambria, Liberation) |
| `api.py --public` | serves the API **and** the built interface on one origin, port `$PORT` |

Same origin means no CORS, no `VITE_API_BASE`, and nothing in the bundle that points
anywhere but the page's own host.

## Size and cost

The model needs ~5–6 GB of memory while loaded, so the plan is **`4c-8g`** (4 CPU,
8 GB; formerly "Pro Plus"). Smaller plans are killed when the model loads. It is a
paid plan charged while the service exists — check the exact rate on the Render
dashboard, and **suspend or delete the service after judging**.

Expect each diagnosis to take on the order of a minute or more on 4 shared CPUs; it
has not yet been timed on Render.

## Public mode (no accounts)

`WTD_PUBLIC=1` is set in the image. It changes exactly these things
([`api.py`](api.py), tested in [`test_api_public.py`](test_api_public.py)):

| Protection | Default | Override |
| --- | --- | --- |
| Job history shows only jobs made by **this browser** (a random key in `localStorage`, sent as `X-WTD-Visitor`) | on | — |
| Uploads per client address | 30 / hour | `WTD_UPLOADS_PER_HOUR` |
| Jobs per client address | 20 / hour | `WTD_JOBS_PER_HOUR` |
| Jobs waiting or running at once; beyond this a job is refused with `busy` | 3 | `WTD_MAX_PENDING` |
| Documents and finished jobs deleted after | 1 hour | `WTD_RETENTION_SECONDS` |
| Fixture (injected) diagnoses | always refused | — |

What it does **not** do: the visitor key is not a login and proves nothing. Every
document and job id is a 128-bit random value, so a file can only be opened by
someone who was given its id. LibreOffice still processes untrusted files; its only
boundary is the container Render provides (see C).

## Deploying

1. Push this repository to GitHub (already: `aarushitandon0/what-the-doc`).
2. Render → **New → Blueprint** → pick the repository. It reads `render.yaml`:
   one Docker web service, plan `4c-8g`, health check `/api/health`.
3. The first build downloads Ollama and the 4.6 GB model, so it takes a while. Watch
   the build log.
4. Once live, open `https://<service>.onrender.com/api/health` and check
   `live_diagnosis_available: true` and `renderer.available: true`. The model loads
   into memory in the background after start; the first diagnosis may be slower.
5. Open the site, run the sample document, and confirm a diagnosis completes.

`autoDeploy` is off, so a push does not rebuild a 5 GB image by surprise: use
**Manual Deploy** in the dashboard.

---

# C. A multi-user production service — not built

B is a demo-scale deployment. A service for real, unknown users at scale would still need:

**Compute for inference.** A 4.6 B model on CPU takes **minutes** per diagnosis here.
That is tolerable for one person on their own machine and unusable for a shared
service. B runs one job at a time on CPU and refuses work beyond a short queue; real
scale means a GPU instance or a paid model API, and the second changes the privacy
story completely: documents would leave the machine.

**Persistent artifact storage.** Documents, candidates and rendered PDFs currently
live on local disk under `work/api`, in memory-backed job records that vanish on
restart. A hosted service needs durable object storage, a real job store, and a
retention and deletion policy it can actually honour.

**Job execution.** Diagnosis and rendering are minutes-long. They cannot run inside a
request. That means a queue, workers, progress reporting that survives a dropped
connection, and a story for what happens to a job when a worker dies mid-render.

**Authentication and ownership.** There is none. Any caller can read any
`document_id` or `job_id` it can name; B's ids are unguessable and its job list is
scoped per browser, which is enough for a demo and is not ownership. A hosted service needs accounts, per-user
ownership on every object, and authorisation checks on every endpoint — not an
API key in front of the whole thing.

**Resource limits.** B has per-address rate limits and a queue cap, held in memory
and keyed on the address Render forwards. There is no per-user quota and no cost
ceiling beyond the plan's fixed price. Rendering is the expensive
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

Summary: **A and B both run the real application. B is sized and protected for a
judged demo, not for open production use.**
