"""Tests for the parts of the diagnosis layer that need no live model.

No test here contacts a model. Prompt wording and real proposal quality are Milestone C
and are not established by these tests.
"""
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import break_fixtures
import diagnosis
import engine
import eval_fixtures
import fixtures
import gemma_adapter


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name)/'sample.docx'
        fixtures.build(self.source)

    def test_evidence_carries_resolved_values_with_their_origin(self):
        found = gemma_adapter.evidence(self.source, 1)
        resolved = found['selected']['effective_properties']['space_before_pt']
        self.assertEqual((resolved['value'], resolved['origin']), (48.0, 'direct'))

    def test_evidence_includes_the_peer_distribution_to_judge_against(self):
        found = gemma_adapter.evidence(self.source, fixtures.FAULT_IDS['excessive_space_before'])
        self.assertEqual(found['peer_comparison']['space_before_pt']['peer_values'],
                         [{'value': 12.0, 'count': 3}])

    def test_evidence_omits_raw_twips_so_the_model_need_not_convert(self):
        blob = json.dumps(gemma_adapter.evidence(self.source, 1))
        self.assertNotIn('"raw"', blob)
        self.assertNotIn('960', blob)

    def test_evidence_is_windowed_around_the_selection(self):
        found = gemma_adapter.evidence(self.source, 6, context=1)
        self.assertEqual([r['paragraph_id'] for r in found['nearby_paragraphs']], [5, 6, 7])

    def test_evidence_does_not_leak_the_file_path(self):
        self.assertNotIn('sample.docx', json.dumps(gemma_adapter.evidence(self.source, 1)))

    def test_evidence_rejects_an_invalid_selection(self):
        with self.assertRaises(ValueError):
            gemma_adapter.evidence(self.source, 999)

    def test_a_paragraph_with_no_blocked_field_reports_an_empty_map(self):
        found = gemma_adapter.evidence(self.source, 1)
        self.assertEqual(found['fields_that_cannot_be_repaired'], {})

    def test_a_paragraph_with_peers_reports_how_many_it_has(self):
        found = gemma_adapter.evidence(self.source, 1)
        self.assertEqual(found['peer_count'], 3)


class BlockedFieldEvidenceTests(unittest.TestCase):
    """The alternative-measurement refusal, restated where a model cannot miss it.

    Both live models proposed a spacing repair on a paragraph whose spacing is already
    decided by an inherited afterAutospacing, and `engine.validate` - not the model -
    is what stopped it. These tests cover the derived summary added so the constraint
    is stated plainly in the payload as well as nested inside effective_properties.

    The summary is derived from the same resolver the validator uses. It grants
    nothing: the refusal still lives in engine.validate and is asserted here to still
    fire regardless of what a model does with the hint.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name)/'diagnostic.docx'
        eval_fixtures.build(self.source)
        self.blocked_id = eval_fixtures.IDS['autospacing']

    def test_an_inherited_alternative_measurement_is_named_at_the_top_level(self):
        found = gemma_adapter.evidence(self.source, self.blocked_id)
        self.assertEqual(found['fields_that_cannot_be_repaired'],
                         {'space_after_pt': ['style:BodyAuto:afterAutospacing']})

    def test_the_summary_agrees_with_the_nested_overridden_by(self):
        found = gemma_adapter.evidence(self.source, self.blocked_id)
        nested = found['selected']['effective_properties']['space_after_pt']['overridden_by']
        self.assertEqual(found['fields_that_cannot_be_repaired']['space_after_pt'], nested)

    def test_only_editable_fields_are_listed_since_only_those_can_be_proposed(self):
        found = gemma_adapter.evidence(self.source, self.blocked_id)
        self.assertTrue(set(found['fields_that_cannot_be_repaired']) <= set(engine.FIELDS))

    def test_the_validator_still_refuses_the_field_whatever_the_model_does_with_the_hint(self):
        # The hint is advisory to the model; the refusal is not advisory to anyone.
        plan = {'source_sha256': engine.signature(self.source),
                'operations': [{'paragraph_id': self.blocked_id,
                                'field': 'space_after_pt', 'value': 6.0}]}
        with self.assertRaises(ValueError) as caught:
            gemma_adapter.validate_proposal(self.source, plan)
        self.assertIn('Alternative measurement', str(caught.exception))

    def test_a_paragraph_with_no_peers_says_so_instead_of_sending_an_empty_list(self):
        # peer_count 0 and peer_values [] previously reached the model as silence, and
        # both live models invented a peer value to fill it. The count is now stated.
        found = gemma_adapter.evidence(self.source, self.blocked_id)
        self.assertEqual(found['peer_count'], 0)
        self.assertEqual(found['peer_comparison']['space_after_pt']['peer_values'], [])

    def test_the_peer_count_matches_what_the_engine_reports(self):
        found = gemma_adapter.evidence(self.source, self.blocked_id)
        self.assertEqual(found['peer_count'],
                         engine.peers(self.source, self.blocked_id)['peer_count'])

    def test_the_summary_does_not_leak_raw_twips_or_the_file_path(self):
        blob = json.dumps(gemma_adapter.evidence(self.source, self.blocked_id))
        self.assertNotIn('diagnostic.docx', blob)
        self.assertNotIn('"raw"', blob)


class AbstentionShapeTests(unittest.TestCase):
    """An abstention carrying operations is rejected, and stays rejected.

    On the ambiguous complaint the live 4.6B model abstained but still filled
    `operations`, so the whole answer was refused as `invalid_response` and the user
    saw an error instead of a question. The prompt now states the rule explicitly and
    shows a worked abstention, but the *validator* is unchanged on purpose: dropping
    the operations and reporting "no_change" would turn invalid model output into a
    successful diagnosis, which is exactly what must never happen. These tests pin
    that decision down so a later "fix" cannot quietly relax it.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name)/'sample.docx'
        fixtures.build(self.source)
        self.hash = engine.signature(self.source)

    def answer(self, **overrides):
        raw = {'status': 'no_change', 'explanation': 'Nothing needs changing.',
               'evidence': [], 'source_sha256': self.hash, 'operations': []}
        raw.update(overrides)
        return raw

    def test_an_abstention_carrying_operations_is_refused_not_silently_emptied(self):
        for status in ('no_change', 'unsupported', 'needs_clarification'):
            with self.subTest(status=status):
                raw = self.answer(status=status, question='Which part?',
                                  operations=[{'paragraph_id': 1,
                                               'field': 'alignment', 'value': 'both'}])
                with self.assertRaises(diagnosis.DiagnosisError) as caught:
                    diagnosis.check(raw, 1, self.hash, {1})
                self.assertEqual(caught.exception.code, diagnosis.INVALID_RESPONSE)
                self.assertIn('must carry no operations', caught.exception.message)

    def test_a_string_valued_operation_is_refused(self):
        # The live model emitted "6.0" rather than 6.0 on the same ambiguous case.
        raw = self.answer(status='proposed', explanation='Match the peers.',
                          evidence=[{'paragraph_id': 1, 'field': 'space_after_pt'}],
                          operations=[{'paragraph_id': 1, 'field': 'space_after_pt',
                                       'value': '6.0'}])
        found = diagnosis.check(raw, 1, self.hash, {1})
        # The shape check passes a string through; engine.validate is the bound.
        with self.assertRaises(ValueError):
            gemma_adapter.validate_proposal(self.source, diagnosis.plan_of(found))

    def test_a_null_valued_operation_is_refused(self):
        # The live 7.5B model proposed "value": null on a paragraph with nothing set.
        raw = self.answer(status='proposed', explanation='Match the peers.',
                          evidence=[{'paragraph_id': 1, 'field': 'space_before_pt'}],
                          operations=[{'paragraph_id': 1, 'field': 'space_before_pt',
                                       'value': None}])
        found = diagnosis.check(raw, 1, self.hash, {1})
        with self.assertRaises(ValueError) as caught:
            gemma_adapter.validate_proposal(self.source, diagnosis.plan_of(found))
        self.assertIn('between 0 and 144', str(caught.exception))

    def test_a_clean_abstention_is_still_accepted(self):
        found = diagnosis.check(self.answer(), 1, self.hash, {1})
        self.assertEqual((found['status'], found['operations']), ('no_change', []))


