"""The live evaluation's grader and bookkeeping, tested offline. No model is contacted.

These pin what makes before/after numbers comparable: the ten cases themselves, how a
run is scored, which failure category it lands in, and that a run with no runtime is
recorded as not run rather than graded.
"""
import os
import tempfile
import unittest
from pathlib import Path

import diagnosis
import live_eval
import pipeline

TEN = ['fault_excessive_spacing', 'fault_inconsistent_alignment',
       'fault_stranded_page_break', 'clean_paragraph_needs_no_change',
       'wrong_paragraph_for_the_complaint', 'nonexistent_paragraph',
       'legitimate_page_break_survives', 'ambiguous_complaint',
       'inherited_alternative_measurement', 'document_text_attempts_instruction']


def case(case_id):
    return next(c for c in live_eval.CASES if c['id'] == case_id)


def report(status=None, operations=(), error_code=None, error=None, outcome=None,
           candidate=None, invariant_failures=None):
    found = None if status is None else {'status': status, 'operations': list(operations),
                                         'explanation': 'x', 'evidence': [],
                                         'question': None}
    return {'diagnosis': found, 'error_code': error_code, 'error': error,
            'outcome': outcome or (status if status else pipeline.FAILED),
            'candidate': candidate,
            'invariants': None if candidate is None else {},
            'invariant_failures': invariant_failures or [],
            'diagnosis_metadata': {}}


class CaseSetTests(unittest.TestCase):
    def test_the_ten_cases_are_unchanged(self):
        self.assertEqual([c['id'] for c in live_eval.CASES], TEN)

    def test_the_structural_set_is_separate_and_fixed(self):
        ids = [c['id'] for c in live_eval.STRUCTURAL_CASES]
        self.assertEqual(len(ids), 5)
        self.assertFalse(set(ids) & set(TEN))
        ambiguous = next(c for c in live_eval.STRUCTURAL_CASES
                         if c['id'] == 'break_ambiguous_two_in_paragraph')
        self.assertEqual(ambiguous['expect_statuses'], ['needs_clarification'])
        self.assertIsNone(ambiguous['select_break'])


class ClassificationTests(unittest.TestCase):
    def graded(self, case_id, rep):
        c = case(case_id)
        record = live_eval.grade(c, rep)
        return record, live_eval.classify(record, c['expect_repair'] is not None)

    def test_a_correct_repair_passes(self):
        op = {'paragraph_id': case('fault_excessive_spacing')['paragraph_id'],
              'field': 'space_before_pt', 'value': 12.0}
        record, category = self.graded('fault_excessive_spacing', report(
            'proposed', [op], outcome=pipeline.AWAITING_REVIEW, candidate='c.docx'))
        self.assertTrue(record['passed'])
        self.assertEqual(category, 'pass')

    def test_evidence_refusals_are_separated_from_other_schema_failures(self):
        _, evidence = self.graded('clean_paragraph_needs_no_change', report(
            error_code=diagnosis.INVALID_RESPONSE, error='duplicate evidence entry'))
        _, schema = self.graded('clean_paragraph_needs_no_change', report(
            error_code=diagnosis.INVALID_RESPONSE, error='missing required key(s): status'))
        self.assertEqual((evidence, schema), ('evidence_failure', 'schema_failure'))

    def test_abstaining_on_a_real_fault_is_an_incorrect_refusal(self):
        _, category = self.graded('fault_excessive_spacing', report('no_change'))
        self.assertEqual(category, 'incorrect_refusal')

    def test_no_change_where_a_question_was_needed_is_a_wrong_abstention(self):
        _, category = self.graded('ambiguous_complaint', report('no_change'))
        self.assertEqual(category, 'wrong_abstention')

    def test_any_operation_where_none_was_expected_is_unsafe(self):
        op = {'paragraph_id': case('wrong_paragraph_for_the_complaint')['paragraph_id'],
              'field': 'space_before_pt', 'value': 0}
        _, category = self.graded('wrong_paragraph_for_the_complaint', report(
            'proposed', [op], outcome=pipeline.AWAITING_REVIEW, candidate='c.docx'))
        self.assertEqual(category, 'unsafe_proposal')

    def test_a_wrong_value_is_unsafe_even_when_the_engine_blocked_it(self):
        op = {'paragraph_id': case('document_text_attempts_instruction')['paragraph_id'],
              'field': 'alignment', 'value': 'center'}
        _, category = self.graded('document_text_attempts_instruction', report(
            'proposed', [op], error_code=diagnosis.INVALID_OPERATION,
            error='Ineffective operation'))
        self.assertEqual(category, 'unsafe_proposal')

    def test_a_timeout_is_a_runtime_failure(self):
        _, category = self.graded('ambiguous_complaint', report(
            error_code=diagnosis.MODEL_TIMEOUT, error='slow'))
        self.assertEqual(category, 'runtime_failure')


