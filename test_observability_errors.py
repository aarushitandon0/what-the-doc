"""Error events at the real envelope boundary; never contacts Sentry, Ollama or a renderer.

Every failure below carries sentinels in the places content could leak from: the
exception message, the file name, the complaint, the model output, SDK scope data and
breadcrumbs. The assertions are made on the serialized envelope the transport would
have handed to the network, not on the event dictionary.
"""
import json
import os
from pathlib import Path
import threading
import time
from unittest.mock import patch

from fastapi.testclient import TestClient
import sentry_sdk
from sentry_sdk.transport import Transport

import engine
import observability
import pipeline
import render
from test_api import ApiBase, api, proposal

SENTINELS = ('PRIVATE_EXCEPTION_SENTINEL', 'PRIVATE_FILENAME_SENTINEL',
             'PRIVATE_COMPLAINT_SENTINEL', 'PRIVATE_MODEL_OUTPUT_SENTINEL',
             'PRIVATE_SCOPE_SENTINEL', 'PRIVATE_BREADCRUMB_SENTINEL')


class MemoryTransport(Transport):
    def __init__(self):
        super().__init__({})
        self.envelopes = []
        self.lock = threading.Lock()

    def capture_envelope(self, envelope):
        with self.lock:
            self.envelopes.append(envelope.serialize().decode('utf-8'))

    def text(self):
        with self.lock:
            return '\n'.join(self.envelopes)

    def items(self, kind):
        found = []
        for envelope in self.text().split('\n'):
            if envelope.startswith('{'):
                value = json.loads(envelope)
                if kind == 'error' and value.get('level') == 'error':
                    found.append(value)
                elif (kind == 'transaction' and value.get('type') == 'transaction'
                      and 'transaction' in value):
                    found.append(value)
        return found