class PromptRevisionTests(unittest.TestCase):
    """What the prompt must keep saying. Wording may change; these rules may not."""

    def setUp(self):
        self.prompt = gemma_adapter.DIAGNOSIS_SYSTEM
        self.collapsed = ' '.join(self.prompt.lower().split())

    def test_the_prompt_states_that_an_abstention_carries_no_operations(self):
        self.assertIn('"operations" must be the empty list', self.prompt)

    def test_the_prompt_shows_a_worked_abstention_so_the_shape_is_not_guessed(self):
        self.assertIn('"status": "needs_clarification"', self.prompt)
        self.assertIn('"operations": [],', self.prompt)

    def test_the_prompt_names_the_blocked_field_summary_it_is_given(self):
        self.assertIn('fields_that_cannot_be_repaired', self.prompt)

    def test_the_prompt_forbids_inventing_peers_when_there_are_none(self):
        self.assertIn('peer_count is 0 there are no comparable paragraphs',
                      ' '.join(self.prompt.split()))

    def test_the_prompt_still_forbids_inventing_a_confidence_score(self):
        self.assertIn('do not include a confidence score', self.collapsed)

    def test_the_prompt_is_the_one_measured_with_the_v3_baseline(self):
        # Rewordings are measured before they ship; this pins the measured text.
        self.assertEqual(gemma_adapter.prompt_sha256(), gemma_adapter.PROMPT_SHA256_V3)

    def test_the_worked_example_cannot_be_copied_as_a_real_hash(self):
        # The example must not carry a hash-shaped literal a model could echo back.
        self.assertIn('"<copy the value from the input>"', self.prompt)


class ResponseHandlingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name)/'sample.docx'
        fixtures.build(self.source)
        self.hash = engine.signature(self.source)

    def plan(self, **overrides):
        base = {'source_sha256': self.hash,
                'operations': [{'paragraph_id': 1, 'field': 'space_before_pt', 'value': 12}]}
        base.update(overrides)
        return base

    def test_well_formed_response_passes_both_checks(self):
        plan = self.plan()
        gemma_adapter.check_shape(plan, 1, self.hash)
        self.assertTrue(gemma_adapter.validate_proposal(self.source, plan))

    def test_abstention_is_accepted_as_a_valid_answer(self):
        gemma_adapter.check_shape(self.plan(operations=[]), 1, self.hash)

    def test_response_targeting_another_paragraph_is_rejected(self):
        plan = self.plan(operations=[{'paragraph_id': 8, 'field': 'space_before_pt', 'value': 12}])
        with self.assertRaises(ValueError):
            gemma_adapter.check_shape(plan, 1, self.hash)

    def test_response_with_a_rewritten_hash_is_rejected(self):
        with self.assertRaises(ValueError):
            gemma_adapter.check_shape(self.plan(source_sha256='0' * 64), 1, self.hash)

    def test_response_with_extra_keys_is_rejected(self):
        with self.assertRaises(ValueError):
            gemma_adapter.check_shape(self.plan(explanation='because'), 1, self.hash)

    def test_out_of_bounds_value_is_rejected_by_the_validator(self):
        plan = self.plan(operations=[{'paragraph_id': 1, 'field': 'space_before_pt', 'value': 5000}])
        with self.assertRaises(ValueError):
            gemma_adapter.validate_proposal(self.source, plan)

    def test_unsupported_field_is_rejected_by_the_validator(self):
        plan = self.plan(operations=[{'paragraph_id': 1, 'field': 'text', 'value': 'rewritten'}])
        with self.assertRaises(ValueError):
            gemma_adapter.validate_proposal(self.source, plan)

    def test_validation_writes_nothing(self):
        before = {p.name: p.read_bytes() for p in Path(self.tmp.name).iterdir()}
        gemma_adapter.validate_proposal(self.source, self.plan())
        after = {p.name: p.read_bytes() for p in Path(self.tmp.name).iterdir()}
        self.assertEqual(before, after)

    def test_propose_refuses_to_run_without_a_configured_model(self):
        saved = os.environ.pop('GEMMA_MODEL', None)
        self.addCleanup(lambda: os.environ.update({'GEMMA_MODEL': saved}) if saved else None)
        with self.assertRaises(RuntimeError):
            gemma_adapter.propose(self.source, 'too much space', 1)


