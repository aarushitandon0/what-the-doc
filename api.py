"""A thin HTTP adapter over the existing pipeline. It adds no repair logic of its own.

Every diagnosis, validation, patch and preservation check in this module is performed
by `pipeline.run`, `engine.validate` and `workflow.check_invariants` exactly as the CLI
drives them. The adapter's whole job is to carry a document in, hand back what the
pipeline already produces, and hold the one thing the CLI leaves to a human: an
explicit acceptance.

Four properties are the reason this file exists rather than a second repair engine:

  * **Acceptance is never implied.** A finished job is `awaiting_review`, never
    `accepted`. `accepted` is reachable only through POST /accept carrying the exact
    candidate hash the caller reviewed, and the candidate's integrity is re-checked
    both at acceptance and again before the bytes are downloaded.
  * **Fixture answers are never silently substituted for live ones.** Every job
    records `mode`, which is `live` unless the caller injected a diagnosis, and
    injection is refused outright unless the server was started with
    `--allow-fixture-mode`. When no Gemma runtime is reachable, a live job is refused
    with `model_unavailable`. Nothing falls back to a canned answer.
  * **Paths are server-controlled.** Callers name documents and jobs only by opaque
    32-hex ids, which are pattern-checked before they touch the filesystem, and an
    uploaded file's own name is used only to reject the upload, never to build a path.
  * **Routine logs carry ids, states and codes.** No complaint text, paragraph text,
    uploaded filename or stored path is logged. (Note that `pipeline.json` on disk does
    record the user's complaint, as it does for the CLI; that is a file in the work
    directory, not a log line.)

Not provided here, deliberately: authentication, rate limiting, a database, any
network egress, and any sandbox around LibreOffice. This binds to 127.0.0.1 and is a
local development service. See API.md.
"""
import argparse
import logging
import os
import re
import secrets
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, Path as PathParam, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

import diagnosis
import engine
import gemma_adapter
import observability
import pipeline
import precheck
import render as render_module
import workflow

log = logging.getLogger('wtd.api')

# ---------------------------------------------------------------------------
# States. Eight, mapped onto what the diagnosis schema and the pipeline actually
# produce; none of them is invented for the interface.
# ---------------------------------------------------------------------------
QUEUED = 'queued'                              # accepted, not yet started
RUNNING = 'running'                            # the pipeline is executing
NEEDS_CLARIFICATION = 'needs_clarification'    # diagnosis status, verbatim
NO_SUPPORTED_REPAIR = 'no_supported_repair'    # diagnosis status no_change | unsupported
FAILED = 'failed'                              # pipeline outcome failed, with a code
AWAITING_REVIEW = 'awaiting_review'            # a candidate exists; nothing is accepted
ACCEPTED = 'accepted'                          # a human accepted it through POST /accept
REJECTED = 'rejected'                          # a human rejected it through POST /reject

TERMINAL_BEFORE_REVIEW = (NEEDS_CLARIFICATION, NO_SUPPORTED_REPAIR, FAILED)

# `no_change` and `unsupported` both mean "no repair is being offered", but they are
# different answers and the real one is reported alongside the state, never replaced.
_ABSTENTION_STATE = {'needs_clarification': NEEDS_CLARIFICATION,
                     'no_change': NO_SUPPORTED_REPAIR,
                     'unsupported': NO_SUPPORTED_REPAIR}

ID_PATTERN = re.compile(r'^[0-9a-f]{32}$')
MAX_UPLOAD_BYTES = engine.MAX_BYTES          # the same 20 MiB cap the engine enforces
# Multipart framing (boundaries, headers) makes the body larger than the file, so the
# Content-Length pre-check allows a little slack and the exact cap is enforced on the
# bytes actually written.
MARGIN = 64 * 1024
MAX_COMPLAINT_CHARS = 2000
UPLOAD_CHUNK = 256 * 1024
DOCX_CONTENT_TYPES = frozenset({
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/octet-stream',              # browsers send this for a dragged file
    '', None})

LIVE = pipeline.LIVE
FIXTURE = pipeline.FIXTURE


class ApiError(HTTPException):
    """An HTTP error that always carries one machine-readable code.

    The frontend branches on `error_code`, never on the prose, and the codes are the
    same ones `diagnosis` defines wherever a diagnosis failure is what went wrong.
    """

    def __init__(self, status_code, code, detail):
        super().__init__(status_code=status_code,
                         detail={'error_code': code, 'detail': detail})
        self.code = code


# Codes that are not diagnosis failures but transport/validation failures.
UNSUPPORTED_UPLOAD = 'unsupported_upload'
UPLOAD_TOO_LARGE = 'upload_too_large'
NOT_FOUND = 'not_found'
INVALID_STATE = 'invalid_state'
CANDIDATE_INTEGRITY_FAILED = 'candidate_integrity_failed'
FIXTURE_MODE_DISABLED = 'fixture_mode_disabled'
NO_DOCUMENT_REPAIR = 'no_document_repair'


# ---------------------------------------------------------------------------
# Server-controlled storage
# ---------------------------------------------------------------------------
class Store:
    """Opaque ids to server-chosen paths, plus the in-process job records.

    Deliberately in-memory for the job table: no database is introduced here. Restart
    loses the job list; the documents and candidates on disk survive, and the originals
    are never written to in the first place.
    """

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.documents_dir = self.root / 'documents'
        self.jobs_dir = self.root / 'jobs'
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.documents = {}
        self.jobs = {}
        self.lock = threading.Lock()

    @staticmethod
    def new_id():
        return secrets.token_hex(16)

    def document_path(self, document_id):
        """The one place a document id becomes a path. Never uses a client filename."""
        return self.documents_dir / document_id / 'original.docx'

    def document_preview_dir(self, document_id):
        """Where this document's own page renders live, beside the original."""
        return self.documents_dir / document_id / 'preview'

    def job_dir(self, job_id):
        return self.jobs_dir / job_id


