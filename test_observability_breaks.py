"""Telemetry for the explicit-break slice, read from real serialized SDK envelopes.

Nothing here contacts Sentry, Ollama or any network service: the SDK is pointed at an
in-memory transport with a fake DSN, and every envelope is inspected after the fact.

Two things are being established. First, that the new stages are actually traced and
carry the operation kind. Second - and this is the part that matters more - that the
things this feature newly handles are *not* exported: the text either side of a break
is how a person identifies it, so it exists in the on-disk report and in the HTTP
response, and it must never reach telemetry. A target id is a hash of the document
revision and a position, not content, so it is simply not in the allowlist either.
"""
import json
import os
from datetime import datetime
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import break_fixtures
import engine
import observability as obs
import pipeline
import render as render_module
from test_observability import MemoryTransport

# Strings that appear in the fixture document and in the break records. If any of
# them shows up in an envelope, document content has leaked into telemetry.
DOCUMENT_SENTINELS = (
    'Quarterly Register', 'the register was closed', 'Then the balances',
    'Finally the register', 'deliberate break', 'committee reviewed',
)


class BreakTelemetryBase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / 'PRIVATE_PATH_SENTINEL.docx'
        break_fixtures.build_manual_page_break(self.source)
        self.report = engine.inspect_breaks(self.source)
        self.targets = [b for b in self.report['breaks'] if b['edit_eligible']]
        self.transport = MemoryTransport()
        self.env = patch.dict(os.environ,
                              {'SENTRY_DSN': 'https://public@example.invalid/1',
                               'WTD_SENTRY_TRACES_SAMPLE_RATE': '1'})
        self.env.start()
        self.addCleanup(self.env.stop)
        for patcher in (patch.object(obs, '_attempted', False),
                        patch.object(obs, '_client', None)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.assertTrue(obs.initialize(transport=self.transport))
        self.addCleanup(obs._client.close)

    def diagnoser(self, target=None, break_type='page'):
        target = target or self.targets[0]
        found = {'status': 'proposed',
                 'explanation': 'PRIVATE_MODEL_OUTPUT_SENTINEL',
                 'evidence': [{'paragraph_id': target['paragraph_id'],
                               'target_id': target['target_id']}],
                 'source_sha256': self.report['source_sha256'],
                 'operations': [{'operation': 'remove_explicit_break',
                                 'target_id': target['target_id'],
                                 'expected_break_type': break_type}],
                 'question': None}

        def injected(*_):
            return found, {'model': 'injected-fixture'}
        return injected

    def execute(self, target=None, do_render=False, break_type='page', name='run'):
        return pipeline.run(self.source, (target or self.targets[0])['paragraph_id'],
                            'PRIVATE_COMPLAINT_SENTINEL', self.root / name,
                            diagnoser=self.diagnoser(target, break_type),
                            do_render=do_render)

    def exported(self):
        obs.flush()
        self.assertTrue(self.transport.payloads, 'nothing was exported')
        return self.transport.events()

    def raw(self):
        obs.flush()
        return '\n'.join(self.transport.payloads)


class BreakSpanTests(BreakTelemetryBase):

    def test_an_unrendered_break_removal_traces_a_render_failure(self):
        result = self.execute()
        self.assertEqual(result['outcome'], pipeline.FAILED)
        self.assertEqual(result['error_code'], 'render_failure')
        event, = self.exported()
        self.assertEqual(event['transaction'], 'repair.execute')
        self.assertEqual(event['contexts']['trace']['data']['wtd.error_code'],
                         'render_failure')
        self.assertEqual(event['contexts']['trace']['data']['wtd.outcome'], 'failed')
        spans = {span['description']: span for span in event['spans']}
        # The candidate was built and the preservation checks ran and passed; it is
        # the rendering requirement that refused it, and the trace shows exactly that.
        self.assertIn('candidate.create', spans)
        self.assertEqual(spans['preservation.check']['data']['wtd.outcome'], 'passed')

    def test_the_operation_kind_is_recorded_on_the_stages_that_have_one(self):
        self.execute()
        event, = self.exported()
        spans = {span['description']: span for span in event['spans']}
        for name in ('proposal.validate', 'proposal.prepare', 'candidate.create',
                     'preservation.check'):
            self.assertEqual(spans[name]['data']['wtd.operation_kind'],
                             'explicit_break_removal', name)
        self.assertEqual(spans['candidate.create']['data']['wtd.break_type'], 'page')

    def test_structure_inspection_is_traced_as_its_own_stage(self):
        # The injected diagnoser skips the model, so the inspection span here is the
        # one the API's own /breaks handler and the adapter's evidence builder use.
        with obs.transaction('repair.execute') as trace:
            with obs.span('structure.inspect') as inspection:
                engine.inspect_breaks(self.source)
                inspection.set(operation_kind='explicit_break_removal')
            trace.set(outcome='awaiting_review')
        event, = self.exported()
        spans = {span['description']: span for span in event['spans']}
        self.assertIn('structure.inspect', spans)
        self.assertEqual(spans['structure.inspect']['data']['wtd.operation_kind'],
                         'explicit_break_removal')
        self.assertEqual(spans['structure.inspect']['op'], 'wtd.stage')

    def test_a_column_break_is_labelled_as_a_column_break(self):
        column_source = self.root / 'columns.docx'
        break_fixtures.build_multicolumn(column_source)
        self.source = column_source
        self.report = engine.inspect_breaks(column_source)
        column = [b for b in self.report['breaks'] if b['break_type'] == 'column'][0]
        self.execute(target=column, break_type='column', name='column-run')
        event, = self.exported()
        spans = {span['description']: span for span in event['spans']}
        self.assertEqual(spans['candidate.create']['data']['wtd.break_type'], 'column')

    def test_a_refused_target_is_traced_as_an_invalid_operation(self):
        stale = dict(self.targets[0], target_id='brk-0000000000000000')
        result = self.execute(target=stale, name='stale-run')
        self.assertEqual(result['error_code'], 'invalid_operation')
        event, = self.exported()
        self.assertEqual(event['contexts']['trace']['data']['wtd.error_code'],
                         'invalid_operation')
        spans = {span['description']: span for span in event['spans']}
        # The refusal happened during validation, so no candidate was ever created.
        self.assertIn('proposal.validate', spans)
        self.assertNotIn('candidate.create', spans)
        # The message naming the refused id stays out of the payload.
        self.assertNotIn('brk-0000000000000000', self.raw())


class BreakPrivacyTests(BreakTelemetryBase):

    def test_no_document_text_target_id_or_path_reaches_the_payload(self):
        self.execute()
        payload = self.raw()
        self.assertTrue(payload)
        for sentinel in DOCUMENT_SENTINELS:
            self.assertNotIn(sentinel, payload, sentinel)
        for sentinel in ('PRIVATE_COMPLAINT_SENTINEL', 'PRIVATE_MODEL_OUTPUT_SENTINEL',
                         'PRIVATE_PATH_SENTINEL', '.docx'):
            self.assertNotIn(sentinel, payload, sentinel)
        # A target id is not content, but it is also not allowlisted, so it is absent.
        self.assertNotIn(self.targets[0]['target_id'], payload)
        self.assertNotIn('text_before', payload)
        self.assertNotIn('text_after', payload)
        self.assertNotIn('w:br', payload)

    def test_the_nearby_text_is_available_where_it_is_needed_and_only_there(self):
        """The evidence a reviewer needs exists; it just does not go to telemetry."""
        result = self.execute()
        shown = result['break_targets'][0]
        self.assertTrue(shown['text_before'] or shown['text_after'])
        on_disk = json.loads((self.root / 'run' / 'pipeline.json').read_text('utf-8'))
        self.assertTrue(on_disk['break_targets'][0]['text_before']
                        or on_disk['break_targets'][0]['text_after'])
        self.assertNotIn(shown['text_before'][:20] or 'IMPOSSIBLE', self.raw())

    def test_an_unknown_operation_kind_is_dropped_rather_than_exported(self):
        with obs.transaction('repair.execute') as trace:
            trace.set(operation_kind='delete_everything', outcome='failed')
        event, = self.exported()
        self.assertNotIn('wtd.operation_kind', event['contexts']['trace']['data'])
        self.assertNotIn('delete_everything', self.raw())

    def test_an_unknown_break_type_is_dropped(self):
        with obs.transaction('repair.execute') as trace:
            trace.set(break_type='textWrapping', outcome='failed')
        event, = self.exported()
        self.assertNotIn('wtd.break_type', event['contexts']['trace']['data'])


@unittest.skipUnless(render_module.find_soffice(),
                     'LibreOffice is not installed; the rendered-stage timings cannot '
                     'be faked, so this is reported as not run')
class RenderedBreakTelemetryTests(BreakTelemetryBase):

    def test_a_rendered_break_removal_traces_every_stage_with_durations(self):
        result = self.execute(do_render=True, name='rendered')
        self.assertEqual(result['outcome'], pipeline.AWAITING_REVIEW)
        event, = self.exported()
        spans = {span['description']: span for span in event['spans']}
        self.assertEqual(set(spans) >= {'diagnosis.execute', 'proposal.validate',
                                        'proposal.prepare', 'candidate.create',
                                        'render.original', 'render.candidate',
                                        'preservation.check'}, True)

        def seconds(name):
            # SDK timestamps are ISO-8601 strings in the serialized envelope.
            span = spans[name]
            return (datetime.fromisoformat(span['timestamp'])
                    - datetime.fromisoformat(span['start_timestamp'])).total_seconds()

        # The observation this test exists to record: for a deterministic (injected)
        # diagnosis, rendering is the dominant cost of the whole repair, by orders of
        # magnitude. Both numbers come from SDK timestamps, not from the application.
        rendering = seconds('render.original') + seconds('render.candidate')
        self.assertGreater(rendering, 0)
        self.assertGreater(rendering, seconds('preservation.check'))
        self.assertGreater(rendering, seconds('candidate.create'))
        self.assertNotIn('PRIVATE_COMPLAINT_SENTINEL', self.raw())
        print('\n[observed] rendering %.2fs, preservation %.3fs, candidate %.3fs'
              % (rendering, seconds('preservation.check'), seconds('candidate.create')))


if __name__ == '__main__':
    unittest.main()
