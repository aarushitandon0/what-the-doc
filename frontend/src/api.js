/** Client for the local FastAPI service described in ../../API.md.
 * Every path, field name and state here comes from that contract.
 * No fallback to fixtures, no browser model calls, no embedded credentials:
 * when the backend is not reachable the UI says so instead of inventing an answer.
 */
export const API_BASE = (import.meta.env?.VITE_API_BASE ?? '').replace(/\/$/, '');

export class ApiError extends Error {
  constructor(message, {status = 0, code = 'network_error', detail = ''} = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

async function request(path, {signal, ...options} = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {...options, signal});
  } catch (cause) {
    if (cause?.name === 'AbortError') throw cause;
    throw new ApiError('The local API is not reachable.', {code: 'backend_unreachable', detail: String(cause?.message || cause)});
  }
  if (!response.ok) {
    let body = null;
    try { body = await response.json(); } catch { /* an error body is not guaranteed */ }
    throw new ApiError(body?.detail || `Request failed (${response.status})`, {
      status: response.status,
      code: body?.error_code || `http_${response.status}`,
      detail: body?.detail || '',
    });
  }
  if (response.status === 204) return null;
  return response.json();
}

const json = (body) => ({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
const id = (value) => encodeURIComponent(value);

/** Absolute URL for a server-supplied path (preview pages, download). */
export const resolveUrl = (path) => (/^https?:/i.test(path) ? path : `${API_BASE}${path}`);

export const health = (signal) => request('/api/health', {signal});

export const uploadDocument = (file, signal) => {
  const body = new FormData();
  body.append('file', file);
  return request('/api/documents', {method: 'POST', body, signal});
};

export const demoDocument = (signal) => request('/api/demo/document', {method: 'POST', signal});

export const getDocument = (documentId, signal) => request(`/api/documents/${id(documentId)}`, {signal});

export const getParagraphs = (documentId, signal) => request(`/api/documents/${id(documentId)}/paragraphs`, {signal});

/** Explicit page and column breaks, each with the server-issued target id that is
 * the only thing POST /api/jobs will accept. The ids belong to this source revision:
 * re-read this rather than keeping one. Identifying a break is not advice to remove it. */
export const getBreaks = (documentId, signal) => request(`/api/documents/${id(documentId)}/breaks`, {signal});

/** Section boundaries and start types, with a target id for each section and, for
 *  the ones that qualify, the single supported start-type change. Nothing else
 *  about a section can be altered. */
export const getSections = (documentId, signal) => request(`/api/documents/${id(documentId)}/sections`, {signal});

/** The uploaded document rendered to page images, before any repair is proposed.
 *  A formatting fault does not live in a document's text, so extracted paragraphs
 *  cannot show a reader what they complained about. The server renders lazily and
 *  caches, so the first call is slow and later ones are not. `rendered: false` with
 *  an `unavailable_reason` is a normal answer, not an error: without LibreOffice the
 *  workspace falls back to paragraph text and says so. */
export const getDocumentPreview = (documentId, signal) =>
  request(`/api/documents/${id(documentId)}/preview`, {signal});

/** The uploaded .docx bytes, unchanged, as a Blob. The browser draws the instant
 *  preview from these; LibreOffice is far too slow on a CPU to show a document the
 *  moment it opens. */
export async function getOriginal(documentId, signal) {
  let response;
  try {
    response = await fetch(`${API_BASE}/api/documents/${id(documentId)}/original`, {signal});
  } catch (cause) {
    if (cause?.name === 'AbortError') throw cause;
    throw new ApiError('The local API is not reachable.', {code: 'backend_unreachable', detail: String(cause?.message || cause)});
  }
  if (!response.ok) throw new ApiError(`Request failed (${response.status})`, {status: response.status, code: `http_${response.status}`});
  return response.blob();
}

export const getPeers =(documentId, paragraphId, signal) =>
  request(`/api/documents/${id(documentId)}/paragraphs/${id(paragraphId)}/peers`, {signal});

export const createJob = ({documentId, paragraphId, complaint, render = true, expectedSourceSha256, model, timeout, breakTargetId, sectionTargetId}, signal) =>
  request('/api/jobs', {
    ...json({
      document_id: documentId,
      paragraph_id: paragraphId,
      complaint,
      render,
      ...(breakTargetId ? {break_target_id: breakTargetId} : {}),
      // A break and a section are different repairs; the server refuses both at once.
      ...(sectionTargetId ? {section_target_id: sectionTargetId} : {}),
      ...(expectedSourceSha256 ? {expected_source_sha256: expectedSourceSha256} : {}),
      ...(model ? {model} : {}),
      ...(timeout ? {timeout} : {}),
    }),
    signal,
  });

export const getJob = (jobId, signal) => request(`/api/jobs/${id(jobId)}`, {signal});

export const listJobs = (signal) => request('/api/jobs', {signal});

/** The hash is mandatory: it must equal candidate.accept_requires_candidate_sha256,
 * so a candidate nobody looked at cannot be accepted by a stray POST. */
export const acceptJob = (jobId, candidateSha256, reviewer, signal) => {
  if (!candidateSha256) throw new ApiError('A candidate hash is required to accept.', {code: 'invalid_state'});
  return request(`/api/jobs/${id(jobId)}/accept`, {...json({candidate_sha256: candidateSha256, ...(reviewer ? {reviewer} : {})}), signal});
};

export const rejectJob = (jobId, reason, reviewer, signal) =>
  request(`/api/jobs/${id(jobId)}/reject`, {...json({...(reason ? {reason} : {}), ...(reviewer ? {reviewer} : {})}), signal});

export const downloadUrl = (jobId) => `${API_BASE}/api/jobs/${id(jobId)}/download`;

export const TERMINAL_STATES = ['needs_clarification', 'no_supported_repair', 'failed', 'awaiting_review', 'accepted', 'rejected'];
export const isPending = (job) => job?.state === 'queued' || job?.state === 'running';

/** Poll a job to a terminal state. CPU inference is 70-180s, so this is slow by
 * nature; onUpdate lets the UI show elapsed time rather than a fabricated bar. */
export async function pollJob(jobId, {signal, interval = 2500, onUpdate} = {}) {
  for (;;) {
    const job = await getJob(jobId, signal);
    onUpdate?.(job);
    if (!isPending(job)) return job;
    await new Promise((resolve, reject) => {
      const timer = setTimeout(resolve, interval);
      signal?.addEventListener('abort', () => {
        clearTimeout(timer);
        reject(new DOMException('Polling aborted', 'AbortError'));
      }, {once: true});
    });
  }
}

/** Human-readable text for the error codes in API.md. Branch on the code, never the prose. */
export const ERROR_TEXT = {
  backend_unreachable: 'The local API at ' + (API_BASE || 'this origin') + ' is not responding. Start it with: python api.py --cors http://localhost:5173',
  unsupported_upload: 'That file could not be opened as a plain .docx. Tracked changes, content controls, macros and signed packages are refused.',
  upload_too_large: 'That document is over 20 MB.',
  not_found: 'The server does not know about that document or job any more. It forgets jobs when it restarts.',
  invalid_selection: 'Choose a paragraph and describe what looks wrong.',
  model_unavailable: 'No Gemma runtime is available, so nothing can be diagnosed. Nothing is substituted in its place.',
  fixture_mode_disabled: 'This server refuses injected diagnoses.',
  invalid_state: 'That action does not apply to the job in its current state.',
  candidate_integrity_failed: 'The candidate file on disk no longer matches what was reviewed. It was not accepted.',
  invalid_response: 'The model returned an answer the validator refused, so nothing was proposed. Nothing was changed.',
  model_timeout: 'The model did not answer in time.',
  unsupported_formatting: 'The paragraph uses formatting the repair engine will not touch.',
  invalid_operation: 'The proposed operation was refused by the validator.',
  render_failure: 'LibreOffice could not render the pages. Removing a page or column break changes the document’s structure, so it is never offered for review without a successful render of both versions.',
};

/** Human label for one identified break. Two breaks in the same paragraph must never
 * read the same, so the ordinal and the surrounding text both appear. */
export const breakLabel = (record) => {
  if (!record) return '';
  const kind = record.break_type === 'column' ? 'Column break' : record.break_type === 'page' ? 'Page break' : 'Line break';
  return `${kind} ${record.ordinal_in_paragraph || 1} in ¶${record.paragraph_id}`;
};

/** Plain words for why the checks threw an answer away. Branches on the server's
 *  recorded reason; anything not recognised gets the general sentence. */
export const refusalText = (detail = '') => {
  const d = String(detail || '');
  if (/^duplicate evidence entry/i.test(d)) return 'Its answer listed the same piece of evidence twice. Answers like that are thrown away, because in testing they often came with a wrong suggestion.';
  if (/^ineffective operation/i.test(d)) return 'The change it suggested would not change anything on the page: that setting already has that value.';
  if (/evidence does not cite/i.test(d)) return 'It tried to change a setting it had not looked at.';
  if (/must carry no operations|empty plan/i.test(d)) return 'Its answer contradicted itself.';
  if (/invalid JSON|no content/i.test(d)) return 'Its answer was not readable.';
  return 'Its answer did not meet the rules every suggestion has to follow.';
};

/** Failures where Gemma answered but the checks discarded the answer. Nothing went
 *  wrong with the app; there is just no safe change to show. */
export const isRefusal = (job) => job?.state === 'failed' && ['invalid_response', 'invalid_operation'].includes(job?.error_code);

export const errorText = (error) =>
  ERROR_TEXT[error?.code] || error?.detail || error?.message || 'Something went wrong.';