class RuntimeGuardTests(unittest.TestCase):
    """Gemma is required, and an absent runtime is reported rather than worked around.

    Nothing here contacts a model: the unreachable cases point at a closed local port
    on purpose, so they assert the failure path instead of needing a live runtime.
    """

    # A reserved TLD never resolves, so this fails fast and deterministically with a
    # connection error rather than hanging until the timeout elapses.
    CLOSED = 'http://wtd-no-such-runtime.invalid/api/chat'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name)/'sample.docx'
        fixtures.build(self.source)

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

    def test_a_gemma_model_tag_is_accepted(self):
        self.environment(WTD_ALLOW_NON_GEMMA=None)
        self.assertTrue(gemma_adapter.require_gemma('gemma4:e2b'))

    def test_another_model_family_is_refused_rather_than_substituted(self):
        self.environment(WTD_ALLOW_NON_GEMMA=None)
        with self.assertRaises(diagnosis.DiagnosisError) as caught:
            gemma_adapter.require_gemma('qwen3:4b')
        self.assertEqual(caught.exception.code, diagnosis.MODEL_UNAVAILABLE)

    def test_a_non_gemma_baseline_must_be_opted_into_explicitly(self):
        self.environment(WTD_ALLOW_NON_GEMMA='1')
        # False records that the run was not Gemma, so a result can never be
        # reported as Gemma's by accident.
        self.assertFalse(gemma_adapter.require_gemma('qwen3:4b'))

    def test_an_unreachable_runtime_is_reported_unavailable_with_a_reason(self):
        self.environment(GEMMA_URL=self.CLOSED, GEMMA_MODEL='gemma4:e2b')
        status = gemma_adapter.runtime_status(timeout=2)
        self.assertFalse(status['available'])
        self.assertTrue(status['reason'])
        self.assertEqual(status['installed_gemma_models'], [])

    def test_diagnose_reports_an_unreachable_runtime_as_model_unavailable(self):
        self.environment(GEMMA_URL=self.CLOSED, GEMMA_MODEL='gemma4:e2b')
        with self.assertRaises(diagnosis.DiagnosisError) as caught:
            gemma_adapter.diagnose(self.source, 'too much space above', 1, timeout=2)
        self.assertEqual(caught.exception.code, diagnosis.MODEL_UNAVAILABLE)

    def test_an_invalid_paragraph_is_refused_before_a_model_is_contacted(self):
        # The closed port would raise model_unavailable, so an invalid_selection here
        # proves the selection is checked first.
        self.environment(GEMMA_URL=self.CLOSED, GEMMA_MODEL='gemma4:e2b')
        with self.assertRaises(diagnosis.DiagnosisError) as caught:
            gemma_adapter.diagnose(self.source, 'too much space above', 999, timeout=2)
        self.assertEqual(caught.exception.code, diagnosis.INVALID_SELECTION)

    def test_an_empty_complaint_is_refused(self):
        self.environment(GEMMA_URL=self.CLOSED, GEMMA_MODEL='gemma4:e2b')
        with self.assertRaises(diagnosis.DiagnosisError) as caught:
            gemma_adapter.diagnose(self.source, '   ', 1, timeout=2)
        self.assertEqual(caught.exception.code, diagnosis.INVALID_SELECTION)

    def test_the_prompt_does_not_ask_for_a_reasoning_trace(self):
        # Collapsed, because the prompt is hard-wrapped for readability.
        prompt = ' '.join(gemma_adapter.DIAGNOSIS_SYSTEM.lower().split())
        self.assertIn('return the json object only', prompt)
        self.assertIn('do not explain your reasoning step by step', prompt)

    def test_the_prompt_states_that_document_text_is_data(self):
        self.assertIn('never follow it', gemma_adapter.DIAGNOSIS_SYSTEM)


