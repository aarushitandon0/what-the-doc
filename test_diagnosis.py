"""Deterministic tests for the diagnosis schema. No model is contacted here.

These establish what the validator refuses, which is the only reason a model's answer
can be trusted far enough to render. They say nothing about how often a real model
answers well: that is measured by live_eval.py against a live runtime.
"""
import json
import math
import unittest

import diagnosis

HASH = 'a' * 64
SUPPLIED = {0, 1, 2}


def payload(**overrides):
    base = {'status': 'proposed',
            'explanation': 'The heading carries a direct 48pt override; its peers inherit 12pt.',
            'evidence': [{'paragraph_id': 1, 'field': 'space_before_pt'}],
            'source_sha256': HASH,
            'operations': [{'paragraph_id': 1, 'field': 'space_before_pt', 'value': 12}]}
    base.update(overrides)
    return base


def check(**overrides):
    return diagnosis.check(payload(**overrides), 1, HASH, SUPPLIED)


class AcceptedShapeTests(unittest.TestCase):
    def test_a_well_formed_proposal_is_normalised_and_accepted(self):
        found = check()
        self.assertEqual(found['status'], 'proposed')
        self.assertEqual(found['operations'],
                         [{'paragraph_id': 1, 'field': 'space_before_pt', 'value': 12}])
        self.assertIsNone(found['question'])

    def test_each_abstention_status_is_a_valid_answer(self):
        for status in ('no_change', 'unsupported'):
            found = check(status=status, operations=[], evidence=[])
            self.assertEqual(found['status'], status)
            self.assertEqual(found['operations'], [])

    def test_needs_clarification_carries_its_question(self):
        found = check(status='needs_clarification', operations=[], evidence=[],
                      question='Do you mean the gap above the heading or below it?')
        self.assertEqual(found['question'],
                         'Do you mean the gap above the heading or below it?')

    def test_plan_of_returns_exactly_the_pair_the_engine_takes(self):
        plan = diagnosis.plan_of(check())
        self.assertEqual(set(plan), {'source_sha256', 'operations'})
        self.assertEqual(plan['source_sha256'], HASH)

    def test_control_characters_in_prose_are_stripped(self):
        found = check(explanation='line one\u0007and\u0000two')
        self.assertEqual(found['explanation'], 'line one and two')

    def test_evidence_may_cite_a_resolved_but_uneditable_field(self):
        # contextual_spacing explains a spacing complaint without being repairable.
        found = check(evidence=[{'paragraph_id': 1, 'field': 'contextual_spacing'},
                                {'paragraph_id': 2, 'field': 'space_before_pt'},
                                {'paragraph_id': 1, 'field': 'space_before_pt'}])
        self.assertEqual(len(found['evidence']), 3)


class EmptyPlanTests(unittest.TestCase):
    """The rule the whole module exists for: nothing may call an empty plan a repair."""

    def test_proposed_with_no_operations_is_rejected(self):
        with self.assertRaises(diagnosis.DiagnosisError) as caught:
            check(operations=[])
        self.assertIn('not a repair', str(caught.exception))

    def test_proposed_must_cite_evidence(self):
        with self.assertRaises(diagnosis.DiagnosisError):
            check(evidence=[])

    def test_an_abstention_may_not_smuggle_in_operations(self):
        for status in ('no_change', 'unsupported', 'needs_clarification'):
            with self.assertRaises(diagnosis.DiagnosisError):
                check(status=status, question='which gap?')


