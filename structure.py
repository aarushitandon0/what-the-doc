"""Read-only structural inspection: explicit run breaks, and section boundaries.

This module answers two questions the paragraph-property layer cannot, and it answers
them from the actual XML rather than from an assumption about how Word writes files.

**Explicit breaks.** `w:br` (ECMA-376 / ISO-IEC 29500-1 §17.3.3.1, CT_Br) is a run-level
element whose `w:type` is one of `page`, `column` or `textWrapping`; the attribute is
optional and its default is `textWrapping`, so an untyped `w:br` is a line break, not a
page break. Four things that are routinely confused are kept apart here:

  * `w:pPr/w:pageBreakBefore` is a *paragraph property*. It is already editable through
    `engine.FIELDS` and is not a break element at all.
  * `w:br w:type="page"` inside a run is an *explicit page break* the user inserted.
  * `w:br w:type="column"` inside a run is an *explicit column break*.
  * `w:lastRenderedPageBreak` (§17.3.3.13) records where the last application that
    paginated the document happened to break a page. It is written by the renderer,
    not by the user, and removing it would repair nothing. It is reported here only so
    a reader can see it was considered, and it is never an editable target.

**Eligibility is derived, not assumed.** A `w:br` is only offered for removal when its
run is a *direct child of a top-level body paragraph*. A break inside a table cell, a
text box, a hyperlink, a field or any other wrapper is reported with the element that
disqualifies it, because "it is a descendant of a body paragraph" is not the same claim
as "it belongs to supported body content".

**Target identity.** Each break carries a server-issued `target_id` derived from the
source hash together with the break's location and type. A caller never supplies XML,
an XPath or an element index: it echoes an id the server minted. Because the source
hash is part of the digest, every id from an earlier revision of a document simply
fails to resolve, which is how a stale target is refused rather than silently applied
to whatever now sits in that position.

**Sections.** `w:sectPr` appears in two places with two different meanings: inside a
paragraph's `w:pPr` it defines the section that *ends* with that paragraph, and as the
last child of `w:body` it defines the document's final section. Both are reported,
with the section-start type (`ST_SectionMark`: `nextPage`, `nextColumn`, `continuous`,
`evenPage`, `oddPage`, defaulting to `nextPage` when the element is absent).
No page number is reported: a visual page number cannot be derived from this XML.

Exactly **one** section mutation exists, and this module decides whether a section
qualifies for it: a `nextPage` start may become `continuous`. ISO/IEC 29500-1 §17.6.22
is explicit that the type says how a section is placed *relative to the previous
section*, which is why the first section of a document is never eligible — it has no
previous section — and why a section that declares its own page geometry, numbering or
header/footer references is refused: continuous means "on the page the previous
section is already on", and that section cannot carry a different page setup onto it.
Every other start type, including `evenPage` and `oddPage`, is inspected and refused.
Each eligible section carries a revision-bound `target_id` minted exactly like a
break's, and each refusal names the rule it hit.
"""
import hashlib

from lxml import etree as E

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS = {'w': W}

# ST_BrType. 'textWrapping' is the schema default when w:type is absent.
BREAK_TYPES = ('page', 'column', 'textWrapping')
DEFAULT_BREAK_TYPE = 'textWrapping'
# Only these two are user-inserted breaks this version can remove.
REMOVABLE_BREAK_TYPES = ('page', 'column')

# ST_SectionMark. 'nextPage' is the schema default when w:type is absent.
SECTION_START_TYPES = ('nextPage', 'nextColumn', 'continuous', 'evenPage', 'oddPage')
DEFAULT_SECTION_START = 'nextPage'

TARGET_PREFIX = 'brk-'
SECTION_TARGET_PREFIX = 'sec-'
TARGET_DIGEST_CHARS = 16
CONTEXT_CHARS = 80

# The only section mutation this version supports, as (from, to) start types.
# ISO/IEC 29500-1  17.6.22: a continuous section "begins the new section on the
# following paragraph". Every other conversion - and every other section property -
# stays read-only.
SUPPORTED_SECTION_START_CHANGES = (('nextPage', 'continuous'),)
SUPPORTED_SECTION_START_SOURCES = tuple(sorted({a for a, _ in SUPPORTED_SECTION_START_CHANGES}))
SUPPORTED_SECTION_START_TARGETS = tuple(sorted({b for _, b in SUPPORTED_SECTION_START_CHANGES}))

SECTION_SCOPE_REASON = (
    'Sections are inspected in full and exactly one mutation is offered: changing a '
    'supported next-page section start to continuous. No other start type, no column '
    'layout, page geometry, margin, orientation, numbering, header or footer, and no '
    'section boundary can be changed, created, merged or deleted through this service.')
