# Verification — 21 October 2026

## Completed

- `npm install` completed; `package-lock.json` records the resolved dependency graph.
- `npm run build`: production build passed (Vite 6.4.4, 1577 modules transformed).
- `npm test`: **24 tests passed, zero skipped** (Vitest 3.2.7, jsdom).
- Source and production assets are included. `node_modules` is excluded.

Interaction checks exercise proposal/approval boundaries, acceptance and history,
rejection, cancellation on navigation and by button, unsupported request clarification,
comparison tabs, file selection without fetch, invalid file feedback logic, all page
routes, corrupt local storage and preservation of sample text across comparison. One
test renders the app inside `React.StrictMode`, as `main.jsx` does, and holds the
skip link's first tab stop against the route-focus effect described below.

`api.test.jsx` additionally drives the connected workspace against a `fetch` router
whose responses are shaped like `API.md`: upload, paragraph listing, job creation,
polling, preview page urls, acceptance carrying the candidate hash, and the download
link. It asserts that an unset property renders as "not set (Word default)" rather than
0, that the demo document is badged, that `error_code` is surfaced instead of prose, and
that no download link exists before a human accepts.

### The offline path (fixed 20 October 2026)

The route dispatch in `App.jsx` fell through to the bundled demo whenever health
reported `offline`. A backend that dropped out **while a real document was open** had
the user's document silently replaced by the sample — hardcoded "Research-report.docx",
a fabricated 48pt to 12pt proposal, its own simulated timer — making the two
indistinguishable on screen.

A regression test was written first and made to fail for the right reason, driving the
real user path: open a document against a live backend, kill the backend, re-check the
connection in Settings. The fix adds an `OfflineHold` panel and one dispatch branch:
when live work exists, an unreachable backend holds it and says so, and the sample
becomes an explicit choice that states it discards the open document.

The behaviour when **no** document was ever opened is unchanged and still tested: at
`#workspace` with no backend, the sample is offered, because it replaces nothing.

## Verified in a real browser — 21 October 2026

Headless **Chromium 153.0.8010.12**, driving the production bundle through the Vite dev
server against real FastAPI processes. This supersedes the earlier statement that no
browser automation surface was available: `playwright-core` is vendored under
`work/browser-sentry-20261020/tooling/` and the Chromium build was already present.
Full composition, hashes and artifact paths are in `../../TEST_RESULTS.md` section 12.

Live and injected evidence ran against **two separate servers**, so they can never be
confused: a live `api.py` with `GEMMA_MODEL=gemma4:e2b` and **fixture mode disabled**,
and an isolated QA service started with `--allow-fixture-mode` whose
`gemma_adapter.diagnose` is replaced by a function that raises if anything calls it.

**The live flow, one real Gemma call.** A synthetic DOCX uploaded through the real file
input; no request intercepted or rerouted. The paragraph list came from the server and
the selected id was the server's, not a list index; the inspector showed `48 pt` from
`direct formatting`. An elapsed timer counted up with no progress bar, because the
runtime reports no progress. All **8 of 8** preview pages actually loaded at 1191×1684
from the server. `accepted` was false on arrival at review; acceptance happened only on
an explicit press and carried the candidate hash the reviewer was shown. The downloaded
`.docx` was **byte-identical** to the accepted server artifact, and the stored original
and the local fixture were both unchanged afterwards. Zero uncaught page errors.

**Injected scenarios, same browser, all passing.** Rejection offers no download and a
direct `GET /download` on the rejected job returns 409. A dropped backend **holds the
open document** behind an offline panel instead of substituting the sample, and
returns to the same job and document on retry; entering demo mode with a document open
requires an explicit press that states it discards the document. An unconfigured
runtime disables diagnosis and shows the real reason. `invalid_response` surfaces as a
failed run with no candidate. With **no renderer**, the review screen states "Visual
comparison is unavailable.", `visual_comparison` is `null`, and acceptance is possible
only through a labelled "Accept after structural review" — never presented as a visual
check. A renderer that is **present and fails** is a failed job with no candidate, no
partial previews and no accept or download control. Both the OS reduced-motion setting
and the in-app toggle stop the hero animation.

**Layout.** 1440, 1024, 768 and 390px, 24 observations across both runs: **no
horizontal page scrolling at any width** and no page errors. One honest detail: on the
home route at 1440 and 1024px a single hero `<img>` extends past the viewport edge and
is clipped by its container, which causes no scrolling but is recorded rather than
dropped.

**A defect this found and fixed.** The "Skip to content" link was unreachable by
keyboard: the route effect focused `#main` on the first render too, consuming the
link's tab stop. The first fix passed in jsdom and still failed in Chromium, because
`main.jsx` mounts inside `React.StrictMode`, which runs mount effects twice and spent
the guard on the first pass. The guard now compares the route actually shown, and the
regression test renders inside `StrictMode` as `main.jsx` does. Focus still moves to
`main` on a route *change*; only the first paint changed.