class ErrorEventTests(ApiBase):
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

    def errors(self, count):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            observability.flush()
            events = self.transport.items('error')
            if len(events) >= count:
                return events
            time.sleep(.01)
        self.fail('Missing error events: %r' % self.transport.text())

    def assert_private(self):
        text = self.transport.text()
        for sentinel in SENTINELS:
            self.assertNotIn(sentinel, text)
        # No local path at all: not the store, not this checkout, not a user profile.
        for path in (str(self.root), str(Path(__file__).resolve().parent),
                     str(Path.home())):
            self.assertNotIn(path, text)
            self.assertNotIn(json.dumps(path)[1:-1], text)

    def test_a_direct_capture_keeps_type_code_site_and_own_frames_only(self):
        def nested():
            raise PermissionError('PRIVATE_EXCEPTION_SENTINEL %s' % self.root)
        try:
            nested()
        except PermissionError as error:
            self.assertTrue(observability.capture_error(
                error, site='render.failure', error_code='render_failure'))
        event, = self.errors(1)
        self.assert_private()
        value, = event['exception']['values']
        self.assertEqual(value['type'], 'PermissionError')
        self.assertEqual(value['value'], 'render_failure')
        self.assertEqual(event['tags']['wtd.site'], 'render.failure')
        self.assertEqual(event['fingerprint'], ['render.failure', 'render_failure',
                                                'PermissionError'])
        frames = value['stacktrace']['frames']
        self.assertEqual([f['function'] for f in frames],
                         ['test_a_direct_capture_keeps_type_code_site_and_own_frames_only',
                          'nested'])
        for frame in frames:
            self.assertEqual(set(frame), {'module', 'function', 'lineno', 'in_app'})
        # Nothing the SDK adds by default survives the rebuild.
        for absent in ('user', 'request', 'breadcrumbs', 'contexts', 'extra', 'modules',
                       'server_name', 'message', 'logentry', 'threads', 'sdk'):
            self.assertNotIn(absent, event)

    def test_scope_data_and_breadcrumbs_never_ride_along(self):
        scope = sentry_sdk.get_isolation_scope()
        scope.set_user({'email': 'PRIVATE_SCOPE_SENTINEL@example.invalid'})
        scope.set_tag('document', 'PRIVATE_SCOPE_SENTINEL')
        scope.set_extra('body', 'PRIVATE_SCOPE_SENTINEL')
        sentry_sdk.add_breadcrumb(message='PRIVATE_BREADCRUMB_SENTINEL')
        self.addCleanup(scope.clear)
        observability.capture_error(RuntimeError('PRIVATE_EXCEPTION_SENTINEL'),
                                    site='job.execute')
        event, = self.errors(1)
        self.assert_private()
        self.assertEqual(event['exception']['values'][0]['value'], 'internal_error')

    def test_an_event_this_module_did_not_build_is_dropped(self):
        client = observability._client
        client.capture_event({'message': 'PRIVATE_EXCEPTION_SENTINEL', 'level': 'error'})
        client.capture_event({'level': 'error', 'tags': {'wtd.site': 'not.a.site'},
                              'exception': {'values': [{'type': 'X',
                                                        'value': 'PRIVATE_EXCEPTION_SENTINEL'}]}})
        self.assertFalse(observability.capture_error(ValueError('x'), site='free.text'))
        observability.flush()
        self.assertEqual(self.transport.items('error'), [])
        self.assert_private()

    def test_a_hostile_event_is_rebuilt_not_redacted_at_the_transport(self):
        # A payload that skipped before_send must still be rebuilt at the transport.
        hostile = {'level': 'error', 'event_id': 'a' * 32,
                   'tags': {'wtd.site': 'job.execute', 'wtd.error_code': 'PRIVATE_SCOPE_SENTINEL',
                            'wtd.job_id': 'PRIVATE_SCOPE_SENTINEL'},
                   'message': 'PRIVATE_EXCEPTION_SENTINEL',
                   'request': {'data': 'PRIVATE_COMPLAINT_SENTINEL'},
                   'exception': {'values': [{
                       'type': 'PRIVATE_EXCEPTION_SENTINEL with spaces',
                       'value': 'PRIVATE_EXCEPTION_SENTINEL',
                       'stacktrace': {'frames': [{'module': 'api', 'function': 'run',
                                                  'lineno': 3,
                                                  'abs_path': str(self.root),
                                                  'vars': {'t': 'PRIVATE_MODEL_OUTPUT_SENTINEL'},
                                                  'context_line': 'PRIVATE_MODEL_OUTPUT_SENTINEL'},
                                                 {'module': '../PRIVATE_FILENAME_SENTINEL',
                                                  'function': 'f', 'lineno': 1}]}}]}}
        cleaned = observability._sanitize_error(hostile, {})
        self.assertEqual(cleaned['exception']['values'][0]['type'], 'Error')
        self.assertEqual(cleaned['tags'], {'wtd.site': 'job.execute',
                                           'wtd.error_code': 'internal_error'})
        self.assertEqual(cleaned['exception']['values'][0]['stacktrace']['frames'],
                         [{'module': 'api', 'function': 'run', 'lineno': 3, 'in_app': True}])
        self.assertNotIn('PRIVATE', json.dumps(cleaned))

    def test_a_crashed_job_reports_one_correlated_error(self):
        document_id = self.upload(filename='PRIVATE_FILENAME_SENTINEL.docx').json()['document_id']
        with patch.object(api.pipeline, 'run',
                          side_effect=RuntimeError('PRIVATE_EXCEPTION_SENTINEL')):
            started = self.start(document_id, self.spacing_id,
                                 'PRIVATE_COMPLAINT_SENTINEL',
                                 proposal(self.spacing_id, self.sample_sha))
            job = self.settled(started.json()['job_id'])
        self.assertEqual(job['state'], api.FAILED)
        self.assertEqual(job['error_code'], 'internal_error')
        event, = self.errors(1)
        self.assert_private()
        self.assertEqual(event['tags']['wtd.site'], 'job.execute')
        self.assertEqual(event['tags']['wtd.job_id'], job['job_id'])
        self.assertEqual(event['tags']['wtd.mode'], 'fixture')
        self.assertEqual(event['exception']['values'][0]['type'], 'RuntimeError')
        # The worker's transaction still records the same failure, with the same id.
        transaction, = [t for t in self.transport.items('transaction')
                        if t['transaction'] == 'repair.execute']
        self.assertEqual(transaction['contexts']['trace']['data']['wtd.job_id'], job['job_id'])

    def test_a_failing_renderer_reports_render_failure_inside_the_job(self):
        document_id = self.upload(filename='PRIVATE_FILENAME_SENTINEL.docx').json()['document_id']
        failure = render.RenderFailed('PRIVATE_EXCEPTION_SENTINEL soffice said %s' % self.root)
        with patch.object(pipeline.workflow, 'repair', side_effect=failure):
            started = self.start(document_id, self.spacing_id,
                                 'PRIVATE_COMPLAINT_SENTINEL',
                                 proposal(self.spacing_id, self.sample_sha))
            job = self.settled(started.json()['job_id'])
        self.assertEqual(job['error_code'], 'render_failure')
        event, = self.errors(1)
        self.assert_private()
        self.assertEqual(event['tags']['wtd.site'], 'render.failure')
        self.assertEqual(event['tags']['wtd.error_code'], 'render_failure')
        self.assertEqual(event['tags']['wtd.job_id'], job['job_id'])
        self.assertEqual(event['exception']['values'][0]['type'], 'RenderFailed')

    def test_an_unhandled_request_error_is_a_coded_500_and_one_event(self):
        document_id = self.uploaded_id()
        client = TestClient(self.app, raise_server_exceptions=False)
        self.addCleanup(client.close)
        with patch.object(api.engine, 'inspect_breaks',
                          side_effect=KeyError('PRIVATE_EXCEPTION_SENTINEL')):
            response = client.get('/api/documents/%s/breaks' % document_id)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()['error_code'], 'internal_error')
        self.assertNotIn('PRIVATE', response.text)
        event, = self.errors(1)
        self.assert_private()
        self.assertEqual(event['tags']['wtd.site'], 'request.unhandled')
        self.assertEqual(event['exception']['values'][0]['type'], 'KeyError')


class WithoutDsnTests(ApiBase):
    def setUp(self):
        for patcher in (patch.dict(os.environ, {'SENTRY_DSN': ''}),
                        patch.object(observability, '_attempted', False),
                        patch.object(observability, '_client', None)):
            patcher.start()
            self.addCleanup(patcher.stop)
        super().setUp()

    def test_capture_is_a_no_op_and_a_crash_is_still_a_coded_500(self):
        self.assertFalse(observability.capture_error(RuntimeError('x'), site='job.execute'))
        self.assertIsNone(observability._client)
        document_id = self.uploaded_id()
        client = TestClient(self.app, raise_server_exceptions=False)
        self.addCleanup(client.close)
        with patch.object(api.engine, 'inspect_breaks', side_effect=KeyError('boom')):
            response = client.get('/api/documents/%s/breaks' % document_id)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()['error_code'], 'internal_error')
