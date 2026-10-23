---
title: "I built a Word formatting repair tool that refuses most of the time"
published: false
tags: ai, opensource, python, showdev
---

# I built a Word formatting repair tool that refuses most of the time

Everyone has had the document. A heading floats half a page from its paragraph. One
section starts on a new page for no reason you can find. You click around in the
ribbon, something else moves, and twenty minutes later the document is worse.

So: point at the paragraph, say what looks wrong in your own words, and let a local
model work out which setting is responsible.

That was the idea. Most of the work turned out to be in the word **no**.

## The thing that nearly went wrong

An early version was happy to remove a page break whenever it found one near a
complaint. Which is fine, until you remember why page breaks exist. A writer puts one
before a chapter. Before an appendix. Before a table they want kept whole. Breaks are
inserted **on purpose far more often than by accident**, and a tool that removes them
helpfully is a tool that quietly vandalises documents.

There's no signal in the file that distinguishes the two. I have a test fixture whose
only purpose is to prove this: `07-section-intentional.docx` is **byte-for-byte
equivalent in structure** to the document the tool is supposed to repair. Same two
sections, same page setup, same `nextPage` start. The only difference is that one is
an appendix somebody wanted on its own page.

The tool cannot tell them apart. Neither can you, from the XML. So it doesn't try: it
identifies, and a person decides. When a paragraph holds two page breaks and the
complaint doesn't say which, the answer is a question, not a guess.

## What it actually repairs

Three families, and I keep the list short on purpose:

1. **Six paragraph properties** — spacing, indent, alignment, `pageBreakBefore`,
   `keepNext — resolved through `docDefaults` and the style chain, so "someone typed
   48pt here" is distinguishable from "the style says 48pt".
2. **One explicit page or column break**, removed one at a time.
3. **One section start**: `nextPage` → `continuous`. That's it. Not columns, not
   margins, not orientation, not headers.

Everything else is *reported* and refused with a reason. Detection is not repair, and
conflating the two is how these tools end up lying.

## Three things called "a page break"

This ate a day, so here it is for free. In OOXML, at least three unrelated things
make Word start a new page:

- `w:pPr/w:pageBreakBefore` — a **paragraph property**.
- `w:br w:type="page"` — an **explicit break element** inside a run, typed by a human.
- `w:lastRenderedPageBreak` — where whichever application last paginated the file
  happened to end a page. **Not user content.** Removing it repairs nothing.

Three different repairs. One wrong guess and you edit something the user never
mentioned. The code keeps them in separate code paths with separate tests, and the
model prompt spends a paragraph insisting they are different.

## Sections are their own trap

ISO/IEC 29500-1 §17.6.22 says a section's type specifies how that section is placed
**relative to the previous section**, and that a continuous section "begins the new
section on the following paragraph".

Read that twice and several rules fall straight out:

- **The first section of a document can never be converted.** It has no previous
  section. Changing its start type is a change with no defined effect.
- **A continuous section shares the previous section's page.** So if it declares its
  own page size, margins, numbering or header references, converting it would change
  page setup *as well as* the start type — more than was asked for. Refused, with the
  differing element named.
- **`evenPage` and `oddPage` are never touched.** Those are binding and printing
  requirements, not formatting accidents.

And the one I wanted to promise and couldn't: **continuous does not guarantee the
page break disappears.** Geometry, columns and keep-with-next can still force
pagination. So the service reports the real before and after page counts and promises
nothing. On my fixture it went 2 pages → 1. It says "2 → 1", not "fixed".

## The model proposes; it never acts

The model gets structured evidence — resolved properties with the layer that supplied
each one, peer distributions, identified breaks with server-issued ids. It returns
JSON. It cannot send XML, an XPath, a file path or an element index. For a break or a
section it may only echo an id **the server minted and showed it**; an id it composed
itself is refused as a fabrication rather than tried.

Then two independent validators run — a schema check, then the engine against the
real XML — and the result is still only a *candidate*. A separate file. The original
is never written to.

After the candidate is built, preservation is checked **against the bytes on disk**:
every character in order, run count and run properties, paragraph count, section count
and boundaries, every other ZIP member byte-identical, and the original's sha256
unchanged.

The section check is the one I'm happiest with, because it doesn't trust my own
mutation code. It takes the file that was written, **reverts the approved attribute on
a copy**, and demands the result serialise byte-for-byte identically to the input. Any
other edit anywhere survives that revert and fails. There's a test that quietly
changes a second section and watches it get caught.

## The part that didn't work

I measured the local model today and it failed.

Asked to diagnose an explicit page break, `gemma4:e2b` returned:

```json
{"operation": "remove_break", "break_id": "brk-7925c71d4abd3fc5"}
```

That is an invented schema. The real envelope has five required keys and the operation
is `remove_explicit_break` with a `target_id`. The validator rejected it as
`invalid_response` — correct behaviour, and also a diagnosis the user never sees.

I ran a controlled comparison against the previous system prompt, before I'd added
anything this session. Same failure, less information: `{"operation": "remove_break"}`.
So it isn't prompt length and it isn't my change. A 4.6B model doesn't reliably hold an
eleven-thousand-character instruction and emit a fixed envelope.

The deterministic slice is unaffected — and I think that's the actual lesson. The
architecture decided in advance that the model is a *proposer* behind two validators.
When it produced nonsense, nothing downstream moved. That's the system working, even
though the feature didn't.

Fixing it is a fine-tuning problem — envelope conformance, not judgement — and it's
written up as an unfinished experiment with a spending cap attached, rather than
quietly dropped.

## What I'd tell anyone building on a document format

**Separate "can see" from "can change", in the code and in the docs.** The gap is where
tools start over-claiming.

**Write the refusals first.** My refusal tests outnumber my success tests and that
ratio feels right. A repair that can't say no isn't safe near something you care about.

**Check preservation against the bytes, not your intentions.** "I only changed one
attribute" is a belief. Reverting it and diffing the whole document is a measurement.

**Go and read the spec sentence.** Every section rule above came from one sentence in
§17.6.22. I'd have guessed three of the five wrong.

## Honest status

Working: three repair families, each demonstrated on real LibreOffice renders and
driven end to end in real Chromium, including the downloaded `.docx` hashing to the
candidate that was reviewed.

Not working: live model diagnosis of the two structural operations, as measured above.

Not done: no hosted Sentry verification (instrumented and privacy-tested locally, but
no DSN, so nothing has been sent). Nothing deployed. Nothing fine-tuned. No candidate
has been opened in Microsoft Word — every render is LibreOffice, which approximates
Word and is not Word.

Not promised: it does not fix any Word document, it does not preserve everything, and
it does not guarantee a page disappears.

*Built with Gemma via Ollama, LibreOffice for rendering, FastAPI and React. Written
with AI assistance.*