# Kept under its original name because API consumers and recorded reports read it.
SECTION_READ_ONLY_REASON = SECTION_SCOPE_REASON

# Page-level properties a continuous section cannot carry independently, because the
# section shares the page the previous section is already on. Compared attribute by
# attribute against the previous section rather than assumed.
PAGE_GEOMETRY_ELEMENTS = ('pgSz', 'pgMar')
# Properties that would change meaning or ownership if the section stopped starting a
# page. Differing values make the request ambiguous, so it is refused.
PAGE_IDENTITY_ELEMENTS = ('pgNumType', 'titlePg', 'headerReference', 'footerReference',
                          'pgBorders', 'docGrid')


def q(name):
    return '{' + W + '}' + name


def _local(element):
    return E.QName(element).localname


def _break_type(element):
    """The resolved w:type of a w:br, applying the documented textWrapping default."""
    raw = element.get(q('type'))
    if raw is None:
        return DEFAULT_BREAK_TYPE, None
    return (raw if raw in BREAK_TYPES else raw), raw


def target_id(source_sha256, paragraph_id, run_index, break_index, break_type):
    """A server-issued id for one break, bound to the document revision and the type.

    The hash covers the source revision, so an id minted against an earlier revision
    cannot resolve against a later one, and it covers the expected break type, so an
    id cannot be reused to remove a different kind of break in the same position.
    """
    payload = '|'.join(('wtd-break-v1', str(source_sha256), str(paragraph_id),
                        str(run_index), str(break_index), str(break_type)))
    digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()
    return TARGET_PREFIX + digest[:TARGET_DIGEST_CHARS]


def _disqualifying_ancestor(element, paragraph):
    """The element that puts `element` outside supported body content, or None.

    Walks from the break up to the paragraph. Supported shape is exactly
    w:p > w:r > w:br: anything else between them is named and reported.
    """
    parent = element.getparent()
    if parent is None or _local(parent) != 'r':
        return _local(parent) if parent is not None else 'detached'
    run_parent = parent.getparent()
    if run_parent is not paragraph:
        return _local(run_parent) if run_parent is not None else 'detached'
    return None


def _run_index_map(paragraph):
    """Every descendant run of the paragraph, indexed. Unsupported runs are indexed too.

    Indexing all descendants rather than only direct children keeps an id stable for a
    break this version refuses, so the refusal names the same target the inspection did.
    """
    return {run: index for index, run in enumerate(paragraph.xpath('.//w:r', namespaces=NS))}


def _paragraph_text(paragraph):
    return ''.join(paragraph.xpath('.//w:t/text()', namespaces=NS))


def _text_around(paragraph, element):
    """Text before and after one element inside the paragraph, for identification.

    Nearby text is what lets a person tell two breaks in the same paragraph apart, so
    a bounded window either side is reported. Nothing here is sent to telemetry.
    """
    before, after, seen = [], [], False
    for node in paragraph.iter():
        if node is element:
            seen = True
            continue
        if node.tag == q('t') and node.text:
            (after if seen else before).append(node.text)
    return (''.join(before)[-CONTEXT_CHARS:], ''.join(after)[:CONTEXT_CHARS])


def _style_of(paragraph):
    ppr = paragraph.find(q('pPr'))
    element = None if ppr is None else ppr.find(q('pStyle'))
    return None if element is None else element.get(q('val'))


def _break_record(source_sha256, paragraph, paragraph_id, element, run_map, ordinals):
    run = element.getparent()
    run_index = run_map.get(run)
    siblings = [] if run is None else run.findall(q('br'))
    break_index = siblings.index(element) if element in siblings else 0
    resolved, raw = _break_type(element)
    disqualified = _disqualifying_ancestor(element, paragraph)

    if resolved not in REMOVABLE_BREAK_TYPES:
        eligible, reason = False, (
            'This is a %s break (w:br w:type=%r%s). Only explicit page and column '
            'breaks can be removed.' % (resolved, resolved,
                                        ', written without w:type, which the schema '
                                        'defaults to textWrapping' if raw is None else ''))
    elif disqualified is not None:
        eligible, reason = False, (
            'The break sits inside <w:%s> rather than directly in a top-level body '
            'paragraph, so this version will not edit it.' % disqualified)
    elif run_index is None:                                 # pragma: no cover - defensive
        eligible, reason = False, 'The containing run could not be located.'
    else:
        eligible, reason = True, None

    # One sequence per paragraph per break type, counting *every* explicit break of
    # that type whether or not it can be edited. Numbering the editable ones separately
    # would let two rows in one paragraph both read "page break 1", which is exactly
    # the ambiguity a person has to be able to resolve.
    ordinals[resolved] = ordinals.get(resolved, 0) + 1
    before, after = _text_around(paragraph, element)
    record = {
        'kind': 'explicit_break',
        'break_type': resolved,
        'break_type_raw': raw,
        'paragraph_id': paragraph_id,
        'style_id': _style_of(paragraph),
        'run_index': run_index,
        'break_index_in_run': break_index,
        'ordinal_in_paragraph': ordinals[resolved],
        'edit_eligible': eligible,
        'ineligible_reason': reason,
        'text_before': before,
        'text_after': after,
        'run_has_properties': run is not None and run.find(q('rPr')) is not None,
        'runs_in_paragraph': len(run_map),
    }
    record['target_id'] = (target_id(source_sha256, paragraph_id, run_index,
                                     break_index, resolved)
                           if run_index is not None else None)
    return record