def _checked_id(value, kind):
    if not isinstance(value, str) or not ID_PATTERN.match(value):
        # A traversal attempt never reaches the filesystem: it is not 32 hex characters.
        raise ApiError(404, NOT_FOUND, 'No such %s' % kind)
    return value


# ---------------------------------------------------------------------------
# Report shaping
# ---------------------------------------------------------------------------
def _state_of(report):
    """Map a finished pipeline report onto one of the eight states."""
    outcome = report.get('outcome')
    if outcome == pipeline.AWAITING_REVIEW:
        return AWAITING_REVIEW
    if outcome == pipeline.FAILED:
        return FAILED
    return _ABSTENTION_STATE.get(outcome, FAILED)


def _preview_index(report):
    """Page image paths, keyed before/after, in page order. Empty when not rendered."""
    preview = report.get('preview') or {}
    return {'before': [p['image'] for p in (preview.get('original_pages') or [])],
            'after': [p['image'] for p in (preview.get('candidate_pages') or [])]}


def _public_job(job):
    """What a client is told about a job. Never includes a filesystem path."""
    report = job.get('report') or {}
    found = report.get('diagnosis')
    preview = job.get('preview_index') or {'before': [], 'after': []}
    body = {
        'job_id': job['job_id'],
        'document_id': job['document_id'],
        'state': job['state'],
        'mode': job['mode'],
        # A fixed rule (precheck.py) can answer before the model is asked. That is not a
        # model answer, and must never be labelled as one.
        'answered_by': report.get('answered_by') or ('model' if job['mode'] == LIVE
                                                     else 'fixture'),
        'is_live_model_answer': job['mode'] == LIVE and report.get('answered_by') != 'rule',
        'paragraph_id': job['paragraph_id'],
        # 'document' for a whole-document break repair, which may change several
        # paragraphs; paragraph_id is then only the first of them.
        'scope': job.get('scope', 'paragraph'),
        # Breaks of the named kind the whole-document repair leaves, each with why.
        'kept_breaks': report.get('kept_breaks') or [],
        'created_at': job['created_at'],
        'updated_at': job['updated_at'],
        'accepted': job['state'] == ACCEPTED,
        'error_code': report.get('error_code'),
        'error_detail': report.get('error'),
        'diagnosis': None,
        'proposal': None,
        'ineffective_operations': report.get('ineffective_operations', []),
        'preview': {'rendered': bool(preview['before'] or preview['after']),
                    'before_page_count': len(preview['before']),
                    'after_page_count': len(preview['after']),
                    'before_urls': ['/api/jobs/%s/preview/before/%d' % (job['job_id'], i)
                                    for i in range(len(preview['before']))],
                    'after_urls': ['/api/jobs/%s/preview/after/%d' % (job['job_id'], i)
                                   for i in range(len(preview['after']))],
                    'renderer_available': (report.get('renderer') or {}).get('available'),
                    'note': 'Rendered by LibreOffice. It approximates Word; it is not Word.'},
        'visual_comparison': report.get('visual_comparison'),
        'operation_kind': report.get('operation_kind'),
        'plan_schema_version': report.get('plan_schema_version'),
        'break_target_id': job.get('break_target_id'),
        # Which break is being removed, with the text either side of it, so a reviewer
        # can see the actual target rather than an id.
        'break_targets': report.get('break_targets'),
        'section_target_id': job.get('section_target_id'),
        # Which section is being changed, with its boundaries and what it governs, so
        # a reviewer sees the actual section rather than an id.
        'section_targets': report.get('section_targets'),
        'rendering_required': report.get('rendering_required'),
        'rendering_satisfied': report.get('rendering_satisfied'),
        'candidate': None,
        'invariants': report.get('invariants'),
        'invariant_failures': report.get('invariant_failures'),
        'model': (report.get('diagnosis_metadata') or {}).get('model'),
        'latency_seconds': (report.get('diagnosis_metadata') or {}).get('latency_seconds'),
        # How many times the model was asked (gemma_adapter.DIAGNOSIS_ATTEMPTS). It
        # ships at 1, so re-sampling is off and a live diagnosis reports one attempt
        # of one; they are kept
        # so a re-enabled budget would show a reviewer that an answer was not the
        # model's first, and that a failed run spent its whole budget.
        'attempts': (report.get('diagnosis_metadata') or {}).get('attempts'),
        'max_attempts': (report.get('diagnosis_metadata') or {}).get('max_attempts'),
        'discarded_attempts': (report.get('diagnosis_metadata')
                               or {}).get('discarded_attempts') or [],
        'decision': job.get('decision'),
    }
    if found:
        body['diagnosis'] = {
            'status': found['status'],
            'explanation': found['explanation'],
            'evidence': found['evidence'],
            'question': found['question'],
            # Exact repeats the validator dropped from the model's evidence list, so a
            # normalised answer is never presented as if it arrived that way.
            'evidence_duplicates_removed': found.get('evidence_duplicates_removed', 0),
        }
        if found['operations']:
            # After preparation, show exactly the writes used for this candidate.
            # The full model diagnosis stays in the private pipeline report.
            body['proposal'] = report.get('plan') or {
                'source_sha256': found['source_sha256'],
                'operations': found['operations']}
    if report.get('candidate'):
        body['candidate'] = {
            'candidate_sha256': report.get('candidate_sha256'),
            'reused': report.get('candidate_reused'),
            # The hash the caller must echo back to accept. Acceptance of a candidate
            # the caller never saw is impossible by construction.
            'accept_requires_candidate_sha256': report.get('candidate_sha256'),
            'download_available': job['state'] == ACCEPTED,
        }
    if job['state'] == AWAITING_REVIEW:
        body['note'] = ('A candidate exists for review and is NOT accepted. Acceptance '
                        'is a separate call. The original file is untouched.')
    elif job['state'] == NO_SUPPORTED_REPAIR:
        body['note'] = ('No repair was proposed, so no candidate exists and there is '
                        'nothing to accept.')
    elif job['state'] == NEEDS_CLARIFICATION:
        body['note'] = 'The model needs one answer before it can diagnose this.'
    return body