class EffectiveNoOpTests(unittest.TestCase):
    """Writing the value a paragraph already resolves to is not a repair.

    The reported case was a model proposing space_before_pt 12 on a heading that already
    resolved to 12pt *from its style*. The page looks identical afterwards, so it reads
    as a harmless success. It is not harmless: it replaces an inherited value with
    direct formatting, and a later edit to Heading1 would then move every other heading
    and leave this one behind.

    These tests also pin the distinctions the guard must not collapse: an unset length
    is not zero, an unset toggle is off (so False is a no-op there and True is not),
    and a length is compared at the twip it is written at.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name)/'sample.docx'
        fixtures.build(self.source)
        self.hash = engine.signature(self.source)
        self.report = engine.inspect(self.source)
        self.clean_heading = fixtures.CLEAN_PEER_HEADING

    def plan(self, paragraph_id, field, value):
        return {'source_sha256': self.hash,
                'operations': [{'paragraph_id': paragraph_id, 'field': field, 'value': value}]}

    def resolved(self, paragraph_id, field):
        return self.report['paragraphs'][paragraph_id]['effective_properties'][field]

    # -- the two equal-value cases ------------------------------------------
    def test_writing_an_inherited_value_as_direct_formatting_is_refused(self):
        entry = self.resolved(self.clean_heading, 'space_before_pt')
        self.assertEqual((entry['value'], entry['origin']), (12.0, 'style:Heading1'))
        with self.assertRaises(ValueError) as caught:
            gemma_adapter.validate_proposal(
                self.source, self.plan(self.clean_heading, 'space_before_pt', entry['value']))
        self.assertIn(gemma_adapter.INEFFECTIVE_MARKER, str(caught.exception))
        self.assertIn('style:Heading1', str(caught.exception))

    def test_writing_a_direct_value_equal_to_the_existing_direct_value_is_refused(self):
        faulty = fixtures.FAULT_IDS['excessive_space_before']
        entry = self.resolved(faulty, 'space_before_pt')
        self.assertEqual((entry['value'], entry['origin']), (48.0, 'direct'))
        with self.assertRaises(ValueError) as caught:
            gemma_adapter.validate_proposal(
                self.source, self.plan(faulty, 'space_before_pt', 48.0))
        self.assertIn(gemma_adapter.INEFFECTIVE_MARKER, str(caught.exception))

    def test_an_equal_length_is_refused_at_the_resolution_it_is_written_at(self):
        # 12.004pt and 12.0pt both encode to 240 twips, so the second is not a repair.
        with self.assertRaises(ValueError):
            gemma_adapter.validate_proposal(
                self.source, self.plan(self.clean_heading, 'space_before_pt', 12.004))

    def test_a_length_that_differs_by_a_whole_twip_is_a_real_change(self):
        self.assertTrue(gemma_adapter.validate_proposal(
            self.source, self.plan(self.clean_heading, 'space_before_pt', 12.05)))

    def test_an_equal_alignment_is_refused_and_a_different_one_is_kept(self):
        centred = fixtures.FAULT_IDS['inconsistent_alignment']
        current = self.resolved(centred, 'alignment')['value']
        self.assertEqual(current, 'center')
        with self.assertRaises(ValueError):
            gemma_adapter.validate_proposal(self.source, self.plan(centred, 'alignment', current))
        self.assertTrue(gemma_adapter.validate_proposal(
            self.source, self.plan(centred, 'alignment', 'both')))

    def test_an_equal_toggle_is_refused(self):
        broken = fixtures.FAULT_IDS['unwanted_page_break_before']
        self.assertIs(self.resolved(broken, 'page_break_before')['value'], True)
        with self.assertRaises(ValueError):
            gemma_adapter.validate_proposal(self.source, self.plan(broken, 'page_break_before', True))

    # -- the real repairs the guard must not touch --------------------------
    def test_every_labelled_repair_in_the_fixture_still_validates(self):
        for paragraph_id, fault in fixtures.FAULTS.items():
            expected = fault['expected_repair']
            plan = self.plan(paragraph_id, expected['field'], expected['value'])
            self.assertTrue(gemma_adapter.validate_proposal(self.source, plan),
                            'the labelled repair for %s must still validate' % fault['fault'])
            self.assertEqual(gemma_adapter.ineffective_operations(self.source, plan), [])

    # -- the distinctions that must not be collapsed ------------------------
    def test_an_unset_property_is_not_zero_so_writing_zero_is_a_real_change(self):
        unset = next(r['paragraph_id'] for r in self.report['paragraphs']
                     if r['effective_properties']['space_before_pt']['origin']
                     == 'application_default')
        self.assertIsNone(self.resolved(unset, 'space_before_pt')['value'])
        plan = self.plan(unset, 'space_before_pt', 0)
        self.assertTrue(gemma_adapter.validate_proposal(self.source, plan))
        self.assertEqual(gemma_adapter.ineffective_operations(self.source, plan), [])

    # Changed deliberately (TEST_RESULTS.md section 18). This test used to pin the
    # opposite: that writing False to an unset toggle is a real change. ECMA-376 says a
    # toggle no layer of the style hierarchy sets "shall not be applied" (pageBreakBefore
    # 17.3.1.23, keepNext 17.3.1.15), so unset already means off. Writing False there
    # changes nothing on the page and only pins a direct override, which is exactly the
    # inherited -> direct case this guard already refuses. Measured live, the model
    # proposed this write and it reached review.
    def test_writing_false_to_an_unset_toggle_is_refused_as_ineffective(self):
        for field in gemma_adapter.TOGGLE_FIELDS:
            unset = next(r['paragraph_id'] for r in self.report['paragraphs']
                         if r['effective_properties'][field]['origin']
                         == 'application_default')
            self.assertIsNone(self.resolved(unset, field)['value'])
            with self.assertRaises(ValueError) as caught:
                gemma_adapter.validate_proposal(self.source, self.plan(unset, field, False))
            self.assertIn(gemma_adapter.INEFFECTIVE_MARKER, str(caught.exception))
            self.assertIn('off (no layer sets it)', str(caught.exception))

    def test_writing_true_to_an_unset_toggle_is_still_a_real_change(self):
        for field in gemma_adapter.TOGGLE_FIELDS:
            unset = next(r['paragraph_id'] for r in self.report['paragraphs']
                         if r['effective_properties'][field]['origin']
                         == 'application_default')
            plan = self.plan(unset, field, True)
            self.assertTrue(gemma_adapter.validate_proposal(self.source, plan))
            self.assertEqual(gemma_adapter.ineffective_operations(self.source, plan), [])

    def test_a_plan_mixing_a_real_change_with_false_on_an_unset_toggle_keeps_only_the_change(self):
        # Measured live (TEST_RESULTS.md 18.3, intermediate run): an answer padded a real
        # repair with keep_with_next False on a paragraph where nothing sets it.
        centred = fixtures.FAULT_IDS['inconsistent_alignment']
        self.assertEqual(self.resolved(centred, 'keep_with_next')['origin'],
                         'application_default')
        plan = {'source_sha256': self.hash, 'operations': [
            {'paragraph_id': centred, 'field': 'alignment', 'value': 'both'},
            {'paragraph_id': centred, 'field': 'keep_with_next', 'value': False}]}
        prepared = gemma_adapter.prepare_proposal(self.source, plan)
        self.assertEqual(prepared['operations'], [plan['operations'][0]])

    def test_the_comparison_keeps_zero_unset_and_booleans_apart(self):
        # Zero is a value, not an absence: once something resolves to 0pt, writing 0pt
        # changes nothing. An unset length compares equal to nothing at all; an unset
        # toggle is off, so it equals False and nothing else.
        self.assertTrue(gemma_adapter._is_effective_noop('space_before_pt', 0.0, 0))
        self.assertFalse(gemma_adapter._is_effective_noop('space_before_pt', None, 0))
        self.assertTrue(gemma_adapter._is_effective_noop('page_break_before', None, False))
        self.assertTrue(gemma_adapter._is_effective_noop('keep_with_next', None, False))
        self.assertFalse(gemma_adapter._is_effective_noop('page_break_before', None, True))
        self.assertFalse(gemma_adapter._is_effective_noop('page_break_before', None, 0))
        self.assertFalse(gemma_adapter._is_effective_noop('page_break_before', False, True))
        self.assertTrue(gemma_adapter._is_effective_noop('page_break_before', False, False))
        self.assertFalse(gemma_adapter._is_effective_noop('alignment', None, 'left'))

    def test_an_alternative_measurement_is_still_reported_as_unsupported_formatting(self):
        # The autospacing refusal must win: "a value written here would be ignored by
        # the layout" is a more useful answer than "this equals the current value".
        other = Path(self.tmp.name)/'blocked.docx'
        eval_fixtures.build(other)
        blocked_id = eval_fixtures.IDS['autospacing']
        current = engine.inspect(other)['paragraphs'][blocked_id]['effective_properties']
        plan = {'source_sha256': engine.signature(other),
                'operations': [{'paragraph_id': blocked_id, 'field': 'space_after_pt',
                                'value': current['space_after_pt']['value'] or 6}]}
        with self.assertRaises(ValueError) as caught:
            gemma_adapter.validate_proposal(other, plan)
        self.assertIn('Alternative measurement', str(caught.exception))
        self.assertEqual(gemma_adapter.ineffective_operations(other, plan), [])

    # -- mixed plans and the deliberate override ----------------------------
    def test_a_plan_mixing_a_real_change_with_a_redundant_one_is_kept_and_named(self):
        spacing = fixtures.FAULT_IDS['excessive_space_before']
        plan = {'source_sha256': self.hash, 'operations': [
            {'paragraph_id': spacing, 'field': 'space_before_pt', 'value': 12.0},
            {'paragraph_id': self.clean_heading, 'field': 'space_before_pt', 'value': 12.0}]}
        self.assertTrue(gemma_adapter.validate_proposal(self.source, plan))
        findings = gemma_adapter.ineffective_operations(self.source, plan)
        self.assertEqual([f['paragraph_id'] for f in findings], [self.clean_heading])
        self.assertEqual(findings[0]['current_origin'], 'style:Heading1')

    def test_pinning_an_inherited_value_stays_possible_for_a_deliberate_caller(self):
        self.assertTrue(gemma_adapter.validate_proposal(
            self.source, self.plan(self.clean_heading, 'space_before_pt', 12.0),
            allow_effective_noop=True))

    def test_preparing_a_mixed_plan_retains_only_real_changes_without_mutating_input(self):
        spacing = fixtures.FAULT_IDS['excessive_space_before']
        plan = {'source_sha256': self.hash, 'operations': [
            {'paragraph_id': spacing, 'field': 'space_before_pt', 'value': 12.0},
            {'paragraph_id': self.clean_heading, 'field': 'space_before_pt', 'value': 12.0}]}
        original = json.loads(json.dumps(plan))
        prepared = gemma_adapter.prepare_proposal(self.source, plan)
        self.assertEqual(prepared['operations'], [plan['operations'][0]])
        self.assertEqual(plan, original)
        prepared['operations'][0]['value'] = 6
        self.assertEqual(plan, original)

    def test_preparation_does_not_hide_an_invalid_operation_in_a_mixed_plan(self):
        plan = {'source_sha256': self.hash, 'operations': [
            {'paragraph_id': self.clean_heading, 'field': 'space_before_pt', 'value': 12.0},
            {'paragraph_id': self.clean_heading, 'field': 'space_after_pt', 'value': 5000}]}
        with self.assertRaises(ValueError):
            gemma_adapter.prepare_proposal(self.source, plan)

    def test_the_engine_validator_contract_is_unchanged(self):
        # The guard lives in the proposal layer, so a hand-written CLI plan through
        # engine.validate keeps exactly the behaviour README documents for it.
        package = engine.open_package(self.source)
        engine.validate(self.plan(self.clean_heading, 'space_before_pt', 12.0),
                        package.document, package.styles)


def conforms(value, schema):
    """The JSON Schema subset response_schema uses; enough to tell shapes apart."""
    if 'anyOf' in schema:
        return any(conforms(value, option) for option in schema['anyOf'])
    kinds = schema.get('type')
    kinds = kinds if isinstance(kinds, list) else [kinds] if kinds else []
    checks = {'object': lambda v: isinstance(v, dict), 'array': lambda v: isinstance(v, list),
              'string': lambda v: isinstance(v, str), 'null': lambda v: v is None,
              'boolean': lambda v: isinstance(v, bool),
              'integer': lambda v: type(v) is int,
              'number': lambda v: type(v) in (int, float)}
    if kinds and not any(checks[k](value) for k in kinds):
        return False
    if 'enum' in schema and value not in schema['enum']:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema.get('minimum', value) or value > schema.get('maximum', value):
            return False
    if isinstance(value, str) and not (schema.get('minLength', 0) <= len(value)
                                       <= schema.get('maxLength', len(value))):
        return False
    if isinstance(value, dict):
        props = schema.get('properties', {})
        if set(schema.get('required', [])) - set(value):
            return False
        if schema.get('additionalProperties') is False and set(value) - set(props):
            return False
        return all(conforms(value[k], props[k]) for k in value if k in props)
    if isinstance(value, list):
        if not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', len(value)):
            return False
        return all(conforms(item, schema.get('items', {})) for item in value)
    return True


class ResponseSchemaTests(unittest.TestCase):
    """The decoding constraint sent to Ollama. No model is contacted."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / 'breaks.docx'
        break_fixtures.build_manual_page_break(self.source)
        self.hash = engine.signature(self.source)
        self.selected = break_fixtures.MANUAL_PAGE_BREAK_IDS['two_breaks_paragraph']
        payload = gemma_adapter.evidence(self.source, self.selected)
        self.break_ids = payload['explicit_breaks']['supplied_target_ids']
        self.assertTrue(self.break_ids)
        self.schema = gemma_adapter.response_schema(
            self.hash, self.selected, {self.selected, self.selected + 1}, self.break_ids, [])

    def answer(self, **overrides):
        base = {'status': 'proposed', 'explanation': 'An unwanted manual page break.',
                'evidence': [{'paragraph_id': self.selected, 'target_id': self.break_ids[0]}],
                'source_sha256': self.hash,
                'operations': [{'operation': engine.REMOVE_BREAK, 'target_id': self.break_ids[0],
                                'expected_break_type': 'page'}],
                'question': None}
        base.update(overrides)
        return base

    def test_answers_the_validator_accepts_conform(self):
        for answer in (self.answer(),
                       self.answer(status='no_change', operations=[], evidence=[]),
                       self.answer(status='needs_clarification', operations=[],
                                   question='Which break?'),
                       self.answer(operations=[{'paragraph_id': self.selected,
                                                'field': 'space_before_pt', 'value': 12}],
                                   evidence=[{'paragraph_id': self.selected,
                                              'field': 'space_before_pt'}])):
            self.assertTrue(conforms(answer, self.schema), answer)

    def test_status_and_operations_must_agree_as_the_validator_requires(self):
        # The second live failure after the schema landed: no_change with an operation.
        for answer in (self.answer(status='no_change'),
                       self.answer(status='unsupported'),
                       self.answer(status='proposed', operations=[]),
                       self.answer(evidence=[]),
                       self.answer(status='needs_clarification', operations=[], question=None),
                       self.answer(status='needs_clarification', operations=[], question='')):
            self.assertFalse(conforms(answer, self.schema), answer)

    def test_the_model_reasons_before_it_commits_to_a_status(self):
        # Decoding follows property order; status-first made the model abstain on its
        # first token before reading its own evidence (TEST_RESULTS.md section 17).
        for branch in self.schema['anyOf']:
            order = list(branch['properties'])
            self.assertEqual(order, branch['required'])
            self.assertGreater(order.index('status'), order.index('explanation'))
            self.assertGreater(order.index('status'), order.index('evidence'))
            self.assertGreater(order.index('status'), order.index('operations'))

    def test_the_recorded_invented_answer_cannot_be_decoded(self):
        # Verbatim from the failed live run in TEST_RESULTS.md section 15.7.
        self.assertFalse(conforms({'operation': 'remove_break',
                                   'break_id': 'brk-7925c71d4abd3fc5'}, self.schema))

    def test_ids_hash_and_values_are_limited_to_what_was_supplied(self):
        for answer in (
                self.answer(source_sha256='0' * 64),
                self.answer(operations=[{'operation': engine.REMOVE_BREAK,
                                         'target_id': 'brk-0000000000000000',
                                         'expected_break_type': 'page'}]),
                self.answer(operations=[{'paragraph_id': self.selected + 1,
                                         'field': 'space_before_pt', 'value': 12}]),
                self.answer(operations=[{'paragraph_id': self.selected,
                                         'field': 'space_before_pt', 'value': 5000}]),
                self.answer(operations=[{'paragraph_id': self.selected,
                                         'field': 'alignment', 'value': 'justify'}]),
                self.answer(evidence=[{'paragraph_id': 99, 'field': 'alignment'}]),
                self.answer(confidence=0.9)):
            self.assertFalse(conforms(answer, self.schema), answer)

    def test_no_break_or_section_shape_is_offered_when_none_was_supplied(self):
        schema = gemma_adapter.response_schema(self.hash, 1, {0, 1, 2}, [], [])
        self.assertFalse(conforms(self.answer(evidence=[]), schema))
        shapes = schema['anyOf'][0]['properties']['operations']['items']['anyOf']
        self.assertTrue(all('operation' not in shape['properties'] for shape in shapes))

    def test_the_request_carries_this_documents_schema_not_bare_json(self):
        sent = {}

        def fake_urlopen(request, timeout=None):
            sent.update(json.loads(request.data))
            reply = {'model': 'gemma4:e2b', 'message': {'content': json.dumps(
                self.answer(status='no_change', operations=[], evidence=[]))}}
            return io.BytesIO(json.dumps(reply).encode())

        os.environ['GEMMA_MODEL'] = 'gemma4:e2b'
        self.addCleanup(os.environ.pop, 'GEMMA_MODEL', None)
        with patch.object(gemma_adapter.urllib.request, 'urlopen', fake_urlopen):
            found, _ = gemma_adapter.diagnose(self.source, 'This break is unwanted.',
                                              self.selected)
        self.assertEqual(found['status'], 'no_change')
        schema = sent['format']
        self.assertIsInstance(schema, dict)
        proposal = schema['anyOf'][0]['properties']
        self.assertEqual(proposal['status']['enum'], ['proposed'])
        self.assertEqual(proposal['source_sha256']['enum'], [self.hash])
        operation_ids = [shape['properties']['target_id']['enum']
                         for shape in proposal['operations']['items']['anyOf']
                         if 'target_id' in shape['properties']]
        # This paragraph holds two removable breaks and none was selected, so no
        # removal shape is offered at all; both ids remain citable as evidence.
        self.assertEqual(operation_ids, [])
        cited = [shape['properties']['target_id']['enum']
                 for shape in proposal['evidence']['items']['anyOf']
                 if 'target_id' in shape['properties']]
        self.assertEqual(cited, [sorted(self.break_ids)])

    def test_a_selected_break_is_the_only_removal_the_request_offers(self):
        sent = {}

        def fake_urlopen(request, timeout=None):
            sent.update(json.loads(request.data))
            reply = {'model': 'gemma4:e2b', 'message': {'content': json.dumps(
                self.answer(status='no_change', operations=[], evidence=[]))}}
            return io.BytesIO(json.dumps(reply).encode())

        os.environ['GEMMA_MODEL'] = 'gemma4:e2b'
        self.addCleanup(os.environ.pop, 'GEMMA_MODEL', None)
        chosen = self.break_ids[0]
        with patch.object(gemma_adapter.urllib.request, 'urlopen', fake_urlopen):
            _, metadata = gemma_adapter.diagnose(self.source, 'This break is unwanted.',
                                                 self.selected, break_target_id=chosen)
        proposal = sent['format']['anyOf'][0]['properties']
        operation_ids = [shape['properties']['target_id']['enum']
                         for shape in proposal['operations']['items']['anyOf']
                         if 'target_id' in shape['properties']]
        self.assertEqual(operation_ids, [[chosen]])
        self.assertEqual(metadata['actionable_target_ids'], [chosen])