def _last_rendered_record(paragraph, paragraph_id, element, run_map):
    """Reported for completeness and never editable. See the module docstring."""
    run = element.getparent()
    before, after = _text_around(paragraph, element)
    return {
        'kind': 'last_rendered_page_break',
        'break_type': None,
        'paragraph_id': paragraph_id,
        'style_id': _style_of(paragraph),
        'run_index': run_map.get(run),
        'target_id': None,
        'edit_eligible': False,
        'ineligible_reason': (
            'w:lastRenderedPageBreak records where the application that last paginated '
            'this document placed a page break. It is not a user-inserted break, so '
            'removing it would repair nothing.'),
        'text_before': before,
        'text_after': after,
    }


def locate_breaks(root, source_sha256, paragraphs):
    """Every w:br and w:lastRenderedPageBreak under the supplied body paragraphs.

    Returns (records, elements) where `elements[target_id]` is the live lxml element
    for each record that carries one. The element map is for `engine.apply`; callers
    outside the engine use the records.
    """
    records, elements = [], {}
    for paragraph_id, paragraph in enumerate(paragraphs):
        run_map = _run_index_map(paragraph)
        ordinals = {}
        for element in paragraph.iter():
            if element.tag == q('br'):
                record = _break_record(source_sha256, paragraph, paragraph_id,
                                       element, run_map, ordinals)
                if record['target_id'] is not None:
                    # A collision would make two different breaks share one id. The
                    # digest covers the full location, so this cannot happen for
                    # distinct breaks; the guard exists so a future change to the
                    # location fields fails loudly instead of silently aliasing.
                    if record['target_id'] in elements:    # pragma: no cover - defensive
                        raise ValueError('Ambiguous break target identity')
                    elements[record['target_id']] = element
                records.append(record)
            elif element.tag == q('lastRenderedPageBreak'):
                records.append(_last_rendered_record(paragraph, paragraph_id,
                                                     element, run_map))
    return records, elements


def _columns(sectpr):
    cols = sectpr.find(q('cols'))
    if cols is None:
        return {'declared': False, 'count': 1, 'equal_width': None,
                'note': 'No w:cols element; the section is single-column unless a '
                        'later section it inherits from supplies one.'}
    raw = cols.get(q('num'))
    try:
        count = int(raw) if raw is not None else 1
    except ValueError:
        count = None
    equal = cols.get(q('equalWidth'))
    return {'declared': True, 'count': count, 'raw_num': raw,
            'equal_width': None if equal is None else equal not in ('0', 'false'),
            'column_definitions': len(cols.findall(q('col'))),
            'note': 'w:cols/@w:num absent defaults to one column.' if raw is None else None}


def section_target_id(source_sha256, section_index, defined_in,
                      defining_paragraph_id, start_type):
    """A server-issued id for one section, bound to the revision and the start type.

    Built exactly like a break target id and for the same reason: the source hash is
    inside the digest, so an id minted against an earlier revision cannot resolve, and
    the start type is inside it too, so an id cannot be replayed against a section
    whose start type has since changed.
    """
    payload = '|'.join(('wtd-section-v1', str(source_sha256), str(section_index),
                        str(defined_in), str(defining_paragraph_id), str(start_type)))
    digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()
    return SECTION_TARGET_PREFIX + digest[:TARGET_DIGEST_CHARS]


def _element_fingerprint(sectpr, name):
    """Every child `w:<name>` of a sectPr as (tag, sorted attributes) tuples.

    Comparing fingerprints rather than serialised XML keeps the comparison insensitive
    to namespace-prefix spelling while still noticing any attribute difference.
    """
    return tuple(sorted(
        (_local(child), tuple(sorted(child.attrib.items())))
        for child in sectpr.findall(q(name))))


