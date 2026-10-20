"""Deterministic tests for the diagnosis -> candidate pipeline.

No model is contacted. Each test injects a diagnoser that returns a fixed answer, which
is how the *plumbing* around a model is tested: that a proposal is re-validated and
rendered into a reviewable candidate, that an abstention produces nothing, that each
failure is reported under its own code, and that no path accepts a repair or touches
the original file.

Because a diagnoser is injected, every report here records mode "fixture". Live model
behaviour is measured only by live_eval.py.
"""
import json
import tempfile
import unittest
from pathlib import Path

import diagnosis
import engine
import eval_fixtures
import fixtures
import pipeline


def answer(status='proposed', operations=None, evidence=None, question=None,
           explanation='A direct override differs from what the peers inherit.'):
    def diagnoser(source, complaint, paragraph_id):
        found = {'status': status,
                 'explanation': explanation,
                 'evidence': [] if evidence is None else evidence,
                 'source_sha256': engine.signature(source),
                 'operations': [] if operations is None else operations,
                 'question': question}
        return found, {'model': 'injected-fixture', 'latency_seconds': 0.0}
    return diagnoser


def failing(code, message='injected failure'):
    def diagnoser(source, complaint, paragraph_id):
        raise diagnosis.DiagnosisError(code, message)
    return diagnoser


class PipelineBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'sample.docx'
        fixtures.build(self.source)
        self.before = engine.signature(self.source)
        self.work = self.root / 'run'

    def run_pipeline(self, paragraph_id, complaint, diagnoser, work=None):
        return pipeline.run(self.source, paragraph_id, complaint, work or self.work,
                            diagnoser=diagnoser, do_render=False)


class ProposalTests(PipelineBase):
    def setUp(self):
        super().setUp()
        self.target = fixtures.FAULT_IDS['excessive_space_before']
        self.diagnoser = answer(
            operations=[{'paragraph_id': self.target,
                         'field': 'space_before_pt', 'value': 12}],
            evidence=[{'paragraph_id': self.target, 'field': 'space_before_pt'}])

    def test_a_valid_proposal_produces_a_candidate_awaiting_review(self):
        report = self.run_pipeline(self.target, 'Too much space above this heading.',
                                   self.diagnoser)
        self.assertEqual(report['outcome'], pipeline.AWAITING_REVIEW)
        self.assertTrue(Path(report['candidate']).exists())
        self.assertEqual(report['invariant_failures'], [])

    def test_the_candidate_is_never_accepted_by_the_pipeline(self):
        report = self.run_pipeline(self.target, 'Too much space above.', self.diagnoser)
        self.assertFalse(report['accepted'])
        self.assertIn('not accepted', report['note'])

    def test_awaiting_review_is_reached_and_not_bypassed(self):
        report = self.run_pipeline(self.target, 'Too much space above.', self.diagnoser)
        self.assertEqual(report['stages'][-1], pipeline.AWAITING_REVIEW)
        self.assertIn('validated', report['workflow_stages'])

    def test_the_original_file_is_untouched(self):
        report = self.run_pipeline(self.target, 'Too much space above.', self.diagnoser)
        self.assertEqual(engine.signature(self.source), self.before)
        self.assertNotEqual(report['candidate_sha256'], self.before)

    def test_the_repair_reaches_the_value_the_fixture_labels_as_expected(self):
        self.run_pipeline(self.target, 'Too much space above.', self.diagnoser)
        report = json.loads((self.work / 'pipeline.json').read_text(encoding='utf-8'))
        candidate = engine.inspect(report['candidate'])['paragraphs'][self.target]
        expected = fixtures.FAULTS[self.target]['expected_repair']
        self.assertEqual(candidate['effective_properties'][expected['field']]['value'],
                         expected['value'])

    def test_a_retry_reuses_the_candidate_instead_of_patching_a_patched_copy(self):
        first = self.run_pipeline(self.target, 'Too much space above.', self.diagnoser)
        second = self.run_pipeline(self.target, 'Too much space above.', self.diagnoser)
        self.assertFalse(first['candidate_reused'])
        self.assertTrue(second['candidate_reused'])
        self.assertEqual(first['candidate_sha256'], second['candidate_sha256'])

    def test_an_injected_diagnoser_is_always_labelled_fixture_mode(self):
        report = self.run_pipeline(self.target, 'Too much space above.', self.diagnoser)
        self.assertEqual(report['mode'], pipeline.FIXTURE)
        self.assertFalse(report['runtime']['available'])

    def test_a_report_is_written_for_the_run(self):
        self.run_pipeline(self.target, 'Too much space above.', self.diagnoser)
        self.assertTrue((self.work / 'pipeline.json').exists())

    def test_the_report_records_no_paragraph_text(self):
        # Operational records carry ids and fields, never document content.
        self.run_pipeline(self.target, 'Too much space above.', self.diagnoser)
        blob = (self.work / 'pipeline.json').read_text(encoding='utf-8')
        self.assertNotIn(fixtures.BODY_TEXTS[0][:60], blob)


