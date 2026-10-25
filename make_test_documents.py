"""Write `test-documents/` : real .docx files plus a manifest of real target ids.

The files are committed, not generated on demand, so a tester never has to run this.
It exists so the manifest can be *computed* from the documents rather than written by
hand: every sha256, paragraph id, break target id and section target id below is read
back out of the file that was just written, which is why the manifest cannot drift
from the fixtures.

Target ids are bound to the source revision, so they are stable only while the file
is. They are printed here for the API and for checking what the interface shows you;
the supported way to choose one is still to click it in the interface.
"""
import json
import shutil
from pathlib import Path

import break_fixtures
import engine
import eval_fixtures
import fixtures
import section_fixtures

HERE = Path(__file__).resolve().parent
OUT = HERE / 'test-documents'


def breaks_of(path):
    report = engine.inspect_breaks(path)
    return [{'target_id': b['target_id'], 'break_type': b['break_type'],
             'paragraph_id': b['paragraph_id'],
             'ordinal_in_paragraph': b.get('ordinal_in_paragraph'),
             'edit_eligible': b['edit_eligible'],
             'text_before': b.get('text_before'), 'text_after': b.get('text_after'),
             'ineligible_reason': b.get('ineligible_reason')}
            for b in report['breaks']]


def sections_of(path):
    report = engine.inspect_sections(path)
    return [{'target_id': s['target_id'], 'section_index': s['section_index'],
             'defined_in': s['defined_in'], 'governs': s['governs'],
             'start_type': s['start_type'],
             'start_type_is_default': s['start_type_is_default'],
             'edit_eligible': s['edit_eligible'],
             'supported_start_changes': s['supported_start_changes'],
             'ineligible_reason': s['ineligible_reason'],
             'differing_page_properties': s['differing_page_properties']}
            for s in report['sections']]


def paragraphs_of(path, limit=400):
    return [{'paragraph_id': p['paragraph_id'], 'style_id': p['style_id'],
             'text': p['text'][:80]}
            for p in engine.inspect(path)['paragraphs']][:limit]


def portable_path(path):
    """`path` relative to the repository root, with forward slashes on every OS."""
    return Path(path).resolve().relative_to(HERE).as_posix()


CASES = []