def _geometry_differences(sectpr, previous):
    """Names of page-level properties this section declares differently to the previous.

    ISO/IEC 29500-1 17.6.22 says a continuous section begins on the following
    paragraph, which is to say it shares the previous section page. A section that
    declares its own page size, margins, numbering, borders or header/footer
    references cannot do that without changing something the request did not ask to
    change, so the difference is reported and the conversion refused.
    """
    differing = []
    for name in PAGE_GEOMETRY_ELEMENTS + PAGE_IDENTITY_ELEMENTS:
        if _element_fingerprint(sectpr, name) != _element_fingerprint(previous, name):
            differing.append('w:' + name)
    return differing


def _section_eligibility(index, sectpr, previous_sectpr, start_type, recognised):
    """Whether this section may have its start type changed, and to what.

    Eligibility is derived from the document, never assumed. Five separate things can
    disqualify a section and each is reported with its own reason, because a refusal
    that does not say which rule it hit is not reviewable.
    """
    if not recognised:
        return False, [], ('Start type %r is not a value this version recognises, so '
                           'no change is offered.' % (start_type,)), None
    if sectpr.find(q('sectPrChange')) is not None:
        return False, [], ('This section carries a tracked revision '
                           '(w:sectPrChange). Section properties under revision '
                           'tracking are not edited by this service.'), None
    if index == 0:
        # 17.6.22: the type says how this section is placed *relative to the previous
        # section*. The first section has no previous section, so changing its type
        # would be a change with no defined effect.
        return False, [], ('This is the first section of the document. A section '
                           'start describes how a section follows the previous one, '
                           'and there is no previous section here, so changing it '
                           'would repair nothing.'), None
    allowed = [b for a, b in SUPPORTED_SECTION_START_CHANGES if a == start_type]
    if not allowed:
        reason = (
            'Start type %r is inspected but not convertible. Only %s can be changed, '
            'and only to %s. An even-page or odd-page start in particular is a '
            'deliberate printing requirement and is never changed without an '
            'explicit, separately supported intent.'
            % (start_type, ' or '.join(SUPPORTED_SECTION_START_SOURCES),
               ' or '.join(SUPPORTED_SECTION_START_TARGETS)))
        return False, [], reason, None
    if previous_sectpr is None:                        # pragma: no cover - defensive
        return False, [], 'The previous section could not be located.', None
    differing = _geometry_differences(sectpr, previous_sectpr)
    if differing:
        reason = (
            'A continuous section continues on the page the previous section is '
            'already on, but this section declares different %s. Converting it would '
            'change page setup, headers/footers or numbering as well as the start '
            'type, which is more than this operation does, so it is refused rather '
            'than guessed at.' % ', '.join(differing))
        return False, [], reason, differing
    return True, allowed, None, []


def _section_record(index, sectpr, defined_in, defining_paragraph_id,
                    first_paragraph_id, last_paragraph_id, total_sections,
                    previous_sectpr, source_sha256):
    type_element = sectpr.find(q('type'))
    raw = None if type_element is None else type_element.get(q('val'))
    start_type = DEFAULT_SECTION_START if raw is None else raw
    recognised = start_type in SECTION_START_TYPES
    page_size = sectpr.find(q('pgSz'))
    eligible, allowed, reason, differing = _section_eligibility(
        index, sectpr, previous_sectpr, start_type, recognised)
    return {
        'section_index': index,
        'defined_in': defined_in,
        'defining_paragraph_id': defining_paragraph_id,
        'governs': ('the section that ends with paragraph %d' % defining_paragraph_id
                    if defined_in == 'paragraph_properties' else
                    'the final section of the document'),
        'start_type': start_type,
        'start_type_raw': raw,
        'start_type_is_default': raw is None,
        'start_type_recognised': recognised,
        'is_continuous': start_type == 'continuous',
        'starts_new_column': start_type == 'nextColumn',
        'columns': _columns(sectpr),
        'has_page_size': page_size is not None,
        'page_size_raw': None if page_size is None else
                         {'w': page_size.get(q('w')), 'h': page_size.get(q('h')),
                          'orient': page_size.get(q('orient'))},
        'has_page_margins': sectpr.find(q('pgMar')) is not None,
        'first_paragraph_id': first_paragraph_id,
        'last_paragraph_id': last_paragraph_id,
        'is_final_section': index == total_sections - 1,
        'target_id': section_target_id(source_sha256, index, defined_in,
                                       defining_paragraph_id, start_type),
        'editable': eligible,
        'edit_eligible': eligible,
        'supported_start_changes': allowed,
        'ineligible_reason': reason,
        'differing_page_properties': differing,
        'read_only_reason': None if eligible else reason,
        'scope_note': SECTION_SCOPE_REASON,
    }


