# What the Doc?! — product concept and implementation plan

Status: proposed product plus an implemented structural repair prototype. No accuracy,
speed, cost saving, model training, production readiness or prize outcome is claimed.

## 1. Product promise

You finished writing your report. An unexplained gap, stubborn page break or misaligned
heading is still wasting your time. Upload the Word file, select the affected paragraph,
describe what looks wrong, and review a targeted repair. Download a new editable Word
file, with the original intact.

Tagline: You did the work. Let us fix the formatting.

The primary audience is students and anyone preparing reports or applications. The
first release handles .docx paragraph-layout faults, not arbitrary PDFs or complete
document redesign. The theme is removing a tedious task: the user describes the
visible symptom while the software investigates the invisible formatting settings.

## 2. Interaction

Upload -> inspect and render -> select paragraph -> describe issue -> propose repair ->
render candidate -> review before/after -> accept and download.

The first UI will show a rendered page alongside a structured paragraph list. Users
select the paragraph by its text, so we do not pretend a screenshot click perfectly
maps to Word XML. PDF-coordinate-to-paragraph selection comes later; repeated text,
tables and wrapping make exact mapping nontrivial.

Example: user selects a heading and says "Too much space above this." The system
shows direct spacing, inherited style information and comparable nearby headings.
If evidence supports changing before-spacing from 48 to 12 points, it proposes that
single edit and shows the result. A legitimate chapter break should be left alone.

The interface offers Preview repair, Accept and download, Reject, and Original.
It never labels an empty plan as a successful repair. Unclear cases request one
focused clarification or explicitly report unsupported structure.

## 3. Scope

First targets: excessive paragraph spacing; inconsistent alignment/indentation;
unwanted page-break-before; accidental keep-with-next chains. Later: explicit page
break removal after verifying intent. Section breaks and blank paragraphs are not
deleted wholesale because they can carry important structure.

First-release boundaries: ordinary body paragraphs; no tracked-change editing,
complex table repair, floating images, equations, macros, signed files, PDF-to-Word
conversion or complete redesign. Preserve unsupported parts; if their interaction
prevents a safe repair, report that limitation.

## 4. Architecture and stack

Frontend: React + TypeScript, a PDF preview and a paragraph-selection panel.
API: Python FastAPI for uploads, job status, proposals, acceptance and downloads.
Document engine: zipfile + lxml for surgical OOXML edits. python-docx can help with
fixtures/inspection, but avoid reconstructing entire user files through a new document.
Rendering: LibreOffice headless in an isolated worker, PDF output, PDF page images.
Gemma: required open-weight diagnosis model, receiving user intent plus structured
paragraph/style evidence. Start by benchmarking a small instruction-tuned Gemma,
for example Gemma 3 4B; this is a candidate, not a claim about the latest or best model.
MongoDB Atlas: hosted job metadata, operation histories, outcomes and explicit feedback.
Sentry: processing traces with private content excluded.
Tinker: gated research extension for a supported open repair-policy model.
Storage: temporary private workspace for local development; private blob/object storage
for hosted originals and outputs. MongoDB stores references, not a collection of full
document binaries. No vector database is necessary for the MVP.

## 5. Model roles

Gemma interprets the complaint and proposes a diagnosis/allowed operation. It receives
only the selected paragraph and needed context, with resolved styles in the completed
implementation. Text inside the document is data, never a tool instruction. No arbitrary
Python, shell commands or XML replacement is accepted from the model.

A strict validator checks the document hash, selected paragraph, operation allowlist,
value bounds and unsupported structures. The deterministic patcher performs edits.
Programmatic invariants check preservation; rendering and user review check appearance.
Model confidence is not treated as proof that a change is safe.

Tinker's current model list inspected on 2026-10-20 did not list Gemma. Do not promise
Gemma training there. A possible extension is a supported Qwen model trained to choose
repair operations from structured evidence, while Gemma handles user-facing diagnosis.
Adopt the second model only if held-out evaluation justifies its complexity.

## 6. MongoDB design

documents: owner_id, document_id, source_hash, private_storage_key, created_at, expires_at.
jobs: owner_id, job_id, document_id, status, stage, error_code, model_version, timestamps.
proposals: job_id, source_hash, target_ids, operations, diagnostic_evidence, status.
versions: document_id, version_id, parent_version_id, output_hash, storage_key, checks.
feedback: proposal_id, accepted_or_rejected, optional reason, separate research consent.

Every query must be owner-scoped. Expiry indexes can remove metadata, but a separate
cleanup task must delete object-storage files and in-memory/cache copies. Default to
short retention (proposed: 24 hours) and provide Delete now. Document content must not
enter training data without separate opt-in. Local mode needs no MongoDB account.

