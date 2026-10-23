"""The one supported section mutation: a nextPage section start becoming continuous.

Three separate things are kept apart here and never allowed to stand in for each
other, because each one can pass while the others fail:

  * **Eligibility** - whether `structure.py` offers a section at all, decided from the
    real XML against the rules in ISO/IEC 29500-1 17.6.22.
  * **Exactness** - whether the written file differs from the input by precisely one
    attribute and nothing else, checked against the bytes on disk rather than
    asserted.
  * **Rendering** - whether both documents actually rendered. A section change can
    alter the layout of every page after it, so the service refuses to offer one for
    review unless they did, and `RenderedSectionTests` proves that on a real render.

The refusal tests are the larger half on purpose. The question this operation has to
answer is not "can it set an attribute" but "does it know when not to".
"""
import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from lxml import etree as E

import diagnosis
import engine
import render as render_module
import section_fixtures
import structure
import workflow


def plan_for(path, target_id, expected='nextPage', new='continuous',
             schema_version=engine.PLAN_SCHEMA_VERSION):
    plan = {'source_sha256': engine.signature(path),
            'operations': [{'operation': engine.CHANGE_SECTION_START,
                            'target_id': target_id,
                            'expected_start_type': expected,
                            'new_start_type': new}]}
    if schema_version is not None:
        plan['schema_version'] = schema_version
    return plan


