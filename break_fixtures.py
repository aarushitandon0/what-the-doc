"""Real, Word-openable documents carrying *explicit* run breaks.

Deliberately separate from `fixtures.py`, whose sample document plants a
`w:pageBreakBefore` **paragraph property**. That is a different thing from a
`w:br w:type="page"` **run element**, with a different repair and a different failure
mode, so a paragraph-property fixture is not evidence about explicit breaks and is
never substituted for one here.

Each document is a genuine package with enough prose to paginate, so a removal can be
demonstrated by rendering it rather than only by reading its XML. The packaging parts
(content types, relationships, settings) are reused from `fixtures.py` so there is one
definition of what a valid package looks like.
"""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from fixtures import CONTENT_TYPES, DOCUMENT_RELS, ROOT_RELS, SETTINGS, W

STYLES = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="%(w)s">
<w:docDefaults>
  <w:rPrDefault><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/><w:sz w:val="24"/></w:rPr></w:rPrDefault>
  <w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/><w:jc w:val="both"/></w:pPr></w:pPrDefault>
</w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading1">
  <w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>
  <w:pPr><w:keepNext/><w:spacing w:before="240" w:after="120"/><w:jc w:val="left"/><w:outlineLvl w:val="0"/></w:pPr>
  <w:rPr><w:b/><w:sz w:val="32"/></w:rPr>
