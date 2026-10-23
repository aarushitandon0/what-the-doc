"""Real, Word-openable documents carrying more than one section.

Separate from `break_fixtures.py` for the same reason that file is separate from
`fixtures.py`: a section start is a third distinct thing. A `w:pageBreakBefore`
paragraph property, a `w:br w:type="page"` run element and a `w:sectPr/w:type`
section start all produce "a new page" in Word and none of them is repaired by
touching either of the others.

Every document here is a genuine package with enough prose to paginate, so a change
can be demonstrated by rendering it rather than only by reading its XML. The
packaging parts and the prose helpers are reused from the existing fixture modules so
there is one definition of what a valid package looks like.

Section geometry is written out in full and *identically* wherever two sections are
meant to be convertible, because the eligibility rule in `structure.py` compares a
section's page setup against the previous section's attribute by attribute. A fixture
that differs by one twip is a refusal fixture, not a supported one, and two of the
builders below are exactly that on purpose.
"""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from break_fixtures import STYLES, escape, filler, paragraph, run
from fixtures import CONTENT_TYPES, DOCUMENT_RELS, ROOT_RELS, SETTINGS, W

# One page geometry, written once and shared, so "identical to the previous section"
# is a property of the fixture rather than an accident of copy-paste.
GEOMETRY = ('<w:pgSz w:w="11906" w:h="16838"/>'
            '<w:pgMar w:top="1418" w:right="1418" w:bottom="1418" w:left="1418" '
            'w:header="709" w:footer="709" w:gutter="0"/>')
LANDSCAPE_GEOMETRY = ('<w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/>'
                      '<w:pgMar w:top="1418" w:right="1418" w:bottom="1418" '
                      'w:left="1418" w:header="709" w:footer="709" w:gutter="0"/>')


def sectpr(start_type=None, geometry=GEOMETRY, extra=''):
    """A sectPr with children in CT_SectPr schema order.

    `start_type=None` omits `w:type` entirely, which is the documented `nextPage`
    default and a case the repair has to handle by *creating* the element.
    """
    kind = '<w:type w:val="%s"/>' % start_type if start_type else ''
    return '<w:sectPr>%s%s%s</w:sectPr>' % (kind, geometry, extra)


def section_paragraph(text, start_type=None, geometry=GEOMETRY, extra=''):
    """A paragraph whose `w:pPr` carries a sectPr: it *ends* the section it describes."""
    return ('<w:p><w:pPr>%s</w:pPr><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>'
            % (sectpr(start_type, geometry, extra), escape(text)))


