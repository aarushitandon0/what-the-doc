# Evidence table

One row per claim, with the artifact that backs it and the date it was produced.
Anything without a row here is not a claim.

Four kinds of verification are kept apart throughout, because they prove different
things and are routinely conflated:

| Kind | What it proves |
| --- | --- |
| **Fixture** | The engine, given a known input, does the right thing. Says nothing about the model. |
| **HTTP** | The API enforces the rules at its own boundary, not just inside the library. |
| **Browser** | A person can actually do it in a real Chromium against the production bundle. |
| **Live model** | What a real model actually returned. The only evidence about model quality. |

All runs below are from **2026-10-21** on the project machine: Windows 11, LibreOffice
26.8.1 at `.tools\lo\program\soffice.exe`, Ollama with `gemma4:e2b` and `gemma4:e4b`.

---

## 1. The new operation: section start `nextPage` → `continuous`

| Claim | Kind | Evidence | Result |
| --- | --- | --- | --- |
| A supported section start is changed, on a real render | Fixture + render | `work/session2/section-summary.json` → `supported_next_page` | **2 pages → 1**, start types `[nextPage, nextPage]` → `[nextPage, continuous]`, section count 2 → 2, boundaries identical, text identical, **12/12 invariants passed, 0 failures** |
| source / candidate hashes | Fixture | same file | src `9100a278b158dcc1…`, candidate `8e329c3b0054ca2c…` |
| Target identity | Fixture | same file | `sec-c914e16cd11db71d`, section 1, defined in `body`, governs *the final section*, paragraphs 3–5 |
| An omitted start type is read as the default and the element is **created** | Fixture + render | `work/session2/section-summary.json` → `default_start` | 2 pages → 1, `start_type_is_default: true`, `created_type_element: true`, 12/12 invariants, src `f6fd8c1294424…` → candidate `eea90ba8b6d26…` |
| Every rendered page is compared, not only the first | Fixture + render | `pages.per_page` in both records | page 1 changed_ratio 0.1215 / 0.07345; page 2 absent after, because the document became one page |
| Exactness is checked against the written bytes | Fixture | `engine._verify_section_changes`; `test_sections.ExactnessTests` | Reverting the approved attribute on the output reproduces the input byte-for-byte. A second, unapproved section change is caught — `test_a_tampered_mutation_is_caught_by_the_verifier` |
| Rendering is required before review | Fixture + HTTP | `test_sections.RenderedSectionTests`, `test_api_sections.UnrenderedSectionTests` | `do_render=False` → `rendering_required: true`, `rendering_satisfied: false`, job `failed` with `render_failure`, download 409 |
| The whole flow over HTTP, with guarded download | HTTP | `test_api_sections.RenderedSectionReviewTests` (7 tests) | **OK.** `operation_kind: section_start_change`, `plan_schema_version: 3`, download before accept → 409, after accept → 200, downloaded bytes = reviewed candidate sha256 |
| A person can do it in a browser | Browser | `work/session2/browser-section-summary.json` | **7/7 steps passed, 0 page errors.** Download sha256 `c48b744c65b81812…` **= the reviewed candidate**; downloaded file's start types `[nextPage, continuous]` |

## 2. Refusals — the larger half of the evidence

| Refused case | Kind | Evidence | Rule named |
| --- | --- | --- | --- |
| First section of a document | Fixture + HTTP + Browser | `section-summary.json` → `refusals.single_section`; `test_sections`; visible in `browser-section-summary.json` | "there is no previous section here, so changing it would repair nothing" |
| `oddPage` start | Fixture + HTTP + Browser | `refusals.odd_page_start`; `test_api_sections` → 400 `unsupported_formatting`; `browser-section-refused-1440.png` | "a deliberate printing requirement … never changed without an explicit, separately supported intent" |
| Differing page geometry (landscape) | Fixture + HTTP | `refusals.differing_geometry` | names `w:pgSz` in `differing_page_properties` |
| Differing page numbering | Fixture | `refusals.differing_page_numbering` | names `w:pgNumType` |
| Differing header reference | Fixture | `test_sections.test_a_differing_header_reference_is_refused` | names `w:headerReference` |
| Tracked section revision | Fixture | `test_sections.test_a_tracked_section_revision_is_refused` | "carries a tracked revision (w:sectPrChange)" |
| Stale id from another revision | Fixture | `test_sections.TargetIdentityTests` | "Unknown or stale section target … re-inspect" |
| Id replayed after the change | Fixture | `test_an_id_minted_before_the_start_type_changed_does_not_resolve` | the start type is inside the digest |
| A section change combined with any other operation | Fixture + schema | `test_sections.PlanValidationTests`, `diagnosis._operations` | "applied on its own" |
| Two section changes in one plan | Fixture | `test_two_section_changes_in_one_plan_are_refused` | "one section-start change at a time" |
| A diagnosis naming a section it was not shown | HTTP | `test_an_injected_diagnosis_naming_an_unsupplied_section_is_refused` | 400, "not supplied" — a real, eligible section it was not offered is still a fabrication |
| A break and a section selected together | HTTP | `test_a_break_and_a_section_cannot_both_be_selected` | 400, "not both" |
| Eligible ≠ recommended | Fixture | `test-documents/07-section-intentional.docx`; `test_sections.EligibilityTests` | structurally identical to the supported fixture on purpose |

## 3. The existing break slice, re-verified this session

