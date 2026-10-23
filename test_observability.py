"""Inspect real SDK envelopes with fake HTTP/model responses; never contact Sentry/Ollama."""
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sentry_sdk.transport import Transport

import diagnosis
import engine
import fixtures
import gemma_adapter
import observability as obs
import pipeline
import render


class MemoryTransport(Transport):
    def __init__(self):
        super().__init__({})
        self.payloads = []

    def capture_envelope(self, envelope):
        self.payloads.append(envelope.serialize().decode('utf-8'))

    def events(self):
        return [json.loads(line) for payload in self.payloads for line in payload.splitlines()
                if '"transaction":"' in line]


class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / 'PRIVATE_PATH_FILENAME_SENTINEL.docx'
        fixtures.build(self.source)
        self.before = engine.signature(self.source)
        self.target = fixtures.FAULT_IDS['excessive_space_before']
        self.transport = MemoryTransport()
        self.env = patch.dict(os.environ, {'SENTRY_DSN': 'https://public@example.invalid/1',
                                         'WTD_SENTRY_TRACES_SAMPLE_RATE': '1'})
        self.env.start()
        self.addCleanup(self.env.stop)
        for patcher in (patch.object(obs, '_attempted', False), patch.object(obs, '_client', None)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.assertTrue(obs.initialize(transport=self.transport))
        self.addCleanup(obs._client.close)

    def found(self, value=12):
        return {'status': 'proposed', 'explanation': 'PRIVATE_MODEL_OUTPUT_SENTINEL',
                'evidence': [{'paragraph_id': self.target, 'field': 'space_before_pt'}],
                'source_sha256': self.before,
                'operations': [{'paragraph_id': self.target, 'field': 'space_before_pt',
                                'value': value}], 'question': None}

    def fixture(self, value=12):
        def injected(*_):
            return self.found(value), {'model': 'injected-fixture'}
        return injected

    def run_fixture(self, **kwargs):
        with patch.object(pipeline.render_module, 'renderer_status', return_value={'available': False}):
            return pipeline.run(self.source, self.target, 'PRIVATE_COMPLAINT_SENTINEL',
                                self.root / 'run', diagnoser=self.fixture(),
                                do_render=False, **kwargs)

    def exported(self):
        obs.flush()
        self.assertTrue(self.transport.payloads)
        text = '\n'.join(self.transport.payloads)
        for sentinel in ('PRIVATE_COMPLAINT_SENTINEL', 'PRIVATE_MODEL_OUTPUT_SENTINEL',
                         'PRIVATE_PATH_FILENAME_SENTINEL', 'PRIVATE_EXCEPTION_SENTINEL',
                         str(self.root), gemma_adapter.DIAGNOSIS_SYSTEM):
            self.assertNotIn(sentinel, text)
        return self.transport.events()

    def fake_runtime(self, found=None, **usage):
        envelope = {'model': 'gemma4:e2b',
                    'message': {'content': json.dumps(found if found is not None else self.found())},
                    **usage}
        return io.BytesIO(json.dumps(envelope).encode())

    def test_actual_adapter_boundaries_emit_ai_conventions_tokens_and_source_hash(self):
        # This uses the live adapter path with an injected HTTP response. It is
        # not evidence of a live model call; it checks the real instrumentation.
        with patch.object(gemma_adapter.urllib.request, 'urlopen',
                          return_value=self.fake_runtime(prompt_eval_count=31, eval_count=7)), \
                patch.object(gemma_adapter, 'runtime_status', return_value={'available': True}):
            result = pipeline.run(self.source, self.target, 'PRIVATE_COMPLAINT_SENTINEL',
                                  self.root / 'adapter', model='gemma4:e2b', do_render=False)
        self.assertEqual(result['outcome'], 'awaiting_review')
        event, = self.exported()
        spans = {span['description']: span for span in event['spans']}
        # structure.inspect covers the break/section inspection that builds the
        # model's evidence. It runs inside diagnosis.execute, before inference.
        self.assertEqual(set(spans), {'structure.inspect', 'diagnosis.execute',
                                     'model.inference', 'diagnosis.validate',
                                     'proposal.validate', 'proposal.prepare', 'candidate.create',
                                     'preservation.check'})
        self.assertEqual(spans['structure.inspect']['parent_span_id'],
                         spans['diagnosis.execute']['span_id'])
        self.assertEqual(spans['candidate.create']['data']['wtd.operation_kind'],
                         'paragraph_property')
        ai = spans['model.inference']
        self.assertEqual(ai['op'], 'gen_ai.chat')
        self.assertEqual(ai['parent_span_id'], spans['diagnosis.execute']['span_id'])
        self.assertEqual(spans['proposal.validate']['parent_span_id'], spans['proposal.prepare']['span_id'])
        self.assertEqual(ai['data']['gen_ai.operation.name'], 'chat')
        self.assertEqual(ai['data']['gen_ai.operation.type'], 'ai_client')
        self.assertEqual(ai['data']['gen_ai.provider.name'], 'ollama')
        self.assertEqual(ai['data']['gen_ai.response.model'], 'gemma4:e2b')
        self.assertEqual(ai['data']['gen_ai.usage.input_tokens'], 31)
        self.assertEqual(ai['data']['gen_ai.usage.output_tokens'], 7)
        self.assertEqual(ai['data']['gen_ai.usage.total_tokens'], 38)
        self.assertEqual(ai['data']['wtd.mode'], 'live')
        self.assertEqual(ai['data']['wtd.prompt_sha256'],
                         hashlib.sha256(gemma_adapter.DIAGNOSIS_SYSTEM.encode()).hexdigest())
        self.assertEqual(ai['data']['wtd.source_revision'], obs.source_revision())
        self.assertFalse(any('cost' in key for key in ai['data']))
        self.assertEqual(engine.signature(self.source), self.before)

    def test_runtime_usage_stays_unknown_when_missing_or_invalid(self):
        for i, (input_tokens, output_tokens) in enumerate(((None, None), (-1, 8), (True, 9), (5, None))):
            with obs.transaction('repair.execute', mode='fixture'):
                with obs.span('model.inference') as trace:
                    trace.set(input_tokens=input_tokens, output_tokens=output_tokens)
        events = self.exported()
        self.assertEqual(len(events), 4)
        for event in events:
            data = event['spans'][0]['data']
            self.assertNotIn('gen_ai.usage.total_tokens', data)
            self.assertFalse(any('cost' in key for key in data))
        self.assertNotIn('gen_ai.usage.input_tokens', events[1]['spans'][0]['data'])
        self.assertNotIn('gen_ai.usage.input_tokens', events[2]['spans'][0]['data'])

    def test_invalid_schema_preserves_real_error_code_without_model_content(self):
        with patch.object(gemma_adapter.urllib.request, 'urlopen',
                          return_value=self.fake_runtime(found={'private': 'PRIVATE_MODEL_OUTPUT_SENTINEL'})), \
                patch.object(gemma_adapter, 'runtime_status', return_value={'available': True}):
            result = pipeline.run(self.source, self.target, 'PRIVATE_COMPLAINT_SENTINEL',
                                  self.root / 'invalid', model='gemma4:e2b', do_render=False)
        self.assertEqual(result['error_code'], diagnosis.INVALID_RESPONSE)
        self.assertIsNone(result['candidate'])
        event, = self.exported()
        self.assertEqual(event['contexts']['trace']['data']['wtd.error_code'], 'invalid_response')
        validation = next(s for s in event['spans'] if s['description'] == 'diagnosis.validate')
        self.assertEqual(validation['data']['wtd.error_code'], 'invalid_response')

    def test_injected_renderer_failure_exports_code_and_no_exception_message(self):
        with patch.object(render, 'renderer_status', return_value={'available': True}), \
                patch.object(render, 'docx_to_pdf', side_effect=render.RenderFailed(
                    'PRIVATE_EXCEPTION_SENTINEL ' + str(self.source))):
            result = pipeline.run(self.source, self.target, 'PRIVATE_COMPLAINT_SENTINEL',
                                  self.root / 'render-failure', diagnoser=self.fixture())
        self.assertEqual(result['error_code'], 'render_failure')
        self.assertIsNone(result['candidate'])
        event, = self.exported()
        rendering = next(s for s in event['spans'] if s['description'] == 'render.original')
        self.assertEqual(rendering['data']['wtd.error_code'], 'render_failure')
        self.assertEqual(event['contexts']['trace']['data']['wtd.mode'], 'fixture')
        self.assertEqual(event['contexts']['trace']['data']['wtd.outcome'], 'failed')

    def test_ineffective_proposal_refusal_is_distinct_from_model_abstention(self):
        result = pipeline.run(self.source, self.target, 'PRIVATE_COMPLAINT_SENTINEL',
                              self.root / 'noop', diagnoser=self.fixture(value=48), do_render=False)
        self.assertEqual(result['error_code'], 'invalid_operation')
        self.assertEqual(result['diagnosis']['status'], 'proposed')
        self.assertIsNone(result['candidate'])
        event, = self.exported()
        refused = next(s for s in event['spans'] if s['description'] == 'proposal.validate')
        self.assertEqual(refused['data']['wtd.error_code'], 'invalid_operation')
        self.assertNotIn('candidate.create', {s['description'] for s in event['spans']})

    def test_all_automatic_integrations_and_collection_categories_disabled(self):
        self.assertEqual(obs._client.integrations, {})
        options = obs._client.options
        self.assertFalse(options['stream_gen_ai_spans'])
        self.assertFalse(options['enable_logs'])
        self.assertEqual(options['max_breadcrumbs'], 0)
        self.assertFalse(options['include_local_variables'])
        collection = options['data_collection']
        self.assertFalse(collection['user_info'])
        self.assertEqual(collection['http_bodies'], [])
        self.assertEqual(collection['http_headers']['request']['mode'], 'off')
        self.assertEqual(collection['cookies']['mode'], 'off')
        self.assertEqual(collection['url_query_params']['mode'], 'off')
        self.assertEqual(collection['gen_ai'], {'inputs': False, 'outputs': False})
        self.assertEqual(collection['graphql'], {'document': False, 'variables': False})
        self.assertFalse(collection['database_query_data'])
        self.assertFalse(collection['queues'])
        self.assertFalse(collection['stack_frame_variables'])
        self.assertEqual(collection['frame_context_lines'], 0)

    def test_missing_configuration_and_initialization_failure_leave_repair_unchanged(self):
        import sentry_sdk
        baseline = self.run_fixture()
        for dsn, sdk_failure in (('', False), ('https://public@example.invalid/1', True)):
            with patch.object(obs, '_client', None), patch.object(obs, '_attempted', False), \
                    patch.dict(os.environ, {'SENTRY_DSN': dsn}), \
                    patch.object(sentry_sdk, 'Client', side_effect=RuntimeError('PRIVATE_EXCEPTION_SENTINEL')):
                self.assertFalse(obs.initialize())
                result = self.run_fixture()
                self.assertEqual((result['outcome'], result['plan'], result['candidate_sha256']),
                                 (baseline['outcome'], baseline['plan'], baseline['candidate_sha256']))

    def test_export_and_span_initialization_failures_leave_repair_unchanged(self):
        import sentry_sdk
        with patch.object(self.transport, 'capture_envelope',
                          side_effect=RuntimeError('PRIVATE_EXCEPTION_SENTINEL')):
            self.assertEqual(self.run_fixture()['outcome'], 'awaiting_review')
        with patch.object(sentry_sdk, 'start_span',
                          side_effect=RuntimeError('PRIVATE_EXCEPTION_SENTINEL')):
            self.assertEqual(self.run_fixture()['outcome'], 'awaiting_review')
        self.exported()


if __name__ == '__main__':
    unittest.main()