## 7. API proposal

POST /documents: validate upload and create private job.
GET /jobs/{id}: stage and status; no document text in operational logs.
GET /documents/{id}/paragraphs: structured body paragraphs and supported properties.
POST /documents/{id}/proposals: selected id + complaint + expected source hash.
GET /proposals/{id}: diagnosis, changes, validation results and preview references.
POST /proposals/{id}/accept: create accepted downloadable version idempotently.
GET /versions/{id}/download: authenticated private download.
DELETE /documents/{id}: remove files and related user records.

Proposed state flow: uploaded -> inspected -> original_rendered -> proposed ->
candidate_rendered -> validated -> awaiting_review -> accepted, rejected or failed.
Do not repeatedly apply a patch to a previously patched copy on a retry. Generate
each candidate from its immutable parent plus its proposal hash.

## 8. Validation and document fidelity

Preserve all text runs, hyperlinks, media, relationships, styles, headers and footers
unless an explicitly supported operation targets them. Compare ZIP part payloads and
XML structure. Reject unknown fields and stale plans. Keep original bytes for rollback.
Resolve paragraph direct formatting, based-on styles and document defaults before
claiming an effective setting. More advanced numbering/theme effects require later work.

Re-render candidate and original. Compare page counts and images as review signals,
not blanket pass/fail rules: legitimate repairs can shift subsequent text. Reject
structural corruption and surface suspicious large changes for review.
LibreOffice and Microsoft Word can paginate differently. Check release fixtures in
Word and state that the web preview is an approximation, with matched fonts where possible.

## 9. Upload and worker isolation

Enforce compressed/expanded size limits, entry limits and parsing timeouts. Reject
macros, encrypted or signed packages and unsupported revision structures. Do not fetch
external relationships. Run LibreOffice as a low-privilege process in a disposable
container with no outbound network, a unique profile, resource limits and a timeout.
Never pass document text to a shell. Protect model and database credentials server-side.
This isolation is planned; the current CLI is not a production upload server.

## 10. Build sequence

Milestone A — structural repair proof (started): inspect top-level paragraphs, accept
bounded JSON operations, patch a copy, preserve other parts and reject invalid plans.
Exit: structural tests pass and limitations are explicit.

Milestone B — real document proof (implemented, Word check outstanding): a generated
Word-openable report with three labelled faults, effective-property resolution through
docDefaults and the w:basedOn chain, same-style peer comparison as diagnostic evidence,
one property repaired per run, LibreOffice before/after rendering with per-page
changed-pixel comparison, and preservation invariants re-checked against the files on
disk. Microsoft Word was used as an independent check: it agrees with the resolver on
15 of 15 comparable fields (11 inherited from a style, 1 from docDefaults) and reports
the same page counts as LibreOffice on this fixture, including 4 pages before and 3
after the page-break repair. Exit: met for the synthetic sample. Still outstanding:
running the workflow on a consented real-world document, where numbering, tables and
theme effects will exercise the resolution caveats the fixture does not.

Milestone C — Gemma integration (implemented for one model): structured prompt,
strictly validated JSON diagnosis, abstention as a first-class answer, and a ten-case
live evaluation covering clean paragraphs, a legitimate page break, an ambiguous
complaint, an inherited alternative measurement, document text that tries to instruct
the model, and a complaint aimed at the wrong or a nonexistent paragraph.
Exit: met on this set. Live `gemma4:e2b` on CPU passed 8 of 10 cases, reached all three
labelled repairs unaided, touched no forbidden field and broke no preservation
invariant; median latency 84s. Two gaps remain recorded rather than smoothed over: a
malformed `evidence` shape when abstaining on an ambiguous complaint, and failure to
notice `overridden_by`, where the deterministic validator stops the ineffective repair
instead. Still outstanding: benchmarking `gemma4:e4b`, and a consented real document.

Milestone D — interface: upload, paragraph selector, complaint box, side-by-side preview,
accept/download and undo-to-original. Display processing stages and actionable errors.
Exit: a fresh user completes one repair without developer help.

Milestone E — hosted persistence: FastAPI endpoints, MongoDB ownership/history, private
file storage, expiry cleanup, Sentry spans, isolated renderer and job concurrency limits.
Exit: two users cannot access each other's jobs; retries do not duplicate edits.

Milestone F — Tinker experiment: collect labelled clean/corrupted examples, measure base
model, train a supported model within an approved budget, evaluate held-out templates.
Exit: report actual baseline and fine-tuned outcomes, including regressions. Do not invent wins.

Milestone G — submission: live synthetic sample demo, a consented real example, short
video, reproducible setup, model licence notes, evaluation table and trace screenshots.

## 11. Training and evaluation

