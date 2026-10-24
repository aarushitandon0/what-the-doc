# Submission checklist

What may be claimed, what may not, and where the evidence for each claim is. The
second column is the one that matters: a claim without a line in
`submission/EVIDENCE_TABLE.md` is not a claim, it is a hope.

---

## Claimable

| Claim | Evidence |
| --- | --- |
| Three repair families, each bounded and separately approved: six paragraph properties, one explicit page/column break, one `nextPage` → `continuous` section start | `CAPABILITIES.md`; `engine.py`; the test modules below |
| Nothing is applied without an explicit human acceptance | Every run ends at `awaiting_review`; download returns `409` until accepted — `test_api_sections.test_download_before_approval_is_refused` |
| The original file is never written to | `original_unmodified` invariant, checked against the sha256 on disk, in every run |
| A break removal was demonstrated on a real LibreOffice render | 6 pages → 5, 5 breaks → 4, text identical, 12/12 invariants — `work/session1-demo/summary.json` |
| A column-break removal reflowed without losing a page, and the service said so | 2 pages → 2, delta 0 — same file |
| The new section change was demonstrated on a real render | `work/session2/section-summary.json` |
| Unsupported section cases are refused with the specific rule they hit | `test_sections.EligibilityTests`; `test_api_sections.SectionSelectionTests` |
| Exactness is verified against the written bytes, not asserted | `engine._verify_section_changes` reverts the approved attribute on the output and demands whole-document equality |
| A model cannot name a target it was not shown | `test_api_sections.test_an_injected_diagnosis_naming_an_unsupplied_section_is_refused` |
| Telemetry carries no document text, complaint, prompt, model output, filename or path | `test_observability*`, asserted at the transport boundary |
| The frontend builds and its tests pass | `npm run build`, `npm test` — see `frontend/docs/VERIFICATION.md` |
| Microsoft Word opens every candidate and agrees with LibreOffice on all eight page counts, with word counts unchanged | `work/session2/word-check.json`; `submission/EVIDENCE_TABLE.md` section 5 |
| A real browser defect was found and fixed by the browser run | `frontend/docs/VERIFICATION.md`; the results view crashed on any non-paragraph operation |
| The live model is measured **repeatedly**, three runs per case, with the two case sets scored separately and every failure categorised | `TEST_RESULTS.md` section 18; `work/eval-final2/live-eval.json`; `submission/EVIDENCE_TABLE.md` section 4b |
| On the current code the model proposes the selected break removal 3/3 runs and the supported section change 3/3, and abstains on the deliberate section 3/3 | same |
| A safety gap was found by measurement and closed: the model removed one of two breaks and it reached review 3/3 runs; a removal now requires the person to choose the break | `test_gemma_adapter.AmbiguousBreakTests`; `test_api_breaks`; `EVIDENCE_TABLE.md` 4c |
| A proposal cannot change a property it did not cite, and writing `False` to an on/off property nothing sets is refused as ineffective | `test_diagnosis`; `test_gemma_adapter.EffectiveNoOpTests`; `work/replay_reached_review.py` |
| Six wrong proposals reached the review screen across 45 graded runs, none was applied, and the remaining stop is human review | `TEST_RESULTS.md` 18.7 — disclosed, not omitted |

## Not claimable

| Do not say | Why | Say instead |
| --- | --- | --- |
| "Fixes any Word document" | Three narrow families are supported. Most of Word is untouched. | "Repairs three specific, documented classes of formatting fault." |
| "Preserves everything" | Preservation is checked for text, runs, run properties, paragraph count, section count and boundaries, unrelated package parts and the original file. It is not a universal guarantee. | "Checks these named invariants against the bytes on disk, and refuses the candidate if any fails." |
| "Making a section continuous removes the page break" | Page geometry, columns and keep-with-next can still force pagination. | "Asks the section to continue on the same page, and reports the real before and after page counts." |
| "Zero cost" | Local inference costs electricity and hardware; a hosted version would cost real money. | "Runs locally with no per-request API fee." |
| "Sentry integrated and verified" | No real DSN is configured; nothing has been sent to sentry.io or seen in a dashboard. | "Backend and browser error events and backend traces are instrumented, with the privacy boundary verified at the transport and over the real HTTP transports to a local capture server. Hosted verification is pending a DSN." |
| "Gemma diagnoses the new section repair" (as a general capability) | Measured on synthetic fixtures only, three runs per case: `gemma4:e2b` proposed the selected break removal 3/3 and the supported section change 3/3, but passed only 7 of 30 ten-case runs, never once returned `needs_clarification`, and six wrong proposals reached review. One machine, one model build, and temperature 0 is not deterministic here. | "On these synthetic cases, run three times each, the live model proposed the supported break and section repairs 3 of 3 times, and those reached review and a verified download in a real browser. It is not a benchmark, and the same measurement records what it gets wrong — see `TEST_RESULTS.md` section 18." |
| "The model asks when a request is ambiguous" | It never did, in any graded run. The *system* refuses to act instead. | "When two breaks share a paragraph and the person has not chosen one, the system accepts no removal at all. The model still does not ask the question itself, which is recorded as an open failure." |
| "Fine-tuned with Tinker" | Nothing was trained. | "Recorded as an unfinished experiment with its design and spending gate written down." |
| "Deployed" | Nothing is published. | "The static demo is configured and ready to deploy; the local application is what runs." |
| "Sandboxed renderer" | A unique profile directory and a timeout. Docker would not change that. | "Isolated by a private profile and a timeout. Real isolation is listed as unbuilt work." |
| "Word-verified" (as a general claim) | Eight synthetic documents were opened in real Word and agreed. The in-product preview is still LibreOffice. | "Previews are LibreOffice. As a separate check, eight originals and candidates were opened in Word, which agreed on every page count." |

## Sponsor and tool integrations

Claim only what was actually performed:

- **Gemma / Ollama** — used, locally, and measured. The measurement includes a
  failure, which is reported as a failure.
- **LibreOffice** — used for every rendered comparison.
- **Sentry** — backend and browser are instrumented, and the privacy boundary is
  tested locally, including the real HTTP transports against a local capture server.
  **No hosted project, no real DSN, nothing ever sent to sentry.io.** Do not list it
  as a verified integration.
- **Tinker** — not used. Do not list it.
- **Render** — configuration committed. **Nothing deployed.** Do not list it as a
  live deployment.

## Before publishing anything

- [x] Licence decided: **MIT**, `LICENSE` at the repository root (committed in
      `d4b906f`).
- [ ] `git status --short` and review every file that would be added.
- [ ] Confirm no private document, model weight, `.env`, DSN or API key is tracked:
      `git ls-files | Select-String '\.docx$|\.gguf$|\.msi$|\.env$'` — the only
      `.docx` hits should be the synthetic files in `test-documents/`.
- [ ] Confirm `work/`, `.venv/` and `.tools/` are ignored.
- [ ] Do not expose the unauthenticated API publicly. See `DEPLOYMENT.md` part C.
- [ ] No DSN or secret in any `VITE_*` variable — they are compiled into the public
      bundle.

## Current state of this repository

- Committed locally only, on `main`, and **not pushed anywhere**. No remote is
  configured for publication.
- No hosting purchased, no Render service created.
- The local API has never been bound to anything but `127.0.0.1`.