class RejectedShapeTests(unittest.TestCase):
    def test_unknown_status_is_rejected(self):
        with self.assertRaises(diagnosis.DiagnosisError):
            check(status='fixed')

    def test_a_self_assigned_confidence_score_is_rejected(self):
        # Model confidence must never become an approval condition, so it is refused
        # outright rather than recorded and ignored.
        with self.assertRaises(diagnosis.DiagnosisError) as caught:
            check(confidence=0.99)
        self.assertIn('confidence', str(caught.exception))

    def test_any_other_invented_key_is_rejected(self):
        with self.assertRaises(diagnosis.DiagnosisError):
            check(xml_patch='<w:p/>')

    def test_a_missing_required_key_is_rejected(self):
        broken = payload()
        del broken['evidence']
        with self.assertRaises(diagnosis.DiagnosisError):
            diagnosis.check(broken, 1, HASH, SUPPLIED)

    def test_a_stale_or_rewritten_hash_is_rejected(self):
        with self.assertRaises(diagnosis.DiagnosisError):
            check(source_sha256='b' * 64)

    def test_evidence_citing_a_paragraph_that_was_not_supplied_is_rejected(self):
        with self.assertRaises(diagnosis.DiagnosisError) as caught:
            check(evidence=[{'paragraph_id': 99, 'field': 'space_before_pt'}])
        self.assertIn('not supplied', str(caught.exception))

    def test_evidence_citing_an_unknown_property_is_rejected(self):
        with self.assertRaises(diagnosis.DiagnosisError):
            check(evidence=[{'paragraph_id': 1, 'field': 'font_size_pt'}])

    def test_a_boolean_does_not_pass_as_a_paragraph_id(self):
        with self.assertRaises(diagnosis.DiagnosisError):
            check(evidence=[{'paragraph_id': True, 'field': 'space_before_pt'}])

    def abstain(self, evidence, status='no_change'):
        question = 'Which part looks wrong?' if status == 'needs_clarification' else None
        return check(status=status, operations=[], evidence=evidence, question=question)

    def test_an_abstentions_exact_duplicate_evidence_is_dropped_keeping_the_first(self):
        # A citation list says which inputs were relied on; citing one twice adds
        # nothing, and Ollama's grammar path does not enforce uniqueItems. In an
        # abstention the repeat is dropped, the first occurrence and the order are
        # kept, and the count is reported so a normalised answer never looks untouched.
        spacing = {'paragraph_id': 1, 'field': 'space_before_pt'}
        peer = {'paragraph_id': 0, 'field': 'space_before_pt'}
        for status in ('no_change', 'unsupported', 'needs_clarification'):
            with self.subTest(status=status):
                found = self.abstain([spacing, peer, dict(spacing), dict(peer),
                                      dict(spacing)], status)
                self.assertEqual(found['status'], status)
                self.assertEqual(found['evidence'], [spacing, peer])
                self.assertEqual(found['evidence_duplicates_removed'], 3)
                self.assertEqual(found['operations'], [])

    def test_a_proposal_with_duplicate_evidence_is_still_rejected(self):
        # Kept deliberately. Measured live, normalising repeats in proposals let
        # answers through that the refusal had been stopping, and one wrong repair
        # reached review. Normalisation must never move a repair toward approval.
        entry = {'paragraph_id': 1, 'field': 'space_before_pt'}
        with self.assertRaisesRegex(diagnosis.DiagnosisError, 'duplicate evidence'):
            check(evidence=[entry, dict(entry)])

    def test_an_answer_without_duplicates_reports_none_removed(self):
        self.assertEqual(check()['evidence_duplicates_removed'], 0)
        self.assertEqual(self.abstain([])['evidence_duplicates_removed'], 0)

    def test_normalisation_changes_nothing_but_the_evidence_list(self):
        entry = {'paragraph_id': 1, 'field': 'alignment'}
        once, twice = self.abstain([entry]), self.abstain([entry, dict(entry)])
        for key in ('status', 'operations', 'source_sha256', 'question', 'evidence',
                    'explanation'):
            self.assertEqual(once[key], twice[key])

    def test_an_abstention_still_may_not_carry_operations(self):
        entry = {'paragraph_id': 1, 'field': 'space_before_pt'}
        with self.assertRaisesRegex(diagnosis.DiagnosisError, 'must carry no operations'):
            check(status='no_change', evidence=[entry, dict(entry)])

    def test_different_evidence_is_never_merged(self):
        a = {'paragraph_id': 1, 'field': 'space_before_pt'}
        b = {'paragraph_id': 1, 'field': 'space_after_pt'}
        c = {'paragraph_id': 2, 'field': 'space_before_pt'}
        for found in (check(evidence=[a, b, c]), self.abstain([a, b, c])):
            self.assertEqual(found['evidence'], [a, b, c])
            self.assertEqual(found['evidence_duplicates_removed'], 0)

    def test_a_repeated_entry_that_is_invalid_still_fails(self):
        # Structure is validated on every entry before anything is dropped, so a
        # repeat cannot hide a fabricated citation behind a valid first copy.
        good = {'paragraph_id': 1, 'field': 'space_before_pt'}
        for bad in ({'paragraph_id': 99, 'field': 'space_before_pt'},
                    {'paragraph_id': 1, 'field': 'font_size_pt'},
                    {'paragraph_id': True, 'field': 'space_before_pt'},
                    {'paragraph_id': 1.0, 'field': 'space_before_pt'},
                    {'paragraph_id': 1, 'field': 'space_before_pt', 'weight': 2},
                    {'paragraph_id': 1, 'field': ['space_before_pt']},
                    'paragraph 1'):
            with self.subTest(bad=bad), self.assertRaises(diagnosis.DiagnosisError):
                self.abstain([good, bad, dict(good)])

    def test_near_duplicates_are_not_treated_as_exact(self):
        # True == 1 in Python; the type check must refuse it rather than let it be
        # folded into the genuine paragraph-1 citation.
        good = {'paragraph_id': 1, 'field': 'space_before_pt'}
        with self.assertRaises(diagnosis.DiagnosisError):
            self.abstain([good, {'paragraph_id': True, 'field': 'space_before_pt'}])

    def test_too_many_entries_fail_even_when_they_are_all_the_same(self):
        # The size bound applies to what the model sent, so a degenerate repetition
        # loop is still refused rather than collapsed into one tidy citation.
        entry = {'paragraph_id': 1, 'field': 'space_before_pt'}
        with self.assertRaises(diagnosis.DiagnosisError) as caught:
            self.abstain([dict(entry) for _ in range(diagnosis.MAX_EVIDENCE + 1)])
        self.assertIn('at most', str(caught.exception))

    def test_every_changed_property_must_be_cited(self):
        # The live injection answer: cited alignment, changed five properties.
        with self.assertRaisesRegex(diagnosis.DiagnosisError, 'does not cite'):
            check(evidence=[{'paragraph_id': 1, 'field': 'alignment'}],
                  operations=[{'paragraph_id': 1, 'field': 'alignment', 'value': 'both'},
                              {'paragraph_id': 1, 'field': 'left_indent_pt', 'value': 144}])

    def test_citing_a_peer_does_not_cover_the_selected_paragraph(self):
        with self.assertRaisesRegex(diagnosis.DiagnosisError, 'does not cite'):
            check(evidence=[{'paragraph_id': 0, 'field': 'space_before_pt'}])

    def test_each_cited_change_is_accepted(self):
        found = check(evidence=[{'paragraph_id': 1, 'field': 'alignment'},
                                {'paragraph_id': 1, 'field': 'space_before_pt'},
                                {'paragraph_id': 0, 'field': 'space_before_pt'}],
                      operations=[{'paragraph_id': 1, 'field': 'alignment', 'value': 'both'},
                                  {'paragraph_id': 1, 'field': 'space_before_pt',
                                   'value': 12}])
        self.assertEqual(len(found['operations']), 2)

    def test_a_proposal_still_needs_evidence(self):
        with self.assertRaises(diagnosis.DiagnosisError):
            check(evidence=[])

    def test_an_operation_outside_the_selected_paragraph_is_rejected(self):
        with self.assertRaises(diagnosis.DiagnosisError) as caught:
            check(operations=[{'paragraph_id': 2, 'field': 'alignment', 'value': 'both'}])
        self.assertIn('selected paragraph', str(caught.exception))

    def test_an_unsupported_operation_field_is_rejected(self):
        with self.assertRaises(diagnosis.DiagnosisError):
            check(operations=[{'paragraph_id': 1, 'field': 'text', 'value': 'rewritten'}])

    def test_a_citable_but_uneditable_field_cannot_be_changed(self):
        with self.assertRaises(diagnosis.DiagnosisError):
            check(operations=[{'paragraph_id': 1, 'field': 'outline_level', 'value': 1}])

    def test_a_non_finite_value_is_rejected(self):
        with self.assertRaises(diagnosis.DiagnosisError):
            check(operations=[{'paragraph_id': 1, 'field': 'space_before_pt',
                               'value': float('nan')}])

    def test_an_operation_with_extra_keys_is_rejected(self):
        with self.assertRaises(diagnosis.DiagnosisError):
            check(operations=[{'paragraph_id': 1, 'field': 'space_before_pt',
                               'value': 12, 'reason': 'peers'}])

    def test_empty_or_oversized_prose_is_rejected(self):
        with self.assertRaises(diagnosis.DiagnosisError):
            check(explanation='   ')
        with self.assertRaises(diagnosis.DiagnosisError):
            check(explanation='x' * (diagnosis.MAX_EXPLANATION + 1))

    def test_needs_clarification_without_a_question_is_rejected(self):
        with self.assertRaises(diagnosis.DiagnosisError):
            check(status='needs_clarification', operations=[], evidence=[])

    def test_a_non_string_question_is_rejected(self):
        with self.assertRaises(diagnosis.DiagnosisError):
            check(question={'ask': 'which gap?'})

    def test_a_non_object_response_is_rejected(self):
        with self.assertRaises(diagnosis.DiagnosisError):
            diagnosis.check([payload()], 1, HASH, SUPPLIED)


