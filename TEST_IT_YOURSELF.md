# Test it yourself

Nine prepared Word documents, what to type, what should happen, and how to tell a
model saying "no" apart from the application breaking.

Everything here is Windows PowerShell. Start the services first — see
**[START_HERE.md](START_HERE.md)** — then come back.

> **Read this once.** Several of these documents are supposed to produce a *refusal*
> or *no change*. Those are the interesting ones. A tool that repairs everything you
> point it at is a tool that will eventually destroy something you meant to keep.

---

## Startup, in short

Three PowerShell windows.

```powershell
# window 1 - the model (optional; skip for the deterministic repairs)
ollama serve

# window 2 - the backend
cd C:\Users\Aarushi\Downloads\What-the-Doc-Starter\what-the-doc
$env:GEMMA_MODEL = "gemma4:e2b"
.\.venv\Scripts\python.exe api.py --cors http://localhost:5173

# window 3 - the interface
cd C:\Users\Aarushi\Downloads\What-the-Doc-Starter\what-the-doc\frontend
npm run dev
```

Open <http://localhost:5173>.

The footer and the Settings page show what the backend reports: whether a model is
configured and whether a renderer was found. If either is missing it says so there
rather than failing later.

## The loop you will repeat

1. **Upload** — drop one of the files from `test-documents\` on the upload panel.
2. **Select** — click a paragraph in the left-hand outline. For a break or a section,
   also click the specific break in the break list or the specific section in the
   SECTIONS panel. Selecting one is *saying which one you mean*, not asking for it to
   be changed.
3. **Complain** — type what looks wrong, in your own words.
4. **Diagnose** — press the button and wait. On CPU this is minutes.
5. **Review** — compare the rendered before and after pages. Read the invariants.
6. **Accept or reject** — both are one click, and both are recorded.
7. **Download** — only exists after accepting. Open it in Word.

---

## The documents

Every id below was read out of the file after it was written and is recorded in
`test-documents\manifest.json`. **Ids are bound to the exact bytes of the file.** If
you open one in Word and save it, every id for it changes and the service will refuse
the old one with "re-inspect the document". Select through the interface, which
always shows current ids; the ids here are for checking what you are shown and for
direct API calls.

---

### 01-paragraph-faults.docx — three paragraph-property faults
*Verified.* Covered by `test_api`, `test_pipeline`, `test_workflow`.

| | |
| --- | --- |
| **What is wrong** | Paragraph 1 has a direct 48 pt space above it where its Heading1 style supplies 12 pt. Paragraph 10 is centred where its peers are justified. Paragraph 19 has a direct `w:pageBreakBefore` stranding the ending on a nearly empty page. |
| **Select** | Paragraph 1, then 10, then 19 — one run each. |
| **Say** | "There is far too much space above this heading." / "This paragraph is centred and the rest of the text is not." / "This heading keeps jumping to a new page and leaves a nearly empty one." |
| **Expect** | Three separate proposals: `space_before_pt` → 12, `alignment` → `both`, `page_break_before` → false. Each stops at *awaiting review*. |
| **Check in Word** | The gap above the heading matches the other headings. The centred paragraph lines up with its neighbours. The ending no longer starts its own page. |

Paragraph 19 is the one worth dwelling on. `w:pageBreakBefore` is a **paragraph
property**, not a break element. It is repaired with a field operation, and it has
nothing to do with documents 03 and 04 even though all three produce "an unwanted new
page" in Word.

---

### 02-second-sample.docx — a document that should mostly be refused
*Verified.* Covered by `test_eval_fixtures` and the recorded live evaluation.

| | |
| --- | --- |
| **What is wrong** | A mixture. Some paragraphs are faulty; some differ from their peers deliberately; some carry an alternative measurement (autospacing, line units) that would silently defeat a written value. |
| **Select** | Any paragraph. |
| **Say** | "The spacing here looks wrong." |
| **Expect** | Frequently `no_change`, `unsupported` or `needs_clarification`. **An abstention is a correct answer.** |
| **Check in Word** | Nothing. That is the point of this document. |

---

### 03-manual-page-break.docx — explicit page breaks, removable and not
*Verified, and demonstrated on a real render this session:* 6 pages → 5, 5 breaks →
4, every character preserved, 12 of 12 invariants passed.

| | |
| --- | --- |
| **What is wrong** | Body paragraph 2 holds **two** explicit page breaks with text either side of each. Paragraph 3 holds a deliberate one. Paragraph 4 holds a line break and a `w:lastRenderedPageBreak`, neither removable. A hyperlink and a table cell each hold a page break in an unsupported context. |
| **Select** | Paragraph 2 in the outline, then **one specific break** in the break list. The text either side is shown so you can tell the two apart. |
| **Say** | "There is a page break in this paragraph I never asked for and it leaves a half-empty page." |
| **Expect** | **Without** selecting a break: `needs_clarification` with a question asking which one. **With** one selected: a proposal to remove exactly that `w:br`. |
| **Check in Word** | One page fewer. The text either side of that break is now joined. Every other break — including the deliberate one in paragraph 3 — is still there. |

First removable page break: `brk-849af3bc0798d816`. Second: `brk-2d774941075c438d`.
The deliberate one in paragraph 3: `brk-5f5bd30ba08e6948`.

**Also worth trying:** click the break inside the hyperlink. It is listed, and it is
refused with the element that disqualifies it. The service shows you things it cannot
fix rather than hiding them.

---

### 04-column-break.docx — a column break in a real two-column section
*Verified, and demonstrated on a real render this session:* 2 pages → 2 (**delta 0**),
2 breaks → 1, every character preserved, 12 of 12 invariants passed.

| | |
| --- | --- |
| **What is wrong** | Body paragraph 2 carries an explicit column break mid-sentence. Paragraph 3 carries a page break, so the two have to be told apart by *type*, not position. |
| **Select** | Paragraph 2, then the break badged **COLUMN** (`brk-e03e1b73721c1a97`). |
| **Say** | "This column break splits the sentence across columns and I did not want it." |
| **Expect** | A proposal to remove that one column break. |
| **Check in Word** | The sentence runs on within the first column. **The document is still two pages.** A reflow without a page-count change is a correct result, and the interface says so in those words rather than claiming a page was saved. |

---

### 05-section-next-page.docx — the supported section start
*Verified, and demonstrated on a real render this session.* This is the new repair.

| | |
| --- | --- |
| **What is wrong** | Possibly nothing. Two sections with identical page setup; the second starts on a new page. This is the one case the section operation supports — *if you ask for it*. |
| **Select** | Any paragraph, then **"Section 2 · starts nextPage"** in the SECTIONS panel (`sec-7ae31e1d47034432`). Section 1 has no button and says why: a section start describes how a section follows the previous one, and there is no previous section. |
| **Say** | "The second part should carry on down the same page instead of starting a new one." |
| **Expect** | A proposal changing that section's start from `nextPage` to `continuous`. Both versions **must** render or the run fails — this is enforced on the server, not by a disabled button. |
| **Check in Word** | Part Two begins directly under Part One. **Check every page, not just the first** — a section change can move everything after it. |

**Do not read a missing page break as a bug.** Continuous asks the section to
continue on the same page; page geometry, columns and keep-with-next can still force
one. The service reports the real before and after page counts and promises nothing.

---

### 06-section-default-start.docx — the start type that is not written down
*Verified.* Covered by `test_sections.ExactnessTests`.

| | |
| --- | --- |
| **What is wrong** | The second section writes **no `w:type` at all**. `ST_SectionMark` defaults to `nextPage`, so it behaves as a next-page start although nothing in the file says so. |
| **Select** | The SECTIONS entry marked *"(schema default; no w:type written)"* (`sec-a583f917144b703b`). |
| **Say** | As 05. |
| **Expect** | The same proposal. The repair **creates** the `w:type` element at its schema position rather than refusing the section or appending it where Word would reject it. |
| **Check in Word** | As 05, and the file still opens without a repair prompt. |

---

### 07-section-intentional.docx — the one that must be left alone
*Verified.* This is the most important document here.

| | |
| --- | --- |
| **What is wrong** | **Nothing.** Appendix A deliberately starts on a new page. Structurally it is identical to 05 — which is exactly the point. The service cannot tell them apart and must not try. |
| **Select** | The section (`sec-753e21515d3e00b6`), then complain about something else. |
| **Say** | "The spacing in this report looks uneven." |
| **Expect** | `no_change` or `unsupported`. A proposal to make the appendix continuous here would be **wrong**. |
| **Check in Word** | Nothing should change. If the appendix stops starting on a new page, that is a bug worth reporting. |

---

### 08-section-unsupported.docx — an odd-page start
*Verified.* Covered by `test_sections` and `test_api_sections`.

| | |
| --- | --- |
| **What is wrong** | The second section starts on the next **odd** page. That is a binding and printing requirement, not a formatting accident. |
| **Select** | You cannot. It is listed with its start type and no button. |
| **Say** | "This section leaves a blank page before it, remove that." |
| **Expect** | A refusal naming the rule. Over the API, selecting it returns `400 unsupported_formatting`. No proposal and no candidate. |
| **Check in Word** | Nothing changes, and the interface says *why* rather than failing silently. |

---

### 09-section-landscape.docx — differing page geometry
*Verified.* Covered by `test_sections.EligibilityTests`.

| | |
| --- | --- |
| **What is wrong** | The second section is landscape. A continuous section continues on the page the previous section is already on, so it cannot carry a different page size onto it. |
| **Select** | You cannot. The panel names `w:pgSz` as the difference. |
| **Say** | "Stop this landscape part starting a new page." |
| **Expect** | A refusal naming the differing property. |
| **Check in Word** | Nothing changes. This refusal is the honest answer: the conversion would silently reformat the document. |

---

## Telling a refusal apart from a failure

This is the distinction that matters most when something does not happen.

| What you see | What it is | What to do |
| --- | --- | --- |
| A sentence explaining why the formatting cannot be repaired, status **unsupported** | The **model or the engine** declined. Working as designed. | Nothing. Read the reason. |
| A question, status **needs_clarification** | The complaint could mean more than one thing. | Answer it: select the specific break or section, or say which symptom you mean. |
| "already matches the rest of the document", status **no_change** | Nothing is wrong, or the difference is deliberate. | Nothing. |
| A red error banner with an **`error_code`** | The **application** failed. | Note the code — see the table below. |
| The button spins for minutes with no banner | CPU inference. **Not** a hang. | Wait. A 4.6 B model on CPU takes minutes on a prompt this size. |
| `503 model_unavailable` | No model runtime reachable. | Start `ollama serve` and set `GEMMA_MODEL`. There is no canned fallback. |

The rule of thumb: **a sentence in plain English is a judgement; an `error_code` is a
fault.** A judgement is the product working. Only the second one is a bug.

---

## Troubleshooting

**The interface says the backend is unavailable.**
The frontend calls `/api`, which the Vite dev server proxies to `127.0.0.1:8000`. If
you started `api.py` on another port, tell Vite:

```powershell
$env:VITE_API_TARGET = "http://127.0.0.1:8010"
npm run dev
```

Confirm the backend is really up first: `curl.exe http://127.0.0.1:8000/api/health`.

