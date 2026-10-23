# Demo script

Six minutes. Every step below has been performed and is backed by a row in
`EVIDENCE_TABLE.md`. Nothing here is a mock-up of something that has not run.

**Before you start:** the backend, Ollama and the frontend running (see
`START_HERE.md`), and `test-documents\` open in a file picker.

> **Disclose the waits.** Live CPU inference takes **minutes** per diagnosis on this
> machine, and it is honest to say so on camera. The deterministic steps below are
> shown at full speed; where a recording is cut, say "this wait is shortened" out
> loud and on screen. Do not let an edit imply the tool is fast.

---

## 0. The frame (30 s)

> "Word has a thousand ways to go wrong. This fixes three of them, on purpose, and
> tells you about the rest."

Put `CAPABILITIES.md` on screen for two seconds. The point being made is that the
list of what it *does* is short, written down, and shorter than the list of what it
*sees*.

---

## 1. A break it can remove (90 s) — *demonstrated*

Open `test-documents\03-manual-page-break.docx`.

1. Click paragraph **2**. The break panel shows **two** page breaks in that one
   paragraph, each with the text either side, so they read differently.
2. **Do not select one.** Type *"There is a page break in this paragraph I never
   asked for."* and diagnose.
   → Say, on screen: this is where a tool that guesses deletes the wrong thing.
3. Now select the **second** break and run it again.
4. Review: both rendered pages, which break is going, and the page counts.
5. **Accept**, then download, then open it in Word.

**What to point at:** `6 pages → 5`. The text either side of the break, now joined.
The *other* breaks, still there. The original file, untouched on disk.

*Evidence:* rendered demo 6→5, 5 breaks→4, 12/12 invariants; browser run 5/5 steps,
downloaded bytes = the reviewed candidate.

---

## 2. The one it leaves alone (45 s) — *demonstrated*

Open `test-documents\04-column-break.docx`, remove the column break, and show the
review screen.

**Point at the page count: `2 pages → 2`.** Read the sentence the interface prints:
the document reflowed without losing a page, and that is a normal result.

> "It would be easy to make this say 'fixed'. It says what actually happened."

*Evidence:* rendered demo, delta 0, 12/12 invariants.

---

## 3. The new repair: a section start (120 s) — *demonstrated*

Open `test-documents\05-section-next-page.docx`.

1. Look at the **SECTIONS** panel. Two sections. **Only one is clickable.** Read the
   other one's reason out loud: *a section start describes how a section follows the
   previous one, and there is no previous section here.*
2. Select section 2. Type *"The second part should carry on down the same page
   instead of starting a whole new one."*
3. Diagnose. **Both documents must render** — this is enforced on the server, not by
   a disabled button.
4. Review: `nextPage → continuous`, both previews, the page counts.
5. Accept, download, open in Word, and **scroll to the last page** — a section change
   can move everything after it.

**Say this line, it matters:**
> "Continuous asks the section to continue on the same page. It does not promise the
> page break disappears — geometry and column settings can still force one. Here it
> did: two pages became one. The service reports that; it doesn't guarantee it."

*Evidence:* rendered demo 2→1, section count and boundaries preserved, 12/12
invariants; HTTP 7/7; browser 7/7 with the download byte-identical to the reviewed
candidate.

---

## 4. The refusals (60 s) — *demonstrated*

This is the part worth the most screen time.

- `test-documents\08-section-unsupported.docx` — an **odd-page** start. Listed, not
  clickable, with the reason: a binding and printing requirement.
- `test-documents\09-section-landscape.docx` — refused, naming **`w:pgSz`**: a
  continuous section continues on the previous section's page and cannot carry a
  different page size onto it.
- `test-documents\07-section-intentional.docx` — the appendix. **Structurally
  identical to document 05.** Eligible, and *should not be changed*. Complain about
  something else and show it abstain.

> "It can't tell a deliberate break from an accidental one. Neither can anything
> else. So it asks, or it declines — it doesn't guess."

---

## 5. Approval is a separate act (30 s) — *demonstrated*

Back on a reviewable job, open the browser network panel and hit the download URL
before accepting. **409.** Then accept, then download: **200**, and the bytes hash to
the candidate that was on screen.

*Evidence:* `test_api_sections.test_download_before_approval_is_refused`, and the
sha256 comparison in both browser summaries.

---

## 6. What is honestly not done (45 s)

Do not skip this. It is the most credible part of the demo.

- **Live Gemma does not work on these two structural tasks.** Measured today:
  `gemma4:e2b` returned `{"operation": "remove_break", "break_id": …}` — an invented
  schema — and was rejected as `invalid_response`. The previous prompt failed the
  same way, so it is not a regression from today's work. The deterministic slice is
  unaffected, and the plan is in `TINKER_NEXT.md`.
- **Sentry is instrumented, not hosted-verified.** No DSN; nothing has been sent.
- **Rendering is LibreOffice, not Word** — though as a one-off check, all eight
  originals and candidates *were* opened in real Microsoft Word, which agreed with
  LibreOffice on every page count and reported identical word counts before and
  after. Eight synthetic documents is a useful check, not a general guarantee that
  the two renderers agree.
- **Nothing is deployed.** The static demo is configured and ready; the hosted
  backend is a list of unbuilt requirements in `DEPLOYMENT.md` part C.

> "Three repairs, each one it can refuse, each one a human approves. That's the
> product."

---

## Phrases to avoid on camera

"Fixes any Word document" · "preserves everything" · "removes the page break" ·
"zero cost" · "Sentry integrated" · "fine-tuned" · "deployed" · "sandboxed" ·
"Word-verified". Each has an accurate alternative in `SUBMISSION_CHECKLIST.md`.
