"""The fixed spacing rule that runs before the model.

It answers one situation only: a spacing complaint about a paragraph that has no spacing
of its own. Its only possible answer is "no_change", so none of these tests can show it
proposing an edit, and several show it declining. No model is contacted: any attempt to
call one fails the test.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import diagnosis
import fixtures
import gemma_adapter
import live_eval
import pipeline
import precheck
from test_api import ApiBase

SPACING = 'Too much space above this heading.'


class PrecheckBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        cls.docs = live_eval.build_documents(cls.root)
        cls.faulty = cls.docs[live_eval.FAULTY]
        cls.clean_heading = 13           # inherits exactly what its peers inherit
        cls.body_prose = 3
        cls.real_fault = fixtures.FAULT_IDS['excessive_space_before']


class RuleTests(PrecheckBase):
    def test_answers_no_change_for_a_heading_that_only_inherits_its_spacing(self):
        found = precheck.inherited_spacing(self.faulty, self.clean_heading, SPACING)
        self.assertEqual(found['status'], 'no_change')
        self.assertEqual(found['operations'], [])
        self.assertEqual(found['evidence'],
                         [{'paragraph_id': self.clean_heading, 'field': 'space_before_pt'}])
        self.assertIn('style', found['explanation'])
        self.assertIn('no spacing of its own', found['explanation'])

    def test_says_nothing_sets_it_when_no_layer_does(self):
        found = precheck.inherited_spacing(self.faulty, self.body_prose, SPACING)
        self.assertEqual(found['status'], 'no_change')
        self.assertIn('Nothing sets the space above', found['explanation'])

    def test_declines_a_paragraph_that_really_has_the_override(self):
        # The planted 48pt direct override is the fault: the model must be asked.
        self.assertIsNone(
            precheck.inherited_spacing(self.faulty, self.real_fault, SPACING))

    def test_declines_when_the_complaint_is_about_something_else(self):
        for text in ('This heading is not aligned like the others.',
                     'There is a page break above this heading I did not want.',
                     'The space above and below this heading is odd.',
                     'Too much space.',                   # which gap? not clear
                     'This bit looks wrong.',
                     ''):
            self.assertIsNone(
                precheck.inherited_spacing(self.faulty, self.clean_heading, text), text)

    def test_below_reads_the_space_after_field(self):
        found = precheck.inherited_spacing(
            self.faulty, self.clean_heading, 'Too much space below this heading.')
        if found is not None:   # only when the facing neighbour is not direct either
            self.assertEqual(found['evidence'][0]['field'], 'space_after_pt')

    def test_declines_when_a_break_or_a_selection_is_involved(self):
        self.assertIsNone(precheck.inherited_spacing(
            self.faulty, self.clean_heading, SPACING, break_target_id='brk-1'))
        self.assertIsNone(precheck.inherited_spacing(
            self.faulty, self.clean_heading, SPACING, section_target_id='sec-1'))
        # A paragraph that holds an explicit break is never answered by this rule.
        breaks = self.docs.get(live_eval.DIAGNOSTIC)
        self.assertIsNotNone(breaks)
        self.assertIsNone(precheck.inherited_spacing(breaks, 13,
            'There is a lot of space above this appendix heading.'))

    def test_declines_an_unknown_paragraph(self):
        self.assertIsNone(precheck.inherited_spacing(self.faulty, 999, SPACING))


class PipelineTests(PrecheckBase):
    def run_live(self, paragraph_id, complaint, **kwargs):
        def never(*args, **kw):
            raise AssertionError('the model must not be contacted')
        out = Path(tempfile.mkdtemp(dir=self.root))
        with patch.object(gemma_adapter, 'diagnose', side_effect=never) as model, \
                patch.object(gemma_adapter, 'runtime_status',
                             return_value={'available': True, 'model_installed': True}):
            report = pipeline.run(self.faulty, paragraph_id, complaint, out,
                                  mode=pipeline.LIVE, do_render=False, **kwargs)
        return report, model

    def test_a_rule_answer_is_labelled_as_one_and_never_calls_the_model(self):
        report, model = self.run_live(self.clean_heading, SPACING)
        self.assertEqual(report['outcome'], 'no_change')
        self.assertEqual(report['answered_by'], 'rule')
        self.assertEqual(report['diagnosis_metadata']['rule'], precheck.RULE)
        self.assertIsNone(report['diagnosis_metadata']['model'])
        self.assertIsNone(report['candidate'])
        self.assertFalse(report['accepted'])
        model.assert_not_called()

    def test_the_real_fault_still_goes_to_the_model(self):
        with self.assertRaises(AssertionError) as caught:
            self.run_live(self.real_fault, SPACING)
        self.assertIn('model must not be contacted', str(caught.exception))

    def test_it_can_be_switched_off_so_the_evaluation_measures_the_model(self):
        with self.assertRaises(AssertionError):
            self.run_live(self.clean_heading, SPACING, use_precheck=False)

    def test_an_injected_diagnoser_is_never_pre_empted(self):
        # Fixture runs exist to test the plumbing around a diagnoser; a rule must not
        # swallow them, and they stay labelled fixture.
        def diagnoser(source, complaint, paragraph_id):
            return ({'status': 'no_change', 'explanation': 'injected', 'evidence': [],
                     'source_sha256': pipeline.engine.signature(source),
                     'operations': [], 'question': None},
                    {'model': 'injected-fixture', 'latency_seconds': 0.0})
        out = Path(tempfile.mkdtemp(dir=self.root))
        report = pipeline.run(self.faulty, self.clean_heading, SPACING, out,
                              diagnoser=diagnoser, do_render=False)
        self.assertEqual(report['mode'], pipeline.FIXTURE)
        self.assertNotEqual(report.get('answered_by'), 'rule')
        self.assertEqual(report['diagnosis']['explanation'], 'injected')

    def test_the_answer_passes_the_same_validator_as_a_model_answer(self):
        report, _ = self.run_live(self.clean_heading, SPACING)
        again = diagnosis.check(
            {k: report['diagnosis'][k] for k in
             ('source_sha256', 'explanation', 'evidence', 'operations', 'status',
              'question')},
            self.clean_heading, report['source_sha256'], {self.clean_heading})
        self.assertEqual(again['status'], 'no_change')


class ApiTests(ApiBase):
    def test_the_job_says_a_rule_answered_and_is_not_a_live_model_answer(self):
        document_id = self.uploaded_id()
        runtime = {'available': True, 'model_installed': True, 'reason': None}

        def never(*args, **kw):
            raise AssertionError('the model must not be contacted')
        with patch.object(gemma_adapter, 'runtime_status', return_value=runtime),                 patch.object(gemma_adapter, 'diagnose', side_effect=never):
            started = self.client.post('/api/jobs', json={
                'document_id': document_id, 'paragraph_id': 13, 'complaint': SPACING,
                'render': False})
            self.assertEqual(started.status_code, 202, started.text)
            job = self.settled(started.json()['job_id'])
        self.assertEqual(job['state'], 'no_supported_repair', job)
        self.assertEqual(job['answered_by'], 'rule')
        self.assertFalse(job['is_live_model_answer'])
        self.assertEqual(job['diagnosis']['status'], 'no_change')
        self.assertIsNone(job['proposal'])
        self.assertIsNone(job['candidate'])
        self.assertFalse(job['accepted'])
        self.assertIsNone(job['model'])


if __name__ == '__main__':
    unittest.main()
