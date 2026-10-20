"""Actual SDK envelopes from asynchronous API workers; no network or model call."""
import json
import os
import threading
import time
from unittest.mock import patch

from sentry_sdk.transport import Transport

import diagnosis
import observability
from test_api import ApiBase, api, proposal


class MemoryTransport(Transport):
    def __init__(self):
        super().__init__({})
        self.envelopes = []
        self.lock = threading.Lock()

    def capture_envelope(self, envelope):
        with self.lock:
            self.envelopes.append(envelope.serialize().decode('utf-8'))

    def transactions(self):
        with self.lock:
            envelopes = list(self.envelopes)
        events = []
        for envelope in envelopes:
            lines = envelope.splitlines()
            for i, line in enumerate(lines):
                if line.startswith('{'):
                    value = json.loads(line)
                    if value.get('type') == 'transaction' and 'transaction' in value:
                        events.append(value)
        return events


class ApiTracingTests(ApiBase):
    def setUp(self):
        self.transport = MemoryTransport()
        for patcher in (
                patch.dict(os.environ, {'SENTRY_DSN': 'https://public@example.invalid/1',
                                        'WTD_SENTRY_TRACES_SAMPLE_RATE': '1',
                                        'GEMMA_MODEL': 'gemma4:e2b'}),
                patch.object(observability, '_attempted', False),
                patch.object(observability, '_client', None)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.assertTrue(observability.initialize(transport=self.transport))
        self.addCleanup(observability._client.close)
        super().setUp()

    def emitted(self, count):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            events = self.transport.transactions()
            if len(events) >= count:
                return events
            time.sleep(.01)
        self.fail('Missing completed SDK transactions: %r' % self.transport.transactions())

    def test_worker_and_later_review_download_are_finite_correlated_private_traces(self):
        complaint = 'PRIVATE_COMPLAINT_SENTINEL too much space above this heading'
        model_text = 'PRIVATE_MODEL_OUTPUT_SENTINEL'
        document_id = self.upload(filename='PRIVATE_FILENAME_SENTINEL.docx').json()['document_id']
        found = proposal(self.spacing_id, self.sample_sha)
        found['explanation'] = model_text
        started = self.start(document_id, self.spacing_id, complaint, found)
        job_id = started.json()['job_id']
        job = self.settled(job_id)
        self.assertEqual(job['state'], api.AWAITING_REVIEW)
        self.assertFalse(job['accepted'])
        events = self.emitted(1)
        # The repair trace has already exported before a human decision exists.
        self.assertEqual([e['transaction'] for e in events], ['repair.execute'])
        repair = events[0]
        stages = {s['description']: s for s in repair['spans']}
        self.assertTrue({'diagnosis.execute', 'proposal.validate',
                         'proposal.prepare', 'candidate.create', 'preservation.check'} <= set(stages))
        self.assertNotIn('model.inference', stages, 'fixture must not invent AI inference')
        trace_id = repair['contexts']['trace']['trace_id']
        self.assertTrue(all(s['trace_id'] == trace_id for s in repair['spans']))
        self.assertTrue(all(s['data']['wtd.job_id'] == job_id for s in repair['spans']))

        accepted = self.client.post('/api/jobs/%s/accept' % job_id, json={
            'candidate_sha256': job['candidate']['candidate_sha256'],
            'reviewer': 'PRIVATE_REVIEWER_SENTINEL'})
        self.assertEqual(accepted.status_code, 200, accepted.text)
        downloaded = self.client.get('/api/jobs/%s/download' % job_id)
        self.assertEqual(downloaded.status_code, 200)
        events = self.emitted(3)
        self.assertEqual({e['transaction'] for e in events},
                         {'repair.execute', 'review.accept', 'download.execute'})
        self.assertEqual(len({e['contexts']['trace']['trace_id'] for e in events}), 3)
        for event in events:
            data = event['contexts']['trace']['data']
            self.assertEqual(data['wtd.job_id'], job_id)
            self.assertEqual(data['wtd.mode'], 'fixture')
            self.assertEqual(data['gen_ai.request.model'], 'injected-fixture')
        payload = '\n'.join(self.transport.envelopes)
        for sentinel in (complaint, model_text, 'PRIVATE_FILENAME_SENTINEL',
                         'PRIVATE_REVIEWER_SENTINEL', str(self.root), str(self.sample)):
            self.assertNotIn(sentinel, payload)

    def test_real_worker_live_error_code_and_exception_body_stay_separate(self):
        document_id = self.uploaded_id()
        failure = diagnosis.DiagnosisError('invalid_response',
                                           'PRIVATE_EXCEPTION_SENTINEL C:\\secret\\PRIVATE_PATH')
        with patch.object(api.gemma_adapter, 'runtime_status', return_value={
                'available': True, 'model_installed': True}), patch.object(
                api.gemma_adapter, 'diagnose', side_effect=failure):
            response = self.client.post('/api/jobs', json={
                'document_id': document_id, 'paragraph_id': self.spacing_id,
                'complaint': 'PRIVATE_COMPLAINT_SENTINEL', 'render': False})
            job = self.settled(response.json()['job_id'])
        self.assertEqual(job['error_code'], 'invalid_response')
        event = self.emitted(1)[0]
        data = event['contexts']['trace']['data']
        # This is an injected inference failure on the live code path, no real model.
        self.assertEqual(data['wtd.mode'], 'live')
        self.assertEqual(data['wtd.error_code'], 'invalid_response')
        self.assertEqual(data['wtd.outcome'], 'failed')
        payload = '\n'.join(self.transport.envelopes)
        for sentinel in ('PRIVATE_EXCEPTION_SENTINEL', 'PRIVATE_PATH',
                         'PRIVATE_COMPLAINT_SENTINEL', str(self.root)):
            self.assertNotIn(sentinel, payload)

    def test_rejection_and_refused_download_have_only_safe_outcomes(self):
        job = self.awaiting()
        job_id = job['job_id']
        response = self.client.post('/api/jobs/%s/reject' % job_id, json={
            'reason': 'PRIVATE_REJECTION_SENTINEL', 'reviewer': 'PRIVATE_REVIEWER_SENTINEL'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/api/jobs/%s/download' % job_id).status_code, 409)
        events = self.emitted(3)
        refusal = next(e for e in events if e['transaction'] == 'download.execute')
        self.assertEqual(refusal['contexts']['trace']['data']['wtd.error_code'], 'invalid_state')
        payload = '\n'.join(self.transport.envelopes)
        self.assertNotIn('PRIVATE_REJECTION_SENTINEL', payload)
        self.assertNotIn('PRIVATE_REVIEWER_SENTINEL', payload)