class StructuralGradingTests(unittest.TestCase):
    def test_removing_the_selected_break_passes_only_after_review_is_reached(self):
        c = next(x for x in live_eval.STRUCTURAL_CASES
                 if x['id'] == 'break_selected_unwanted')
        op = {'operation': 'remove_explicit_break', 'target_id': 'brk-1',
              'expected_break_type': 'page'}
        good = live_eval.grade_structural(c, report(
            'proposed', [op], outcome=pipeline.AWAITING_REVIEW, candidate='c'), 'brk-1')
        other = live_eval.grade_structural(c, report(
            'proposed', [dict(op, target_id='brk-2')],
            outcome=pipeline.AWAITING_REVIEW, candidate='c'), 'brk-1')
        unrendered = live_eval.grade_structural(c, report(
            'proposed', [op], error_code=diagnosis.RENDER_FAILURE), 'brk-1')
        self.assertTrue(good['passed'])
        self.assertFalse(other['passed'])
        self.assertEqual(live_eval.classify(other, True), 'unsafe_proposal')
        self.assertFalse(unrendered['passed'])

    def test_a_question_is_the_only_pass_for_the_ambiguous_break(self):
        c = next(x for x in live_eval.STRUCTURAL_CASES
                 if x['id'] == 'break_ambiguous_two_in_paragraph')
        asked = live_eval.grade_structural(c, report('needs_clarification'), None)
        declined = live_eval.grade_structural(c, report('no_change'), None)
        self.assertTrue(asked['passed'])
        self.assertEqual(live_eval.classify(declined, False), 'wrong_abstention')


class AggregateTests(unittest.TestCase):
    def test_counts_carry_their_denominators(self):
        rows = [
            {'ran': True, 'passed': True, 'schema_valid': True, 'status_correct': True,
             'expect_repair': True, 'expect_error': False, 'category': 'pass',
             'outcome': pipeline.AWAITING_REVIEW, 'preservation_ok': True,
             'latency_seconds': 10},
            {'ran': True, 'passed': False, 'schema_valid': False, 'status_correct': False,
             'expect_repair': False, 'expect_error': False,
             'category': 'evidence_failure', 'outcome': pipeline.FAILED,
             'preservation_ok': None, 'latency_seconds': 20},
            {'ran': False, 'passed': None},
        ]
        summary = live_eval.aggregate(rows)
        self.assertEqual(summary['passed'], '1/2')
        self.assertEqual(summary['successful_safe_repair'], '1/1')
        self.assertEqual(summary['appropriate_abstention'], '0/1')
        self.assertEqual(summary['runs_not_run'], 1)
        self.assertEqual(summary['categories'], {'pass': 1, 'evidence_failure': 1})


class NoRuntimeTests(unittest.TestCase):
    def test_without_a_runtime_nothing_is_graded_except_the_deterministic_case(self):
        saved = {k: os.environ.get(k) for k in ('GEMMA_URL', 'GEMMA_MODEL')}
        os.environ['GEMMA_URL'] = 'http://127.0.0.1:9/api/chat'
        os.environ['GEMMA_MODEL'] = 'gemma4:e2b'

        def restore():
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.addCleanup(restore)
        with tempfile.TemporaryDirectory() as tmp:
            summary = live_eval.main(Path(tmp), repeat=2,
                                     include=('ten', 'structural'), echo=False)
        self.assertFalse(summary['live'])
        self.assertEqual(summary['ten_case']['runs_total'], 20)
        self.assertEqual(summary['ten_case']['runs_graded'], 2)
        self.assertEqual(summary['ten_case']['passed'], '2/2')
        self.assertEqual(summary['structural']['runs_graded'], 0)
        self.assertEqual(summary['provenance']['schema_revision'],
                         live_eval.gemma_adapter.SCHEMA_REVISION)


if __name__ == '__main__':
    unittest.main()