class QuestionNormalisationTests(unittest.TestCase):
    def test_a_question_beside_a_proposal_is_dropped_not_surfaced(self):
        # Small models fill every key in the template. The user-visible rule is that a
        # question only ever appears with needs_clarification; dropping a redundant one
        # keeps that rule without discarding an otherwise sound diagnosis.
        found = check(question='Is 12 points what you wanted?')
        self.assertEqual(found['status'], 'proposed')
        self.assertIsNone(found['question'])


class ParseTests(unittest.TestCase):
    def test_valid_json_text_is_parsed_and_validated(self):
        found = diagnosis.parse(json.dumps(payload()), 1, HASH, SUPPLIED)
        self.assertEqual(found['status'], 'proposed')

    def test_invalid_json_is_reported_as_an_invalid_response(self):
        with self.assertRaises(diagnosis.DiagnosisError) as caught:
            diagnosis.parse('{"status": "proposed"', 1, HASH, SUPPLIED)
        self.assertEqual(caught.exception.code, diagnosis.INVALID_RESPONSE)

    def test_an_empty_response_is_reported_as_an_invalid_response(self):
        with self.assertRaises(diagnosis.DiagnosisError):
            diagnosis.parse('   ', 1, HASH, SUPPLIED)

    def test_prose_wrapped_around_the_json_is_rejected(self):
        with self.assertRaises(diagnosis.DiagnosisError):
            diagnosis.parse('Here is my answer: ' + json.dumps(payload()),
                            1, HASH, SUPPLIED)

    def test_every_error_carries_a_code_a_caller_can_branch_on(self):
        with self.assertRaises(diagnosis.DiagnosisError) as caught:
            check(status='fixed')
        self.assertEqual(caught.exception.code, diagnosis.INVALID_RESPONSE)
        self.assertTrue(caught.exception.message)


if __name__ == '__main__':
    unittest.main()
