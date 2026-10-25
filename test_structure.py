"""Deterministic verification of explicit-break inspection and removal.

No model, no network and no Sentry is contacted here. Where a test needs a rendered
page count it says so and is skipped when LibreOffice is absent, so a machine without
a renderer reports "not run" rather than a pass that proved nothing.

The fixtures are the real packages in `break_fixtures.py`. A `page_break_before`
paragraph-property document is deliberately never substituted for an explicit-break
one: they are different repairs and evidence about one is not evidence about the other.
"""
import copy
import shutil
import tempfile
import unittest
from pathlib import Path

from lxml import etree as E

import break_fixtures
import diagnosis
import engine
import fixtures
import render as render_module
import structure
import workflow


class BreakFixtureCase(unittest.TestCase):
    """One scratch directory per test, with both explicit-break documents built."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='wtd-breaks-'))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.page_doc = self.dir / 'page.docx'
        self.column_doc = self.dir / 'column.docx'
        self.manifest = break_fixtures.build_manual_page_break(self.page_doc)
        break_fixtures.build_multicolumn(self.column_doc)

    # -- helpers ------------------------------------------------------------
    def breaks(self, path=None):
        return engine.inspect_breaks(path or self.page_doc)

    def removable(self, path=None):
        return [b for b in self.breaks(path)['breaks'] if b['edit_eligible']]

    def plan(self, record, path=None, break_type='page'):
        report = self.breaks(path)
        return {'schema_version': engine.BREAK_PLAN_SCHEMA_VERSION,
                'source_sha256': report['source_sha256'],
                'operations': [{'operation': 'remove_explicit_break',
                                'target_id': record['target_id'],
                                'expected_break_type': break_type}]}

    def apply_plan(self, plan, path=None, name='out.docx'):
        out = self.dir / name
        if out.exists():
            out.unlink()
        return out, engine.apply(path or self.page_doc, out, plan)

    @staticmethod
    def text_of(path):
        root = engine.open_package(path).document
        return ''.join(root.xpath('//w:t/text()', namespaces=engine.NS))

    @staticmethod
    def runs_of(path):
        root = engine.open_package(path).document
        return [E.tostring(r) for r in root.xpath('//w:r', namespaces=engine.NS)]


# ---------------------------------------------------------------------------
# What the inspection reports, and what it refuses to call editable
# ---------------------------------------------------------------------------
class InspectionTest(BreakFixtureCase):

    def test_reports_page_and_column_breaks_as_different_types(self):
        page_types = {b['break_type'] for b in self.removable()}
        column_types = {b['break_type'] for b in self.removable(self.column_doc)}
        self.assertEqual(page_types, {'page'})
        self.assertEqual(column_types, {'page', 'column'})

    def test_untyped_break_is_a_line_break_not_a_page_break(self):
        untyped = [b for b in self.breaks()['breaks']
                   if b['kind'] == 'explicit_break' and b['break_type_raw'] is None]
        self.assertEqual(len(untyped), 1)
        self.assertEqual(untyped[0]['break_type'], 'textWrapping')
        self.assertFalse(untyped[0]['edit_eligible'])
        self.assertIn('textWrapping', untyped[0]['ineligible_reason'])

    def test_last_rendered_page_break_is_reported_but_never_a_target(self):
        report = self.breaks()
        rendered = [b for b in report['breaks']
                    if b['kind'] == 'last_rendered_page_break']
        self.assertEqual(report['last_rendered_page_break_count'], 1)
        self.assertEqual(len(rendered), 1)
        self.assertIsNone(rendered[0]['target_id'])
        self.assertFalse(rendered[0]['edit_eligible'])
        self.assertIn('not a user-inserted break', rendered[0]['ineligible_reason'])

    def test_break_inside_an_unsupported_wrapper_is_ineligible_and_names_it(self):
        inside = [b for b in self.breaks()['breaks']
                  if b['paragraph_id'] == break_fixtures.MANUAL_PAGE_BREAK_IDS[
                      'unsupported_wrapper_paragraph']]
        self.assertEqual(len(inside), 1)
        self.assertEqual(inside[0]['break_type'], 'page')
        self.assertFalse(inside[0]['edit_eligible'])
        self.assertIn('w:smartTag', inside[0]['ineligible_reason'])

    def test_break_inside_a_hyperlink_is_removable_and_nothing_else_changes(self):
        # A hyperlink only wraps ordinary runs; the link, its target and its text
        # must all survive the removal.
        root = engine.open_package(self.page_doc).document
        paragraph = engine.paras(root)[1]
        link = E.SubElement(paragraph, engine.q('hyperlink'))
        link.set(engine.q('anchor'), 'top')
        run = E.SubElement(link, engine.q('r'))
        E.SubElement(run, engine.q('br')).set(engine.q('type'), 'page')
        E.SubElement(run, engine.q('t')).text = 'Linked text.'
        linked = _write_document(self.page_doc, self.dir / 'hyperlinked.docx', root)
        record, = [b for b in engine.inspect_breaks(linked)['breaks']
                   if b['paragraph_id'] == 1]
        self.assertTrue(record['edit_eligible'], record['ineligible_reason'])
        plan = {'source_sha256': engine.signature(linked), 'schema_version': 2,
                'operations': [{'operation': engine.REMOVE_BREAK,
                                'target_id': record['target_id'],
                                'expected_break_type': 'page'}]}
        out = self.dir / 'hyperlinked-out.docx'
        engine.apply(linked, out, plan)
        after = engine.open_package(out).document
        link_after = engine.paras(after)[1].find(engine.q('hyperlink'))
        self.assertEqual(link_after.get(engine.q('anchor')), 'top')
        self.assertIsNone(link_after.find('.//' + engine.q('br')))
        self.assertEqual(''.join(link_after.itertext()), 'Linked text.')

    def test_break_inside_a_table_cell_is_listed_and_never_a_target(self):
        # The cell's paragraph is not a top-level body paragraph, so it has no
        # paragraph id and can never be a candidate target. It is still *listed*, as
        # CAPABILITIES.md promises: an earlier version left it out entirely, so a
        # person was never told the break existed.
        root = engine.open_package(self.page_doc).document
        self.assertEqual(len(root.xpath('//w:tbl//w:br', namespaces=engine.NS)), 1)
        report = self.breaks()
        in_table, = [b for b in report['breaks'] if b['paragraph_id'] is None]
        self.assertEqual(in_table['container'], 'tbl')
        self.assertEqual(in_table['break_type'], 'page')
        self.assertFalse(in_table['edit_eligible'])
        self.assertIsNone(in_table['target_id'])
        self.assertIn('<w:tbl>', in_table['ineligible_reason'])
        self.assertEqual(in_table['text_after'], 'In a table cell.')
        self.assertTrue(all(b['paragraph_id'] < len(engine.paras(root))
                            for b in report['breaks'] if b['paragraph_id'] is not None))
        self.assertEqual(report['break_count'], 6)
        self.assertEqual(report['removable_count'], 3)

    def test_removing_a_break_inside_a_table_is_caught_by_the_preservation_census(self):
        # Listing the table break also puts it in the invariant census, so a candidate
        # that lost it would now fail the preservation check instead of passing.
        import workflow
        root = engine.open_package(self.page_doc).document
        before = workflow._break_census(root, 'x')
        cell_break, = root.xpath('//w:tbl//w:br', namespaces=engine.NS)
        cell_break.getparent().remove(cell_break)
        self.assertNotEqual(workflow._break_census(root, 'x'), before)

    def test_two_breaks_in_one_paragraph_are_separately_identifiable(self):
        here = [b for b in self.removable()
                if b['paragraph_id'] == break_fixtures.MANUAL_PAGE_BREAK_IDS[
                    'two_breaks_paragraph']]
        self.assertEqual(len(here), 2)
        self.assertNotEqual(here[0]['target_id'], here[1]['target_id'])
        self.assertEqual([b['ordinal_in_paragraph'] for b in here], [1, 2])
        # The nearby text is what lets a person tell them apart, so it has to differ.
        self.assertNotEqual(here[0]['text_before'], here[1]['text_before'])
        self.assertTrue(here[0]['text_after'])

    def test_run_properties_are_reported_for_a_formatted_run(self):
        formatted = [b for b in self.removable()
                     if b['paragraph_id'] == break_fixtures.MANUAL_PAGE_BREAK_IDS[
                         'deliberate_break_paragraph']]
        self.assertTrue(formatted[0]['run_has_properties'])

    def test_a_paragraph_with_no_break_contributes_nothing(self):
        clean = break_fixtures.MANUAL_PAGE_BREAK_IDS['clean_paragraph']
        self.assertEqual([b for b in self.breaks()['breaks']
                          if b['paragraph_id'] == clean], [])

    def test_ordinals_are_unique_per_paragraph_and_type(self):
        # Two rows reading "page break 1 in this paragraph" would leave a person with
        # no way to say which they meant, so the ordinal counts every break of that
        # type in the paragraph, editable or not.
        seen = [(b['paragraph_id'], b['break_type'], b['ordinal_in_paragraph'])
                for b in self.breaks()['breaks'] if b['kind'] == 'explicit_break']
        self.assertEqual(len(seen), len(set(seen)))
        page_in_two = [b['ordinal_in_paragraph'] for b in self.breaks()['breaks']
                       if b['paragraph_id'] == 2 and b['break_type'] == 'page']
        self.assertEqual(page_in_two, [1, 2])

    def test_an_ineligible_break_continues_the_same_ordinal_sequence(self):
        root = engine.open_package(self.page_doc).document
        paragraph = engine.paras(root)[2]
        link = E.SubElement(paragraph, engine.q('smartTag'))
        run = E.SubElement(link, engine.q('r'))
        E.SubElement(run, engine.q('br')).set(engine.q('type'), 'page')
        mixed = _write_document(self.page_doc, self.dir / 'mixed-eligibility.docx', root)
        ordinals = [(b['ordinal_in_paragraph'], b['edit_eligible'])
                    for b in engine.inspect_breaks(mixed)['breaks']
                    if b['paragraph_id'] == 2]
        self.assertEqual(ordinals, [(1, True), (2, True), (3, False)])

    def test_target_ids_are_stable_across_repeated_inspection(self):
        self.assertEqual([b['target_id'] for b in self.breaks()['breaks']],
                         [b['target_id'] for b in self.breaks()['breaks']])

    def test_target_ids_differ_between_documents(self):
        mine = {b['target_id'] for b in self.removable()}
        theirs = {b['target_id'] for b in self.removable(self.column_doc)}
        self.assertFalse(mine & theirs)

    def test_warning_states_the_identification_is_not_a_recommendation(self):
        self.assertIn('not a recommendation', self.breaks()['warning'])

    def test_paragraph_property_document_has_no_explicit_breaks(self):
        # The pre-existing sample plants w:pageBreakBefore, a paragraph property.
        # If this ever reported an explicit break, the two faults would be confused.
        sample = self.dir / 'sample.docx'
        fixtures.build(sample)
        report = engine.inspect_breaks(sample)
        self.assertEqual(report['break_count'], 0)
        self.assertEqual(report['removable_count'], 0)
        inspection = engine.inspect(sample)
        faulty = fixtures.FAULT_IDS['unwanted_page_break_before']
        self.assertTrue(inspection['paragraphs'][faulty]
                        ['effective_properties']['page_break_before']['value'])


# ---------------------------------------------------------------------------
# Section inspection: read-only, and correct about which section is which
# ---------------------------------------------------------------------------
class SectionInspectionTest(BreakFixtureCase):

    def setUp(self):
        super().setUp()
        self.two_section = self.dir / 'sections.docx'
        break_fixtures.build_two_section(self.two_section)

    def test_paragraph_sectpr_governs_the_section_ending_with_that_paragraph(self):
        report = engine.inspect_sections(self.two_section)
        first = report['sections'][0]
        self.assertEqual(first['defined_in'], 'paragraph_properties')
        self.assertEqual(first['defining_paragraph_id'], 1)
        self.assertEqual(first['last_paragraph_id'], 1)
        self.assertEqual(first['first_paragraph_id'], 0)
        self.assertIn('ends with paragraph 1', first['governs'])

    def test_final_body_sectpr_governs_the_final_section(self):
        report = engine.inspect_sections(self.two_section)
        final = report['sections'][-1]
        self.assertEqual(final['defined_in'], 'body')
        self.assertIsNone(final['defining_paragraph_id'])
        self.assertTrue(final['is_final_section'])
        self.assertTrue(report['has_final_body_section_properties'])

    def test_continuous_and_default_start_types_are_distinguished(self):
        sections = engine.inspect_sections(self.two_section)['sections']
        self.assertEqual(sections[0]['start_type'], 'continuous')
        self.assertFalse(sections[0]['start_type_is_default'])
        self.assertTrue(sections[0]['is_continuous'])
        # The body section carries no w:type, so nextPage is applied as the schema
        # default and the record says the value was defaulted, not written.
        self.assertEqual(sections[1]['start_type'], 'nextPage')
        self.assertTrue(sections[1]['start_type_is_default'])
        self.assertIsNone(sections[1]['start_type_raw'])

    def test_next_column_start_type_is_recognised(self):
        root = engine.open_package(self.two_section).document
        element = root.xpath('//w:sectPr/w:type', namespaces=engine.NS)[0]
        element.set(engine.q('val'), 'nextColumn')
        records, _ = structure.locate_sections(root, engine.paras(root))
        self.assertEqual(records[0]['start_type'], 'nextColumn')
        self.assertTrue(records[0]['starts_new_column'])
        self.assertFalse(records[0]['is_continuous'])

    def test_columns_are_reported_from_the_actual_cols_element(self):
        sections = engine.inspect_sections(self.two_section)['sections']
        self.assertEqual(sections[0]['columns']['count'], 2)
        self.assertTrue(sections[0]['columns']['declared'])
        self.assertFalse(sections[1]['columns']['declared'])
        self.assertEqual(sections[1]['columns']['count'], 1)

    def test_this_fixture_offers_no_section_change_and_says_why(self):
        # Section 0 is the first section, and section 1 declares page margins the
        # first section does not. Neither qualifies, and each refusal names its own
        # rule rather than a blanket "sections are read-only".
        report = engine.inspect_sections(self.two_section)
        self.assertFalse(report['editable'])
        self.assertEqual(report['editable_target_ids'], [])
        self.assertTrue(all(not s['edit_eligible'] for s in report['sections']))
        self.assertIn('first section', report['sections'][0]['ineligible_reason'])
        self.assertIn('w:pgMar', report['sections'][1]['differing_page_properties'])
        self.assertIn('continuous', report['read_only_reason'])

    def test_the_one_supported_change_is_declared_in_the_report(self):
        report = engine.inspect_sections(self.two_section)
        self.assertEqual(report['supported_start_changes'],
                         [{'from': 'nextPage', 'to': 'continuous'}])

    def test_no_page_number_is_invented(self):
        report = engine.inspect_sections(self.two_section)
        self.assertIn('not page numbers', report['warning'])
        for record in report['sections']:
            self.assertFalse([k for k in record if 'page_number' in k])

    def test_a_document_without_a_body_sectpr_is_handled(self):
        root = engine.open_package(self.two_section).document
        body = root.find(engine.q('body'))
        body.remove(list(body)[-1])
        records, _ = structure.locate_sections(root, engine.paras(root))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['defined_in'], 'paragraph_properties')
        self.assertTrue(records[0]['is_final_section'])


# ---------------------------------------------------------------------------
# Validation: what is refused, and with what message
# ---------------------------------------------------------------------------
class ValidationTest(BreakFixtureCase):

    def validate(self, plan, path=None):
        package = engine.open_package(path or self.page_doc)
        engine.validate(plan, package.document, package.styles)

    def test_a_valid_removal_passes(self):
        self.validate(self.plan(self.removable()[0]))

    def test_an_unknown_target_is_refused(self):
        plan = self.plan(self.removable()[0])
        plan['operations'][0]['target_id'] = 'brk-0000000000000000'
        with self.assertRaisesRegex(ValueError, 'Unknown or stale break target'):
            self.validate(plan)

    def test_a_target_from_another_revision_is_refused_as_stale(self):
        # Same document, a different recorded hash: every id derives from the hash, so
        # the ids in this plan belong to a revision that is not the one on disk.
        plan = self.plan(self.removable()[0])
        plan['source_sha256'] = 'a' * 64
        with self.assertRaisesRegex(ValueError, 'Unknown or stale break target'):
            self.validate(plan)

    def test_a_target_from_a_different_document_is_refused(self):
        other = self.removable(self.column_doc)[0]
        plan = self.plan(self.removable()[0])
        plan['operations'][0]['target_id'] = other['target_id']
        with self.assertRaisesRegex(ValueError, 'Unknown or stale break target'):
            self.validate(plan)

    def test_the_wrong_expected_type_is_refused(self):
        column = [b for b in self.removable(self.column_doc)
                  if b['break_type'] == 'column'][0]
        plan = self.plan(column, self.column_doc, break_type='page')
        with self.assertRaisesRegex(ValueError, 'is a column break, not the page break'):
            self.validate(plan, self.column_doc)

    def test_an_ineligible_context_is_refused_with_its_reason(self):
        wrapped = [b for b in self.breaks()['breaks']
                   if b['target_id'] and not b['edit_eligible']
                   and b['break_type'] == 'page'][0]
        plan = self.plan(wrapped)
        with self.assertRaisesRegex(ValueError, 'w:smartTag'):
            self.validate(plan)

    def test_a_line_break_cannot_be_targeted(self):
        line = [b for b in self.breaks()['breaks']
                if b.get('break_type') == 'textWrapping'][0]
        plan = self.plan(line)
        # The type is not even an accepted value for expected_break_type.
        with self.assertRaisesRegex(ValueError, 'expected_break_type must be one of'):
            plan['operations'][0]['expected_break_type'] = 'textWrapping'
            self.validate(plan)

    def test_duplicate_removal_of_one_target_is_refused(self):
        plan = self.plan(self.removable()[0])
        plan['operations'] = plan['operations'] * 2
        with self.assertRaisesRegex(ValueError, 'Conflicting duplicate operation'):
            self.validate(plan)

    def test_a_malformed_break_operation_is_refused(self):
        plan = self.plan(self.removable()[0])
        plan['operations'][0]['extra'] = 1
        with self.assertRaisesRegex(ValueError, 'needs exactly operation, target_id'):
            self.validate(plan)

    def test_an_unknown_operation_name_is_refused(self):
        plan = self.plan(self.removable()[0])
        plan['operations'][0]['operation'] = 'delete_paragraph'
        with self.assertRaisesRegex(ValueError, 'Unsupported operation'):
            self.validate(plan)

    def test_a_break_operation_requires_the_new_schema_version(self):
        plan = self.plan(self.removable()[0])
        plan.pop('schema_version')
        with self.assertRaisesRegex(ValueError, 'require schema_version 2'):
            self.validate(plan)

    def test_an_unknown_schema_version_is_refused(self):
        plan = self.plan(self.removable()[0])
        plan['schema_version'] = 99
        with self.assertRaisesRegex(ValueError, 'Unsupported plan schema_version'):
            self.validate(plan)

    def test_paragraph_plans_without_a_schema_version_still_validate(self):
        # Backward compatibility, stated as a test rather than as a claim: the exact
        # plan shape used before this feature existed must still be accepted.
        report = engine.inspect(self.page_doc)
        legacy = {'source_sha256': report['source_sha256'],
                  'operations': [{'paragraph_id': 1, 'field': 'space_before_pt',
                                  'value': 6.0}]}
        self.validate(legacy)
        self.assertEqual(engine.plan_schema_version(legacy), 1)
        self.assertEqual(engine.operation_kind(legacy), 'paragraph_property')


# ---------------------------------------------------------------------------
# Removal: exactly one element, and nothing else
# ---------------------------------------------------------------------------
class RemovalTest(BreakFixtureCase):

    def test_removing_the_second_break_leaves_the_first_in_place(self):
        here = [b for b in self.removable()
                if b['paragraph_id'] == break_fixtures.MANUAL_PAGE_BREAK_IDS[
                    'two_breaks_paragraph']]
        out, report = self.apply_plan(self.plan(here[1]))
        remaining = [(b['paragraph_id'], b['run_index'], b['break_type'])
                     for b in engine.inspect_breaks(out)['breaks'] if b['edit_eligible']]
        self.assertEqual(remaining, [(2, 1, 'page'), (3, 0, 'page')])
        self.assertEqual(report['break_changes'][0]['ordinal_in_paragraph'], 2)
        self.assertEqual(report['break_changes'][0]['run_index'], here[1]['run_index'])

    def test_removing_the_first_break_leaves_the_second_in_place(self):
        here = [b for b in self.removable()
                if b['paragraph_id'] == break_fixtures.MANUAL_PAGE_BREAK_IDS[
                    'two_breaks_paragraph']]
        out, _ = self.apply_plan(self.plan(here[0]))
        remaining = [(b['paragraph_id'], b['run_index'])
                     for b in engine.inspect_breaks(out)['breaks'] if b['edit_eligible']]
        self.assertEqual(remaining, [(2, 2), (3, 0)])

    def test_text_and_its_order_survive(self):
        out, _ = self.apply_plan(self.plan(self.removable()[0]))
        self.assertEqual(self.text_of(self.page_doc), self.text_of(out))

    def test_run_count_and_run_properties_survive(self):
        formatted = [b for b in self.removable()
                     if b['paragraph_id'] == break_fixtures.MANUAL_PAGE_BREAK_IDS[
                         'deliberate_break_paragraph']][0]
        out, _ = self.apply_plan(self.plan(formatted))
        before, after = self.runs_of(self.page_doc), self.runs_of(out)
        self.assertEqual(len(before), len(after))
        # The bold run keeps its w:rPr; only the w:br is gone from it.
        self.assertIn(b'<w:b/>', [r for r in after if b'deliberate' in r][0])
        self.assertNotIn(b'<w:br', [r for r in after if b'deliberate' in r][0])

    def test_the_run_is_not_deleted_even_when_the_break_was_its_only_content(self):
        root = engine.open_package(self.page_doc).document
        paragraph = engine.paras(root)[1]
        run = E.SubElement(paragraph, engine.q('r'))
        E.SubElement(run, engine.q('br')).set(engine.q('type'), 'page')
        lone = self.dir / 'lone.docx'
        _write_document(self.page_doc, lone, root)
        report = engine.inspect_breaks(lone)
        target = [b for b in report['breaks']
                  if b['edit_eligible'] and b['paragraph_id'] == 1][0]
        plan = {'schema_version': 2, 'source_sha256': report['source_sha256'],
                'operations': [{'operation': 'remove_explicit_break',
                                'target_id': target['target_id'],
                                'expected_break_type': 'page'}]}
        out = self.dir / 'lone-out.docx'
        engine.apply(lone, out, plan)
        self.assertEqual(len(self.runs_of(lone)), len(self.runs_of(out)))
        self.assertEqual(self.text_of(lone), self.text_of(out))

    def test_paragraph_properties_and_section_properties_survive(self):
        sections = self.dir / 'sections.docx'
        break_fixtures.build_two_section(sections)
        root = engine.open_package(sections).document
        run = engine.paras(root)[0].xpath('.//w:r', namespaces=engine.NS)[0]
        run.insert(0, E.SubElement(E.Element('x'), engine.q('br')))
        run[0].set(engine.q('type'), 'page')
        with_break = self.dir / 'sect-break.docx'
        _write_document(sections, with_break, root)
        report = engine.inspect_breaks(with_break)
        target = [b for b in report['breaks'] if b['edit_eligible']][0]
        plan = {'schema_version': 2, 'source_sha256': report['source_sha256'],
                'operations': [{'operation': 'remove_explicit_break',
                                'target_id': target['target_id'],
                                'expected_break_type': 'page'}]}
        out = self.dir / 'sect-out.docx'
        engine.apply(with_break, out, plan)
        self.assertEqual(engine.inspect_sections(with_break)['sections'][0]['start_type'],
                         engine.inspect_sections(out)['sections'][0]['start_type'])
        self.assertEqual(len(engine.inspect_sections(out)['sections']), 2)

    def test_unrelated_zip_members_are_byte_identical(self):
        from zipfile import ZipFile
        out, _ = self.apply_plan(self.plan(self.removable()[0]))
        with ZipFile(self.page_doc) as before, ZipFile(out) as after:
            self.assertEqual(before.namelist(), after.namelist())
            for name in before.namelist():
                if name != 'word/document.xml':
                    self.assertEqual(before.read(name), after.read(name), name)

    def test_the_original_file_is_untouched(self):
        signature = engine.signature(self.page_doc)
        self.apply_plan(self.plan(self.removable()[0]))
        self.assertEqual(engine.signature(self.page_doc), signature)

    def test_the_original_is_never_overwritten(self):
        plan = self.plan(self.removable()[0])
        with self.assertRaisesRegex(ValueError, 'originals are never overwritten'):
            engine.apply(self.page_doc, self.page_doc, plan)

    def test_a_second_removal_attempt_against_the_candidate_is_stale(self):
        """Re-running the same plan is refused rather than removing a second break."""
        plan = self.plan(self.removable()[0])
        out, _ = self.apply_plan(plan)
        # The candidate is a different revision, so the plan's ids no longer resolve.
        # Validation runs before the hash comparison, so the stale *target* is what is
        # reported; re-stamping the plan with the candidate's hash does not rescue it,
        # because the ids derive from the hash as well.
        second = self.dir / 'second.docx'
        with self.assertRaisesRegex(ValueError, 'Unknown or stale break target'):
            engine.apply(out, second, plan)
        rebased = dict(plan, source_sha256=engine.signature(out))
        with self.assertRaisesRegex(ValueError, 'Unknown or stale break target'):
            engine.apply(out, second, rebased)
        self.assertFalse(second.exists())

    def test_a_column_break_removal_removes_only_the_column_break(self):
        column = [b for b in self.removable(self.column_doc)
                  if b['break_type'] == 'column'][0]
        out, _ = self.apply_plan(self.plan(column, self.column_doc, 'column'),
                                 self.column_doc, 'col-out.docx')
        after = engine.inspect_breaks(out)['breaks']
        self.assertEqual([b['break_type'] for b in after], ['page'])
        self.assertEqual(self.text_of(self.column_doc), self.text_of(out))

    def test_a_mixed_plan_applies_both_kinds_of_change(self):
        target = [b for b in self.removable()
                  if b['paragraph_id'] == break_fixtures.MANUAL_PAGE_BREAK_IDS[
                      'two_breaks_paragraph']][0]
        plan = self.plan(target)
        plan['operations'].append({'paragraph_id': 1, 'field': 'space_after_pt',
                                   'value': 18.0})
        out, report = self.apply_plan(plan, name='mixed.docx')
        self.assertEqual(report['operation_kind'], 'mixed')
        self.assertEqual(len(report['break_changes']), 1)
        self.assertEqual(len(report['changes']), 1)
        self.assertEqual(self.text_of(self.page_doc), self.text_of(out))
        self.assertEqual(engine.inspect(out)['paragraphs'][1]
                         ['effective_properties']['space_after_pt']['value'], 18.0)

    def test_a_mixed_plan_on_one_paragraph_applies_both(self):
        target = [b for b in self.removable()
                  if b['paragraph_id'] == break_fixtures.MANUAL_PAGE_BREAK_IDS[
                      'two_breaks_paragraph']][0]
        plan = self.plan(target)
        plan['operations'].append({'paragraph_id': target['paragraph_id'],
                                   'field': 'alignment', 'value': 'left'})
        out, report = self.apply_plan(plan, name='mixed-same.docx')
        self.assertEqual(len(engine.inspect_breaks(out)['breaks']),
                         len(self.breaks()['breaks']) - 1)
        self.assertEqual(engine.inspect(out)['paragraphs'][target['paragraph_id']]
                         ['effective_properties']['alignment']['value'], 'left')

    def test_the_verifier_catches_a_change_beyond_the_approved_break(self):
        """The narrow check is narrow: an extra edit inside the run still fails."""
        before = engine.open_package(self.page_doc).document
        after = copy.deepcopy(before)
        paragraph_before = engine.paras(before)[2]
        paragraph_after = engine.paras(after)[2]
        run = paragraph_after.xpath('.//w:r', namespaces=engine.NS)[1]
        element = run.findall(engine.q('br'))[0]
        child_index = list(run).index(element)
        run.remove(element)
        # ... and then one extra, unapproved edit.
        run.xpath('.//w:t', namespaces=engine.NS)[0].text = 'tampered'
        with self.assertRaisesRegex(ValueError, 'beyond the approved break removal'):
            engine._verify_break_removal(
                paragraph_before, paragraph_after,
                [{'run_index': 1, 'child_index': child_index}])


def _write_document(source, destination, root):
    """Repack `source` with a replaced word/document.xml. Test helper only."""
    from zipfile import ZipFile
    xml = E.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)
    with ZipFile(source) as original, ZipFile(destination, 'w') as out:
        for info in original.infolist():
            out.writestr(info, xml if info.filename == 'word/document.xml'
                         else original.read(info.filename))
    return destination


# ---------------------------------------------------------------------------
# Preservation invariants, checked against the bytes on disk
# ---------------------------------------------------------------------------
class InvariantTest(BreakFixtureCase):

    def repair(self, plan, path=None, name='wf', **kwargs):
        return workflow.repair(path or self.page_doc, plan, self.dir / name,
                               **{'do_render': False, **kwargs})

    def test_a_break_removal_passes_every_structural_invariant(self):
        report = self.repair(self.plan(self.removable()[0]))
        failures = [f for f in report['invariant_failures'] if f != 'rendering_required']
        self.assertEqual(failures, [])
        for name in ('run_count_preserved', 'run_properties_preserved',
                     'run_text_preserved', 'only_approved_breaks_removed',
                     'approved_break_count_matches'):
            self.assertTrue(report['invariants'][name]['passed'], name)

    def test_the_strict_run_check_still_applies_to_paragraph_repairs(self):
        """The original invariant is not disabled globally, only replaced per plan."""
        inspection = engine.inspect(self.page_doc)
        plan = {'source_sha256': inspection['source_sha256'],
                'operations': [{'paragraph_id': 1, 'field': 'space_after_pt',
                                'value': 18.0}]}
        report = self.repair(plan, name='wf-prop')
        self.assertIn('runs_preserved', report['invariants'])
        self.assertTrue(report['invariants']['runs_preserved']['passed'])
        self.assertNotIn('only_approved_breaks_removed', report['invariants'])
        self.assertEqual(report['invariant_failures'], [])
        self.assertEqual(report['operation_kind'], 'paragraph_property')

    def test_only_the_targeted_paragraph_changes(self):
        report = self.repair(self.plan(self.removable()[0]))
        self.assertTrue(report['invariants']['only_targeted_paragraphs_changed']['passed'])
        self.assertIn('within targeted [2]',
                      report['invariants']['only_targeted_paragraphs_changed']['detail'])

    def test_a_tampered_candidate_fails_the_break_census(self):
        plan = self.plan(self.removable()[0])
        report = self.repair(plan, name='wf-tamper')
        candidate = Path(report['candidate'])
        root = engine.open_package(candidate).document
        # Remove a second, unapproved break from the candidate behind the check's back.
        extra = engine.paras(root)[3].xpath('.//w:br', namespaces=engine.NS)[0]
        extra.getparent().remove(extra)
        tampered = _write_document(candidate, self.dir / 'tampered.docx', root)
        checks, failures = workflow.check_invariants(
            self.page_doc, tampered, plan, engine.signature(self.page_doc))
        self.assertIn('only_approved_breaks_removed', failures)

    def test_rendering_is_required_for_a_break_removal(self):
        report = self.repair(self.plan(self.removable()[0]), name='wf-norender')
        self.assertTrue(report['rendering_required'])
        self.assertFalse(report['rendering_satisfied'])
        self.assertEqual(report['invariant_failures'], ['rendering_required'])
        self.assertEqual(report['outcome'], 'failed')
        self.assertIn('requires a successful rendering',
                      report['render_requirement_failure'])

    def test_rendering_is_not_required_for_a_paragraph_repair(self):
        inspection = engine.inspect(self.page_doc)
        plan = {'source_sha256': inspection['source_sha256'],
                'operations': [{'paragraph_id': 1, 'field': 'space_after_pt',
                                'value': 18.0}]}
        report = self.repair(plan, name='wf-prop-norender')
        self.assertFalse(report['rendering_required'])
        self.assertTrue(report['rendering_satisfied'])
        self.assertEqual(report['outcome'], 'awaiting_review')


# ---------------------------------------------------------------------------
# Rendered demonstrations. Skipped, not faked, without LibreOffice.
# ---------------------------------------------------------------------------
@unittest.skipUnless(render_module.find_soffice(),
                     'LibreOffice is not installed; a rendered demonstration cannot '
                     'be faked, so this is reported as not run')
class RenderedDemonstrationTest(BreakFixtureCase):

    def test_removing_a_manual_page_break_reflows_the_rendered_document(self):
        plan = self.plan(self.removable()[0])
        report = workflow.repair(self.page_doc, plan, self.dir / 'render-page',
                                 do_render=True)
        self.assertEqual(report['invariant_failures'], [])
        self.assertEqual(report['outcome'], 'awaiting_review')
        self.assertTrue(report['rendering_satisfied'])
        comparison = report['visual_comparison']
        # A page break removal is *expected* to pull content back, and here it does.
        # The assertion is that the rendering changed, not that a page was always lost:
        # a document can reflow without the page count moving, which the column case
        # below actually demonstrates.
        self.assertGreater(comparison['before_pages'], 0)
        self.assertEqual(comparison['page_count_delta'], -1)
        self.assertTrue(any(p.get('changed_ratio', 0) > 0 for p in comparison['pages']))
        self.assertTrue(report['original_render']['pages'])
        self.assertTrue(report['candidate_render']['pages'])

    def test_removing_a_column_break_reflows_without_losing_a_page(self):
        column = [b for b in self.removable(self.column_doc)
                  if b['break_type'] == 'column'][0]
        plan = self.plan(column, self.column_doc, 'column')
        report = workflow.repair(self.column_doc, plan, self.dir / 'render-column',
                                 do_render=True)
        self.assertEqual(report['invariant_failures'], [])
        comparison = report['visual_comparison']
        self.assertEqual(comparison['page_count_delta'], 0)
        # The page count is unchanged and the page still changed: removing a break
        # does not have to cost a page, and nothing here promises that it does.
        self.assertTrue(any(p.get('changed_ratio', 0) > 0 for p in comparison['pages']))


# ---------------------------------------------------------------------------
# The diagnosis schema's half of the contract
# ---------------------------------------------------------------------------
class DiagnosisSchemaTest(BreakFixtureCase):

    def answer(self, **overrides):
        base = {'status': 'proposed', 'explanation': 'The second break is unwanted.',
                'evidence': [{'paragraph_id': 2, 'target_id': 'brk-aaaaaaaaaaaaaaaa'}],
                'source_sha256': 'b' * 64,
                'operations': [{'operation': 'remove_explicit_break',
                                'target_id': 'brk-aaaaaaaaaaaaaaaa',
                                'expected_break_type': 'page'}]}
        base.update(overrides)
        return base

    def check(self, raw, targets=('brk-aaaaaaaaaaaaaaaa',)):
        return diagnosis.check(raw, 2, 'b' * 64, supplied_ids={2},
                               supplied_targets=targets)

    def test_a_break_removal_is_accepted_and_versions_the_plan(self):
        found = self.check(self.answer())
        plan = diagnosis.plan_of(found)
        # A break removal versions the plan at 2, the version that introduced it,
        # not at whatever the newest schema version happens to be.
        self.assertEqual(plan['schema_version'], engine.BREAK_PLAN_SCHEMA_VERSION)
        self.assertEqual(plan['operations'][0]['operation'], 'remove_explicit_break')

    def test_a_paragraph_plan_carries_no_schema_version(self):
        found = self.check(self.answer(
            evidence=[{'paragraph_id': 2, 'field': 'space_before_pt'}],
            operations=[{'paragraph_id': 2, 'field': 'space_before_pt', 'value': 6.0}]))
        self.assertNotIn('schema_version', diagnosis.plan_of(found))

    def test_an_unsupplied_target_is_refused_as_invented(self):
        with self.assertRaisesRegex(diagnosis.DiagnosisError, 'was not supplied'):
            self.check(self.answer(), targets=('brk-bbbbbbbbbbbbbbbb',))

    def test_evidence_citing_an_unsupplied_break_is_refused(self):
        with self.assertRaisesRegex(diagnosis.DiagnosisError, 'cites break'):
            self.check(self.answer(
                evidence=[{'paragraph_id': 2, 'target_id': 'brk-cccccccccccccccc'}]))

    def asking(self, evidence):
        return self.answer(status='needs_clarification', operations=[],
                           evidence=evidence, question='Which break do you mean?')

    def test_an_exact_repeat_of_a_break_citation_is_dropped_from_a_question(self):
        cited = {'paragraph_id': 2, 'target_id': 'brk-aaaaaaaaaaaaaaaa'}
        found = self.check(self.asking([cited, dict(cited)]))
        self.assertEqual(found['evidence'], [cited])
        self.assertEqual(found['evidence_duplicates_removed'], 1)

    def test_a_break_removal_with_a_repeated_citation_is_still_refused(self):
        cited = {'paragraph_id': 2, 'target_id': 'brk-aaaaaaaaaaaaaaaa'}
        with self.assertRaisesRegex(diagnosis.DiagnosisError, 'duplicate evidence'):
            self.check(self.answer(evidence=[cited, dict(cited)]))

    def test_one_break_cited_under_two_paragraphs_is_refused_as_conflicting(self):
        # A break belongs to exactly one paragraph. The same id under two different
        # paragraph ids is a contradiction, not a repeat, and is never merged.
        conflicting = [{'paragraph_id': 2, 'target_id': 'brk-aaaaaaaaaaaaaaaa'},
                       {'paragraph_id': 3, 'target_id': 'brk-aaaaaaaaaaaaaaaa'}]
        for raw in (self.answer(evidence=conflicting), self.asking(conflicting)):
            with self.subTest(status=raw['status']):
                with self.assertRaisesRegex(diagnosis.DiagnosisError, 'conflicting'):
                    diagnosis.check(raw, 2, 'b' * 64, supplied_ids={2, 3},
                                    supplied_targets=('brk-aaaaaaaaaaaaaaaa',))

    def test_a_repeated_citation_of_an_unsupplied_break_still_fails(self):
        cited = {'paragraph_id': 2, 'target_id': 'brk-aaaaaaaaaaaaaaaa'}
        invented = {'paragraph_id': 2, 'target_id': 'brk-cccccccccccccccc'}
        with self.assertRaisesRegex(diagnosis.DiagnosisError, 'cites break'):
            self.check(self.asking([cited, invented, dict(invented)]))

    def test_an_exact_repeat_of_a_section_citation_is_dropped(self):
        cited = {'section_id': 'sec-aaaaaaaaaaaaaaaa'}
        found = diagnosis.check(self.answer(
            status='no_change', operations=[], evidence=[cited, dict(cited)]),
            2, 'b' * 64, supplied_ids={2}, supplied_targets=('sec-aaaaaaaaaaaaaaaa',))
        self.assertEqual(found['evidence'], [cited])
        self.assertEqual(found['evidence_duplicates_removed'], 1)

    def test_only_one_break_may_be_removed_at_a_time(self):
        with self.assertRaisesRegex(diagnosis.DiagnosisError, 'at most 1 break removal'):
            self.check(self.answer(operations=[
                {'operation': 'remove_explicit_break',
                 'target_id': 'brk-aaaaaaaaaaaaaaaa', 'expected_break_type': 'page'},
                {'operation': 'remove_explicit_break',
                 'target_id': 'brk-dddddddddddddddd', 'expected_break_type': 'page'}]),
                targets=('brk-aaaaaaaaaaaaaaaa', 'brk-dddddddddddddddd'))

    def test_an_unknown_operation_name_is_refused(self):
        with self.assertRaisesRegex(diagnosis.DiagnosisError, 'operation must be'):
            self.check(self.answer(operations=[
                {'operation': 'remove_section', 'target_id': 'brk-aaaaaaaaaaaaaaaa',
                 'expected_break_type': 'page'}]))

    def test_an_abstention_may_not_carry_a_break_removal(self):
        with self.assertRaisesRegex(diagnosis.DiagnosisError, 'must carry no operations'):
            self.check(self.answer(status='no_change'))

    def test_needs_clarification_about_two_breaks_is_a_complete_answer(self):
        found = self.check(self.answer(
            status='needs_clarification',
            explanation='This paragraph contains two page breaks.',
            evidence=[], operations=[],
            question='Which break should go: the one before "Then", or before "Finally"?'))
        self.assertEqual(found['status'], 'needs_clarification')
        self.assertEqual(found['operations'], [])
        self.assertIn('Which break', found['question'])

    def test_a_mistyped_expected_break_type_is_refused(self):
        with self.assertRaisesRegex(diagnosis.DiagnosisError,
                                    'expected_break_type must be one of'):
            self.check(self.answer(operations=[
                {'operation': 'remove_explicit_break',
                 'target_id': 'brk-aaaaaaaaaaaaaaaa',
                 'expected_break_type': 'textWrapping'}]))


if __name__ == '__main__':
    unittest.main()
