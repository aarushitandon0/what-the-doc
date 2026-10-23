"""Deterministic tests for the HTTP adapter. No model is contacted, ever.

Two devices keep it that way, both already used elsewhere in this repository:

  * The approval and download flow runs on an **injected diagnosis**, the same
    injection point `test_pipeline.py` uses. Every such job is stamped mode `fixture`,
    and the tests assert that stamp, so a fixture result can never be mistaken for a
    live one here or in a response.
  * Where a test needs "no runtime is reachable", `GEMMA_URL` points at a reserved
    `.invalid` hostname, as `RuntimeGuardTests` does. Nothing listens there, and
    nothing is mocked into answering.

Rendering is disabled or explicitly unavailable in this module, so these are
structural checks of the boundary, state machine and approval gate. Renderer-process
failure coverage lives in test_api_render.py; neither module tests LibreOffice's
layout fidelity.
"""
import os
import time
import unittest
from pathlib import Path
import tempfile

try:
    from fastapi.testclient import TestClient
    import api
except ImportError as _error:                      # pragma: no cover - optional dep
    # FastAPI is an extra, not a core dependency. Without it these tests are *skipped*
    # and said to be skipped, exactly as the rendering tests are without LibreOffice.
    # They are never quietly treated as passing.
    raise unittest.SkipTest('fastapi is not installed: %s' % _error)

import engine
import fixtures

CLOSED = 'http://wtd-no-such-runtime.invalid/api/chat'


def proposal(paragraph_id, source_sha256, field='space_before_pt', value=12.0):
    """A well-formed diagnosis of the kind the model returns for the spacing fault."""
    return {'status': 'proposed',
            'explanation': 'A direct 48pt override where the peers inherit 12pt.',
            'evidence': [{'paragraph_id': paragraph_id, 'field': field}],
            'source_sha256': source_sha256,
            'operations': [{'paragraph_id': paragraph_id, 'field': field, 'value': value}],
            'question': None}


def abstention(source_sha256, status='no_change'):
    return {'status': status,
            'explanation': 'This paragraph already matches the rest of the document.',
            'evidence': [], 'source_sha256': source_sha256,
            'operations': [],
            'question': 'Which part looks wrong to you?'
                        if status == 'needs_clarification' else None}


