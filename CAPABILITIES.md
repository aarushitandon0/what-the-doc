# What this actually repairs

One page, kept deliberately blunt, because the easiest way to mislead someone about a
tool like this is to let "it can find that" blur into "it can fix that". Detection is
not repair. Inspection is not mutation. A rendered preview is not an approval.

Four separate things are listed below and they are not interchangeable:

1. **Paragraph-property repairs** — six properties, unchanged.
2. **Explicit page/column-break removal** — one `w:br` element at a time.
3. **Section inspection, and exactly one section change** — a supported `nextPage`
   start may become `continuous`. Nothing else about a section can be changed.
4. **Everything else** — not supported, including things the service can *see*.

Every repair in groups 1, 2 and 3 ends at `awaiting_review`. The original file is
never written to, and a human accepts or rejects as a separate, explicit step.

---

## 1. Paragraph-property repairs (existing, unchanged)

Six resolved paragraph properties can be written into `w:pPr`, one operation per
property per paragraph, on top-level body paragraphs only:

| Field | XML written | Accepted values |
| --- | --- | --- |
| `space_before_pt` | `w:spacing/@w:before` | number, 0–144 pt |
| `space_after_pt` | `w:spacing/@w:after` | number, 0–144 pt |
| `left_indent_pt` | `w:ind/@w:left` | number, 0–144 pt |
| `alignment` | `w:jc/@w:val` | `left`, `center`, `right`, `both` |
| `page_break_before` | `w:pageBreakBefore/@w:val` | boolean |
| `keep_with_next` | `w:keepNext/@w:val` | boolean |

Values resolve through `docDefaults` and the `w:basedOn` style chain before anything is
proposed, so an inherited value is distinguished from a direct one and from "nothing
set it at all". An operation that would write a value the paragraph already resolves to
is refused as ineffective rather than performed. A paragraph whose measurement comes
from an alternative mechanism (autospacing, line or character units) is refused for
manual review instead of being silently overridden.

`page_break_before` belongs in this group and nowhere else. It is a **paragraph
property**, not a break element, and repairing it has nothing to do with group 2.

## 2. Explicit page- and column-break removal (new in this session)

One identified `w:br` element whose `w:type` is `page` or `column` can be removed.

| Property | Value |
| --- | --- |
| Operation | `{"operation": "remove_explicit_break", "target_id": …, "expected_break_type": "page"\|"column"}` |
| Plan schema | `schema_version: 2` required; a plan with no version is read as 1 and keeps working |
| Per diagnosis | At most **one** removal. Each break is a separate judgement, approved separately |
| Eligible context | The break's run must be a direct child of a top-level body paragraph |
| Targeting | Server-issued `target_id` only. No XML, XPath, element index or path is ever accepted from a caller or a model |
| Rendering | A successful render of **both** the original and the candidate is **required** before review, acceptance or download. Enforced server-side |

What is preserved, and checked against the bytes on disk rather than asserted: every
`w:t` character and its order, run count, every `w:rPr`, each run's text, paragraph and
section properties, every break that was not approved, all other ZIP members
byte-for-byte, and the original file itself. Only the one `w:br` element is deleted; a
run left empty by the removal is kept rather than tidied away, because deleting a run
or a paragraph would be a different change from the one that was approved.

Refused, each with a reason rather than a silent no-op: an unknown or stale
`target_id` (ids are bound to the source revision, so one from an earlier revision
cannot resolve), a `target_id` whose real break type differs from
`expected_break_type`, a duplicate target in one plan, a break in an unsupported
context, a line break, a `w:lastRenderedPageBreak`, more than one removal at a time,
and a break operation in a `schema_version: 1` plan.

**Three things that are called "a page break" and are not the same thing.** A
`w:pageBreakBefore` paragraph property (group 1); an explicit `w:br w:type="page"` the
writer typed (this group); and a `w:lastRenderedPageBreak`, which is where the
application that last paginated the file happened to end a page. The third is written
by the renderer, not by a person, so it is reported for completeness and is never
removable — removing it would repair nothing.

**A break is not a fault.** Writers insert breaks on purpose far more often than by
accident, and a break before a chapter, an appendix or a table they wanted kept whole
is a decision, not a defect. Identifying a break is therefore never phrased as a
recommendation. When a paragraph holds more than one break and neither the complaint
nor the interface says which one is meant, the correct answer is
`needs_clarification` with a question — not a guess.

**Removing a break does not have to reduce the page count.** The service reports the
before and after counts and says plainly when a document reflowed without losing a
page. Nothing promises a reduction.

## 3. Sections: full inspection, and one change

### What is reported

`GET /api/documents/{id}/sections` reports, for each section: its start type
(`nextPage`, `nextColumn`, `continuous`, `evenPage`, `oddPage`, with `nextPage`
applied as the documented schema default when `w:type` is absent, flagged as a
default rather than as an authored value), whether it is continuous or starts a new
column, its column declaration, whether page size and margins are present, which body
paragraphs it spans, where it was defined, a server-issued `target_id`, and either
that it may be converted or the exact rule that stops it.

Both forms of `w:sectPr` are handled with their documented meanings: one inside a
paragraph's `w:pPr` defines the section that **ends with that paragraph**; the one
that is the last child of `w:body` defines the document's **final** section. A
document with no final body `w:sectPr` is handled too. No visual page number is ever
reported — one cannot be derived from this XML, so none is invented.