def case(filename, builder, **entry):
    path = OUT / filename
    manifest = builder(path)
    # The builders report the absolute path they wrote to. The manifest is committed,
    # so it records the path relative to the repository root instead: an absolute path
    # would name one developer's machine and be wrong on every other one.
    if isinstance(manifest, dict) and 'path' in manifest:
        manifest = dict(manifest, path=portable_path(path))
    entry.update({
        'file': filename,
        'sha256': engine.signature(path),
        'bytes': path.stat().st_size,
        'paragraph_count': len(engine.inspect(path)['paragraphs']),
        'breaks': breaks_of(path),
        'sections': sections_of(path),
        'generator': manifest,
    })
    CASES.append(entry)
    return entry


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    case('01-paragraph-faults.docx', fixtures.build,
         title='Three paragraph-property faults in one document',
         repair_kind='paragraph_property',
         what_is_wrong='Paragraph 1 has a direct 48pt space above it where the '
                       'Heading1 style supplies 12pt. Paragraph 10 is centred where '
                       'its peers are justified. Paragraph 19 carries a direct '
                       'w:pageBreakBefore that strands the closing section on a '
                       'nearly empty page.',
         select_in_ui=['Paragraph 1 for the spacing fault',
                       'Paragraph 10 for the alignment fault',
                       'Paragraph 19 for the page-break-before fault'],
         complaints=['There is far too much space above this heading.',
                     'This paragraph is centred and the rest of the text is not.',
                     'This heading keeps jumping to a new page and leaves a nearly '
                     'empty one.'],
         expected='A proposal for each: space_before_pt to 12, alignment to both, '
                  'page_break_before to false. Each ends at awaiting_review.',
         check_visually='The gap above the heading matches the other headings; the '
                        'centred paragraph lines up with its neighbours; the closing '
                        'section no longer begins on its own page.',
         status='verified',
         status_detail='Covered by test_api, test_pipeline and test_workflow, and by '
                       'the rendered demonstrations recorded in TEST_RESULTS.md.')

    case('02-second-sample.docx', eval_fixtures.build,
         title='A second sample with abstention cases',
         repair_kind='paragraph_property',
         what_is_wrong='A mixture: some paragraphs are genuinely faulty, others '
                       'differ from their peers on purpose, and some carry an '
                       'alternative measurement the engine refuses to override.',
         select_in_ui=['Any paragraph the outline lists; its case is in '
                       'eval_fixtures.CASES'],
         complaints=['The spacing here looks wrong.'],
         expected='Several of these are meant to produce no_change, unsupported or '
                  'needs_clarification rather than a repair. An abstention is a '
                  'correct answer, not a failure.',
         check_visually='Nothing, when the service abstains. That is the point of '
                        'this document.',
         status='verified',
         status_detail='Covered by test_eval_fixtures and the recorded live-model '
                       'evaluation in TEST_RESULTS.md.')

    case('03-manual-page-break.docx', break_fixtures.build_manual_page_break,
         title='Explicit page breaks, including ones that cannot be removed',
         repair_kind='remove_explicit_break',
         what_is_wrong='Body paragraph 2 holds two explicit page breaks with text '
                       'either side of each. Paragraph 3 holds a deliberate one. '
                       'Paragraph 4 holds a line break and a lastRenderedPageBreak, '
                       'neither removable. A smart tag and a table cell each hold a '
                       'page break in an unsupported context.',
         select_in_ui=['Paragraph 2 in the outline, then the specific break in the '
                       'break list - the nearby text tells the two apart'],
         complaints=['There is a page break in this paragraph I never asked for and '
                     'it leaves a half-empty page.'],
         expected='With no break selected and two in the paragraph, the correct '
                  'answer is needs_clarification. With one selected, a proposal to '
                  'remove exactly that w:br, ending at awaiting_review.',
         check_visually='One page fewer, the text either side of the break now '
                        'joined, and every other break still present.',
         status='verified',
         status_detail='Rendered demonstration run this session: 6 pages to 5, 5 '
                       'breaks to 4, text identical, 12 of 12 invariants passed. See '
                       'TEST_RESULTS.md section 14.')

    case('04-column-break.docx', break_fixtures.build_multicolumn,
         title='An explicit column break in a genuinely two-column section',
         repair_kind='remove_explicit_break',
         what_is_wrong='Body paragraph 2 carries an explicit column break mid '
                       'sentence. Paragraph 3 carries a page break, so the two break '
                       'types have to be told apart by type and not by position.',
         select_in_ui=['Paragraph 2, then the break marked COLUMN'],
         complaints=['This column break splits the sentence across columns and I did '
                     'not want it.'],
         expected='A proposal to remove that one column break, ending at '
                  'awaiting_review. The page count does not change.',
         check_visually='The sentence runs on within the first column. The document '
                        'is still two pages: a reflow without a page-count change is '
                        'a correct result, not a failure.',
         status='verified',
         status_detail='Rendered demonstration run this session: 2 pages to 2 '
                       '(delta 0), 2 breaks to 1, text identical, 12 of 12 '
                       'invariants passed. See TEST_RESULTS.md section 14.')

    case('05-section-next-page.docx', section_fixtures.build_supported_next_page,
         title='A supported next-page section start',
         repair_kind='change_section_start',
         what_is_wrong='The document has two sections with identical page setup. '
                       'The second starts on a new page. Nothing is necessarily '
                       'wrong with that - this is the one case the new operation '
                       'supports, if you ask for it.',
         select_in_ui=['Any paragraph, then "Section 2 · starts nextPage" in the '
                       'SECTIONS panel. Section 1 is not selectable and says why.'],
         complaints=['The second part should carry on down the same page instead of '
                     'starting a new one.'],
         expected='A proposal changing that section start from nextPage to '
                  'continuous, with both renderings, ending at awaiting_review.',
         check_visually='Part Two begins directly under Part One instead of on a new '
                        'page. Check EVERY page, not only the first: a section change '
                        'can move everything after it. Continuous is not a promise '
                        'that a page disappears - page geometry can still force one.',
         status='verified',
         status_detail='Rendered demonstration run this session; see '
                       'TEST_RESULTS.md section 15 and test_sections.py.')

    case('06-section-default-start.docx', section_fixtures.build_default_start,
         title='A section whose start type is the schema default',
         repair_kind='change_section_start',
         what_is_wrong='The second section writes no w:type at all. ST_SectionMark '
                       'defaults to nextPage, so it behaves as a next-page start '
                       'even though nothing in the file says so.',
         select_in_ui=['The SECTIONS panel entry marked "(schema default; no w:type '
                       'written)"'],
         complaints=['The second part should carry on down the same page instead of '
                     'starting a new one.'],
         expected='The same proposal as 05. The repair creates the w:type element at '
                  'its schema position rather than refusing the section.',
         check_visually='As 05.',
         status='verified',
         status_detail='Covered by test_sections.ExactnessTests.'
                       'test_the_type_element_is_created_at_its_schema_position.')

    case('07-section-intentional.docx',
         section_fixtures.build_intentional_section_break,
         title='A deliberate section break that must survive',
         repair_kind='none_expected',
         what_is_wrong='Nothing. Appendix A deliberately starts on a new page. The '
                       'section is structurally identical to 05, which is exactly '
                       'the point: the service cannot tell them apart and must not '
                       'try.',
         select_in_ui=['Select the section, then complain about something else'],
         complaints=['The spacing in this report looks uneven.'],
         expected='no_change or unsupported. A proposal to make the appendix '
                  'continuous here would be WRONG, and is the single most important '
                  'thing to check on this document.',
         check_visually='Nothing should change. If the appendix stops starting on a '
                        'new page, that is a bug worth reporting.',
         status='verified',
         status_detail='Eligibility-is-not-a-recommendation is asserted in '
                       'test_sections.EligibilityTests and the abstention path in '
                       'test_sections.DiagnosisSchemaTests.')

    case('08-section-unsupported.docx', section_fixtures.build_odd_page_start,
         title='An unsupported case: an odd-page section start',
         repair_kind='refusal_expected',
         what_is_wrong='The second section starts on the next odd page. That is a '
                       'binding and printing requirement, not a formatting accident.',
         select_in_ui=['The SECTIONS panel lists it and offers no button for it'],
         complaints=['This section leaves a blank page before it, remove that.'],
         expected='A refusal. The section is reported with its start type and the '
                  'reason it is not convertible; selecting it over the API returns '
                  '400 unsupported_formatting. No proposal, no candidate.',
         check_visually='Nothing changes, and the interface says why rather than '
                        'failing silently.',
         status='verified',
         status_detail='Covered by test_sections and '
                       'test_api_sections.SectionSelectionTests.'
                       'test_an_ineligible_section_cannot_be_selected.')

    case('09-section-landscape.docx', section_fixtures.build_differing_geometry,
         title='An unsupported case: differing page geometry',
         repair_kind='refusal_expected',
         what_is_wrong='The second section is landscape. A continuous section '
                       'continues on the page the previous section is already on, so '
                       'it cannot carry a different page size onto it.',
         select_in_ui=['The SECTIONS panel names w:pgSz as the difference'],
         complaints=['Stop this landscape part starting a new page.'],
         expected='A refusal naming the differing property. This is the honest '
                  'answer: the conversion would silently reformat the document.',
         check_visually='Nothing changes.',
         status='verified',
         status_detail='Covered by test_sections.EligibilityTests.'
                       'test_differing_page_geometry_is_refused_and_named.')

    manifest = {
        'generated_by': 'make_test_documents.py',
        'what_this_is': 'Synthetic Word documents for trying the service by hand. '
                        'Every id below was read back out of the file after it was '
                        'written, so it matches the bytes in this directory.',
        'how_ids_work': 'A break or section target_id is a digest of the source '
                        'sha256 together with that element\'s position, so an id is '
                        'valid only for exactly these bytes. If you edit or re-save '
                        'a document in Word, every id for it changes and the service '
                        'will refuse the old one. Select through the interface, '
                        'which always shows current ids; the values here are for '
                        'checking what you are shown and for direct API calls.',
        'supported_repairs': [
            'six paragraph properties (see CAPABILITIES.md group 1)',
            'removing one explicit page or column break (group 2)',
            'changing one supported nextPage section start to continuous (group 3)'],
        'nothing_is_applied_automatically':
            'Every repair stops at awaiting_review. The original file in this '
            'directory is never written to, and a human accepts or rejects as a '
            'separate step before any download exists.',
        'documents': CASES,
    }
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2),
                                       encoding='utf-8')
    for entry in CASES:
        print('%-34s %s  %d breaks  %d sections  %s'
              % (entry['file'], entry['sha256'][:12], len(entry['breaks']),
                 len(entry['sections']), entry['repair_kind']))
    print('\nmanifest: %s' % (OUT / 'manifest.json'))


if __name__ == '__main__':
    main()