class EffectiveNoOpPipelineTests(PipelineBase):
    """The candidate contains only real changes; a refused proposal stays a proposal."""

    def assert_refused_proposal(self, target, operations):
        report = self.run_pipeline(
            target, 'Too much space above this heading.',
            answer(operations=operations,
                   evidence=[{'paragraph_id': target, 'field': op['field']}
                             for op in operations]))
        self.assertEqual((report['outcome'], report['error_code']),
                         (pipeline.FAILED, diagnosis.INVALID_OPERATION))
        self.assertEqual(report['diagnosis']['status'], 'proposed')
        self.assertEqual(report['diagnosis']['operations'], operations)
        self.assertEqual(report['plan']['operations'], operations)
        self.assertEqual(len(report['ineffective_operations']), len(operations))
        self.assertFalse(report['accepted'])
        self.assertIsNone(report['candidate'])
        self.assertIsNone(report['preview'])
        self.assertNotIn('plan_validated', report['stages'])
        self.assertEqual(engine.signature(self.source), self.before)
        self.assertEqual(sorted(p.relative_to(self.work).as_posix()
                                for p in self.work.rglob('*') if p.is_file()),
                         ['pipeline.json'])
        saved = json.loads((self.work / 'pipeline.json').read_text(encoding='utf-8'))
        self.assertEqual(saved, report)
        return report

    def test_all_inherited_equal_operations_fail_without_creating_a_candidate(self):
        target = fixtures.CLEAN_PEER_HEADING
        current = engine.inspect(self.source)['paragraphs'][target]['effective_properties']
        operations = [
            {'paragraph_id': target, 'field': 'space_before_pt', 'value': 12.0},
            {'paragraph_id': target, 'field': 'space_after_pt', 'value': 6.0}]
        for operation in operations:
            entry = current[operation['field']]
            self.assertEqual((entry['value'], entry['origin']),
                             (operation['value'], 'style:Heading1'))

        report = self.assert_refused_proposal(target, operations)
        self.assertEqual([item['current_origin']
                          for item in report['ineffective_operations']],
                         ['style:Heading1', 'style:Heading1'])

    def test_all_direct_equal_operations_fail_without_creating_a_candidate(self):
        target = fixtures.FAULT_IDS['excessive_space_before']
        current = engine.inspect(self.source)['paragraphs'][target]['effective_properties']
        self.assertEqual((current['space_before_pt']['value'],
                          current['space_before_pt']['origin']), (48.0, 'direct'))
        operations = [{'paragraph_id': target, 'field': 'space_before_pt', 'value': 48.0}]

        report = self.assert_refused_proposal(target, operations)
        self.assertEqual(report['ineffective_operations'][0]['current_origin'], 'direct')

    def test_a_mixed_proposal_preserves_inheritance_and_keeps_the_raw_diagnosis(self):
        target = fixtures.FAULT_IDS['excessive_space_before']
        real = {'paragraph_id': target, 'field': 'space_before_pt', 'value': 12.0}
        redundant = {'paragraph_id': target, 'field': 'space_after_pt', 'value': 6.0}
        operations = [real, redundant]
        before = engine.inspect(self.source)['paragraphs'][target]
        self.assertEqual(before['effective_properties']['space_after_pt']['origin'],
                         'style:Heading1')
        report = self.run_pipeline(
            target, 'Too much space above this heading.',
            answer(operations=operations,
                   evidence=[{'paragraph_id': target, 'field': 'space_before_pt'}]))

        self.assertEqual(report['outcome'], pipeline.AWAITING_REVIEW)
        self.assertEqual(report['plan'], {'source_sha256': self.before, 'operations': [real]})
        self.assertEqual(report['diagnosis']['status'], 'proposed')
        self.assertEqual(report['diagnosis']['operations'], [real, redundant])
        self.assertEqual(len(report['ineffective_operations']), 1)
        dropped = report['ineffective_operations'][0]
        self.assertEqual((dropped['paragraph_id'], dropped['field'],
                          dropped['proposed_value'], dropped['current_value'],
                          dropped['current_origin']),
                         (target, 'space_after_pt', 6.0, 6.0, 'style:Heading1'))
        inspected = engine.inspect(report['candidate'])
        candidate = inspected['paragraphs'][target]
        self.assertEqual(candidate['effective_properties']['space_before_pt']['value'], 12.0)
        self.assertEqual(candidate['direct_properties_raw']['space_before_pt'], '240')
        self.assertEqual(candidate['effective_properties']['space_after_pt'],
                         before['effective_properties']['space_after_pt'])
        self.assertIsNone(candidate['direct_properties_raw']['space_after_pt'])
        self.assertEqual(inspected['source_sha256'], report['candidate_sha256'])
        self.assertNotEqual(report['candidate_sha256'], self.before)
        self.assertEqual(engine.signature(self.source), self.before)
        self.assertEqual(report['invariant_failures'], [])
        self.assertFalse(report['accepted'])
        saved = json.loads((self.work / 'pipeline.json').read_text(encoding='utf-8'))
        self.assertEqual(saved, report)

    def test_setting_an_unset_property_to_zero_creates_a_real_candidate_change(self):
        target = fixtures.FAULT_IDS['excessive_space_before']
        before = engine.inspect(self.source)['paragraphs'][target]
        entry = before['effective_properties']['left_indent_pt']
        self.assertEqual((entry['value'], entry['origin']), (None, 'application_default'))
        operation = {'paragraph_id': target, 'field': 'left_indent_pt', 'value': 0}
        report = self.run_pipeline(
            target, 'Remove the indentation from this paragraph.',
            answer(operations=[operation],
                   evidence=[{'paragraph_id': target, 'field': 'left_indent_pt'}]))

        self.assertEqual(report['outcome'], pipeline.AWAITING_REVIEW)
        self.assertEqual(report['plan']['operations'], [operation])
        self.assertEqual(report['ineffective_operations'], [])
        candidate = engine.inspect(report['candidate'])['paragraphs'][target]
        self.assertEqual((candidate['effective_properties']['left_indent_pt']['value'],
                          candidate['effective_properties']['left_indent_pt']['origin']),
                         (0.0, 'direct'))
        self.assertEqual(candidate['direct_properties_raw']['left_indent_pt'], '0')
        self.assertEqual(candidate['effective_properties']['space_before_pt'],
                         before['effective_properties']['space_before_pt'])
        self.assertNotEqual(report['candidate_sha256'], self.before)
        self.assertEqual(engine.signature(self.source), self.before)
        self.assertEqual(report['invariant_failures'], [])
        self.assertFalse(report['accepted'])