**Port already in use.**

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
  Select-Object LocalPort, OwningProcess
Get-Process -Id <PID>        # look at it before you kill it
```

Older development servers from previous sessions may still be listening. Identify a
process before stopping it.

**No model available.**

```powershell
ollama list
ollama serve                       # if nothing responds
$env:GEMMA_MODEL = "gemma4:e2b"    # set BEFORE starting api.py, in the same window
```

An environment variable set in one PowerShell window does not reach another. If
`api.py` was already running, restart it.

**Inference takes forever.**
It takes minutes on CPU and that is expected. The client waits up to 600 seconds by
default. If you want to prove the interface works without waiting, use a deterministic
run instead: start the backend with `--allow-fixture-mode` and post an injected
diagnosis (see [API.md](API.md)). Those runs are labelled `mode: fixture` in every
response and are **never** presented as model answers.

**No renderer.**

```powershell
.\.venv\Scripts\python.exe -c "import render; print(render.find_soffice())"
$env:SOFFICE_PATH = "C:\Program Files\LibreOffice\program\soffice.exe"
```

Paragraph-property repairs still work without one. Break removals and section changes
are **refused** without one, with `error_code: render_failure` — a structural change
you cannot see is not a change you can approve.

**The download link does nothing / returns 409.**
You have not accepted yet. `awaiting_review` is not approval. Press accept, then
download.

**Word says the file needs repair.**
Keep the file and report it. Every candidate is checked against the original bytes
before it is offered, so this would be a real bug, not a configuration problem.

**An id is "unknown or stale".**
You re-saved the document, or the id came from an earlier upload. Re-read the breaks
or sections list and use a current id. This refusal is deliberate: an id from another
revision must not resolve against whatever now sits in that position.

---

## Checking the result in Word

Open the downloaded file and the original side by side.

1. **Text**: identical. Every character, in order. The service checks this itself,
   against the bytes, and shows it as `text_preserved`.
2. **The one change**: exactly what the review screen said, and nothing else.
3. **Page count**: compare with what the review screen reported. It reports; it does
   not promise.
4. **Later pages**: for a section change especially, scroll to the end.
5. **Your original**: still on disk, unmodified. The service never writes to it.

LibreOffice renders the preview. **It approximates Word; it is not Word.** If the two
disagree about pagination, Word is the authority for a Word document, and the preview
is the thing that was wrong. That caveat is on the preview panel too.

For what it is worth, all eight originals and candidates from this session's
demonstrations *were* opened in real Microsoft Word, which agreed with LibreOffice on
every page count and reported identical word counts before and after
(`work/session2/word-check.json`). That is eight synthetic documents, not a promise
about yours — which is exactly why this step asks you to look.
