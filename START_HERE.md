# Start here

Five minutes, Windows, PowerShell. This page gets the application running on your own
machine and points you at everything else.

If you only want to *try* it, you need steps 1–4. The model in step 5 is optional:
without it you can still inspect documents and run the deterministic repairs, and the
interface says plainly which parts are unavailable rather than pretending.

---

## What this is, in one paragraph

You point at a paragraph in a Word document, say what looks wrong in your own words,
and a local model proposes **one bounded repair**. A deterministic engine — not the
model — decides whether that repair is allowed, builds a **separate candidate file**,
renders both versions, and checks that nothing else changed. You compare the two and
accept or reject. Your original is never written to, and nothing is applied without
you clicking accept.

What it actually repairs is listed in **[CAPABILITIES.md](CAPABILITIES.md)**. Read it
before deciding whether this is useful to you — it is deliberately blunt, and it is
shorter than the list of things Word can do wrong.

---

## 1. Check what you have

```powershell
cd C:\Users\Aarushi\Downloads\What-the-Doc-Starter\what-the-doc
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -c "import render; print('renderer:', render.find_soffice())"
node --version
```

You should see a Python 3.11+ version, a path ending in `soffice.exe`, and a Node
version. If `renderer:` prints `None`, see *Troubleshooting* in
**[TEST_IT_YOURSELF.md](TEST_IT_YOURSELF.md)** — the paragraph repairs still work, but
break and section repairs are refused without a renderer, on purpose.

If `.venv` does not exist yet:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 2. Start the backend

```powershell
cd C:\Users\Aarushi\Downloads\What-the-Doc-Starter\what-the-doc
.\.venv\Scripts\python.exe api.py --cors http://localhost:5173
```

It binds to `127.0.0.1:8000` and prints `Uvicorn running on http://127.0.0.1:8000`.
It has **no authentication**. Leave it on localhost. See
**[DEPLOYMENT.md](DEPLOYMENT.md)** for why that matters.

Check it in a second window:

```powershell
curl.exe http://127.0.0.1:8000/api/health
```

## 3. Start the frontend

```powershell
cd C:\Users\Aarushi\Downloads\What-the-Doc-Starter\what-the-doc\frontend
npm ci
npm run dev
```

Open <http://localhost:5173>. The dev server proxies `/api` to `127.0.0.1:8000`, so
the browser makes same-origin requests.

## 4. Try it on a prepared document

Open any file from **[test-documents/](test-documents/)**. Each one has a known fault,
a suggested complaint and a documented expected answer — including the ones that are
*supposed* to be refused. Start with `03-manual-page-break.docx` or
`05-section-next-page.docx`.

Step-by-step instructions, including what to check in Word afterwards, are in
**[TEST_IT_YOURSELF.md](TEST_IT_YOURSELF.md)**.

## 5. Optional: the local model

Diagnosis uses a local Gemma through Ollama. Without it, the service returns
`503 model_unavailable` for a live diagnosis and says so; it never substitutes a
canned answer.

```powershell
ollama list                      # what you already have
$env:GEMMA_MODEL = "gemma4:e2b"  # set BEFORE starting api.py
```

`gemma4:e2b` is the model this project's recorded results were measured against. A
larger model being loaded in Ollama does not make it the baseline — set the variable
deliberately. **Inference on CPU takes minutes, not seconds.** A blank screen for two
minutes is normal; see the troubleshooting section before concluding it hung.

## 6. Stopping

`Ctrl+C` in each window. If something is still holding a port:

```powershell
Get-NetTCPConnection -LocalPort 8000,5173 -State Listen -ErrorAction SilentlyContinue |
  Select-Object LocalPort, OwningProcess
# then, for a PID you recognise as yours:
Stop-Process -Id <PID>
```

Do not kill a PID you have not identified — these are the ports a development machine
reuses constantly.

---

## Where everything is

| File | What it is for |
| --- | --- |
| [CAPABILITIES.md](CAPABILITIES.md) | What is actually repaired, and what is only *seen*. Read first. |
| [TEST_IT_YOURSELF.md](TEST_IT_YOURSELF.md) | Hands-on walkthrough, per fixture, with troubleshooting. |
| [test-documents/](test-documents/) | Nine real .docx files and a manifest with real ids. |
| [API.md](API.md) | The HTTP contract. |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Local, public static demo, and what a hosted backend would still need. |
| [SENTRY_SETUP.md](SENTRY_SETUP.md) | Telemetry: the exact variables, and what is never sent. |
| [OBSERVABILITY.md](OBSERVABILITY.md) | What is traced and the local-versus-hosted boundary. |
| [TEST_RESULTS.md](TEST_RESULTS.md) | Measured results, with what was and was not run. |
| [TINKER_NEXT.md](TINKER_NEXT.md) | An unfinished training experiment, recorded as unfinished. |
| [SUBMISSION_CHECKLIST.md](SUBMISSION_CHECKLIST.md) | What is claimable and what is not. |

---

## Three things worth knowing before you start

**A preview is not an approval.** Every run stops at `awaiting_review`. The download
endpoint returns `409` until you accept.

**A break is not a fault.** Writers insert page breaks on purpose far more often than
by accident. The service identifies breaks and section starts; identifying one is
never a recommendation to remove it. `07-section-intentional.docx` exists to let you
check that it leaves a deliberate break alone.

**Continuous does not promise a page disappears.** Changing a section start to
continuous asks the section to continue on the same page. Page geometry, column
settings and keep-with-next rules can still force a new page. The service reports the
before and after page counts and does not claim a reduction it did not produce.