| Claim | Kind | Evidence | Result |
| --- | --- | --- | --- |
| Explicit page-break removal, on a real render | Fixture + render | `work/session1-demo/summary.json` → `manual_page_break` | **6 pages → 5**, 5 breaks → 4, target `brk-db99a64173c587c9` (page, ¶2, ordinal 1), text identical, **12/12 invariants, 0 failures** |
| Column-break removal reflows **without** losing a page | Fixture + render | same file → `manual_column_break` | **2 pages → 2, delta 0**, 2 breaks → 1, target `brk-61113bc53a4b1a1b`, text identical, 12/12 invariants |
| The HTTP break slice | HTTP | `work/session2/test_api_breaks.log` | **21/21 OK, rc=0, 912 s.** This closes the item the handoff left unconfirmed |
| A person can do it in a browser | Browser | `work/session1-demo/browser-break-summary.json` | **5/5 steps passed, 0 page errors.** Two page breaks in one paragraph rendered distinguishably; the **second** was chosen; download sha256 `a14006b18c9cbdbe…` **= the reviewed candidate**; the downloaded file holds 4 breaks, 2 removable |

## 4. Live model — measured, and it failed

This is the one section that is evidence about the model, and the result is negative.
It is reported as measured rather than omitted.

| Run | Model | Result |
| --- | --- | --- |
| `work/session1-demo/live-break-cases.json`, case `selected` | `gemma4:e2b` | **`invalid_response`** after 255.6 s: "missing required key(s): evidence, explanation, operations, source_sha256, status" |
| same file, case `ambiguous` | `gemma4:e2b` | **`invalid_response`** after 157.5 s, same reason |
| Raw capture of what the model actually returned | `gemma4:e2b` | `{"operation": "remove_break", "break_id": "brk-7925c71d4abd3fc5"}` — an invented schema. The real operation is `remove_explicit_break` with a `target_id`, inside a five-key envelope |
| **Controlled A/B against the previous system prompt** | `gemma4:e2b` | `{"operation": "remove_break"}` — the **same failure, with less information**. So this session's prompt addition did **not** cause it |

`gemma4:e2b` was chosen deliberately as the measured historical baseline.
`gemma4:e4b` is installed on this machine; a loaded model is not a baseline, and it
was not substituted.

**What this does and does not mean.** The deterministic slice is unaffected: the
validator rejected a malformed answer, which is the designed behaviour, and no
candidate was built from it. What it means is that **live Gemma diagnosis of the
structural operations is not working**, and the submission must not claim otherwise.
The remedy is recorded in `TINKER_NEXT.md` as an unfinished experiment.

The historical **8/10** model figure belongs to its original prompt and capability
scope (paragraph properties) and is not transferable to the break or section tasks.

## 5. Microsoft Word — an independent check that agreed

Word 16 was installed on this machine and **not running**, so `word_check.ps1` (which
refuses to start if Word is already open, opens read-only with macros disabled, and
always quits without saving) could be used without disturbing anyone's session. Every
original and every candidate was opened in Word itself and asked for its own page and
word counts.

| Document | Word pages | Word words | LibreOffice pages |
| --- | --- | --- | --- |
| section supported — original | 2 | 480 | 2 |
| section supported — candidate | **1** | 480 | **1** |
| section default-start — original | 2 | 361 | 2 |
| section default-start — candidate | **1** | 361 | **1** |
| break page — original | 6 | 874 | 6 |
| break page — candidate | **5** | 874 | **5** |
| break column — original | 2 | 613 | 2 |
| break column — candidate | **2** | 613 | **2** |

Three things this shows, and one it does not. Word **opened every candidate** without
a repair prompt. Word's page count **agreed with LibreOffice on all eight documents**,
including the column-break case where the correct answer is "no change". Word's word
count is **identical before and after** in every pair, which is an independent
confirmation of text preservation by the application the user actually uses.

What it does not show: that LibreOffice and Word agree in general. They agreed on
these eight synthetic documents. LibreOffice approximates Word; it is not Word, and
the preview panel says so. Record: `work/session2/word-check.json`.

## 6. Test suites

**Backend, module by module after the last edit:** 18 modules, **427 tests**, ~22
minutes. 17 modules clean in that pass; `test_api_breaks` failed on one assertion of
the pre-session read-only contract, which was rewritten afterwards and the module
re-run. Log: `work/session2/backend-suite.log`, detail in `TEST_RESULTS.md` section 16.

**Frontend:** `npm test -- --run` → **34 tests passing**; `npm run build` → succeeded.

`test_api_breaks` was then re-run after its test fix: 22 tests, one failure — a
**renderer timeout** under CPU contention, not a logic fault
(`"Nothing to reject; this job is 'failed'"`). `RenderedBreakReviewTests` re-run on
an idle machine: **6 tests, OK**. The rendered tests are timing-sensitive under load
and this is recorded as a property of the suite, not hidden.

Counts are reported per run and **never summed across overlapping runs**.

## 7. Sentry

| Claim | Status |
| --- | --- |
| Instrumented, with a closed-set tag allowlist and an envelope-rebuilding transport | **Verified locally** — `test_observability`, `test_observability_breaks`, `test_observability_review`, `test_api_observability` |
| No document text, complaint, prompt, model output, filename, path or screenshot in telemetry | **Verified locally at the transport boundary** |
| A hosted Sentry project received a trace | **NOT VERIFIED.** No DSN is configured; nothing has ever been sent to sentry.io from this code. Setup steps are in `SENTRY_SETUP.md` |

## 8. Not verified, and not claimed

- **Word, in general.** Eight documents were opened in real Word and agreed (section
  5). That is eight synthetic documents, not a claim that the two renderers agree.
  The in-product preview is still LibreOffice.
- **Hosted deployment.** Nothing is published. `render.yaml` is configuration, not a
  deployment.
- **Tinker.** Nothing trained.
- **Live model on the section task.** Not run to completion in this session; the
  break result above makes the expected outcome clear but that is an inference, not
  a measurement, and is labelled as one.