def _build(path, body, final_section):
    path = Path(path)
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="%s"><w:body>%s%s</w:body></w:document>'
                % (W, ''.join(body), final_section))
    with ZipFile(path, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', CONTENT_TYPES)
        archive.writestr('_rels/.rels', ROOT_RELS)
        archive.writestr('word/_rels/document.xml.rels', DOCUMENT_RELS)
        archive.writestr('word/document.xml', document)
        archive.writestr('word/styles.xml', STYLES)
        archive.writestr('word/settings.xml', SETTINGS)
    return path


def build_supported_next_page(path):
    """Two sections, identical page setup, the second an explicit `nextPage` start.

    Section 1 is the document's final section and is defined by the body-level
    `w:sectPr`, so this fixture also exercises the association the specification
    states: the final `w:sectPr` governs the final section, not the paragraph it
    happens to sit after.
    """
    body = [
        paragraph(run('Part One')),
        paragraph(run(filler(3))),
        section_paragraph('End of part one.', start_type='nextPage'),
        paragraph(run('Part Two')),
        paragraph(run(filler(3))),
        paragraph(run(filler(2))),
    ]
    _build(path, body, sectpr(start_type='nextPage'))
    return {
        'path': str(path),
        'section_count': 2,
        'eligible_section_index': 1,
        'eligible_defined_in': 'body',
        'section_defining_paragraph_id': 2,
        'note': 'Section 0 ends with body paragraph 2 and is the first section, so it '
                'is never eligible. Section 1 is the final body sectPr, starts '
                'nextPage, and declares page setup identical to section 0, so it is '
                'the one supported target.'}


def build_default_start(path):
    """The eligible section relies on the documented default: no `w:type` at all.

    The repair has to read the absent element as `nextPage` and then *create* it at
    its schema position, rather than refuse the section or append the element at the
    end of the sectPr where Word would reject it.
    """
    body = [
        paragraph(run('Opening')),
        paragraph(run(filler(3))),
        section_paragraph('End of the opening section.', start_type='nextPage'),
        paragraph(run('Continuation')),
        paragraph(run(filler(3))),
    ]
    _build(path, body, sectpr(start_type=None))
    return {
        'path': str(path),
        'section_count': 2,
        'eligible_section_index': 1,
        'start_type_is_default': True,
        'note': 'Section 1 omits w:type. ST_SectionMark defaults to nextPage, so the '
                'section is eligible and the repair must create w:type rather than '
                'edit one.'}


def build_three_sections(path):
    """Three sections, two of them eligible, so a target has to be chosen not guessed."""
    body = [
        paragraph(run('Chapter One')),
        paragraph(run(filler(2))),
        section_paragraph('End of chapter one.', start_type='nextPage'),
        paragraph(run('Chapter Two')),
        paragraph(run(filler(2))),
        section_paragraph('End of chapter two.', start_type='nextPage'),
        paragraph(run('Chapter Three')),
        paragraph(run(filler(2))),
    ]
    _build(path, body, sectpr(start_type='nextPage'))
    return {
        'path': str(path),
        'section_count': 3,
        'eligible_section_indexes': [1, 2],
        'note': 'Section 0 is first and never eligible. Sections 1 and 2 both start '
                'nextPage with identical page setup, so both are eligible and exactly '
                'one must be named.'}


def build_differing_geometry(path):
    """The second section is landscape. Continuous cannot carry a different page."""
    body = [
        paragraph(run('Portrait Part')),
        paragraph(run(filler(2))),
        section_paragraph('End of the portrait part.', start_type='nextPage'),
        paragraph(run('Landscape Part')),
        paragraph(run(filler(2))),
    ]
    _build(path, body, sectpr(start_type='nextPage', geometry=LANDSCAPE_GEOMETRY))
    return {
        'path': str(path),
        'section_count': 2,
        'eligible_section_indexes': [],
        'refusal': 'differing page geometry',
        'note': 'Section 1 is landscape. A continuous section continues on the page '
                'the previous section is on, so this conversion is refused rather '
                'than silently reformatting the document.'}


def build_differing_page_numbering(path):
    """The second section restarts page numbering, which continuous would change."""
    body = [
        paragraph(run('Front Matter')),
        paragraph(run(filler(2))),
        section_paragraph('End of the front matter.', start_type='nextPage'),
        paragraph(run('Main Matter')),
        paragraph(run(filler(2))),
    ]
    _build(path, body,
           sectpr(start_type='nextPage',
                  extra='<w:pgNumType w:start="1" w:fmt="decimal"/>'))
    return {
        'path': str(path),
        'section_count': 2,
        'eligible_section_indexes': [],
        'refusal': 'differing w:pgNumType',
        'note': 'Section 1 restarts page numbering at 1. Making it continuous would '
                'change where that restart lands, which is more than a start-type '
                'change, so it is refused.'}


def build_odd_page_start(path):
    """An odd-page start: a deliberate printing requirement, never auto-converted."""
    body = [
        paragraph(run('Left-hand Part')),
        paragraph(run(filler(2))),
        section_paragraph('End of the left-hand part.', start_type='nextPage'),
        paragraph(run('Recto Chapter')),
        paragraph(run(filler(2))),
    ]
    _build(path, body, sectpr(start_type='oddPage'))
    return {
        'path': str(path),
        'section_count': 2,
        'eligible_section_indexes': [],
        'refusal': 'unsupported start type oddPage',
        'note': 'Section 1 starts on the next odd page, which is a binding and '
                'printing decision. It is inspected and reported, and never changed '
                'by this operation.'}


def build_single_section(path):
    """One section. Its start type describes a relationship that does not exist."""
    body = [
        paragraph(run('Only Part')),
        paragraph(run(filler(3))),
    ]
    _build(path, body, sectpr(start_type='nextPage'))
    return {
        'path': str(path),
        'section_count': 1,
        'eligible_section_indexes': [],
        'refusal': 'first section has no previous section',
        'note': 'A section start says how a section follows the previous one. There '
                'is no previous section here, so changing it would repair nothing.'}


def build_intentional_section_break(path):
    """Eligible, and deliberate. Eligibility is never a recommendation.

    Structurally identical to `build_supported_next_page`; the difference is that the
    break is obviously wanted. Nothing in the service distinguishes these two
    documents, which is the point: only the person can say which it is, so a
    diagnosis that proposes a change here without being asked is wrong.
    """
    body = [
        paragraph(run('Report')),
        paragraph(run(filler(3))),
        section_paragraph('End of the report body.', start_type='nextPage'),
        paragraph(run('Appendix A')),
        paragraph(run(filler(3))),
    ]
    _build(path, body, sectpr(start_type='nextPage'))
    return {
        'path': str(path),
        'section_count': 2,
        'eligible_section_index': 1,
        'intentional': True,
        'note': 'The appendix deliberately starts on a new page. The section is '
                'technically eligible; the correct outcome for a complaint that does '
                'not ask for this change is no_change, not a repair.'}


BUILDERS = {
    'supported_next_page': build_supported_next_page,
    'default_start': build_default_start,
    'three_sections': build_three_sections,
    'differing_geometry': build_differing_geometry,
    'differing_page_numbering': build_differing_page_numbering,
    'odd_page_start': build_odd_page_start,
    'single_section': build_single_section,
    'intentional_section_break': build_intentional_section_break,
}


if __name__ == '__main__':
    import json
    import sys
    kind = sys.argv[1] if len(sys.argv) > 1 else 'supported_next_page'
    target = sys.argv[2] if len(sys.argv) > 2 else 'sample-%s.docx' % kind
    print(json.dumps(BUILDERS[kind](target), indent=2))