## Not verified

- **Chromium only.** Firefox and WebKit were not driven.
- **No real screen reader.** The focus, `aria-pressed` and reduced-motion checks are
  assertions and computed styles in one engine. That is not an accessibility audit,
  and the before/after review screen in particular carries meaning in images.
- **No Word round-trip in this batch.** The downloaded file was verified by bytes and
  hash against the accepted server artifact, not opened in Microsoft Word.
- **No real-world document.** Every run used a synthetic fixture generated by this
  repository, so numbered lists, tables and theme effects remain unexercised.
- **Not a model measurement.** One live call on one document says nothing about
  accuracy; the revision-4 ten-case result remains 8/10 and was not rerun or
  relabelled.
- **Hosted Sentry tracing is unverified.** Local envelope tests only; see
  `../../OBSERVABILITY.md`.

The jsdom suite is now **24 tests**, and a jsdom test is still not a browser check.
Both kinds of evidence are kept separate in `../../TEST_RESULTS.md`.

## Manual browser checklist

Run the backend and the dev server, then work through this in a real browser. The
Chromium run above now covers the connected workflow, review and approval, the
offline and degraded states, the renderer cases, reduced motion and keyboard focus.
What is left here is what automation cannot stand in for: a second browser engine, a
real screen reader, and opening the downloaded file in Microsoft Word.

```powershell
# Terminal 1, project root; use an unused local port for a fresh process.
$env:GEMMA_MODEL = "gemma4:e2b"
.\.venv\Scripts\python.exe api.py --host 127.0.0.1 --port 8012 --root work/api-browser-8012 --cors http://127.0.0.1:5173

# Terminal 2, project root
Set-Location frontend
$env:VITE_API_TARGET = "http://127.0.0.1:8012"
npm run dev -- --port 5173 --strictPort
```

**Check the port is free first.** At the end of the 21 October batch, 8000, 8010,
8012 and 8014 were all occupied by earlier sessions' servers, and 5173, 5175 and 5177
by dev servers, so the commands above need a port of their own:
`netstat -ano | Select-String "127.0.0.1:8012.*LISTENING"` prints nothing when 8012 is
free. The Vite proxy keeps its default `http://127.0.0.1:8000` when `VITE_API_TARGET`
is unset, and that default server has no `GEMMA_MODEL`, so diagnosis is disabled
there — a frontend that seems unable to diagnose is usually pointed at it. No
historical PID in this document is a shutdown instruction.
Use Ctrl+C in the terminals you started to stop these services.

**Layout.** Inspect 1440px, 1024px, 768px and 390px widths. Check the hero, the
upload/drop feedback, the workspace outline and paper, the stacked inspector, the
comparison pages and the sticky review controls. Confirm no horizontal page scroll at
390px.

**The connected workflow, against a live model.** Upload a real `.docx`. Confirm the
paragraph list comes from the server and that paragraph ids in the UI are the server's,
not list indices. Select a paragraph, describe the problem, start a diagnosis. While it
runs, confirm the elapsed timer counts up and that **no progress percentage or bar is
shown** — the runtime reports no progress. Expect 70-180s on CPU, and longer on a first
load.

**Review and approval.** Confirm the before/after images are the server's rendered
pages and that the page counts match what the job reported. Confirm `accepted` stays
false until you press accept, that the download link appears only afterwards, and that
the saved file opens in Word with the repair present. Confirm your original is
untouched. Then run a second job and reject it: no download should be offered.
When no renderer is available, confirm the prominent "Not rendered" message and
`visual_comparison: null`; do not interpret absent images as an unchanged document.
The API currently permits explicit acceptance after structural checks without
previews. An available renderer that fails instead produces `render_failure` and
must offer no accept or download action.

**Formatting honesty.** Confirm an unset property reads "not set (Word default)" and
never 0. Confirm formatting origins and any resolution caveats are shown. Confirm the
demo document carries its fixture badge and a real upload does not.

**Offline and degraded states.** With a document open, stop `api.py`, then re-check the
connection in Settings: your document must be held with an offline notice, **not**
replaced by the sample, and "Try the demo instead" must say it discards the document.
Restart the backend with `GEMMA_MODEL` unset: inspection should work while diagnosis is
disabled and shows the real reason (`GEMMA_MODEL is not set`). Stop the backend
mid-diagnosis and confirm the failure is reported rather than silently resolved.

**Accessibility and motion.** Tab through every control and confirm focus is always
visible. Enable the system reduced-motion setting and the Settings toggle. Try a screen
reader on the review screen, where the before/after images carry the meaning.

**Persistence.** Refresh the page and confirm it does not invent an active approval. An
existing sample history entry can be explicitly reopened.
