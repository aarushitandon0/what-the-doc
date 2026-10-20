"""Builds a real, Word-openable .docx carrying labelled formatting faults.

The structural tests use minimal XML fragments. Rendering needs a genuine package:
proper content types, relationships, a styles part with a basedOn chain, and a section
with real page geometry, so LibreOffice and Word paginate it the way a report paginates.

Every fault is recorded in FAULTS with the paragraph it was injected into and the repair
it is expected to need, so a before/after run can be checked against a known answer
instead of against an impression. This is synthetic sample content, not a user document.
"""
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

CONTENT_TYPES = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>
</Types>'''

ROOT_RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''

DOCUMENT_RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>
</Relationships>'''

# compatibilityMode 15 keeps Word out of its legacy layout mode, so Word and
# LibreOffice are at least being asked to lay out the same document.
SETTINGS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:settings xmlns:w="%s"><w:defaultTabStop w:val="720"/>
<w:compat><w:compatSetting w:name="compatibilityMode" w:val="15"
 w:uri="http://schemas.microsoft.com/office/word"/></w:compat>
</w:settings>''' % W

# docDefaults supply body spacing and justification; Heading1 is based on Normal and
# contributes its own before-spacing and keepNext. Nothing here is faulty: the faults
# are injected as direct formatting on individual paragraphs below.
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
<w:style w:type="paragraph" w:styleId="Title">
  <w:name w:val="Title"/><w:basedOn w:val="Normal"/>
  <w:pPr><w:spacing w:after="360"/><w:jc w:val="center"/></w:pPr>
  <w:rPr><w:b/><w:sz w:val="48"/></w:rPr>
</w:style>
</w:styles>''' % {'w': W}

# Enough varied prose for a four-page report. The length is deliberate: the injected
# page break only costs a page, the way the real complaint does, in a document whose
# content already fills more than a page.
BODY_TEXTS = [
    'Course enrolment rose across both campuses during the reporting period, and the intake '
    'survey returned a higher completion rate than in the previous year. The figures in this '
    'review summarise the position at the close of the term, with comparisons drawn against '
    'the same point twelve months earlier. Where a measure changed definition during the year '
    'the earlier value has been restated, so that the series remains comparable across the '
    'full period rather than only within it. Two departments submitted their returns after '
    'the cut-off date; their totals are included, and the late submission is noted in the '
    'relevant table so that the reader can judge how much weight the comparison carries.',

    'Retention was measured as the proportion of students enrolled at the census date who '
    'remained enrolled at the end of the teaching period. That definition excludes students '
    'who transferred between programmes within the institution, because counting a transfer '
    'as a withdrawal would overstate attrition in exactly the programmes that absorbed those '
    'students. Where a student interrupted study and returned within the same academic year, '
    'the enrolment is treated as continuous. The resulting series is therefore slightly more '
    'conservative than the figures published in previous reviews, and the difference is '
    'quantified in the appendix rather than left for the reader to infer.',

    'Survey responses were collected online over three weeks, with two reminders sent to '
    'non-respondents. The overall response rate was sufficient for institution-level '
    'reporting but not for reliable breakdowns by individual programme, and no programme '
    'with fewer than fifteen responses is reported separately. Free-text comments were coded '
    'independently by two reviewers, and disagreements were resolved by discussion rather '
    'than by averaging the codes. The coding frame is reproduced in full so that a later '
    'review can apply the same categories and obtain a comparable result.',

    'Timetabling pressure remains the most frequently raised concern, appearing in roughly a '
    'third of all free-text comments and in every programme with more than fifty responses. '
    'Students described the difficulty of combining compulsory sessions with paid work, and '
    'several noted that published timetables changed after they had arranged their commitments. '
    'The scheduling team has since moved the publication date earlier in the cycle. Whether '
    'that change improves the position will not be visible until the next reporting period, '
    'and this review makes no claim about its effect.',

    'Library and study-space use recovered to pre-review levels, though the pattern of use '
    'shifted towards evenings and weekends. Occupancy sensors recorded the change consistently '
    'across both sites, and the finding is supported by gate counts collected independently. '
    'The practical consequence is that peak demand no longer coincides with staffed opening '
    'hours. Options for extending supported access are being costed, and no commitment has '
    'been made in advance of that work.',
]


CONCLUSION = ('Enrolment and retention both improved against the restated baseline, while the '
              'concerns raised about timetabling and supported access remain open. The next '
              'review will report on the scheduling change using the same definitions.')