class AbstentionTests(PipelineBase):
    def test_no_change_produces_no_candidate_and_is_not_called_a_repair(self):
        report = self.run_pipeline(fixtures.CLEAN_PEER_HEADING,
                                   'Too much space above this heading.',
                                   answer(status='no_change',
                                          explanation='This heading matches its peers.'))
        self.assertEqual(report['outcome'], 'no_change')
        self.assertIsNone(report['candidate'])
        self.assertIsNone(report['plan'])
        self.assertNotEqual(report['outcome'], pipeline.AWAITING_REVIEW)

    def test_needs_clarification_carries_the_question_and_builds_nothing(self):
        report = self.run_pipeline(4, 'It looks wrong.',
                                   answer(status='needs_clarification',
                                          question='Which part looks wrong?',
                                          explanation='The complaint could mean several things.'))
        self.assertEqual(report['outcome'], 'needs_clarification')
        self.assertEqual(report['diagnosis']['question'], 'Which part looks wrong?')
        self.assertIsNone(report['candidate'])

    def test_unsupported_produces_no_candidate(self):
        report = self.run_pipeline(4, 'The table below is misaligned.',
                                   answer(status='unsupported',
                                          explanation='Table layout is out of scope.'))
        self.assertEqual(report['outcome'], 'unsupported')
        self.assertIsNone(report['candidate'])

    def test_an_abstention_leaves_the_original_untouched(self):
        self.run_pipeline(4, 'It looks wrong.', answer(status='no_change',
                                                       explanation='Nothing to repair.'))
        self.assertEqual(engine.signature(self.source), self.before)