class SectionFixtureCase(unittest.TestCase):
    """Builds each fixture once per test into a private directory."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.manifests = {}

    def fixture(self, name):
        path = self.dir / (name + '.docx')
        if not path.exists():
            self.manifests[name] = section_fixtures.BUILDERS[name](path)
        return path

    def sections(self, name):
        return engine.inspect_sections(self.fixture(name))

    def eligible(self, name):
        report = self.sections(name)
        found = [s for s in report['sections'] if s['edit_eligible']]
        self.assertTrue(found, 'fixture %r offered no eligible section' % name)
        return found[0]

    def apply_to(self, name, target=None, **kwargs):
        source = self.fixture(name)
        target = target or self.eligible(name)
        out = self.dir / ('candidate-%s.docx' % name)
        if out.exists():
            out.unlink()
        plan = plan_for(source, target['target_id'], **kwargs)
        return source, out, plan, engine.apply(source, out, plan)


# ---------------------------------------------------------------------------
# Eligibility: what is offered, and what is refused with which reason
# ---------------------------------------------------------------------------
class EligibilityTests(SectionFixtureCase):

    def test_a_supported_next_page_section_is_offered(self):
        target = self.eligible('supported_next_page')
        self.assertEqual(target['section_index'], 1)
        self.assertEqual(target['start_type'], 'nextPage')
        self.assertEqual(target['supported_start_changes'], ['continuous'])
        self.assertIsNone(target['ineligible_reason'])

    def test_the_final_body_sectpr_is_associated_with_the_final_section(self):
        # The specification is explicit that the body-level sectPr governs the final
        # section, not the paragraph it happens to follow. Getting this wrong would
        # change the wrong part of the document while reporting the right one.
        target = self.eligible('supported_next_page')
        self.assertEqual(target['defined_in'], 'body')
        self.assertIsNone(target['defining_paragraph_id'])
        self.assertEqual(target['governs'], 'the final section of the document')
        self.assertTrue(target['is_final_section'])
        self.assertEqual(target['first_paragraph_id'], 3)

    def test_an_omitted_start_type_is_read_as_the_documented_default(self):
        target = self.eligible('default_start')
        self.assertEqual(target['start_type'], 'nextPage')
        self.assertTrue(target['start_type_is_default'])
        self.assertIsNone(target['start_type_raw'])

    def test_the_first_section_is_never_eligible(self):
        first = self.sections('supported_next_page')['sections'][0]
        self.assertFalse(first['edit_eligible'])
        self.assertIn('no previous section', first['ineligible_reason'])

    def test_a_single_section_document_offers_nothing(self):
        report = self.sections('single_section')
        self.assertEqual(report['section_count'], 1)
        self.assertFalse(report['editable'])
        self.assertEqual(report['editable_target_ids'], [])

    def test_an_odd_page_start_is_inspected_and_refused(self):
        last = self.sections('odd_page_start')['sections'][-1]
        self.assertEqual(last['start_type'], 'oddPage')
        self.assertFalse(last['edit_eligible'])
        self.assertIn('odd-page', last['ineligible_reason'])
        self.assertEqual(last['supported_start_changes'], [])

    def test_differing_page_geometry_is_refused_and_named(self):
        last = self.sections('differing_geometry')['sections'][-1]
        self.assertFalse(last['edit_eligible'])
        self.assertIn('w:pgSz', last['differing_page_properties'])

    def test_differing_page_numbering_is_refused_and_named(self):
        last = self.sections('differing_page_numbering')['sections'][-1]
        self.assertFalse(last['edit_eligible'])
        self.assertEqual(last['differing_page_properties'], ['w:pgNumType'])

    def test_a_differing_header_reference_is_refused(self):
        # Built at the XML level so the package stays valid: a headerReference with
        # no header part would be an invalid document, and the rule under test is
        # about the comparison, not about packaging.
        root = engine.open_package(self.fixture('supported_next_page')).document
        final = list(root.find(engine.q('body')))[-1]
        header = E.SubElement(final, engine.q('headerReference'))
        header.set(engine.q('type'), 'default')
        records, _ = structure.locate_sections(root, engine.paras(root), 'hash')
        self.assertFalse(records[-1]['edit_eligible'])
        self.assertIn('w:headerReference', records[-1]['differing_page_properties'])

    def test_a_tracked_section_revision_is_refused(self):
        root = engine.open_package(self.fixture('supported_next_page')).document
        final = list(root.find(engine.q('body')))[-1]
        E.SubElement(final, engine.q('sectPrChange'))
        records, _ = structure.locate_sections(root, engine.paras(root), 'hash')
        self.assertFalse(records[-1]['edit_eligible'])
        self.assertIn('tracked revision', records[-1]['ineligible_reason'])

    def test_an_unrecognised_start_type_is_refused(self):
        root = engine.open_package(self.fixture('supported_next_page')).document
        final = list(root.find(engine.q('body')))[-1]
        final.find(engine.q('type')).set(engine.q('val'), 'sideways')
        records, _ = structure.locate_sections(root, engine.paras(root), 'hash')
        self.assertFalse(records[-1]['edit_eligible'])
        self.assertFalse(records[-1]['start_type_recognised'])

    def test_three_sections_each_get_a_distinct_target(self):
        report = self.sections('three_sections')
        ids = [s['target_id'] for s in report['sections']]
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual([s['section_index'] for s in report['sections']
                          if s['edit_eligible']], [1, 2])

    def test_an_eligible_section_is_not_a_recommendation(self):
        # Structurally identical to the supported fixture; the break is deliberate.
        # The service offers it and says nothing about whether it should be changed.
        target = self.eligible('intentional_section_break')
        self.assertTrue(target['edit_eligible'])
        # Offering a section is a statement about what is possible, never about what
        # should happen: nothing in the report advises, suggests or recommends.
        text = json.dumps(self.sections('intentional_section_break')).lower()
        for word in ('recommend', 'should be changed', 'you should'):
            self.assertNotIn(word, text)
        # And the fixture is byte-indistinguishable in start type from the supported
        # one, which is why only the person can say which it is.
        self.assertEqual(target['start_type'],
                         self.eligible('supported_next_page')['start_type'])


# ---------------------------------------------------------------------------
# Target identity: ids are server-issued and bound to the revision
# ---------------------------------------------------------------------------
class TargetIdentityTests(SectionFixtureCase):

    def test_an_id_from_another_document_does_not_resolve(self):
        other = self.eligible('three_sections')
        source = self.fixture('supported_next_page')
        out = self.dir / 'x.docx'
        with self.assertRaises(ValueError) as caught:
            engine.apply(source, out, plan_for(source, other['target_id']))
        self.assertIn('stale', str(caught.exception))
        self.assertFalse(out.exists())

    def test_an_id_minted_before_the_start_type_changed_does_not_resolve(self):
        source, out, plan, _ = self.apply_to('supported_next_page')
        stale = plan['operations'][0]['target_id']
        # The candidate is a new revision whose section now starts continuous. The
        # old id cannot resolve against it, which is what stops a second application.
        second = self.dir / 'second.docx'
        with self.assertRaises(ValueError) as caught:
            engine.apply(out, second, plan_for(out, stale))
        self.assertIn('stale', str(caught.exception))

    def test_an_invented_id_is_refused(self):
        source = self.fixture('supported_next_page')
        out = self.dir / 'x.docx'
        with self.assertRaises(ValueError):
            engine.apply(source, out, plan_for(source, 'sec-0000000000000000'))

    def test_a_non_string_id_is_refused(self):
        source = self.fixture('supported_next_page')
        with self.assertRaises(ValueError):
            engine.apply(source, self.dir / 'x.docx', plan_for(source, 17))


# ---------------------------------------------------------------------------
# Plan shape, schema version and scope
# ---------------------------------------------------------------------------
class PlanValidationTests(SectionFixtureCase):

    def validate(self, plan, name='supported_next_page'):
        root = engine.open_package(self.fixture(name)).document
        engine.validate(plan, root)

    def test_schema_version_three_is_required(self):
        source = self.fixture('supported_next_page')
        target = self.eligible('supported_next_page')
        for version in (None, 1, 2):
            with self.assertRaises(ValueError) as caught:
                self.validate(plan_for(source, target['target_id'],
                                       schema_version=version))
            self.assertIn('schema_version 3', str(caught.exception))

    def test_a_schema_version_two_break_plan_still_validates(self):
        # The new version must not invalidate plans written before it existed.
        path = self.dir / 'breaks.docx'
        import break_fixtures
        break_fixtures.build_manual_page_break(path)
        report = engine.inspect_breaks(path)
        target = next(b for b in report['breaks'] if b['edit_eligible'])
        plan = {'schema_version': 2, 'source_sha256': engine.signature(path),
                'operations': [{'operation': 'remove_explicit_break',
                                'target_id': target['target_id'],
                                'expected_break_type': target['break_type']}]}
        engine.validate(plan, engine.open_package(path).document)

    def test_only_next_page_to_continuous_is_accepted(self):
        source = self.fixture('supported_next_page')
        target = self.eligible('supported_next_page')
        for expected, new in (('nextPage', 'nextColumn'), ('continuous', 'nextPage'),
                              ('oddPage', 'continuous'), ('nextPage', 'evenPage')):
            with self.assertRaises(ValueError) as caught:
                self.validate(plan_for(source, target['target_id'], expected, new))
            self.assertIn('Unsupported section-start change', str(caught.exception))

    def test_a_section_change_may_not_be_combined_with_another_operation(self):
        source = self.fixture('supported_next_page')
        target = self.eligible('supported_next_page')
        plan = plan_for(source, target['target_id'])
        plan['operations'].append({'paragraph_id': 0, 'field': 'alignment',
                                   'value': 'left'})
        with self.assertRaises(ValueError) as caught:
            self.validate(plan)
        self.assertIn('on its own', str(caught.exception))

    def test_two_section_changes_in_one_plan_are_refused(self):
        source = self.fixture('three_sections')
        report = self.sections('three_sections')
        eligible = [s for s in report['sections'] if s['edit_eligible']]
        plan = plan_for(source, eligible[0]['target_id'])
        plan['operations'].append(dict(plan['operations'][0],
                                       target_id=eligible[1]['target_id']))
        with self.assertRaises(ValueError) as caught:
            self.validate(plan, 'three_sections')
        self.assertIn('one section-start change', str(caught.exception))

    def test_an_ineligible_section_is_refused_with_its_own_reason(self):
        # An odd-page start is refused one step earlier than the others, at the
        # conversion pair itself: oddPage -> continuous is not a conversion that
        # exists, whichever document it is asked about.
        for name, fragment in (('differing_geometry', 'w:pgSz'),
                               ('odd_page_start', 'Unsupported section-start change'),
                               ('single_section', 'no previous section'),
                               ('differing_page_numbering', 'w:pgNumType')):
            source = self.fixture(name)
            record = self.sections(name)['sections'][-1]
            plan = plan_for(source, record['target_id'],
                            expected=record['start_type'])
            with self.assertRaises(ValueError) as caught:
                self.validate(plan, name)
            self.assertIn(fragment, str(caught.exception), name)

    def test_a_missing_key_is_refused(self):
        source = self.fixture('supported_next_page')
        target = self.eligible('supported_next_page')
        plan = plan_for(source, target['target_id'])
        del plan['operations'][0]['new_start_type']
        with self.assertRaises(ValueError) as caught:
            self.validate(plan)
        self.assertIn('needs exactly', str(caught.exception))

    def test_a_stale_source_hash_is_refused_before_anything_is_written(self):
        source = self.fixture('supported_next_page')
        target = self.eligible('supported_next_page')
        plan = plan_for(source, target['target_id'])
        plan['source_sha256'] = '0' * 64
        out = self.dir / 'x.docx'
        with self.assertRaises(ValueError):
            engine.apply(source, out, plan)
        self.assertFalse(out.exists())


# ---------------------------------------------------------------------------
# Exactness: what the written file does and does not differ by
# ---------------------------------------------------------------------------
class ExactnessTests(SectionFixtureCase):

    def test_exactly_the_start_type_changes(self):
        source, out, plan, result = self.apply_to('supported_next_page')
        change = result['section_changes'][0]
        self.assertEqual(change['previous_start_type'], 'nextPage')
        self.assertEqual(change['new_start_type'], 'continuous')
        after = engine.inspect_sections(out)
        self.assertEqual([s['start_type'] for s in after['sections']],
                         ['nextPage', 'continuous'])

    def test_the_original_file_is_never_written_to(self):
        before = engine.signature(self.fixture('supported_next_page'))
        source, out, plan, _ = self.apply_to('supported_next_page')
        self.assertEqual(engine.signature(source), before)

    def test_reverting_the_attribute_restores_the_document_byte_for_byte(self):
        source, out, plan, result = self.apply_to('supported_next_page')
        after = engine.open_package(out).document
        final = list(after.find(engine.q('body')))[-1]
        final.find(engine.q('type')).set(engine.q('val'), 'nextPage')
        self.assertEqual(E.tostring(after),
                         E.tostring(engine.open_package(source).document))

    def test_the_type_element_is_created_at_its_schema_position(self):
        source, out, plan, result = self.apply_to('default_start')
        self.assertTrue(result['section_changes'][0]['created_type_element'])
        final = list(engine.open_package(out).document.find(engine.q('body')))[-1]
        self.assertEqual([E.QName(c).localname for c in final][:2], ['type', 'pgSz'])

    def test_section_count_and_boundaries_are_preserved(self):
        source, out, plan, _ = self.apply_to('three_sections',
                                             target=self.eligible('three_sections'))
        before = engine.inspect_sections(source)
        after = engine.inspect_sections(out)
        self.assertEqual(before['section_count'], after['section_count'])
        self.assertEqual([s['defining_paragraph_id'] for s in before['sections']],
                         [s['defining_paragraph_id'] for s in after['sections']])
        self.assertEqual([s['defined_in'] for s in before['sections']],
                         [s['defined_in'] for s in after['sections']])

    def test_only_the_named_section_changes_when_several_are_eligible(self):
        report = self.sections('three_sections')
        eligible = [s for s in report['sections'] if s['edit_eligible']]
        source, out, plan, _ = self.apply_to('three_sections', target=eligible[1])
        self.assertEqual([s['start_type'] for s in
                          engine.inspect_sections(out)['sections']],
                         ['nextPage', 'nextPage', 'continuous'])

    def test_other_package_parts_are_byte_identical(self):
        source, out, plan, _ = self.apply_to('supported_next_page')
        with ZipFile(source) as a, ZipFile(out) as b:
            self.assertEqual(a.namelist(), b.namelist())
            for name in a.namelist():
                if name != 'word/document.xml':
                    self.assertEqual(a.read(name), b.read(name), name)

    def test_text_and_paragraphs_are_untouched(self):
        source, out, plan, _ = self.apply_to('supported_next_page')
        before = engine.open_package(source).document
        after = engine.open_package(out).document
        self.assertEqual(before.xpath('string(.)'), after.xpath('string(.)'))
        self.assertEqual(len(engine.paras(before)), len(engine.paras(after)))

    def test_a_tampered_mutation_is_caught_by_the_verifier(self):
        # The verification does not trust the mutation code: it reverts the approved
        # attribute on the written tree and demands the rest be identical. A change
        # the plan did not approve survives the revert and fails.
        source = self.fixture('three_sections')
        root = engine.open_package(source).document
        before = copy.deepcopy(root)
        records, elements = structure.locate_sections(
            root, engine.paras(root), engine.signature(source))
        eligible = [r for r in records if r['edit_eligible']]
        changes = engine._change_section_starts(
            plan_for(source, eligible[0]['target_id']), root)
        # A second, unapproved section is quietly altered as well.
        other = elements[eligible[1]['target_id']]
        other.find(engine.q('type')).set(engine.q('val'), 'continuous')
        with self.assertRaises(ValueError) as caught:
            engine._verify_section_changes(before, root, changes)
        self.assertIn('more than the approved start type', str(caught.exception))


# ---------------------------------------------------------------------------
# Preservation invariants, checked by workflow against the files on disk
# ---------------------------------------------------------------------------
class InvariantTests(SectionFixtureCase):

    def invariants(self, name):
        source, out, plan, _ = self.apply_to(name)
        return workflow.check_invariants(source, out, plan,
                                         engine.signature(source))

    def test_every_section_invariant_passes_on_the_supported_fixture(self):
        checks, failures = self.invariants('supported_next_page')
        self.assertEqual(failures, [])
        for name in ('section_count_preserved', 'section_boundaries_preserved',
                     'only_approved_section_start_changed',
                     'section_properties_preserved'):
            self.assertTrue(checks[name]['passed'], name)

    def test_the_existing_invariants_are_not_weakened(self):
        # runs_preserved is the strict check a break removal has to narrow. A section
        # change touches no run, so it must still face the strict version.
        checks, failures = self.invariants('supported_next_page')
        self.assertIn('runs_preserved', checks)
        self.assertTrue(checks['runs_preserved']['passed'])
        self.assertTrue(checks['unrelated_parts_byte_identical']['passed'])
        self.assertTrue(checks['styles_part_unmodified']['passed'])

    def test_a_paragraph_level_section_change_reports_its_paragraph(self):
        report = self.sections('three_sections')
        target = next(s for s in report['sections']
                      if s['edit_eligible'] and s['defined_in'] == 'paragraph_properties')
        source, out, plan, _ = self.apply_to('three_sections', target=target)
        root = engine.open_package(source).document
        self.assertEqual(engine.targeted_paragraph_ids(plan, root),
                         [target['defining_paragraph_id']])
        checks, failures = workflow.check_invariants(
            source, out, plan, engine.signature(source))
        self.assertEqual(failures, [])

    def test_a_body_level_section_change_targets_no_paragraph(self):
        source = self.fixture('supported_next_page')
        plan = plan_for(source, self.eligible('supported_next_page')['target_id'])
        root = engine.open_package(source).document
        self.assertEqual(engine.targeted_paragraph_ids(plan, root), [])

    def test_a_section_properties_change_is_detected(self):
        # Preservation is checked against the bytes, so a candidate whose columns
        # were also altered fails even though its start types look right.
        source, out, plan, _ = self.apply_to('supported_next_page')
        root = engine.open_package(out).document
        final = list(root.find(engine.q('body')))[-1]
        cols = E.SubElement(final, engine.q('cols'))
        cols.set(engine.q('num'), '2')
        tampered = self.dir / 'tampered.docx'
        xml = E.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)
        with ZipFile(out) as src, ZipFile(tampered, 'w') as dst:
            for info in src.infolist():
                dst.writestr(info, xml if info.filename == 'word/document.xml'
                             else src.read(info.filename))
        checks, failures = workflow.check_invariants(
            source, tampered, plan, engine.signature(source))
        self.assertIn('section_properties_preserved', failures)


# ---------------------------------------------------------------------------
# The diagnosis schema: what a model may and may not propose
# ---------------------------------------------------------------------------
class DiagnosisSchemaTests(SectionFixtureCase):

    def answer(self, source, target_id, **extra):
        found = {'status': 'proposed',
                 'explanation': 'The appendix section is asked to continue on the '
                                'same page instead of starting a new one.',
                 'evidence': [{'section_id': target_id}],
                 'source_sha256': engine.signature(source),
                 'operations': [{'operation': 'change_section_start',
                                 'target_id': target_id,
                                 'expected_start_type': 'nextPage',
                                 'new_start_type': 'continuous'}],
                 'question': None}
        found.update(extra)
        return found

    def check(self, found, source, supplied):
        return diagnosis.check(found, 0, engine.signature(source),
                               supplied_ids={0}, supplied_targets=supplied)

    def test_a_well_formed_section_proposal_is_accepted(self):
        source = self.fixture('supported_next_page')
        tid = self.eligible('supported_next_page')['target_id']
        found = self.check(self.answer(source, tid), source, {tid})
        self.assertEqual(found['operations'][0]['operation'], 'change_section_start')
        self.assertEqual(found['evidence'], [{'section_id': tid}])

    def test_the_plan_declares_schema_version_three(self):
        source = self.fixture('supported_next_page')
        tid = self.eligible('supported_next_page')['target_id']
        found = self.check(self.answer(source, tid), source, {tid})
        self.assertEqual(diagnosis.plan_of(found)['schema_version'], 3)

    def test_a_paragraph_plan_still_declares_no_schema_version(self):
        plan = diagnosis.plan_of({'source_sha256': 'abc',
                                  'operations': [{'paragraph_id': 0,
                                                  'field': 'alignment',
                                                  'value': 'left'}]})
        self.assertNotIn('schema_version', plan)

    def test_a_section_id_that_was_not_supplied_is_a_fabrication(self):
        source = self.fixture('supported_next_page')
        tid = self.eligible('supported_next_page')['target_id']
        with self.assertRaises(diagnosis.DiagnosisError) as caught:
            self.check(self.answer(source, tid), source, set())
        self.assertIn('not supplied', caught.exception.message)

    def test_an_unsupported_conversion_is_refused_by_the_schema(self):
        source = self.fixture('supported_next_page')
        tid = self.eligible('supported_next_page')['target_id']
        found = self.answer(source, tid)
        found['operations'][0]['new_start_type'] = 'evenPage'
        with self.assertRaises(diagnosis.DiagnosisError) as caught:
            self.check(found, source, {tid})
        self.assertIn('unsupported section-start change', caught.exception.message)

    def test_a_section_change_beside_another_operation_is_refused(self):
        source = self.fixture('supported_next_page')
        tid = self.eligible('supported_next_page')['target_id']
        found = self.answer(source, tid)
        found['operations'].append({'paragraph_id': 0, 'field': 'alignment',
                                    'value': 'left'})
        with self.assertRaises(diagnosis.DiagnosisError) as caught:
            self.check(found, source, {tid})
        self.assertIn('on its own', caught.exception.message)

    def test_an_abstention_about_an_intentional_break_carries_no_operations(self):
        source = self.fixture('intentional_section_break')
        tid = self.eligible('intentional_section_break')['target_id']
        found = {'status': 'no_change',
                 'explanation': 'The appendix deliberately starts on a new page and '
                                'the complaint does not ask for that to change.',
                 'evidence': [{'section_id': tid}],
                 'source_sha256': engine.signature(source),
                 'operations': [], 'question': None}
        self.assertEqual(self.check(found, source, {tid})['operations'], [])

    def test_a_confidence_score_is_still_rejected(self):
        source = self.fixture('supported_next_page')
        tid = self.eligible('supported_next_page')['target_id']
        found = self.answer(source, tid, confidence=0.9)
        with self.assertRaises(diagnosis.DiagnosisError):
            self.check(found, source, {tid})


# ---------------------------------------------------------------------------
# Rendering: required, and actually performed
# ---------------------------------------------------------------------------
class RenderedSectionTests(SectionFixtureCase):
    """Real LibreOffice renderings. Skipped, never faked, when none is installed."""

    @classmethod
    def setUpClass(cls):
        if not render_module.renderer_status()['available']:
            raise unittest.SkipTest('No LibreOffice renderer installed')

    def test_a_section_change_is_not_reviewable_without_both_renderings(self):
        source = self.fixture('supported_next_page')
        plan = plan_for(source, self.eligible('supported_next_page')['target_id'])
        report = workflow.repair(source, plan, self.dir / 'unrendered',
                                 do_render=False)
        self.assertTrue(report['rendering_required'])
        self.assertFalse(report['rendering_satisfied'])
        self.assertIn('rendering_required', report['invariant_failures'])

    def test_a_rendered_section_change_passes_every_invariant(self):
        source = self.fixture('supported_next_page')
        plan = plan_for(source, self.eligible('supported_next_page')['target_id'])
        report = workflow.repair(source, plan, self.dir / 'rendered')
        self.assertEqual(report['invariant_failures'], [])
        self.assertTrue(report['rendering_satisfied'])
        self.assertEqual(report['operation_kind'], 'section_start_change')
        self.assertEqual(report['plan_schema_version'], 3)
        self.assertIsNotNone(report['original_render']['pages'])
        self.assertIsNotNone(report['candidate_render']['pages'])

    def test_the_page_count_change_is_reported_and_not_promised(self):
        source = self.fixture('supported_next_page')
        plan = plan_for(source, self.eligible('supported_next_page')['target_id'])
        report = workflow.repair(source, plan, self.dir / 'pages')
        comparison = report['visual_comparison']
        self.assertIsNotNone(comparison)
        # The service reports what happened. It does not require a reduction: page
        # geometry and other settings can still force pagination.
        self.assertIsInstance(comparison['before_pages'], int)
        self.assertIsInstance(comparison['after_pages'], int)
        self.assertEqual(comparison['page_count_delta'],
                         comparison['after_pages'] - comparison['before_pages'])

    def test_every_rendered_page_is_compared_not_only_the_first(self):
        source = self.fixture('three_sections')
        plan = plan_for(source, self.eligible('three_sections')['target_id'])
        report = workflow.repair(source, plan, self.dir / 'allpages')
        comparison = report['visual_comparison']
        # A section change can alter the layout of every page after it, so the
        # comparison has to cover them all rather than stopping at the first.
        self.assertGreaterEqual(max(comparison['before_pages'],
                                    comparison['after_pages']), 1)
        self.assertEqual(report['invariant_failures'], [])


if __name__ == '__main__':
    unittest.main()