# ---------------------------------------------------------------------------
# Integrity, re-checked rather than remembered
# ---------------------------------------------------------------------------
def _check_candidate(job):
    """Re-run the preservation invariants against the files on disk.

    Called at acceptance and again before a download. A remembered "it passed once" is
    not evidence that the bytes on disk are still that candidate.
    """
    report = job.get('report') or {}
    candidate = Path(report.get('candidate') or '')
    source = Path(job['source_path'])
    problems = []
    if not candidate.is_file():
        problems.append('the candidate file is missing')
    elif engine.signature(candidate) != report.get('candidate_sha256'):
        problems.append('the candidate on disk no longer matches its recorded sha256')
    if not source.is_file():
        problems.append('the original document is missing')
    elif engine.signature(source) != report.get('source_sha256'):
        problems.append('the original document has changed since the candidate was built')
    if problems:
        return problems
    checks, failures = workflow.check_invariants(
        source, candidate, report['plan'], report['source_sha256'])
    job['last_integrity_check'] = {'at': time.time(), 'checks': checks,
                                   'failures': failures}
    return failures


def _revalidate_candidate(job, stage='review.integrity'):
    with observability.span(stage) as trace:
        failures = _check_candidate(job)
        trace.set(outcome='failed' if failures else 'passed',
                  error_code=CANDIDATE_INTEGRITY_FAILED if failures else None)
        return failures