</w:style>
</w:styles>''' % {'w': W}

PAGE_SECTION = ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                '<w:pgMar w:top="1418" w:right="1418" w:bottom="1418" w:left="1418" '
                'w:header="709" w:footer="709" w:gutter="0"/></w:sectPr>')

# Two equal columns with a real gutter. A column break only means anything inside a
# section that genuinely has more than one column, which is why this exists at all.
TWO_COLUMN_SECTION = ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                      '<w:pgMar w:top="1418" w:right="1418" w:bottom="1418" w:left="1418" '
                      'w:header="709" w:footer="709" w:gutter="0"/>'
                      '<w:cols w:num="2" w:space="425" w:equalWidth="1"/></w:sectPr>')

PAGE_BREAK = '<w:br w:type="page"/>'
COLUMN_BREAK = '<w:br w:type="column"/>'
LINE_BREAK = '<w:br/>'                    # untyped: textWrapping by schema default
LAST_RENDERED = '<w:lastRenderedPageBreak/>'
BOLD = '<w:rPr><w:b/></w:rPr>'

_FILLER = (
    'The committee reviewed each submission against the published criteria and recorded '
    'the outcome in the register, together with the date on which the decision took '
    'effect and the officer who confirmed it. Where a submission was returned for '
    'amendment the original date is retained, so that the elapsed time reflects the '
    'whole process rather than only its final stage. ')


def filler(times=3):
    return _FILLER * times


def escape(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def run(text='', *, before='', properties=''):
    """One run, optionally carrying break markup ahead of its text.

    `before` places an explicit break *inside* a run rather than between runs, which
    is the shape Word writes and therefore the shape a removal has to survive.
    """
    body = '<w:t xml:space="preserve">%s</w:t>' % escape(text) if text else ''
    return '<w:r>%s%s%s</w:r>' % (properties, before, body)


def paragraph(*runs, style=None):
    ppr = '<w:pPr><w:pStyle w:val="%s"/></w:pPr>' % style if style else ''
    return '<w:p>%s%s</w:p>' % (ppr, ''.join(runs))


MANUAL_PAGE_BREAK_BODY = [
    paragraph(run('Quarterly Register'), style='Heading1'),
    # A plain body paragraph with no break of any kind.
    paragraph(run(filler(3))),
    # The complaint: TWO explicit page breaks in ONE paragraph, with text either side
    # of each, so a target has to be chosen rather than guessed.
    paragraph(run('First the register was closed for the quarter. '),
              run('Then the balances were confirmed. ', before=PAGE_BREAK),
              run('Finally the register was reopened for the next quarter. ',
                  before=PAGE_BREAK)),
    # A run carrying a page break *and* bold run properties, so a removal that
    # disturbed w:rPr would show up.
    paragraph(run('A deliberate break before the appendix.', before=PAGE_BREAK,
                  properties=BOLD)),
    # An untyped w:br (a line break) and a lastRenderedPageBreak. Neither is a
    # removable explicit break, and both are reported with the reason why.
    paragraph(run('A line break follows this sentence.'),
              run('And pagination was recorded here.',
                  before=LINE_BREAK + LAST_RENDERED)),
    # A page break inside a smart tag: a descendant of a body paragraph, but not
    # supported content. Eligibility has to come from the real structure. (A
    # hyperlink was used here until hyperlinks became a supported wrapper.)
    '<w:p><w:smartTag w:uri="urn:x" w:element="place">%s</w:smartTag></w:p>'
    % run('An unsupported context.', before=PAGE_BREAK),
    # A page break inside a table cell: also a descendant, and not a body paragraph
    # at all, so it never appears in the body-paragraph list.
    '<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr><w:tr><w:tc>'
    '<w:tcPr><w:tcW w:w="0" w:type="auto"/></w:tcPr>%s</w:tc></w:tr></w:tbl>'
    % paragraph(run('In a table cell.', before=PAGE_BREAK)),
    # Enough prose after the breaks that removing one really reflows the pages.
    paragraph(run(filler(4))),
    paragraph(run(filler(4))),
    paragraph(run(filler(3))),
]

# Body paragraph ids, which `engine.paras` counts over /w:document/w:body/w:p only.
# The table is not a body paragraph, so the paragraph inside it has no id at all.
MANUAL_PAGE_BREAK_IDS = {
    'heading': 0,
    'clean_paragraph': 1,
    'two_breaks_paragraph': 2,
    'deliberate_break_paragraph': 3,
    'line_break_paragraph': 4,
    'unsupported_wrapper_paragraph': 5,
    'first_trailing_paragraph': 6,
}

MULTICOLUMN_BODY = [
    paragraph(run('Two Columns'), style='Heading1'),
    paragraph(run(filler(2))),
    # The complaint: an explicit column break mid-paragraph in a two-column section.
    paragraph(run('The first column ends with this sentence. '),
              run('The second column begins with this one. ', before=COLUMN_BREAK)),
    # A page break in the same section, so the two break types have to be told apart
    # by type rather than by position.
    paragraph(run('A page break, not a column break.', before=PAGE_BREAK)),
    paragraph(run(filler(4))),
    paragraph(run(filler(4))),
]

MULTICOLUMN_IDS = {'column_break_paragraph': 2, 'page_break_paragraph': 3}


def _build(path, body, section, styles=None):
    path = Path(path)
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="%s"><w:body>%s%s</w:body></w:document>'
                % (W, ''.join(body), section))
    with ZipFile(path, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', CONTENT_TYPES)
        archive.writestr('_rels/.rels', ROOT_RELS)
        archive.writestr('word/_rels/document.xml.rels', DOCUMENT_RELS)
        archive.writestr('word/document.xml', document)
        archive.writestr('word/styles.xml', styles or STYLES)
        archive.writestr('word/settings.xml', SETTINGS)
    return path


def build_manual_page_break(path):
    """A document whose body carries explicit `w:br w:type="page"` elements."""
    _build(path, MANUAL_PAGE_BREAK_BODY, PAGE_SECTION)
    return {
        'path': str(path),
        'paragraph_ids': dict(MANUAL_PAGE_BREAK_IDS),
        'removable_page_breaks': 3,
        'note': 'Two page breaks in body paragraph 2, one deliberate break in body '
                'paragraph 3, a line break and a lastRenderedPageBreak in body '
                'paragraph 4, and two unsupported contexts (a smart tag and a table '
                'cell). Only the first three are removable.'}


def build_multicolumn(path):
    """A genuinely two-column section containing one explicit column break."""
    _build(path, MULTICOLUMN_BODY, TWO_COLUMN_SECTION)
    return {
        'path': str(path),
        'paragraph_ids': dict(MULTICOLUMN_IDS),
        'columns': 2,
        'note': 'A two-column final section with one explicit column break and one '
                'explicit page break.'}


def build_two_section(path):
    """Two sections, for section *inspection* only. Nothing here can be changed.

    Section 0 is defined by a paragraph-level `w:sectPr` and therefore ends with that
    paragraph; section 1 is the final body-level `w:sectPr`, which carries no `w:type`
    and so takes the schema default `nextPage`.
    """
    body = [
        paragraph(run(filler(2))),
        '<w:p><w:pPr><w:sectPr><w:type w:val="continuous"/>'
        '<w:pgSz w:w="11906" w:h="16838"/><w:cols w:num="2" w:space="425"/>'
        '</w:sectPr></w:pPr>%s</w:p>' % run('End of the first section.'),
        paragraph(run(filler(2))),
    ]
    _build(path, body, PAGE_SECTION)
    return {
        'path': str(path),
        'section_defining_paragraph_id': 1,
        'note': 'Section 0: paragraph 1 w:pPr/w:sectPr, continuous, two columns. '
                'Section 1: final body w:sectPr, nextPage by schema default.'}


BUILDERS = {'manual_page_break': build_manual_page_break,
            'multicolumn': build_multicolumn,
            'two_section': build_two_section}


if __name__ == '__main__':
    import json
    import sys
    kind = sys.argv[1] if len(sys.argv) > 1 else 'manual_page_break'
    target = sys.argv[2] if len(sys.argv) > 2 else 'sample-%s.docx' % kind
    print(json.dumps(BUILDERS[kind](target), indent=2))