class FailureCodeTests(PipelineBase):
    def test_a_model_that_cannot_be_reached_is_reported_as_unavailable(self):
        report = self.run_pipeline(1, 'Too much space.',
                                   failing(diagnosis.MODEL_UNAVAILABLE, 'connection refused'))
        self.assertEqual((report['outcome'], report['error_code']),
                         (pipeline.FAILED, diagnosis.MODEL_UNAVAILABLE))

    def test_a_timeout_is_distinguished_from_an_unavailable_model(self):
        report = self.run_pipeline(1, 'Too much space.',
                                   failing(diagnosis.MODEL_TIMEOUT, 'no answer in 600s'))
        self.assertEqual(report['error_code'], diagnosis.MODEL_TIMEOUT)

    def test_an_unusable_answer_is_reported_as_an_invalid_response(self):
        report = self.run_pipeline(1, 'Too much space.',
                                   failing(diagnosis.INVALID_RESPONSE, 'bad JSON'))
        self.assertEqual(report['error_code'], diagnosis.INVALID_RESPONSE)

    def test_a_nonexistent_paragraph_is_refused_as_an_invalid_selection(self):
        report = self.run_pipeline(999, 'Too much space.',
                                   failing(diagnosis.INVALID_SELECTION,
                                           'Invalid selected paragraph'))
        self.assertEqual(report['error_code'], diagnosis.INVALID_SELECTION)

    def test_an_out_of_bounds_value_is_refused_before_any_candidate_exists(self):
        report = self.run_pipeline(
            1, 'Too much space.',
            answer(operations=[{'paragraph_id': 1, 'field': 'space_before_pt', 'value': 5000}],
                   evidence=[{'paragraph_id': 1, 'field': 'space_before_pt'}]))
        self.assertEqual(report['error_code'], diagnosis.INVALID_OPERATION)
        self.assertIsNone(report['candidate'])

    def test_a_failed_run_leaves_the_original_untouched(self):
        self.run_pipeline(1, 'Too much space.',
                          failing(diagnosis.MODEL_UNAVAILABLE, 'connection refused'))
        self.assertEqual(engine.signature(self.source), self.before)

    def test_every_failure_writes_a_report_with_a_code(self):
        report = self.run_pipeline(1, 'x', failing(diagnosis.MODEL_TIMEOUT))
        saved = json.loads((self.work / 'pipeline.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['error_code'], report['error_code'])
        self.assertFalse(saved['accepted'])


class AlternativeMeasurementTests(unittest.TestCase):
    """A repair the layout would ignore is reported as unsupported formatting."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'diagnosis-sample.docx'
        eval_fixtures.build(self.source)
        self.before = engine.signature(self.source)

    def test_an_inherited_autospacing_blocks_the_repair_with_its_own_code(self):
        target = eval_fixtures.IDS['autospacing']
        report = pipeline.run(
            self.source, target, 'Too much space below this paragraph.', self.root / 'run',
            diagnoser=answer(
                operations=[{'paragraph_id': target, 'field': 'space_after_pt', 'value': 6}],
                evidence=[{'paragraph_id': target, 'field': 'space_after_pt'}]),
            do_render=False)
        self.assertEqual(report['error_code'], diagnosis.UNSUPPORTED_FORMATTING)
        self.assertIn('afterAutospacing', report['error'])
        self.assertIsNone(report['candidate'])
        self.assertEqual(engine.signature(self.source), self.before)

    def test_a_legitimate_page_break_is_only_removed_if_a_diagnosis_asks(self):
        # The engine will happily remove it; the restraint lives in the diagnosis, so
        # this records that an abstention is what keeps the break in place.
        target = eval_fixtures.IDS['legitimate_break']
        report = pipeline.run(
            self.source, target, 'There is a lot of space above this heading.',
            self.root / 'run2',
            diagnoser=answer(status='no_change',
                             explanation='The break starts the appendix deliberately.'),
            do_render=False)
        self.assertEqual(report['outcome'], 'no_change')
        self.assertIsNone(report['candidate'])


if __name__ == '__main__':
    unittest.main()