def _trace_metadata(job):
    metadata = (job.get('report') or {}).get('diagnosis_metadata') or {}
    return {'job_id': job['job_id'], 'mode': job['mode'],
            'operation_kind': (job.get('report') or {}).get('operation_kind'),
            'model': ('injected-fixture' if job['mode'] == FIXTURE else
                      metadata.get('model') or job['model'] or os.environ.get('GEMMA_MODEL'))}


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
def create_app(root=None, allow_fixture_mode=False, allow_origins=None, max_workers=1):
    """Build the app. `allow_fixture_mode` is off unless a developer turns it on."""
    observability.initialize()
    store = Store(root or os.environ.get('WTD_API_ROOT') or 'work/api')
    # One worker: CPU inference is the bottleneck and running two at once only makes
    # both slower. Jobs therefore genuinely queue, which is why `queued` is a state.
    executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='wtd-job')
    app = FastAPI(title='What the Doc?! local API', version='0.1.0',
                  description='Local document formatting diagnosis. Not for public exposure.')
    app.state.store = store
    app.state.allow_fixture_mode = bool(allow_fixture_mode)
    app.state.executor = executor

    if allow_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(allow_origins),
                           allow_methods=['*'], allow_headers=['*'])

    @app.middleware('http')
    async def _refuse_oversized_body(request, call_next):
        """Refuse a declared oversized body before the form parser buffers it.

        The streaming cap in the upload handler runs only after FastAPI has already
        read the multipart body to a spool file, so by itself it bounds what is
        *stored*, not what is *buffered*. This bounds the buffer too, for any client
        that sends a Content-Length. A chunked upload without one still falls through
        to the streaming cap, which is why both exist.
        """
        declared = request.headers.get('content-length')
        if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES + MARGIN:
            return JSONResponse(status_code=413, content={
                'error_code': UPLOAD_TOO_LARGE,
                'detail': 'Maximum upload size is %d bytes' % MAX_UPLOAD_BYTES})
        return await call_next(request)

    @app.exception_handler(HTTPException)
    async def _http_error(request, exc):
        detail = exc.detail
        if not isinstance(detail, dict):
            detail = {'error_code': 'http_error', 'detail': str(detail)}
        return JSONResponse(status_code=exc.status_code, content=detail)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request, exc):
        # FastAPI's own 422 would otherwise be the one response without an error_code,
        # and the contract says a client may always branch on that field.
        return JSONResponse(status_code=422, content={
            'error_code': 'invalid_request',
            'detail': 'The request body or path did not match the expected shape',
            'fields': [{'location': list(e.get('loc', [])), 'problem': e.get('msg')}
                       for e in exc.errors()][:10]})

    @app.exception_handler(Exception)
    async def _unhandled_error(request, exc):
        # The message is not echoed: it can carry a stored path or document text.
        log.error('request crashed type=%s', type(exc).__name__)
        observability.capture_error(exc, site='request.unhandled',
                                    error_code='internal_error')
        return JSONResponse(status_code=500, content={
            'error_code': 'internal_error',
            'detail': 'The service hit an unexpected error.'})

    # -- health -------------------------------------------------------------
    @app.get('/api/health')
    def health():
        """Whether a live run is possible right now, and why not when it is not."""
        runtime = gemma_adapter.runtime_status()
        renderer = render_module.renderer_status()
        return {
            'service': 'what-the-doc',
            'model_runtime': runtime,
            'live_diagnosis_available': bool(runtime['available']
                                             and runtime.get('model_installed')),
            'renderer': renderer,
            'fixture_mode_allowed': app.state.allow_fixture_mode,
            'note': ('When live_diagnosis_available is false, job creation is refused '
                     'with model_unavailable. No canned answer is ever substituted.'),
        }

    # -- documents ----------------------------------------------------------
    @app.post('/api/documents', status_code=201)
    async def upload_document(file: UploadFile = File(...)):
        """Store one .docx under a server-chosen path and return its opaque id."""
        name = file.filename or ''
        # The client's filename is used only to reject the upload. It never becomes a
        # path component, so '../' in it is inert as well as refused.
        if not name.lower().endswith('.docx'):
            raise ApiError(415, UNSUPPORTED_UPLOAD,
                           'Only .docx is supported. .doc, .pdf and .rtf are not.')
        if file.content_type not in DOCX_CONTENT_TYPES:
            raise ApiError(415, UNSUPPORTED_UPLOAD,
                           'Unexpected content type %r' % file.content_type)
        document_id = store.new_id()
        target = store.document_path(document_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        try:
            with open(target, 'wb') as handle:
                while True:
                    chunk = await file.read(UPLOAD_CHUNK)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_UPLOAD_BYTES:
                        raise ApiError(413, UPLOAD_TOO_LARGE,
                                       'Maximum upload size is %d bytes' % MAX_UPLOAD_BYTES)
                    handle.write(chunk)
            # engine.open_package is the real gate: ZIP bomb caps, entry count, tracked
            # changes, content controls, VBA, signatures and duplicate entries. A file
            # that only looks like a .docx is rejected here, not when a job runs.
            engine.open_package(target)
            inspection = engine.inspect(target)
        except ApiError:
            shutil.rmtree(target.parent, ignore_errors=True)
            raise
        except Exception as error:
            shutil.rmtree(target.parent, ignore_errors=True)
            log.info('upload rejected id=%s reason=%s', document_id, type(error).__name__)
            raise ApiError(415, UNSUPPORTED_UPLOAD,
                           'Not a usable .docx: %s' % error)
        record = {
            'document_id': document_id,
            'source_sha256': inspection['source_sha256'],
            'paragraph_count': len(inspection['paragraphs']),
            'has_styles_part': inspection['has_styles_part'],
            'default_paragraph_style': inspection['default_paragraph_style'],
            'size_bytes': written,
            'uploaded_at': time.time(),
            'origin': 'upload',
        }
        with store.lock:
            store.documents[document_id] = record
        log.info('document stored id=%s bytes=%d paragraphs=%d',
                 document_id, written, record['paragraph_count'])
        return record

    @app.post('/api/demo/document', status_code=201)
    def demo_document():
        """Build the synthetic sample from `fixtures.py` and register it.

        Clearly labelled: this is a generated demo document with known planted faults,
        not a user document, and the label travels with it in every response.
        """
        import fixtures
        document_id = store.new_id()
        target = store.document_path(document_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        fixtures.build(target)
        inspection = engine.inspect(target)
        record = {
            'document_id': document_id,
            'source_sha256': inspection['source_sha256'],
            'paragraph_count': len(inspection['paragraphs']),
            'has_styles_part': inspection['has_styles_part'],
            'default_paragraph_style': inspection['default_paragraph_style'],
            'size_bytes': target.stat().st_size,
            'uploaded_at': time.time(),
            'origin': 'synthetic_demo_fixture',
            'is_demo_document': True,
            'planted_faults': {str(k): v for k, v in fixtures.FAULTS.items()},
            'note': ('Generated by fixtures.py with three labelled faults. Diagnoses of '
                     'it are still live model answers unless the job says otherwise.'),
        }
        with store.lock:
            store.documents[document_id] = record
        return record

    def _document(document_id):
        _checked_id(document_id, 'document')
        record = store.documents.get(document_id)
        if record is None or not store.document_path(document_id).is_file():
            raise ApiError(404, NOT_FOUND, 'No such document')
        return record

    @app.get('/api/documents/{document_id}')
    def get_document(document_id: str = PathParam(...)):
        return _document(document_id)

    @app.get('/api/documents/{document_id}/paragraphs')
    def get_paragraphs(document_id: str = PathParam(...)):
        """Per-paragraph effective formatting with the layer that supplied each value.

        This is `engine.inspect` unchanged, including its warning: an
        `application_default` origin means nothing set the value, so it is null and
        must not be rendered as 0.
        """
        _document(document_id)
        inspection = engine.inspect(store.document_path(document_id))
        return {'document_id': document_id,
                'source_sha256': inspection['source_sha256'],
                'has_styles_part': inspection['has_styles_part'],
                'default_paragraph_style': inspection['default_paragraph_style'],
                'paragraphs': inspection['paragraphs'],
                'warning': inspection['warning']}

    @app.get('/api/documents/{document_id}/paragraphs/{paragraph_id}/peers')
    def get_peers(document_id: str = PathParam(...), paragraph_id: int = PathParam(...)):
        """How paragraphs sharing this style resolve. Evidence, never proof."""
        _document(document_id)
        try:
            return engine.peers(store.document_path(document_id), paragraph_id)
        except ValueError as error:
            raise ApiError(404, diagnosis.INVALID_SELECTION, str(error))

    @app.get('/api/documents/{document_id}/breaks')
    def get_breaks(document_id: str = PathParam(...)):
        """Explicit page and column breaks, each with a target id and its eligibility.

        Read-only. The ids here are the only ones POST /api/jobs and the engine will
        accept, and they are issued for this source revision: re-read this endpoint
        rather than reusing an id from an earlier revision of the same document.
        Identifying a break is not a recommendation to remove it.
        """
        _document(document_id)
        with observability.span('structure.inspect'):
            return engine.inspect_breaks(store.document_path(document_id))

    # -- the document as it stands ------------------------------------------
    # A reader cannot check a repair against a document they have never seen. Before
    # any job runs, the only view of an upload was its extracted text, which is
    # precisely the layer formatting faults do not live in: a 48pt space above a
    # heading looks identical to a 12pt one in a list of paragraphs. These two
    # endpoints render the *original* so the page can show the document before
    # anything is proposed, and so "before" and "after" are the same kind of picture.
    #
    # Rendering is lazy and cached: LibreOffice costs seconds, an upload that is never
    # looked at should not pay it, and the original is immutable, so one render stays
    # valid for the life of the document.
    _preview_locks = {}

    def _document_preview(document_id):
        """Page image paths for one original, rendering once on first request."""
        record = _document(document_id)
        with store.lock:
            lock = _preview_locks.setdefault(document_id, threading.Lock())
        with lock:
            cached = record.get('preview_pages')
            if cached is not None:
                return cached
            if not render_module.find_soffice():
                record['preview_pages'] = []
                record['preview_unavailable'] = (
                    'LibreOffice was not found, so the document cannot be rendered. '
                    'Paragraph text is still shown.')
                return []
            out = store.document_preview_dir(document_id)
            try:
                pdf = render_module.docx_to_pdf(store.document_path(document_id), out)
                pages = render_module.pdf_pages(pdf, out, 'original')
            except (render_module.RendererUnavailable,
                    render_module.RenderFailed) as error:
                # A renderer that fails is reported, not raised: the document is still
                # usable for a structural diagnosis, which is what the rest of the
                # workspace does with it.
                log.info('document preview failed id=%s reason=%s',
                         document_id, type(error).__name__)
                record['preview_pages'] = []
                record['preview_unavailable'] = str(error)
                return []
            record['preview_pages'] = [page['image'] for page in pages]
            record['preview_page_count'] = len(pages)
            return record['preview_pages']

    @app.get('/api/documents/{document_id}/preview')
    def get_document_preview(document_id: str = PathParam(...)):
        """How many pages the original renders to, and where to fetch each one."""
        pages = _document_preview(document_id)
        record = store.documents[document_id]
        return {
            'document_id': document_id,
            'rendered': bool(pages),
            'page_count': len(pages),
            'urls': ['/api/documents/%s/preview/%d' % (document_id, i)
                     for i in range(len(pages))],
            'renderer_available': render_module.renderer_status()['available'],
            'unavailable_reason': record.get('preview_unavailable'),
            'note': ('The document as uploaded, rendered by LibreOffice. It '
                     'approximates Word; it is not Word. Nothing here is a repair.'),
        }

    @app.get('/api/documents/{document_id}/preview/{index}')
    def get_document_preview_page(document_id: str = PathParam(...),
                                  index: int = PathParam(...)):
        """One rendered page of the original. The path comes from the renderer."""
        pages = _document_preview(document_id)
        if not 0 <= index < len(pages):
            raise ApiError(404, NOT_FOUND, 'No such preview page')
        image = Path(pages[index]).resolve()
        root = store.document_preview_dir(document_id).resolve()
        # Defence in depth, exactly as the job previews do: a page is only ever served
        # from inside this document's own render directory.
        if not image.is_file() or root not in image.parents:
            raise ApiError(404, NOT_FOUND, 'No such preview page')
        return FileResponse(image, media_type='image/png')

    @app.get('/api/documents/{document_id}/original')
    def get_document_original(document_id: str = PathParam(...)):
        """The uploaded .docx bytes, unchanged, so the browser can draw a preview at once.

        LibreOffice takes tens of seconds on a CPU-only machine, so the workspace draws
        the file itself in the browser and keeps LibreOffice for the before/after
        comparison, where both sides must come from the same renderer. The path is the
        server's own, never a client value, and the file is never written to.
        """
        _document(document_id)
        return FileResponse(store.document_path(document_id),
                            media_type=('application/vnd.openxmlformats-officedocument.'
                                        'wordprocessingml.document'),
                            headers={'Cache-Control': 'no-store'})

    @app.get('/api/documents/{document_id}/sections')
    def get_sections(document_id: str = PathParam(...)):
        """Section boundaries, start types, and which sections may be converted.

        Exactly one conversion exists: a supported `nextPage` start may become
        `continuous`. Every section carries a revision-bound `target_id`, and every
        section that cannot be converted carries the rule it failed. The ids here are
        the only ones POST /api/jobs and the engine accept, and they are issued for
        this source revision: re-read this endpoint rather than reusing an older id.
        Eligibility is not a recommendation - a section break is usually deliberate.
        """
        _document(document_id)
        with observability.span('structure.inspect'):
            return engine.inspect_sections(store.document_path(document_id))

    # -- jobs ---------------------------------------------------------------
    def _execute_job(job_id):
        job = store.jobs[job_id]
        job['state'], job['updated_at'] = RUNNING, time.time()
        log.info('job running id=%s mode=%s', job_id, job['mode'])
        try:
            report = pipeline.run(
                job['source_path'], job['paragraph_id'], job['complaint'],
                store.job_dir(job_id), diagnoser=job.pop('_diagnoser', None),
                mode=job['mode'], do_render=job['render'],
                model=job['model'], timeout=job['timeout'],
                break_target_id=job.get('break_target_id'),
                section_target_id=job.get('section_target_id'),
                scope=job.get('scope', 'paragraph'))
        except Exception as error:                     # pragma: no cover - defensive
            job['state'] = FAILED
            job['report'] = {'outcome': pipeline.FAILED,
                             'error_code': 'internal_error',
                             'error': '%s: %s' % (type(error).__name__, error),
                             'accepted': False}
            job['updated_at'] = time.time()
            log.error('job crashed id=%s code=internal_error type=%s',
                      job_id, type(error).__name__)
            observability.capture_error(error, site='job.execute',
                                        error_code='internal_error',
                                        job_id=job_id, mode=job['mode'])
            return
        job['report'] = report
        job['preview_index'] = _preview_index(report)
        job['state'] = _state_of(report)
        job['updated_at'] = time.time()
        # The code, not the prose: the prose can quote the model, which can quote the
        # document. Full detail stays in the report and is served over the API.
        log.info('job finished id=%s state=%s code=%s latency=%s',
                 job_id, job['state'], report.get('error_code'),
                 (report.get('diagnosis_metadata') or {}).get('latency_seconds'))

    def _run_job(job_id):
        # Start in the actual executor thread: submission/request scopes do not
        # reliably survive ThreadPoolExecutor. Human review uses later short traces
        # correlated by this opaque id, never an open transaction waiting for a user.
        job = store.jobs[job_id]
        with observability.transaction('repair.execute', **_trace_metadata(job)) as trace:
            _execute_job(job_id)
            trace.set(outcome=job['state'],
                      error_code=(job.get('report') or {}).get('error_code'))

    @app.post('/api/jobs', status_code=202)
    def create_job(payload: dict = Body(...)):
        """Queue one diagnosis. Returns immediately; poll GET /api/jobs/{id}.

        A `fixture_diagnosis` injects a pre-made answer instead of calling the model.
        It is refused unless the server was started with --allow-fixture-mode, it is
        validated by the same `diagnosis.check` a model answer goes through, and the
        resulting job is stamped mode `fixture` in every response.
        """
        if not isinstance(payload, dict):
            raise ApiError(400, 'invalid_request', 'Expected a JSON object')
        document_id = payload.get('document_id')
        record = _document(document_id)
        scope = payload.get('scope', 'paragraph')
        if scope not in ('paragraph', 'document'):
            raise ApiError(400, 'invalid_request', "scope must be 'paragraph' or 'document'")
        if scope == 'document':
            return _create_document_job(payload, document_id, record)
        paragraph_id = payload.get('paragraph_id')
        if type(paragraph_id) is not int or paragraph_id < 0:
            raise ApiError(400, diagnosis.INVALID_SELECTION,
                           'paragraph_id must be a non-negative integer')
        if paragraph_id >= record['paragraph_count']:
            raise ApiError(400, diagnosis.INVALID_SELECTION,
                           'This document has %d top-level body paragraphs'
                           % record['paragraph_count'])
        complaint = payload.get('complaint')
        if not isinstance(complaint, str) or not complaint.strip():
            raise ApiError(400, diagnosis.INVALID_SELECTION,
                           'A complaint describing the problem is required')
        if len(complaint) > MAX_COMPLAINT_CHARS:
            raise ApiError(400, diagnosis.INVALID_SELECTION,
                           'complaint exceeds %d characters' % MAX_COMPLAINT_CHARS)
        # Optional, as IDEATION section 7 proposes: the caller states the revision it
        # was looking at. Stored originals are immutable here, so this is a consistency
        # check rather than a race guard, and it is skipped when not supplied.
        expected = payload.get('expected_source_sha256')
        if expected is not None and expected != record['source_sha256']:
            raise ApiError(409, INVALID_STATE,
                           'expected_source_sha256 does not match this document')

        # Optional: the break the person pointed at in the interface. It is checked
        # against this document's current breaks here, so a stale or invented id is
        # refused before a model is called rather than after.
        break_target_id = payload.get('break_target_id')
        if break_target_id is not None and payload.get('section_target_id') is not None:
            raise ApiError(400, diagnosis.INVALID_SELECTION,
                           'Select a break or a section, not both: they are different '
                           'repairs and are approved separately.')
        if break_target_id is not None:
            if not isinstance(break_target_id, str):
                raise ApiError(400, diagnosis.INVALID_SELECTION,
                               'break_target_id must be a string')
            with observability.span('structure.inspect'):
                found = engine.inspect_breaks(store.document_path(document_id))
            selected = next((b for b in found['breaks']
                             if b.get('target_id') == break_target_id), None)
            if selected is None:
                raise ApiError(409, INVALID_STATE,
                               'break_target_id does not match any break in this '
                               'document revision. Re-read GET /breaks.')
            if not selected['edit_eligible']:
                raise ApiError(400, diagnosis.UNSUPPORTED_FORMATTING,
                               selected['ineligible_reason'])
            if selected['paragraph_id'] != paragraph_id:
                raise ApiError(400, diagnosis.INVALID_SELECTION,
                               'That break is in paragraph %d, not the selected '
                               'paragraph %d' % (selected['paragraph_id'], paragraph_id))

        # Optional: the section the person pointed at. Checked against this
        # document's current sections here, so a stale, ineligible or invented id is
        # refused before a model is called rather than after. A section and a break
        # are different things and cannot both be the subject of one complaint.
        section_target_id = payload.get('section_target_id')
        if section_target_id is not None:
            if not isinstance(section_target_id, str):
                raise ApiError(400, diagnosis.INVALID_SELECTION,
                               'section_target_id must be a string')
            with observability.span('structure.inspect'):
                found_sections = engine.inspect_sections(
                    store.document_path(document_id))
            chosen = next((x for x in found_sections['sections']
                           if x.get('target_id') == section_target_id), None)
            if chosen is None:
                raise ApiError(409, INVALID_STATE,
                               'section_target_id does not match any section in this '
                               'document revision. Re-read GET /sections.')
            if not chosen['edit_eligible']:
                raise ApiError(400, diagnosis.UNSUPPORTED_FORMATTING,
                               chosen['ineligible_reason'])

        fixture = payload.get('fixture_diagnosis')
        diagnoser, mode = None, LIVE
        if fixture is not None:
            if not app.state.allow_fixture_mode:
                raise ApiError(403, FIXTURE_MODE_DISABLED,
                               'Injected diagnoses are refused. Start the server with '
                               '--allow-fixture-mode to use them in development.')
            diagnoser, mode = _fixture_diagnoser(
                fixture, paragraph_id, record['source_sha256'],
                store.document_path(document_id), break_target_id,
                section_target_id), FIXTURE
        else:
            runtime = gemma_adapter.runtime_status()
            if not (runtime['available'] and runtime.get('model_installed')):
                # Explicit runtime-unavailable. There is no mock to fall back to.
                raise ApiError(503, diagnosis.MODEL_UNAVAILABLE,
                               runtime.get('reason') or 'No Gemma runtime is reachable')

        job_id = store.new_id()
        job = {
            'job_id': job_id, 'document_id': document_id,
            'source_path': str(store.document_path(document_id)),
            'source_sha256': record['source_sha256'],
            'paragraph_id': paragraph_id, 'complaint': complaint,
            'mode': mode, 'state': QUEUED,
            'render': bool(payload.get('render', True)),
            'model': payload.get('model'), 'timeout': payload.get('timeout'),
            'break_target_id': break_target_id,
            'section_target_id': section_target_id,
            'created_at': time.time(), 'updated_at': time.time(),
            'report': None, 'preview_index': None, 'decision': None,
            '_diagnoser': diagnoser,
        }
        with store.lock:
            store.jobs[job_id] = job
        app.state.executor.submit(_run_job, job_id)
        log.info('job queued id=%s document=%s mode=%s', job_id, document_id, mode)
        return _public_job(job)

    def _create_document_job(payload, document_id, record):
        """Queue one whole-document repair, answered by a fixed rule in one candidate:
        every unwanted page or column break, or every clearly misaligned paragraph.

        Answered by precheck.document_repair, not the model, and only for those two
        complaints. Anything else is refused with `no_document_repair` *before* a job
        exists, so the client can fall back to checking paragraph by paragraph. The job
        then runs the same pipeline: validation, invariants, render, human review.
        """
        complaint = payload.get('complaint')
        if not isinstance(complaint, str) or not complaint.strip():
            raise ApiError(400, diagnosis.INVALID_SELECTION,
                           'A complaint describing the problem is required')
        if len(complaint) > MAX_COMPLAINT_CHARS:
            raise ApiError(400, diagnosis.INVALID_SELECTION,
                           'complaint exceeds %d characters' % MAX_COMPLAINT_CHARS)
        expected = payload.get('expected_source_sha256')
        if expected is not None and expected != record['source_sha256']:
            raise ApiError(409, INVALID_STATE,
                           'expected_source_sha256 does not match this document')
        if payload.get('fixture_diagnosis') is not None or                 payload.get('break_target_id') is not None or                 payload.get('section_target_id') is not None:
            raise ApiError(400, 'invalid_request',
                           'A whole-document repair takes no break, section or '
                           'injected diagnosis')
        with observability.span('structure.inspect'):
            ruled = precheck.document_repair(store.document_path(document_id),
                                             complaint)
        if ruled is None:
            raise ApiError(422, NO_DOCUMENT_REPAIR,
                           'No whole-document repair applies: it is offered only for '
                           'unwanted page or column breaks, and for paragraphs whose '
                           'alignment clearly differs from the rest of their style. '
                           'Check paragraph by paragraph instead.')
        first = min(op['paragraph_id'] for op in ruled[0]['evidence'])
        job_id = store.new_id()
        job = {
            'job_id': job_id, 'document_id': document_id,
            'source_path': str(store.document_path(document_id)),
            'source_sha256': record['source_sha256'],
            'paragraph_id': first, 'complaint': complaint, 'scope': 'document',
            'mode': LIVE, 'state': QUEUED,
            'render': bool(payload.get('render', True)),
            'model': None, 'timeout': None,
            'break_target_id': None, 'section_target_id': None,
            'created_at': time.time(), 'updated_at': time.time(),
            'report': None, 'preview_index': None, 'decision': None,
            '_diagnoser': None,
        }
        with store.lock:
            store.jobs[job_id] = job
        app.state.executor.submit(_run_job, job_id)
        log.info('job queued id=%s document=%s scope=document', job_id, document_id)
        return _public_job(job)

    def _job(job_id):
        _checked_id(job_id, 'job')
        job = store.jobs.get(job_id)
        if job is None:
            raise ApiError(404, NOT_FOUND, 'No such job')
        return job

    def _observe_job(name):
        def decorate(handler):
            @wraps(handler)
            def observed(job_id, *args, **kwargs):
                job = _job(job_id)
                with observability.transaction(name, **_trace_metadata(job)) as trace:
                    try:
                        result = handler(job_id, *args, **kwargs)
                    except ApiError as error:
                        trace.set(outcome='failed', error_code=error.code)
                        raise
                    trace.set(outcome=job['state'])
                    return result
            return observed
        return decorate

    @app.get('/api/jobs/{job_id}')
    def get_job(job_id: str = PathParam(...)):
        return _public_job(_job(job_id))

    @app.get('/api/jobs')
    def list_jobs():
        return {'jobs': [_public_job(j) for j in list(store.jobs.values())]}

    @app.get('/api/jobs/{job_id}/preview/{side}/{index}')
    def get_preview(job_id: str = PathParam(...), side: str = PathParam(...),
                    index: int = PathParam(...)):
        """One rendered page image. The path comes from the report, never the caller."""
        job = _job(job_id)
        if side not in ('before', 'after'):
            raise ApiError(404, NOT_FOUND, 'side must be before or after')
        pages = (job.get('preview_index') or {}).get(side) or []
        if not 0 <= index < len(pages):
            raise ApiError(404, NOT_FOUND, 'No such preview page')
        image = Path(pages[index]).resolve()
        job_root = store.job_dir(job_id).resolve()
        # Defence in depth: the index is built by the renderer, but a page is only ever
        # served from inside this job's own directory.
        if not image.is_file() or job_root not in image.parents:
            raise ApiError(404, NOT_FOUND, 'No such preview page')
        return FileResponse(image, media_type='image/png')

    # -- the human decision -------------------------------------------------
    @app.post('/api/jobs/{job_id}/accept')
    @_observe_job('review.accept')
    def accept(job_id: str = PathParam(...), payload: dict = Body(default=None)):
        """Accept a reviewed candidate. The only path to state `accepted`.

        The caller must echo the candidate's sha256, so a candidate nobody looked at
        cannot be accepted by a stray POST, and the preservation invariants are
        re-checked against the files on disk before the state changes.
        """
        job = _job(job_id)
        if job['state'] != AWAITING_REVIEW:
            raise ApiError(409, INVALID_STATE,
                           'Only a job awaiting review can be accepted; this one is %r'
                           % job['state'])
        expected = (job['report'] or {}).get('candidate_sha256')
        supplied = (payload or {}).get('candidate_sha256')
        if supplied != expected:
            raise ApiError(409, INVALID_STATE,
                           'candidate_sha256 must match the candidate under review')
        # Enforced here as well as in the pipeline, because "the button was disabled"
        # is not a rule. A structural change is not acceptable without both renderings.
        report = job['report'] or {}
        if report.get('rendering_required') and not report.get('rendering_satisfied'):
            raise ApiError(409, diagnosis.RENDER_FAILURE,
                           'An explicit-break removal cannot be accepted without a '
                           'successful rendering of the original and the candidate.')
        failures = _revalidate_candidate(job)
        if failures:
            log.info('acceptance refused id=%s failures=%d', job_id, len(failures))
            raise ApiError(409, CANDIDATE_INTEGRITY_FAILED,
                           'Re-check failed: ' + '; '.join(failures))
        job['state'] = ACCEPTED
        job['decision'] = {'decision': 'accepted', 'at': time.time(),
                           'candidate_sha256': expected,
                           'by': (payload or {}).get('reviewer'),
                           'revalidated': True}
        job['updated_at'] = time.time()
        log.info('job accepted id=%s', job_id)
        return _public_job(job)

    @app.post('/api/jobs/{job_id}/reject')
    @_observe_job('review.reject')
    def reject(job_id: str = PathParam(...), payload: dict = Body(default=None)):
        """Reject a candidate. The candidate file is left in place for inspection."""
        job = _job(job_id)
        if job['state'] not in (AWAITING_REVIEW, ACCEPTED):
            raise ApiError(409, INVALID_STATE,
                           'Nothing to reject; this job is %r' % job['state'])
        reason = (payload or {}).get('reason')
        job['state'] = REJECTED
        job['decision'] = {'decision': 'rejected', 'at': time.time(),
                           'reason': reason if isinstance(reason, str) else None,
                           'by': (payload or {}).get('reviewer')}
        job['updated_at'] = time.time()
        log.info('job rejected id=%s', job_id)
        return _public_job(job)

    @app.get('/api/jobs/{job_id}/download')
    @_observe_job('download.execute')
    def download(job_id: str = PathParam(...)):
        """Serve an accepted candidate, after re-checking it one more time."""
        job = _job(job_id)
        if job['state'] != ACCEPTED:
            raise ApiError(409, INVALID_STATE,
                           'Only an accepted candidate can be downloaded; this job is '
                           '%r. Completing a job is not accepting it.' % job['state'])
        report = job['report'] or {}
        if report.get('rendering_required') and not report.get('rendering_satisfied'):
            raise ApiError(409, diagnosis.RENDER_FAILURE,
                           'This candidate contains a structural change that was never '
                           'rendered for review; it will not be served.')
        failures = _revalidate_candidate(job, stage='download.integrity')
        if failures:
            log.info('download refused id=%s failures=%d', job_id, len(failures))
            raise ApiError(409, CANDIDATE_INTEGRITY_FAILED,
                           'Re-check failed: ' + '; '.join(failures))
        candidate = Path(job['report']['candidate'])
        return FileResponse(
            candidate,
            media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            # A server-chosen name. The uploaded filename never round-trips.
            filename='repaired-%s.docx' % job_id[:12])

    return app


def _fixture_diagnoser(fixture, paragraph_id, source_sha256, source_path=None,
                       break_target_id=None, section_target_id=None):
    """Turn an injected answer into a diagnoser, validating it exactly like a model's.

    The injection point is the one `pipeline.run` already has for tests. It does not
    skip `diagnosis.check`, and a proposal it carries is still re-validated by
    `engine.validate` downstream, so an injected answer has no more power than a live
    one - only a different origin, which every response states.
    """
    if not isinstance(fixture, dict):
        raise ApiError(400, diagnosis.INVALID_RESPONSE, 'fixture_diagnosis must be an object')
    # A break id in an injected answer is checked against the document exactly as a
    # model's would be: an injected diagnosis has a different origin, never more power.
    targets = actionable = ()
    if source_path is not None:
        try:
            targets = gemma_adapter.supplied_target_ids(source_path, paragraph_id,
                                                        break_target_id,
                                                        section_target_id)
            actionable = gemma_adapter.actionable_target_ids(source_path, paragraph_id,
                                                             break_target_id,
                                                             section_target_id)
        except Exception:                              # pragma: no cover - defensive
            targets = actionable = ()
    try:
        found = diagnosis.check(fixture, paragraph_id, source_sha256,
                                supplied_ids=_citable_ids(fixture),
                                supplied_targets=targets,
                                actionable_targets=actionable)
    except diagnosis.DiagnosisError as error:
        raise ApiError(400, error.code, error.message)

    def diagnoser(source, complaint, selected):
        return found, {'model': 'injected-fixture', 'latency_seconds': 0.0,
                       'is_live_model_answer': False}
    return diagnoser


def _citable_ids(fixture):
    """Evidence ids an injected answer may cite: the ones it cites, if they are ints.

    A fixture is written by a developer, not produced by a model, so "did the model
    invent this id?" is not the question being asked here. Shape, status/operations
    consistency, scope and hash are all still checked.
    """
    ids = set()
    for entry in (fixture.get('evidence') or []):
        if isinstance(entry, dict) and type(entry.get('paragraph_id')) is int:
            ids.add(entry['paragraph_id'])
    return ids


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Local HTTP adapter over the What the Doc?! pipeline. '
                    'Binds to localhost. Do not expose this service publicly.')
    parser.add_argument('--host', default='127.0.0.1',
                        help='default 127.0.0.1; this service has no authentication')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--root', default=None,
                        help='storage root for documents and jobs (default work/api)')
    parser.add_argument('--allow-fixture-mode', action='store_true',
                        help='permit injected diagnoses, labelled mode "fixture"')
    parser.add_argument('--cors', action='append', dest='origins', default=None,
                        help='allow this origin, e.g. http://localhost:5173; repeatable')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    if args.host not in ('127.0.0.1', 'localhost', '::1'):
        log.warning('Binding to %s. This service has no authentication, no rate '
                    'limiting and no renderer sandbox. Do not expose it publicly.',
                    args.host)
    import uvicorn
    try:
        uvicorn.run(create_app(root=args.root, allow_fixture_mode=args.allow_fixture_mode,
                              allow_origins=args.origins),
                    host=args.host, port=args.port, log_level='info')
    finally:
        observability.flush()


if __name__ == '__main__':
    main()