class ApiBase(unittest.TestCase):
    allow_fixture_mode = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = api.create_app(root=self.root / 'store',
                                  allow_fixture_mode=self.allow_fixture_mode)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        # Drain the worker before the temporary directory goes away, so a job thread
        # can never outlive the files it is reading.
        self.addCleanup(self.app.state.executor.shutdown, True)
        self.sample = self.root / 'sample.docx'
        fixtures.build(self.sample)
        self.sample_sha = engine.signature(self.sample)
        self.spacing_id = fixtures.FAULT_IDS['excessive_space_before']

    def environment(self, **values):
        saved = {key: os.environ.get(key) for key in values}

        def restore():
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.addCleanup(restore)
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def upload(self, path=None, filename='report.docx', content_type=None):
        path = path or self.sample
        files = {'file': (filename, path.read_bytes(),
                          content_type or
                          'application/vnd.openxmlformats-officedocument.wordprocessingml.document')}
        return self.client.post('/api/documents', files=files)

    def uploaded_id(self):
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['document_id']

    def start(self, document_id, paragraph_id, complaint, fixture_diagnosis, **extra):
        body = {'document_id': document_id, 'paragraph_id': paragraph_id,
                'complaint': complaint, 'render': False,
                'fixture_diagnosis': fixture_diagnosis}
        body.update(extra)
        return self.client.post('/api/jobs', json=body)

    def settled(self, job_id, timeout=60):
        """Poll until the job leaves queued/running, as a frontend would."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            body = self.client.get('/api/jobs/%s' % job_id).json()
            if body['state'] not in (api.QUEUED, api.RUNNING):
                return body
            time.sleep(0.05)
        self.fail('job %s never settled' % job_id)

    def awaiting(self, complaint='Too much space above this heading.'):
        document_id = self.uploaded_id()
        started = self.start(document_id, self.spacing_id, complaint,
                             proposal(self.spacing_id, self.sample_sha))
        self.assertEqual(started.status_code, 202, started.text)
        job = self.settled(started.json()['job_id'])
        self.assertEqual(job['state'], api.AWAITING_REVIEW, job)
        return job


# ---------------------------------------------------------------------------
class UploadBoundaryTests(ApiBase):
    def test_a_docx_upload_returns_an_opaque_id_and_the_paragraph_count(self):
        body = self.upload().json()
        self.assertRegex(body['document_id'], r'^[0-9a-f]{32}$')
        self.assertEqual(body['source_sha256'], self.sample_sha)
        self.assertEqual(body['paragraph_count'], len(engine.inspect(self.sample)['paragraphs']))

    def test_the_stored_path_is_server_controlled_and_ignores_the_client_name(self):
        body = self.upload(filename='..\\..\\escape.docx').json()
        stored = self.app.state.store.document_path(body['document_id'])
        self.assertTrue(stored.is_file())
        self.assertEqual(stored.name, 'original.docx')
        self.assertIn(body['document_id'], stored.parts)
        self.assertNotIn('escape.docx', str(stored))

    def test_a_traversal_id_is_refused_without_touching_the_filesystem(self):
        for candidate in ('../../etc/passwd', '..', 'a' * 31, 'NOTHEX' * 5,
                          '%2e%2e%2fsecret'):
            response = self.client.get('/api/documents/%s' % candidate)
            self.assertIn(response.status_code, (404, 400), candidate)

    def test_a_non_docx_extension_is_refused(self):
        response = self.upload(filename='notes.pdf')
        self.assertEqual(response.status_code, 415)
        self.assertEqual(response.json()['error_code'], api.UNSUPPORTED_UPLOAD)

    def test_a_file_that_is_not_a_real_docx_is_refused(self):
        bogus = self.root / 'bogus.docx'
        bogus.write_bytes(b'this is not a zip archive at all')
        response = self.upload(path=bogus)
        self.assertEqual(response.status_code, 415)
        self.assertEqual(response.json()['error_code'], api.UNSUPPORTED_UPLOAD)

    def test_a_rejected_upload_leaves_nothing_stored(self):
        bogus = self.root / 'bogus.docx'
        bogus.write_bytes(b'PK\x03\x04 not really')
        self.upload(path=bogus)
        documents = self.app.state.store.documents_dir
        self.assertEqual(list(documents.iterdir()), [])

    def test_an_oversized_upload_is_refused_at_the_cap(self):
        big = self.root / 'big.docx'
        big.write_bytes(b'\0' * (api.MAX_UPLOAD_BYTES + 1024))
        response = self.upload(path=big)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()['error_code'], api.UPLOAD_TOO_LARGE)

    def test_paragraph_evidence_reports_effective_values_with_their_origin(self):
        document_id = self.uploaded_id()
        body = self.client.get('/api/documents/%s/paragraphs' % document_id).json()
        record = body['paragraphs'][self.spacing_id]
        spacing = record['effective_properties']['space_before_pt']
        self.assertEqual(spacing['value'], 48.0)
        self.assertEqual(spacing['origin'], 'direct')
        self.assertIn('application_default', body['warning'])

    def test_peer_comparison_is_served_for_a_paragraph(self):
        document_id = self.uploaded_id()
        body = self.client.get('/api/documents/%s/paragraphs/%d/peers'
                               % (document_id, self.spacing_id)).json()
        self.assertEqual(body['comparison']['space_before_pt']['selected'], 48.0)
        self.assertTrue(body['comparison']['space_before_pt']['peer_values'])

    def test_peers_for_a_nonexistent_paragraph_is_an_invalid_selection(self):
        document_id = self.uploaded_id()
        response = self.client.get('/api/documents/%s/paragraphs/999/peers' % document_id)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['error_code'], 'invalid_selection')


# ---------------------------------------------------------------------------
class JobStateTests(ApiBase):
    def test_a_job_is_queued_then_reaches_awaiting_review(self):
        job = self.awaiting()
        self.assertEqual(job['state'], api.AWAITING_REVIEW)
        self.assertIsNotNone(job['candidate']['candidate_sha256'])

    def test_a_finished_job_is_not_accepted(self):
        job = self.awaiting()
        self.assertFalse(job['accepted'])
        self.assertFalse(job['candidate']['download_available'])

    def test_an_injected_diagnosis_is_labelled_fixture_in_every_response(self):
        job = self.awaiting()
        self.assertEqual(job['mode'], api.FIXTURE)
        self.assertFalse(job['is_live_model_answer'])

    def test_repeated_evidence_in_an_abstention_is_normalised_and_counted(self):
        document_id = self.uploaded_id()
        answer = abstention(self.sample_sha, 'no_change')
        cited = {'paragraph_id': self.spacing_id, 'field': 'space_before_pt'}
        answer['evidence'] = [cited, dict(cited)]
        started = self.start(document_id, self.spacing_id, 'Looks odd.', answer)
        self.assertEqual(started.status_code, 202, started.text)
        job = self.settled(started.json()['job_id'])
        self.assertEqual(job['state'], api.NO_SUPPORTED_REPAIR)
        self.assertEqual(job['diagnosis']['evidence'], [cited])
        self.assertEqual(job['diagnosis']['evidence_duplicates_removed'], 1)
        self.assertIsNone(job['candidate'])

    def test_a_proposal_with_repeated_evidence_is_refused(self):
        document_id = self.uploaded_id()
        answer = proposal(self.spacing_id, self.sample_sha)
        answer['evidence'] = answer['evidence'] * 2
        started = self.start(document_id, self.spacing_id,
                             'Too much space above this heading.', answer)
        self.assertEqual(started.status_code, 400, started.text)
        self.assertEqual(started.json()['error_code'], 'invalid_response')

    def test_no_change_maps_to_no_supported_repair_with_no_candidate(self):
        document_id = self.uploaded_id()
        started = self.start(document_id, self.spacing_id, 'Looks odd.',
                             abstention(self.sample_sha, 'no_change'))
        job = self.settled(started.json()['job_id'])
        self.assertEqual(job['state'], api.NO_SUPPORTED_REPAIR)
        self.assertEqual(job['diagnosis']['status'], 'no_change')
        self.assertIsNone(job['candidate'])
        self.assertIsNone(job['proposal'])

    def test_unsupported_also_maps_to_no_supported_repair_but_keeps_its_own_status(self):
        document_id = self.uploaded_id()
        started = self.start(document_id, self.spacing_id, 'Looks odd.',
                             abstention(self.sample_sha, 'unsupported'))
        job = self.settled(started.json()['job_id'])
        self.assertEqual(job['state'], api.NO_SUPPORTED_REPAIR)
        self.assertEqual(job['diagnosis']['status'], 'unsupported')

    def test_needs_clarification_is_its_own_state_and_carries_the_question(self):
        document_id = self.uploaded_id()
        started = self.start(document_id, self.spacing_id, 'This bit looks wrong.',
                             abstention(self.sample_sha, 'needs_clarification'))
        job = self.settled(started.json()['job_id'])
        self.assertEqual(job['state'], api.NEEDS_CLARIFICATION)
        self.assertTrue(job['diagnosis']['question'])

    def test_an_out_of_bounds_value_fails_the_job_with_invalid_operation(self):
        document_id = self.uploaded_id()
        started = self.start(document_id, self.spacing_id, 'Too much space.',
                             proposal(self.spacing_id, self.sample_sha, value=9999.0))
        # 0..144pt is an engine.validate bound, not a schema one, so the shape check
        # lets this through and the engine refuses it. The adapter adds no bound of
        # its own: the single place that rule lives is still engine.validate.
        self.assertEqual(started.status_code, 202, started.text)
        job = self.settled(started.json()['job_id'])
        self.assertEqual(job['state'], api.FAILED)
        self.assertEqual(job['error_code'], 'invalid_operation')
        self.assertIsNone(job['candidate'])

    def test_an_injected_answer_targeting_another_paragraph_is_refused(self):
        document_id = self.uploaded_id()
        started = self.start(document_id, self.spacing_id, 'Too much space.',
                             proposal(self.spacing_id + 1, self.sample_sha))
        self.assertEqual(started.status_code, 400)

    def test_an_injected_proposal_with_no_operations_is_refused(self):
        document_id = self.uploaded_id()
        bad = proposal(self.spacing_id, self.sample_sha)
        bad['operations'] = []
        started = self.start(document_id, self.spacing_id, 'Too much space.', bad)
        self.assertEqual(started.status_code, 400)
        self.assertIn('not a repair', started.json()['detail'])

    def test_a_nonexistent_paragraph_is_refused_before_a_job_exists(self):
        document_id = self.uploaded_id()
        response = self.client.post('/api/jobs', json={
            'document_id': document_id, 'paragraph_id': 999,
            'complaint': 'Too much space.', 'render': False})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error_code'], 'invalid_selection')
        self.assertEqual(self.app.state.store.jobs, {})

    def test_an_empty_complaint_is_refused(self):
        document_id = self.uploaded_id()
        response = self.client.post('/api/jobs', json={
            'document_id': document_id, 'paragraph_id': 0,
            'complaint': '   ', 'render': False})
        self.assertEqual(response.status_code, 400)

    def test_a_stale_expected_source_hash_is_refused(self):
        document_id = self.uploaded_id()
        response = self.client.post('/api/jobs', json={
            'document_id': document_id, 'paragraph_id': self.spacing_id,
            'complaint': 'Too much space.', 'render': False,
            'expected_source_sha256': 'b' * 64,
            'fixture_diagnosis': proposal(self.spacing_id, self.sample_sha)})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.app.state.store.jobs, {})

    def test_a_matching_expected_source_hash_is_accepted(self):
        document_id = self.uploaded_id()
        response = self.start(document_id, self.spacing_id, 'Too much space.',
                              proposal(self.spacing_id, self.sample_sha),
                              expected_source_sha256=self.sample_sha)
        self.assertEqual(response.status_code, 202, response.text)

    def test_a_malformed_request_body_still_carries_an_error_code(self):
        response = self.client.post('/api/jobs', content=b'not json at all',
                                    headers={'Content-Type': 'application/json'})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['error_code'], 'invalid_request')

    def test_a_job_for_an_unknown_document_is_a_404(self):
        response = self.client.post('/api/jobs', json={
            'document_id': 'f' * 32, 'paragraph_id': 0, 'complaint': 'x'})
        self.assertEqual(response.status_code, 404)

    def test_the_original_document_is_never_modified_by_a_job(self):
        document_id = self.uploaded_id()
        stored = self.app.state.store.document_path(document_id)
        before = engine.signature(stored)
        self.start(document_id, self.spacing_id, 'Too much space above this heading.',
                   proposal(self.spacing_id, self.sample_sha))
        self.settled(list(self.app.state.store.jobs)[0])
        self.assertEqual(engine.signature(stored), before)


# ---------------------------------------------------------------------------
class ApprovalFlowTests(ApiBase):
    def test_download_before_acceptance_is_refused(self):
        job = self.awaiting()
        response = self.client.get('/api/jobs/%s/download' % job['job_id'])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error_code'], api.INVALID_STATE)
        self.assertIn('Completing a job is not accepting it', response.json()['detail'])

    def test_acceptance_requires_the_candidate_hash(self):
        job = self.awaiting()
        response = self.client.post('/api/jobs/%s/accept' % job['job_id'], json={})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.get('/api/jobs/%s' % job['job_id']).json()['state'],
                         api.AWAITING_REVIEW)

    def test_acceptance_with_a_wrong_candidate_hash_is_refused(self):
        job = self.awaiting()
        response = self.client.post('/api/jobs/%s/accept' % job['job_id'],
                                    json={'candidate_sha256': 'a' * 64})
        self.assertEqual(response.status_code, 409)

    def test_accepting_then_downloading_serves_the_candidate(self):
        job = self.awaiting()
        digest = job['candidate']['candidate_sha256']
        accepted = self.client.post('/api/jobs/%s/accept' % job['job_id'],
                                    json={'candidate_sha256': digest})
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()['state'], api.ACCEPTED)
        self.assertTrue(accepted.json()['accepted'])
        download = self.client.get('/api/jobs/%s/download' % job['job_id'])
        self.assertEqual(download.status_code, 200)
        self.assertTrue(download.content.startswith(b'PK'))
        self.assertIn('repaired-', download.headers['content-disposition'])

    def test_the_downloaded_bytes_are_the_candidate_on_disk(self):
        job = self.awaiting()
        digest = job['candidate']['candidate_sha256']
        self.client.post('/api/jobs/%s/accept' % job['job_id'],
                         json={'candidate_sha256': digest})
        download = self.client.get('/api/jobs/%s/download' % job['job_id'])
        import hashlib
        self.assertEqual(hashlib.sha256(download.content).hexdigest(), digest)

    def test_a_tampered_candidate_is_caught_at_acceptance(self):
        job = self.awaiting()
        digest = job['candidate']['candidate_sha256']
        candidate = Path(self.app.state.store.jobs[job['job_id']]['report']['candidate'])
        candidate.write_bytes(candidate.read_bytes() + b'tampered')
        response = self.client.post('/api/jobs/%s/accept' % job['job_id'],
                                    json={'candidate_sha256': digest})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error_code'], api.CANDIDATE_INTEGRITY_FAILED)

    def test_a_candidate_tampered_after_acceptance_is_caught_before_download(self):
        job = self.awaiting()
        digest = job['candidate']['candidate_sha256']
        self.client.post('/api/jobs/%s/accept' % job['job_id'],
                         json={'candidate_sha256': digest})
        candidate = Path(self.app.state.store.jobs[job['job_id']]['report']['candidate'])
        candidate.write_bytes(b'PK\x03\x04 replaced')
        response = self.client.get('/api/jobs/%s/download' % job['job_id'])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error_code'], api.CANDIDATE_INTEGRITY_FAILED)

    def test_a_deleted_candidate_is_caught_before_download(self):
        job = self.awaiting()
        digest = job['candidate']['candidate_sha256']
        self.client.post('/api/jobs/%s/accept' % job['job_id'],
                         json={'candidate_sha256': digest})
        Path(self.app.state.store.jobs[job['job_id']]['report']['candidate']).unlink()
        response = self.client.get('/api/jobs/%s/download' % job['job_id'])
        self.assertEqual(response.status_code, 409)

    def test_rejecting_leaves_nothing_downloadable(self):
        job = self.awaiting()
        rejected = self.client.post('/api/jobs/%s/reject' % job['job_id'],
                                    json={'reason': 'the heading should stand out'})
        self.assertEqual(rejected.json()['state'], api.REJECTED)
        self.assertFalse(rejected.json()['accepted'])
        self.assertEqual(rejected.json()['decision']['reason'],
                         'the heading should stand out')
        self.assertEqual(
            self.client.get('/api/jobs/%s/download' % job['job_id']).status_code, 409)

    def test_a_rejected_job_cannot_then_be_accepted(self):
        job = self.awaiting()
        digest = job['candidate']['candidate_sha256']
        self.client.post('/api/jobs/%s/reject' % job['job_id'], json={})
        response = self.client.post('/api/jobs/%s/accept' % job['job_id'],
                                    json={'candidate_sha256': digest})
        self.assertEqual(response.status_code, 409)

    def test_an_abstaining_job_has_nothing_to_accept(self):
        document_id = self.uploaded_id()
        started = self.start(document_id, self.spacing_id, 'Looks odd.',
                             abstention(self.sample_sha))
        job = self.settled(started.json()['job_id'])
        response = self.client.post('/api/jobs/%s/accept' % job['job_id'],
                                    json={'candidate_sha256': 'a' * 64})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error_code'], api.INVALID_STATE)

    def test_acceptance_records_that_it_revalidated(self):
        job = self.awaiting()
        digest = job['candidate']['candidate_sha256']
        body = self.client.post('/api/jobs/%s/accept' % job['job_id'],
                                json={'candidate_sha256': digest}).json()
        self.assertTrue(body['decision']['revalidated'])
        self.assertEqual(body['decision']['candidate_sha256'], digest)


# ---------------------------------------------------------------------------
class PreviewTests(ApiBase):
    def test_a_job_run_without_rendering_reports_no_preview_pages(self):
        job = self.awaiting()
        self.assertFalse(job['preview']['rendered'])
        self.assertEqual(job['preview']['before_urls'], [])

    def test_a_preview_page_that_does_not_exist_is_a_404(self):
        job = self.awaiting()
        self.assertEqual(
            self.client.get('/api/jobs/%s/preview/before/0' % job['job_id']).status_code, 404)

    def test_an_unknown_preview_side_is_refused(self):
        job = self.awaiting()
        self.assertEqual(
            self.client.get('/api/jobs/%s/preview/sideways/0' % job['job_id']).status_code, 404)

    def test_a_preview_is_only_served_from_inside_its_own_job_directory(self):
        job = self.awaiting()
        outsider = self.root / 'outside.png'
        outsider.write_bytes(b'\x89PNG\r\n\x1a\n')
        # Even with the index pointing outside, the containment check refuses it.
        self.app.state.store.jobs[job['job_id']]['preview_index'] = {
            'before': [str(outsider)], 'after': []}
        response = self.client.get('/api/jobs/%s/preview/before/0' % job['job_id'])
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
class RuntimeSeparationTests(ApiBase):
    allow_fixture_mode = False

    def test_fixture_mode_is_refused_unless_the_server_enables_it(self):
        document_id = self.uploaded_id()
        response = self.start(document_id, self.spacing_id, 'Too much space.',
                              proposal(self.spacing_id, self.sample_sha))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error_code'], api.FIXTURE_MODE_DISABLED)

    def test_an_unreachable_runtime_refuses_the_job_instead_of_faking_one(self):
        self.environment(GEMMA_URL=CLOSED, GEMMA_MODEL='gemma4:e2b')
        document_id = self.uploaded_id()
        response = self.client.post('/api/jobs', json={
            'document_id': document_id, 'paragraph_id': self.spacing_id,
            'complaint': 'Too much space above this heading.', 'render': False})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error_code'], 'model_unavailable')
        self.assertEqual(self.app.state.store.jobs, {})

    def test_health_reports_an_unreachable_runtime_as_unavailable(self):
        self.environment(GEMMA_URL=CLOSED, GEMMA_MODEL='gemma4:e2b')
        body = self.client.get('/api/health').json()
        self.assertFalse(body['live_diagnosis_available'])
        self.assertFalse(body['model_runtime']['available'])
        self.assertTrue(body['model_runtime']['reason'])
        self.assertFalse(body['fixture_mode_allowed'])

    def test_the_demo_document_is_labelled_as_synthetic(self):
        body = self.client.post('/api/demo/document').json()
        self.assertTrue(body['is_demo_document'])
        self.assertEqual(body['origin'], 'synthetic_demo_fixture')
        self.assertTrue(body['planted_faults'])


# ---------------------------------------------------------------------------
class RuntimeFailsMidJobTests(ApiBase):
    """A runtime that answers the health probe and then fails the actual call.

    `/api/health` only lists installed models, so a passing probe says nothing about
    whether the next generate call will succeed: the runtime can be busy, be shut down
    between the two calls, or refuse the model. The stub below answers `/api/tags`
    normally and fails `POST /api/chat`, which is the one shape the pre-flight check
    cannot catch. Nothing is mocked into *answering* a diagnosis; the call really is
    made, over real HTTP, and really fails.
    """
    chat_status = 503

    def setUp(self):
        super().setUp()
        import http.server, json as _json, threading

        chat_status = self.chat_status

        class Handler(http.server.BaseHTTPRequestHandler):
            def _send(self, status, body):
                raw = _json.dumps(body).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if self.path == '/api/tags':
                    self._send(200, {'models': [{'name': 'gemma4:e2b',
                                                 'details': {'family': 'gemma'}}]})
                else:
                    self._send(404, {'error': 'not found'})

            def do_POST(self):
                length = int(self.headers.get('Content-Length') or 0)
                self.rfile.read(length)
                self._send(chat_status, {'error': 'runtime is not ready'})

            def log_message(self, *args):
                pass

        self.runtime = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=self.runtime.serve_forever, daemon=True)
        thread.start()
        # addCleanup is LIFO, so server_close is registered first and runs last:
        # closing the socket before serve_forever stops raises WinError 10038.
        self.addCleanup(self.runtime.server_close)
        self.addCleanup(self.runtime.shutdown)
        port = self.runtime.server_address[1]
        self.environment(GEMMA_URL='http://127.0.0.1:%d/api/chat' % port,
                         GEMMA_MODEL='gemma4:e2b')

    def live_job(self, render=False):
        document_id = self.uploaded_id()
        response = self.client.post('/api/jobs', json={
            'document_id': document_id, 'paragraph_id': self.spacing_id,
            'complaint': 'Too much space above this heading.', 'render': render})
        return response

    def test_health_passes_while_the_generate_call_still_fails(self):
        body = self.client.get('/api/health').json()
        self.assertTrue(body['live_diagnosis_available'], body)
        self.assertTrue(body['model_runtime']['model_installed'], body)

    def test_a_runtime_failure_during_diagnosis_fails_the_job_with_its_code(self):
        started = self.live_job()
        # The pre-flight check passes, so the job is genuinely accepted...
        self.assertEqual(started.status_code, 202, started.text)
        job = self.settled(started.json()['job_id'])
        # ...and the failure is reported on the job, not swallowed into a fake answer.
        self.assertEqual(job['state'], api.FAILED, job)
        self.assertEqual(job['error_code'], 'model_unavailable', job)
        self.assertIsNone(job['diagnosis'])
        self.assertIsNone(job['proposal'])
        self.assertIsNone(job['candidate'])
        self.assertFalse(job['accepted'])

    def test_a_failed_job_offers_no_candidate_to_accept_or_download(self):
        job_id = self.settled(self.live_job().json()['job_id'])['job_id']
        accept = self.client.post('/api/jobs/%s/accept' % job_id,
                                  json={'candidate_sha256': 'f' * 64})
        self.assertEqual(accept.status_code, 409)
        self.assertEqual(accept.json()['error_code'], api.INVALID_STATE)
        download = self.client.get('/api/jobs/%s/download' % job_id)
        self.assertEqual(download.status_code, 409)
        self.assertEqual(download.json()['error_code'], api.INVALID_STATE)


class RuntimeReturnsNonsenseTests(RuntimeFailsMidJobTests):
    """The same stub, answering 200 with a body that is not a diagnosis."""
    chat_status = 200

    def test_a_runtime_failure_during_diagnosis_fails_the_job_with_its_code(self):
        job = self.settled(self.live_job().json()['job_id'])
        self.assertEqual(job['state'], api.FAILED, job)
        self.assertEqual(job['error_code'], 'invalid_response', job)
        self.assertIsNone(job['candidate'])
        self.assertFalse(job['accepted'])

    def test_health_passes_while_the_generate_call_still_fails(self):
        body = self.client.get('/api/health').json()
        self.assertTrue(body['live_diagnosis_available'], body)


# ---------------------------------------------------------------------------
class MissingRendererTests(ApiBase):
    """No LibreOffice: previews are reported absent, never as "nothing changed".

    `SOFFICE_PATH` pointing at a path that does not exist is the documented override,
    so this needs no mocking: the pipeline really looks for a binary and finds none.
    The structural repair still happens, because the invariants and the validator do
    not depend on rendering; what must not happen is a missing render being presented
    as a comparison showing no visible difference.
    """

    def setUp(self):
        super().setUp()
        self.environment(SOFFICE_PATH=str(self.root / 'no-such-soffice'))

    def reviewable(self):
        document_id = self.uploaded_id()
        started = self.start(document_id, self.spacing_id,
                             'Too much space above this heading.',
                             proposal(self.spacing_id, self.sample_sha), render=True)
        self.assertEqual(started.status_code, 202, started.text)
        return self.settled(started.json()['job_id'])

    def test_health_reports_the_renderer_as_unavailable(self):
        body = self.client.get('/api/health').json()
        self.assertFalse(body['renderer']['available'], body)

    def test_a_repair_still_reaches_review_without_a_renderer(self):
        job = self.reviewable()
        self.assertEqual(job['state'], api.AWAITING_REVIEW, job)
        self.assertIsNone(job['error_code'], job)
        self.assertTrue(job['candidate']['candidate_sha256'])
        self.assertEqual(job['invariant_failures'], [])
        self.assertFalse(job['accepted'])

    def test_the_absence_of_previews_is_stated_and_not_shown_as_no_difference(self):
        job = self.reviewable()
        preview = job['preview']
        self.assertFalse(preview['rendered'], preview)
        self.assertFalse(preview['renderer_available'], preview)
        self.assertEqual(preview['before_page_count'], 0)
        self.assertEqual(preview['after_page_count'], 0)
        self.assertEqual(preview['before_urls'], [])
        self.assertEqual(preview['after_urls'], [])
        # The one thing a reviewer must never be told is that the pages were compared
        # and looked the same: nothing was rendered, so nothing was compared.
        self.assertIsNone(job['visual_comparison'], job)

    def test_no_preview_page_is_served_when_nothing_was_rendered(self):
        job = self.reviewable()
        for side in ('before', 'after'):
            response = self.client.get('/api/jobs/%s/preview/%s/0' % (job['job_id'], side))
            self.assertEqual(response.status_code, 404, (side, response.text))


# ---------------------------------------------------------------------------
class LoggingTests(ApiBase):
    def test_routine_logs_carry_no_complaint_or_document_text(self):
        import logging
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = Capture()
        api.log.addHandler(handler)
        api.log.setLevel(logging.INFO)
        self.addCleanup(api.log.removeHandler, handler)

        secret = 'the quick brown vole complaint text'
        document_id = self.uploaded_id()
        started = self.start(document_id, self.spacing_id, secret,
                             proposal(self.spacing_id, self.sample_sha))
        job = self.settled(started.json()['job_id'])
        self.client.post('/api/jobs/%s/accept' % job['job_id'],
                         json={'candidate_sha256': job['candidate']['candidate_sha256']})

        self.assertTrue(records, 'expected the adapter to log something')
        joined = '\n'.join(records)
        self.assertNotIn(secret, joined)
        self.assertNotIn('original.docx', joined)
        self.assertNotIn('Enrolment', joined)     # body text from the fixture
        self.assertIn('job accepted', joined)


if __name__ == '__main__':
    unittest.main()
