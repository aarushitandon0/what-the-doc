"""The alignment rule: a paragraph whose direct alignment contradicts its peers.

Measured on a real upload: the model's answer for the planted centred paragraph was
discarded (a repeated citation), so nothing was found. The rule proposes the repair
without the model when the document is unambiguous, and declines otherwise.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import engine
import fixtures
import gemma_adapter
import pipeline
import precheck

CENTRED = fixtures.FAULT_IDS['inconsistent_alignment']
COMPLAINT = 'This paragraph is not aligned like the others'


class AlignmentRuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.doc = self.root / 'sample.docx'
        fixtures.build(self.doc)

    def test_the_centred_paragraph_is_set_back_to_justified(self):
        found = precheck.peer_alignment(self.doc, CENTRED, COMPLAINT)
        self.assertEqual(found['status'], 'proposed')
        self.assertEqual(found['operations'],
                         [{'paragraph_id': CENTRED, 'field': 'alignment', 'value': 'both'}])
        self.assertEqual(found['evidence'], [{'paragraph_id': CENTRED, 'field': 'alignment'}])
        self.assertIn('Normal', found['explanation'])

    def test_a_paragraph_that_matches_its_peers_is_left_to_the_model(self):
        self.assertIsNone(precheck.peer_alignment(self.doc, CENTRED - 1, COMPLAINT))

    def test_a_complaint_about_something_else_is_left_to_the_model(self):
        self.assertIsNone(precheck.peer_alignment(self.doc, CENTRED,
                                                  'Too much space above this'))

    def test_inherited_alignment_is_never_overridden(self):
        # The title is centred by its style, not directly: nothing of its own to fix.
        self.assertIsNone(precheck.peer_alignment(self.doc, 0, 'The title is centred'))

    def test_a_selected_break_or_section_is_left_to_the_model(self):
        self.assertIsNone(precheck.peer_alignment(self.doc, CENTRED, COMPLAINT,
                                                  break_target_id='brk-x'))

    def test_whole_document_finds_only_the_planted_paragraph(self):
        found, kept, rule = precheck.document_repair(
            self.doc, 'Some paragraphs are not aligned like the others')
        self.assertEqual(rule, precheck.ALIGNMENT_RULE)
        self.assertEqual(kept, [])
        self.assertEqual([op['paragraph_id'] for op in found['operations']], [CENTRED])

    def test_live_pipeline_answers_by_rule_without_calling_the_model(self):
        with mock.patch.object(gemma_adapter, 'diagnose',
                               side_effect=AssertionError('the model was called')):
            report = pipeline.run(self.doc, CENTRED, COMPLAINT, self.root / 'work',
                                  do_render=False)
        self.assertEqual(report['outcome'], pipeline.AWAITING_REVIEW,
                         '%s: %s' % (report['error_code'], report['error']))
        self.assertEqual(report['answered_by'], 'rule')
        self.assertEqual(report['diagnosis_metadata']['rule'], precheck.ALIGNMENT_RULE)
        self.assertFalse(report['accepted'])
        record = engine.inspect(report['candidate'])['paragraphs'][CENTRED]
        self.assertEqual(record['effective_properties']['alignment']['value'], 'both')

    def test_the_live_evaluation_still_measures_the_model(self):
        # use_precheck=False is what live_eval.py passes; the rule must stay out of it.
        with mock.patch.object(gemma_adapter, 'diagnose',
                               side_effect=gemma_adapter.diagnosis.DiagnosisError(
                                   'model_unavailable', 'no model in tests')) as called:
            pipeline.run(self.doc, CENTRED, COMPLAINT, self.root / 'eval',
                         do_render=False, use_precheck=False)
        self.assertTrue(called.called)


if __name__ == '__main__':
    unittest.main()