### The one change

| Property | Value |
| --- | --- |
| Operation | `{"operation": "change_section_start", "target_id": …, "expected_start_type": "nextPage", "new_start_type": "continuous"}` |
| Plan schema | `schema_version: 3` required. Versions 1 and 2 keep working exactly as before; a break plan still declares 2 |
| Per diagnosis | At most **one**, and it is the **only** operation in its plan. It is never combined with a paragraph repair or a break removal |
| Targeting | Server-issued `target_id` only, bound to the source revision *and* to the current start type |
| Rendering | A successful render of **both** documents is **required** before review, acceptance or download, and every rendered page is compared — a section change can move the layout of everything after it |

Only the `w:val` of that section's `w:type` is written, and `w:type` is created at its
CT_SectPr schema position when the section relied on the documented default.

### Why a section is, or is not, eligible

ISO/IEC 29500-1 §17.6.22 is explicit that a section's type says how that section is
placed **relative to the previous section**, and that a continuous section "begins
the new section on the following paragraph". Every rule below follows from that
sentence, and each refusal names the rule it hit:

- **The first section is never eligible.** It has no previous section, so changing
  its start type would be a change with no defined effect.
- **Only `nextPage` converts, and only to `continuous`.** `nextColumn`, `evenPage`
  and `oddPage` are inspected and refused. An even- or odd-page start is a binding
  and printing requirement and is never "fixed" without an explicit, separately
  supported intent that does not exist in this version.
- **Page setup must match the previous section.** Continuous means "on the page the
  previous section is already on", so a section declaring its own `w:pgSz`,
  `w:pgMar`, `w:pgNumType`, `w:titlePg`, `w:pgBorders`, `w:docGrid` or
  header/footer references different from the previous section's is refused, with
  the differing element named. Converting it would change page setup, numbering or
  headers as well as the start type.
- **A tracked section revision (`w:sectPrChange`) is refused.**
- **An unrecognised start type is refused.**

### What is preserved, checked against the bytes

The verification does not trust the code that made the change: it takes the document
that was written, reverts exactly the approved attribute on a copy of it, and requires
the result to serialise byte-for-byte identically to the input. Any other edit
anywhere — another section, a paragraph, a run, a column declaration, a margin —
survives that revert and fails. On top of that, `workflow.check_invariants` compares
the files on disk for section count, section boundaries, the start-type list differing
at exactly the approved index, and every sectPr with its `w:type` removed being
byte-identical. The strict `runs_preserved` check that a break removal has to narrow
is **not** narrowed here: a section change touches no run, so it still faces the
strict version.

### What this does not promise

**Continuous does not guarantee that a visible page break disappears.** Page
geometry, column settings and keep-with-next rules can still force pagination. The
service reports the real before and after page counts, per page, and claims nothing
beyond them.

**Eligible is not a recommendation.** A section break before an appendix or a chapter
is a decision, not a defect. `test-documents/07-section-intentional.docx` is
structurally identical to the supported fixture precisely because the service cannot
tell them apart and must not try: only the person can say which it is.

**No other section change exists.** No section can be created, merged, split or
deleted; no column layout, page size, margin, orientation, numbering, header or
footer can be changed.

## 4. Not supported

Visible but not repairable, which is exactly the distinction this page exists for:

- Breaks inside a table cell, text box, hyperlink, field or any other wrapper. These
  are *listed*, with the element that disqualifies them, and refused.
- `w:lastRenderedPageBreak` and line breaks (`w:br` with no type, or
  `w:type="textWrapping"`).
- Any section change other than the single `nextPage` → `continuous` conversion in
  group 3: no section creation, merging, splitting or deletion, and no change to
  columns, page size, margins, orientation, numbering, headers or footers.

Not handled at all: tables and table styles, numbering and list indentation, fonts and
character formatting, headers, footers, footnotes, comments, images, text boxes,
content controls, tracked changes, documents with macros or signatures, encrypted
packages, and anything outside `/w:document/w:body/w:p`. Numbering indentation and
table styles are not resolved by the property resolver and are reported as such.

No feature here edits a document in place, applies a change without an explicit human
acceptance, or treats a model's own confidence in itself as evidence. A confidence
score is rejected by the diagnosis schema rather than ignored.

## What the model is and is not allowed to do

The model proposes; it never acts. It cannot submit XML, an XPath, code, a file path or
a filesystem operation. For a break removal it may only echo a `target_id` the server
minted **and showed it** for this document revision — an id it composes itself, or one
for a break it was not shown, is refused as a fabrication rather than tried. Its
proposal is validated twice by independent code paths (the diagnosis schema, then the
engine against the real XML) before any file is written, and the result is still only a
candidate. The deterministic engine, not the prompt, is the boundary: no instruction in
a document or a complaint can widen it.

## Where the evidence is

`TEST_RESULTS.md` sections 13 to 15 record the measured results for these slices,
including
which demonstrations were rendered, which were injected-diagnosis runs, which were
live-model runs and which were driven in a real browser. `OBSERVABILITY.md` records
the tracing and the local-versus-hosted verification boundary. `API.md` is the request
and response contract. Capability claims here that are not backed by one of those is a
documentation bug worth reporting.
