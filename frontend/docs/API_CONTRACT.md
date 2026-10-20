# Proposed backend integration contract

This is a suggested interface, NOT a description of already implemented Python endpoints. Your local pipeline, diagnosis schema and seven failure codes are authoritative. Reconcile field names and routes with the backend before enabling real uploads.

src/api.js is an unused starting client; the demo has no automatic network mode. Do not connect only the upload call while leaving simulated success downstream.

## Suggested endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| POST | /api/documents | Multipart file; return opaque document_id and import job_id |
| GET | /api/documents/{id} | Filename, paragraph IDs, effective formatting with origins/caveats, preview metadata |
| POST | /api/documents/{id}/diagnoses | paragraph_id + complaint; return job_id (202) |
| GET | /api/jobs/{id} | Job phase, status, mode, diagnosis or structured failure, proposal ID |
| POST | /api/proposals/{id}/review | Explicit accept/reject, server-side integrity validation |
| GET | /api/proposals/{id}/download | Accepted candidate only; integrity rechecked |
| GET | server-issued preview URL | Original/candidate page images with authorized access |

Example diagnosis request:

```json
{"paragraph_id": 1, "complaint": "Too much space above this heading."}
```

Example pending response:

```json
{"job_id":"opaque-id","status":"queued","mode":"live"}
```

Example reviewable job (illustrative, adapt the nested diagnosis to diagnosis.py):

```json
{
  "job_id":"opaque-id",
  "status":"awaiting_review",
  "mode":"live",
  "proposal_id":"opaque-proposal-id",
  "accepted":false,
  "diagnosis":{},
  "previews":{"original":[],"candidate":[]},
  "preservation":{"passed":true,"checks":[]}
}
```

The example is a shape sketch, not valid evidence or a fabricated diagnosis. Populate the real diagnosis/preview/check fields from pipeline outputs. Display unavailable evidence as unavailable.

## State mapping

- queued/running: actual phase text, elapsed time, cancellable only if the backend supports cancellation. Never invent percentages.
- needs_clarification: render a validated model question and allow a new complaint.
- no_supported_repair: explain the limit and keep the original available.
- failed: map the real seven failure codes to readable explanations; keep error details out of casual document logs. invalid_response remains a failure.
- awaiting_review: load authoritative previews and evidence; accept/reject available only after validation.
- accepted/rejected: reflect server response, not an optimistic client assumption.

The UI’s current clarification page describes a demo limitation. Do not use it as a replacement for a malformed live diagnosis.

## Long-running work

CPU inference may take well over a minute. Create asynchronous jobs and poll with a bounded interval/backoff, timeout/retry feedback, and AbortController for abandoned requests. Aborting a fetch does not cancel server inference. Persist opaque job IDs if reload recovery is required. Keep transient connection failures distinct from a failed repair.

## Real preview and download

Replace Paper with images from LibreOffice/Word rendering; retain paragraph selection through a separate evidence list if image-to-paragraph coordinates are unavailable. Never assume HTML sample positions correspond to DOCX positions. Handle changed page counts, removed pages, unsupported render formats and missing preview assets. For page-break repair, show removed pages explicitly.

On acceptance and download, recheck candidate and original hashes and preservation on the server. Reject tampered, stale or unaccepted candidates. Downloads need the actual .docx content type and a safe filename; the demo’s JSON download must be removed from live mode.

## Local boundary

For development, use a Vite /api proxy to a localhost FastAPI service, or explicitly allow the exact local frontend origin in backend CORS. Do not add browser API secrets. Validate the uploaded ZIP container/OOXML on the server: extension checks in this frontend are UX only. Use server-owned paths, opaque IDs, upload limits, bounded jobs and explicit retention. Current renderer sandboxing is not provided by this frontend or this document.