def _section(heading, heading_direct=None, centred_body=None, texts=None):
    """A heading plus its run of body paragraphs."""
    rows = [('Heading1', heading, heading_direct or {})]
    for position, text in enumerate(BODY_TEXTS if texts is None else texts):
        direct = {'jc': {'val': 'center'}} if position == centred_body else {}
        rows.append(('Normal', text, direct))
    return rows


# (style, text, direct_properties) where direct_properties are raw OOXML attributes.
# The page break is injected on the *last* heading, which is the form of the complaint
# that costs a page: a short closing section stranded alone on a final sheet. Removing
# it pulls the section back into the space left on the preceding page.
PARAGRAPHS = (
    [('Title', 'Enrolment and Retention Review', {})]
    # fault 1: 48pt above a heading whose style sets 12pt
    + _section('Introduction', heading_direct={'spacing': {'before': '960'}})
    # fault 3: one centred body paragraph among justified ones
    + _section('Method', centred_body=2)
    + _section('Results')                                            # clean peer section
    # fault 2: a page break that strands the conclusion on its own page
    + _section('Conclusion', heading_direct={'pageBreakBefore': {'val': '1'}},
               texts=[CONCLUSION])
)

# Indices are derived, so adding or reordering content cannot silently point a fault
# at the wrong paragraph.
_INTRO_HEADING = 1
_METHOD_HEADING = _INTRO_HEADING + 1 + len(BODY_TEXTS)
_CENTRED_BODY = _METHOD_HEADING + 1 + 2
_CLEAN_HEADING = _METHOD_HEADING + 1 + len(BODY_TEXTS)
_CONCLUSION_HEADING = _CLEAN_HEADING + 1 + len(BODY_TEXTS)

# paragraph_id -> what was injected and the repair a correct diagnosis should reach.
FAULTS = {
    _INTRO_HEADING: {
        'fault': 'excessive_space_before',
        'injected': 'direct w:spacing/@w:before=960 (48pt) over the 12pt the Heading1 style sets',
        'expected_repair': {'field': 'space_before_pt', 'value': 12.0},
        'symptom': 'Too much space above this heading.'},
    _CONCLUSION_HEADING: {
        'fault': 'unwanted_page_break_before',
        'injected': 'direct w:pageBreakBefore=1 stranding the closing section on its own page',
        'expected_repair': {'field': 'page_break_before', 'value': False},
        'symptom': 'This heading keeps jumping to a new page and leaves a nearly empty one.'},
    _CENTRED_BODY: {
        'fault': 'inconsistent_alignment',
        'injected': 'direct w:jc=center on a body paragraph whose peers resolve to justified',
        'expected_repair': {'field': 'alignment', 'value': 'both'},
        'symptom': 'This paragraph is centred and the rest of the text is not.'},
}
# The untouched Heading1 the faulty one is compared against.
CLEAN_PEER_HEADING = _CLEAN_HEADING
# fault name -> paragraph id, so callers never hardcode an index.
FAULT_IDS = {fault['fault']: paragraph_id for paragraph_id, fault in FAULTS.items()}


def _paragraph(style, text, direct):
    ppr = ['<w:pStyle w:val="%s"/>' % style]
    # Child order inside w:pPr is schema-significant; these three keep it valid.
    for tag in ('pageBreakBefore', 'spacing', 'jc'):
        if tag in direct:
            attrs = ''.join(' w:%s="%s"' % (k, v) for k, v in direct[tag].items())
            ppr.append('<w:%s%s/>' % (tag, attrs))
    escaped = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return ('<w:p><w:pPr>%s</w:pPr><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>'
            % (''.join(ppr), escaped))


def document_xml():
    body = ''.join(_paragraph(*p) for p in PARAGRAPHS)
    section = ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
               '<w:pgMar w:top="1418" w:right="1418" w:bottom="1418" w:left="1418" '
               'w:header="709" w:footer="709" w:gutter="0"/></w:sectPr>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="%s"><w:body>%s%s</w:body></w:document>' % (W, body, section))


def build(path):
    """Write the faulty sample document and return its fault manifest."""
    path = Path(path)
    with ZipFile(path, 'w', ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', CONTENT_TYPES)
        z.writestr('_rels/.rels', ROOT_RELS)
        z.writestr('word/_rels/document.xml.rels', DOCUMENT_RELS)
        z.writestr('word/document.xml', document_xml())
        z.writestr('word/styles.xml', STYLES)
        z.writestr('word/settings.xml', SETTINGS)
    return {'path': str(path), 'faults': FAULTS}


if __name__ == '__main__':
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else 'sample-faulty.docx'
    print(json.dumps(build(target), indent=2))