def locate_sections(root, paragraphs, source_sha256=None):
    """Section records in document order, following the documented sectPr semantics.

    A w:sectPr inside w:pPr defines the section ending with that paragraph; the
    w:sectPr that is the last child of w:body defines the final section. Both are
    handled, including a document whose body carries no final w:sectPr at all. The
    sectPr element itself is returned alongside each record so a caller that is
    allowed to change one does not have to find it again by guesswork.
    """
    body = root.find(q('body'))
    entries = []
    for paragraph_id, paragraph in enumerate(paragraphs):
        ppr = paragraph.find(q('pPr'))
        sectpr = None if ppr is None else ppr.find(q('sectPr'))
        if sectpr is not None:
            entries.append(('paragraph_properties', paragraph_id, sectpr))
    final = None
    if body is not None:
        children = list(body)
        if children and children[-1].tag == q('sectPr'):
            final = children[-1]
    if final is not None:
        entries.append(('body', None, final))

    records, elements, first = [], {}, 0
    for index, (defined_in, paragraph_id, sectpr) in enumerate(entries):
        last = paragraph_id if paragraph_id is not None else (
            len(paragraphs) - 1 if paragraphs else None)
        # A final body sectPr after a paragraph-level sectPr on the very last paragraph
        # governs a section with no paragraphs in it. That is reported as an empty
        # range rather than as a backwards one.
        start = first if paragraphs and last is not None and first <= last else None
        previous = entries[index - 1][2] if index else None
        record = _section_record(index, sectpr, defined_in, paragraph_id,
                                 start, last if start is not None else None,
                                 len(entries), previous, source_sha256)
        if record['target_id'] in elements:            # pragma: no cover - defensive
            raise ValueError('Ambiguous section target identity')
        elements[record['target_id']] = sectpr
        records.append(record)
        first = (paragraph_id + 1) if paragraph_id is not None else first
    return records, elements


def section_report(root, paragraphs, source_sha256):
    sections, _ = locate_sections(root, paragraphs, source_sha256)
    has_final = bool(sections and sections[-1]['defined_in'] == 'body')
    editable = [s['target_id'] for s in sections if s['edit_eligible']]
    return {
        'source_sha256': source_sha256,
        'section_count': len(sections),
        'has_final_body_section_properties': has_final,
        'sections': sections,
        'editable': bool(editable),
        'editable_target_ids': editable,
        'supported_start_changes': [{'from': a, 'to': b}
                                    for a, b in SUPPORTED_SECTION_START_CHANGES],
        'read_only_reason': SECTION_SCOPE_REASON,
        'scope_note': SECTION_SCOPE_REASON,
        'warning': (
            'Section information is reported for inspection, and exactly one change '
            'is offered: a supported next-page start may be changed to continuous. '
            'start_type is the ST_SectionMark value, with nextPage applied as the '
            'schema default when w:type is absent (start_type_is_default marks that '
            'case). A paragraph-level w:sectPr governs the section ending with its '
            'paragraph; a body-level w:sectPr governs the final section. Paragraph '
            'ranges are body-paragraph ids, not page numbers: no visual page number '
            'can be derived from this XML. Continuous does not guarantee that a '
            'visible page break disappears - page geometry, column settings and '
            'keep-with-next rules can still force pagination.'),
    }


def break_report(root, paragraphs, source_sha256):
    records, _ = locate_breaks(root, source_sha256, paragraphs)
    explicit = [r for r in records if r['kind'] == 'explicit_break']
    removable = [r for r in explicit if r['edit_eligible']]
    return {
        'source_sha256': source_sha256,
        'break_count': len(explicit),
        'removable_count': len(removable),
        'last_rendered_page_break_count': len(records) - len(explicit),
        'removable_break_types': list(REMOVABLE_BREAK_TYPES),
        'breaks': records,
        'warning': (
            'A target_id is issued by this server for this source revision only: an id '
            'from another revision will not resolve. An untyped w:br is a line break '
            '(textWrapping) by schema default, not a page break. '
            'w:lastRenderedPageBreak is pagination written by the rendering '
            'application, not a user-inserted break, and is never removable. A break '
            'that is a descendant of a body paragraph but not a direct child of one '
            'carries edit_eligible false and the element that disqualifies it. '
            'A deliberate break is not a fault: identification is not a recommendation '
            'to remove.'),
    }