class AmbiguousBreakTests(unittest.TestCase):
    """Two removable breaks, none selected: the model may ask, never pick."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / 'breaks.docx'
        break_fixtures.build_manual_page_break(self.source)
        self.hash = engine.signature(self.source)
        self.two = break_fixtures.MANUAL_PAGE_BREAK_IDS['two_breaks_paragraph']
        self.one = break_fixtures.MANUAL_PAGE_BREAK_IDS['deliberate_break_paragraph']

    def breaks(self, paragraph_id, selected=None):
        return gemma_adapter.evidence(self.source, paragraph_id,
                                      break_target_id=selected)['explicit_breaks']

    def guess(self, ids):
        return {'status': 'proposed', 'explanation': 'Remove the first.',
                'evidence': [{'paragraph_id': self.two, 'target_id': ids[0]}],
                'source_sha256': self.hash,
                'operations': [{'operation': engine.REMOVE_BREAK, 'target_id': ids[0],
                                'expected_break_type': 'page'}],
                'question': None}

    def test_no_selection_is_reported_when_nothing_was_selected(self):
        # Regression: None matched the id-less lastRenderedPageBreak in paragraph 4,
        # which the model was then told was "the break the person pointed at".
        for paragraph_id in (self.two, self.one, 0):
            found = self.breaks(paragraph_id)
            self.assertIsNone(found['user_selected_break'], paragraph_id)
            self.assertTrue(all(r['paragraph_id'] == paragraph_id
                                for r in found['breaks_in_selected_paragraph']))

    def test_the_ambiguity_is_stated_and_nothing_is_removable(self):
        found = self.breaks(self.two)
        self.assertTrue(found['break_selection_required'])
        self.assertEqual(found['removal_target_ids'], [])
        self.assertEqual(len(found['supplied_target_ids']), 2)
        self.assertIn('has not selected', found['selection_note'])

    def test_the_suggested_question_names_each_break_and_fits_the_limit(self):
        found = self.breaks(self.two)
        question = found['suggested_question']
        self.assertLessEqual(len(question), diagnosis.MAX_QUESTION)
        self.assertTrue(question.startswith('Which page break'))
        for record in found['breaks_in_selected_paragraph']:
            if record['edit_eligible']:
                tail = ' '.join(record['text_before'].split())[-10:]
                self.assertIn(tail, question)

    def test_a_long_context_still_yields_a_bounded_question(self):
        long = [{'break_type': 'page', 'text_before': 'word ' * 200}] * 5
        self.assertLessEqual(len(gemma_adapter.clarifying_question(long)),
                             diagnosis.MAX_QUESTION)

    def test_selecting_one_makes_only_that_one_removable(self):
        first = self.breaks(self.two)['supplied_target_ids'][0]
        found = self.breaks(self.two, selected=first)
        self.assertFalse(found['break_selection_required'])
        self.assertEqual(found['removal_target_ids'], [first])
        self.assertIsNone(found['suggested_question'])

    def test_a_paragraph_with_one_removable_break_is_not_ambiguous(self):
        found = self.breaks(self.one)
        self.assertFalse(found['break_selection_required'])
        self.assertEqual(found['removal_target_ids'], found['supplied_target_ids'])
        self.assertEqual(len(found['removal_target_ids']), 1)

    def test_the_schema_offers_a_question_but_no_removal(self):
        ids = self.breaks(self.two)['supplied_target_ids']
        schema = gemma_adapter.response_schema(self.hash, self.two, {self.two}, ids, [],
                                               removal_break_ids=[])
        ask = dict(self.guess(ids), status='needs_clarification', operations=[],
                   question='Which page break do you mean?')
        self.assertFalse(conforms(self.guess(ids), schema))
        self.assertTrue(conforms(ask, schema))

    def test_the_validator_refuses_a_guess_even_if_decoding_allowed_one(self):
        ids = self.breaks(self.two)['supplied_target_ids']
        with self.assertRaisesRegex(diagnosis.DiagnosisError, 'did not select'):
            diagnosis.check(self.guess(ids), self.two, self.hash, {self.two},
                            supplied_targets=set(ids), actionable_targets=set())
        # Citing the same breaks while asking is a complete, valid answer.
        ask = dict(self.guess(ids), status='needs_clarification', operations=[],
                   question='Which page break do you mean?')
        found = diagnosis.check(ask, self.two, self.hash, {self.two},
                                supplied_targets=set(ids), actionable_targets=set())
        self.assertEqual(found['status'], 'needs_clarification')
        self.assertEqual(found['operations'], [])


class ResampleTests(unittest.TestCase):
    """The re-sampling loop: shipped disabled, kept correct.

    `gemma4:e2b` sometimes repeats an evidence entry because Ollama's grammar path
    does not enforce `uniqueItems` (TEST_RESULTS.md section 18.5). Re-sampling the
    identical request was added to absorb that, but the live failing case gave three
    identical draws (section 18.12), so it ships with a budget of 1. Tests that
    exercise the loop patch the budget to 3. Nothing about what counts as a valid
    answer changes either way: each draw faces the same `diagnosis.check`, and only
    `invalid_response` is ever redrawn.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name)/'sample.docx'
        fixtures.build(self.source)
        self.hash = engine.signature(self.source)
        self.selected = fixtures.FAULT_IDS['excessive_space_before']
        os.environ['GEMMA_MODEL'] = 'gemma4:e2b'
        self.addCleanup(os.environ.pop, 'GEMMA_MODEL', None)

    def answer(self, **overrides):
        cited = {'paragraph_id': self.selected, 'field': 'space_before_pt'}
        body = {'status': 'proposed', 'explanation': 'Too much space above.',
                'evidence': [dict(cited)], 'source_sha256': self.hash,
                'operations': [{'paragraph_id': self.selected,
                                'field': 'space_before_pt', 'value': 12}]}
        body.update(overrides)
        return body

    def repeated(self):
        """The exact shape measured live: one citation sent twice."""
        cited = {'paragraph_id': self.selected, 'field': 'space_before_pt'}
        return self.answer(evidence=[dict(cited), dict(cited)])

    def replies(self, *bodies):
        """Serve one canned reply per call, recording how many were drawn."""
        drawn = []

        def fake_urlopen(request, timeout=None):
            body = bodies[min(len(drawn), len(bodies) - 1)]
            drawn.append(body)
            reply = {'model': 'gemma4:e2b',
                     'message': {'content': json.dumps(body)}}
            return io.BytesIO(json.dumps(reply).encode())

        return fake_urlopen, drawn

    def test_resampling_ships_disabled(self):
        """Pinned: three live draws of the failing case were identical
        (TEST_RESULTS.md section 18.12), so a redraw only lengthened the wait."""
        self.assertEqual(gemma_adapter.DIAGNOSIS_ATTEMPTS, 1)

    def test_with_resampling_disabled_a_repeat_fails_after_one_draw(self):
        fake, drawn = self.replies(self.repeated(), self.answer())
        with patch.object(gemma_adapter.urllib.request, 'urlopen', fake):
            with self.assertRaises(diagnosis.DiagnosisError) as caught:
                gemma_adapter.diagnose(
                    self.source, 'There is too much space above this heading.',
                    self.selected)
        self.assertEqual(caught.exception.code, diagnosis.INVALID_RESPONSE)
        self.assertIn('duplicate evidence entry', caught.exception.message)
        self.assertEqual(len(drawn), 1)
        self.assertEqual(caught.exception.attempts, 1)
        self.assertEqual(caught.exception.max_attempts, 1)

    # The tests below exercise the re-sampling loop itself with a budget of 3, so the
    # plumbing stays correct if it is ever re-enabled.

    @patch.object(gemma_adapter, 'DIAGNOSIS_ATTEMPTS', 3)
    def test_a_repeated_citation_is_redrawn_rather_than_failing_the_run(self):
        fake, drawn = self.replies(self.repeated(), self.answer())
        with patch.object(gemma_adapter.urllib.request, 'urlopen', fake):
            found, metadata = gemma_adapter.diagnose(
                self.source, 'There is too much space above this heading.',
                self.selected)
        self.assertEqual(found['status'], 'proposed')
        self.assertEqual(len(drawn), 2)
        self.assertEqual(metadata['attempts'], 2)
        # The discarded draw is reported, not quietly swallowed: a reviewer can see
        # the model needed more than one try.
        self.assertEqual([d['error'] for d in metadata['discarded_attempts']],
                         ['duplicate evidence entry'])

    def test_a_sound_answer_is_still_drawn_exactly_once(self):
        fake, drawn = self.replies(self.answer())
        with patch.object(gemma_adapter.urllib.request, 'urlopen', fake):
            found, metadata = gemma_adapter.diagnose(
                self.source, 'There is too much space above this heading.',
                self.selected)
        self.assertEqual(len(drawn), 1)
        self.assertEqual(metadata['attempts'], 1)
        self.assertEqual(metadata['discarded_attempts'], [])

    @patch.object(gemma_adapter, 'DIAGNOSIS_ATTEMPTS', 3)
    def test_a_model_that_keeps_repeating_still_fails_with_its_own_reason(self):
        fake, drawn = self.replies(self.repeated())
        with patch.object(gemma_adapter.urllib.request, 'urlopen', fake):
            with self.assertRaises(diagnosis.DiagnosisError) as caught:
                gemma_adapter.diagnose(
                    self.source, 'There is too much space above this heading.',
                    self.selected)
        self.assertEqual(caught.exception.code, diagnosis.INVALID_RESPONSE)
        self.assertIn('duplicate evidence entry', caught.exception.message)
        # Bounded: the budget is spent, not retried forever.
        self.assertEqual(len(drawn), 3)

    @patch.object(gemma_adapter, 'DIAGNOSIS_ATTEMPTS', 3)
    def test_an_unsafe_proposal_is_not_redrawn_until_it_passes(self):
        """Re-sampling must not become a search for an answer that gets through.

        A proposal refused for citing nothing is refused for what it *says*, so a
        redraw is still only a redraw of a malformed answer - the budget is shared
        with every other `invalid_response`, and when it runs out the refusal stands.
        """
        fake, drawn = self.replies(self.answer(evidence=[]))
        with patch.object(gemma_adapter.urllib.request, 'urlopen', fake):
            with self.assertRaises(diagnosis.DiagnosisError):
                gemma_adapter.diagnose(
                    self.source, 'There is too much space above this heading.',
                    self.selected)
        self.assertEqual(len(drawn), 3)

    @patch.object(gemma_adapter, 'DIAGNOSIS_ATTEMPTS', 3)
    def test_an_unreachable_runtime_is_not_retried(self):
        """A stable condition is not a bad draw. Redrawing it only wastes the wait."""
        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append(1)
            raise gemma_adapter.urllib.error.URLError('connection refused')

        with patch.object(gemma_adapter.urllib.request, 'urlopen', fake_urlopen):
            with self.assertRaises(diagnosis.DiagnosisError) as caught:
                gemma_adapter.diagnose(self.source, 'too much space above',
                                       self.selected, timeout=2)
        self.assertEqual(caught.exception.code, diagnosis.MODEL_UNAVAILABLE)
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
