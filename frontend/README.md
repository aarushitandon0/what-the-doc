# What the Doc?! — The document repair workshop

Complete React frontend and design kit based on the selected orange/sloth workshop concept.

## Start in VS Code

Install a supported Node.js LTS release (Node 22 recommended). Extract this folder, open it in VS Code, and run:

```sh
npm ci
npm run dev
```

Open the localhost address printed by Vite (normally http://127.0.0.1:5173). Do not double-click index.html: this is a React application built with Vite.

```sh
npm test       # interaction and boundary checks in jsdom
npm run build # production files in dist/
npm run preview
```

Dependencies are locked in package-lock.json. No account, API key, paid service, or database is required for the frontend demo. Installation needs internet; the running app makes no external font, analytics or CDN request (the Rubik display font is bundled as woff2 and served from this origin). Its only network calls go to the local API. node_modules is intentionally excluded from the ZIP. A prebuilt dist/ is included.

## Connecting it to the Python backend

The frontend now talks to `api.py` (the contract is `../API.md`). It decides what to show
from `GET /api/health` alone, and it never substitutes a canned answer for a model:

| health result | what the UI does |
| --- | --- |
| reachable, `live_diagnosis_available: true` | **connected** — real uploads, paragraphs, jobs, previews, accept/reject, repaired download |
| reachable, `live_diagnosis_available: false` | **connected, no model** — you can upload and inspect resolved properties; Diagnose is disabled and the runtime's own reason is shown |
| unreachable | **offline demo** — the original bundled sample walkthrough, with a banner saying the API is not responding |

Run both halves:

```sh
# terminal 1, repository root, venv active
$env:GEMMA_MODEL = "gemma4:e2b"     # an installed Ollama tag
python api.py                        # 127.0.0.1:8000

# terminal 2
npm run dev                          # 127.0.0.1:5173
```

`vite.config.js` proxies `/api` to `http://127.0.0.1:8000`, so the browser makes
same-origin requests and `api.py` needs no `--cors` flag during development. Point it
elsewhere with `VITE_API_TARGET=http://127.0.0.1:9000 npm run dev`, or, for a build that
calls an absolute origin instead of a proxy, `VITE_API_BASE=http://127.0.0.1:8000 npm run build`
(that one does need `python api.py --cors <your origin>`).

### What the connected flow does

1. `POST /api/documents` with the chosen `.docx`, or `POST /api/demo/document` for the
   synthetic fixture — which stays labelled **DEMO DOCUMENT** everywhere it appears.
2. `GET /api/documents/{id}/paragraphs` fills the outline and the evidence panel. A
   property whose origin is `application_default` is rendered **"not set (Word default)"**,
   never `0`.
3. `POST /api/jobs` with the selected `paragraph_id`, the complaint, and
   `expected_source_sha256`, then polling every 2.5 s. CPU inference takes 70–180 s, so
   the panel shows elapsed time, not a fabricated progress bar. "Stop watching" stops the
   polling only — the API has no cancellation.
4. Each terminal state is handled on its own terms: `needs_clarification` shows Gemma's
   question, `no_supported_repair` shows the real `diagnosis.status`, `failed` shows
   `error_code` and `error_detail`.
5. `awaiting_review` renders the LibreOffice pages from `preview.before_urls` /
   `after_urls` with the per-page changed-pixel ratios, the proposed operations and the
   preservation invariants. Not rendered is reported as *not rendered*, never as "no
   visible change".
6. Accept posts `candidate_sha256` from `candidate.accept_requires_candidate_sha256`;
   reject posts a reason. Only after acceptance does the download link to
   `GET /api/jobs/{id}/download` appear.
7. `#history` lists `GET /api/jobs`, the server's in-memory job table, which empties when
   `api.py` restarts.

A job reaching `awaiting_review` is not an approval, and the UI never treats it as one.
Fixture-mode jobs (`is_live_model_answer: false`) are badged as injected by a developer.

`src/api.js` is the only module that talks to the network; it branches on `error_code`,
never on the prose, and `ERROR_TEXT` holds the wording for each code in API.md.

## Try the offline demo


1. Home → Try a messy example.
2. Keep Methodology selected; click Preview a repair.
3. Wait for the explicitly simulated analysis, then Review the change.
4. Compare Original / Proposed / Side by side.
5. Accept sample repair → Results → Download demo report.
6. Visit My documents to reopen the accepted sample or clear history.

Try Wrong alignment to see clarification; cancel analysis to stop it; keep the original to reject a proposal. Upload handles selection, drag/drop, type/size feedback and removal. Actual selected files remain in memory and cannot be analysed until the backend is connected.

## Included screens

| Route | Screen |
| --- | --- |
| #home | Orange workshop landing page, mascot, three steps, example problems |
| #upload | Dropzone, local file selection, validation, sample entry |
| #workspace | Document outline, paragraph selection, zoom, complaint, loading, proposal, rejection |
| #review | Original/proposed comparison and explicit approval |
| #results | Accepted sample receipt and JSON report download |
| #history | Local sample history, reopen and clear |
| #clarify | Unsupported demo request / clarification |
| #settings | Motion preferences, connection explanation, clear history |
| #guide | Workflow explanation |
| #privacy | Actual demo data handling |
| #design | Palette, typography, reusable controls and status language |
| unknown | Recovery/empty state |

## Stack and files

React 19 + Vite + JavaScript/JSX + plain CSS + Lucide icons. Vitest, React Testing Library and jsdom cover interaction checks. No Tailwind dependency; all visual tokens and breakpoints are editable in src/styles.css.

- src/App.jsx: routes, state, screen composition, demo workflow.
- src/components.jsx: header, footer, buttons, badges, document preview, steps.
- src/data.js: illustrative sample and file/history validation.
- src/api.js: unused integration scaffold; proposed routes, not a claim about your existing API.
- public/assets/workshop.png: generated transparent sloth/workshop artwork.
- docs/DESIGN_SYSTEM.md: design decisions and visual tokens.
- docs/API_CONTRACT.md: suggested integration contract and state mapping.
- docs/CODEX_HANDOFF.md: next prompt for local frontend integration.
- docs/VERIFICATION.md: checks and limits.

## Important boundaries

This is a functioning frontend demo, not a connected DOCX processor. It does not call Gemma, parse OOXML, run LibreOffice, validate preservation, or generate a repaired Word file. The download is honestly labelled JSON. The sample preview is HTML, not a Word/LibreOffice render. Checks and peer values describe the fixed illustrative sample.

Approval is explicit. Direct navigation to results does not create approval. In production, the server must enforce the same rule; client state is never a security boundary. Hash navigation makes the static frontend easy to serve without SPA rewrite rules.

## Integrate with your existing project

Copy this folder into your current repo as frontend/. Keep your newer Python files and 136-test suite intact. Follow docs/CODEX_HANDOFF.md. The Python API is authoritative; reconcile this scaffold to it before connecting. Do not replace your working engine with an earlier project snapshot.

MongoDB is unnecessary for the demo and not configured. Your current backend progress is not included in this ZIP. A later hosted service needs appropriate authentication, retention, job isolation and renderer containment; those are not implemented here.