Initial data proposal: 30 diverse clean templates, several controlled corruptions each,
and paraphrased complaints. Include clean no-op cases, intentional whitespace, unknown
intent, inherited formatting and unsupported structures. Record the precise corruption
and intended repair as labels. Data counts are targets, not completed dataset claims.

Split by original template before making variants (e.g. 60/20/20 train/validation/test).
All derived variants of a source stay in one split. Add real files with author consent.
Deduplicate near-identical templates and do not train on the held-out test set.

Compare rules-only, prompted Gemma, and (if built) Gemma plus the trained repair policy.
Metrics: correct operation and target, abstention on unsafe cases, text preservation,
unrelated-change rate, Word-reviewed visual success, acceptance rate, latency, token
usage and actual cost per attempt. Publish numerator/denominator, not just percentages.

## 12. Sponsor plan

Gemma is central to interpreting complaints and contextual repair. Tinker is a potential
primary differentiator if training improves held-out results. Sentry supports a concrete
debugging story: trace diagnosis -> patch -> render -> validate and show a caught failure.
MongoDB stores the version/decision history. Render is a deployment option, not the idea.
Temporal is deferred unless durable job recovery proves necessary. SerpApi and TabPFN
have no essential role in this scoped document repair task.

## 13. Cost and deployment

Start locally. Structural tests require no paid API or database. Gemma local inference
requires adequate hardware and a model download; CPU speed is uncertain until measured.
An initial static frontend can use free hosting, but document rendering plus model
inference cannot be promised on a tiny free web instance. Render free web services sleep
after 15 minutes idle and cron jobs are not a free service type. Do not schedule a public
demo around an unmeasured cold start.

For a public demo, separate lightweight frontend/API from a suitably sized renderer and
model service; use a hard spend budget and any verified available credits. Alternatively
demonstrate local inference in the video and label the public sample experience accurately.
Do not describe precomputed output as a live model run. Tinker training is optional paid
work and must wait for access, supported-model choice and a spending limit.

## 14. Demo story

Open a short report with three labelled injected faults: excessive spacing, inconsistent
alignment and page-break-before. Select one paragraph, describe the fault, show the
model's targeted proposal, compare pages and download the editable result. Then show a
case where the system correctly refuses an uncertain repair. Finish with actual measured
test results and one Sentry trace explaining a bug fixed during development.

Pitch: You finished the report an hour ago. You're still fighting a gap backspace won't
delete. What the Doc?! lets you point at the problem, describe it naturally and preview
a targeted repair. Open-weight AI investigates the formatting; constrained tools make
the change; you keep control of the document.

## 15. Current deliverable versus planned work

Implemented: CLI inspection, six bounded paragraph properties, source hashes, immutable
outputs, basic archive limits and structural tests. Effective-property resolution over
docDefaults and the w:basedOn style chain, with the originating layer reported per value
and caveats where resolution is incomplete. Same-style peer comparison. A generated
Word-openable sample document with labelled faults. LibreOffice before/after rendering,
page images, per-page changed-pixel comparison, and a workflow that patches a copy,
re-verifies preservation against the files on disk and stops at awaiting_review. The
Gemma adapter receives resolved evidence and validates responses, and is now tested
against a live local model: `gemma_adapter.diagnose` returns a strictly validated
diagnosis (status, user-facing explanation, cited evidence, bounded operations, and a
clarification question only where one is required), and `pipeline.py` carries one
complaint through to a rendered candidate that stops at awaiting_review. A model
confidence score is rejected outright rather than recorded, and `status: proposed` with
an empty operations list is refused, so no abstention can be presented as a repair.

Microsoft Word verification of the synthetic sample is implemented and recorded in
TEST_RESULTS.md, including a cross-check of inherited values and before/after page
counts. It is optional, Windows-only, and refuses to run when Word is already open.

Planned: UI, a consented real-world document, benchmarking the larger `gemma4:e4b`,
MongoDB, Sentry, renderer sandboxing, Tinker experiment and hosting. Numbering indentation, table styles
and theme effects are still unresolved and reported as such. No live account or paid
resource was created; LibreOffice, pypdfium2 and the local Word install are free of
additional cost.

## Sources checked on 2026-10-20

- Gemma 3 model card: https://ai.google.dev/gemma/docs/core/model_card_3
- Tinker models and pricing: https://tinker-docs.thinkingmachines.ai/tinker/models/models_and_pricing/
- Tinker first SFT: https://tinker-docs.thinkingmachines.ai/tutorials/basics/first-sft/
- Render free services: https://render.com/docs/free
- Render cron jobs: https://render.com/docs/cronjobs

Sponsor eligibility descriptions come from the challenge text supplied in this conversation.
