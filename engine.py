"""Constrained DOCX paragraph-format patcher. No model required.

Only word/document.xml is changed. Other ZIP member payloads are preserved.
Inspection resolves effective paragraph properties through docDefaults and the
paragraph style chain (see styles.py); rendering comparison lives in render.py.
Structural guarantees are checked here, visual ones only by rendering.

Two kinds of operation exist, and a plan may mix them:

  * A **paragraph-property** operation `{paragraph_id, field, value}` writes one of
    `FIELDS` into `w:pPr`. This is the original operation and is unchanged.
  * An **explicit-break removal** `{operation: 'remove_explicit_break', target_id,
    expected_break_type}` deletes exactly one `w:br` element whose `w:type` is `page`
    or `column`. The caller never supplies XML, an XPath or an element index: it
    echoes a `target_id` this server minted for this source revision (see
    `structure.py`). The run that held the break, its properties, its text and every
    other break in the document are left exactly as they were.

`PLAN_SCHEMA_VERSION` records that addition. A plan may carry `schema_version`
explicitly; a plan without one is read as version 1, which is why every plan written
before this change still validates and applies unchanged.
"""
import argparse
import copy
import hashlib
import json
from collections import namedtuple
from pathlib import Path
from zipfile import ZipFile, BadZipFile
from lxml import etree as E

import structure as structure_module
import styles as styles_module

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS = {'w': W}
MAX_BYTES = 20 * 1024 * 1024
MAX_EXPANDED = 80 * 1024 * 1024

# 1: paragraph-property operations only. 2: adds remove_explicit_break.
# 3: adds change_section_start. A plan declares the lowest version that covers the
# operations it actually contains, so every plan written before this change still
# validates and applies byte-for-byte unchanged.
PLAN_SCHEMA_VERSION = 3
BREAK_PLAN_SCHEMA_VERSION = 2
LEGACY_PLAN_SCHEMA_VERSION = 1
SUPPORTED_PLAN_SCHEMA_VERSIONS = (LEGACY_PLAN_SCHEMA_VERSION,
                                  BREAK_PLAN_SCHEMA_VERSION,
                                  PLAN_SCHEMA_VERSION)
REMOVE_BREAK = 'remove_explicit_break'
CHANGE_SECTION_START = 'change_section_start'
OPERATIONS = (REMOVE_BREAK, CHANGE_SECTION_START)
PARAGRAPH_OPERATION_KEYS = {'paragraph_id', 'field', 'value'}
BREAK_OPERATION_KEYS = {'operation', 'target_id', 'expected_break_type'}
SECTION_OPERATION_KEYS = {'operation', 'target_id', 'expected_start_type',
                          'new_start_type'}
# CT_SectPr child order (ISO/IEC 29500-1 A.1). A w:type that has to be created is
# inserted at its schema position rather than appended, because Word rejects a
# sectPr whose children are out of order.
SECTPR_CHILD_ORDER = (
    'footnotePr', 'endnotePr', 'type', 'pgSz', 'pgMar', 'paperSrc', 'pgBorders',
    'lnNumType', 'pgNumType', 'cols', 'formProt', 'vAlign', 'noEndnote', 'titlePg',
    'textDirection', 'bidi', 'rtlGutter', 'docGrid', 'printerSettings', 'sectPrChange')

FIELDS = {
    'space_before_pt': ('spacing', 'before'),
    'space_after_pt': ('spacing', 'after'),
    'left_indent_pt': ('ind', 'left'),
    'alignment': ('jc', 'val'),
    'page_break_before': ('pageBreakBefore', 'val'),
    'keep_with_next': ('keepNext', 'val'),
}

def q(name):
    return '{' + W + '}' + name

Package = namedtuple('Package', 'document styles')


def _parse(raw):
    root = E.fromstring(raw, E.XMLParser(resolve_entities=False, no_network=True))
    if root.getroottree().docinfo.doctype:
        raise ValueError('DTD is unsupported')
    return root


def open_package(path):
    """Archive guards once, then the parts this prototype reads.

    styles is None when the package carries no word/styles.xml; resolution then
    reports that limitation rather than inventing inherited values.
    """
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('Input exceeds 20 MiB')
    with ZipFile(path) as z:
        infos = z.infolist()
        names = [i.filename for i in infos]
        if len(names) != len(set(names)) or len(names) > 2000:
            raise ValueError('Duplicate or excessive ZIP entries')
        if sum(i.file_size for i in infos) > MAX_EXPANDED:
            raise ValueError('Expanded archive exceeds limit')
        if any(i.flag_bits & 1 for i in infos):
            raise ValueError('Encrypted archives are unsupported')
        if any('vbaproject' in n.lower() or n.startswith('_xmlsignatures/') for n in names):
            raise ValueError('Macros and signed packages are unsupported')
        if any(n not in names for n in ('[Content_Types].xml', 'word/document.xml')):
            raise ValueError('Not a supported DOCX package')
        raw = z.read('word/document.xml')
        style_raw = z.read('word/styles.xml') if 'word/styles.xml' in names else None
    root = _parse(raw)
    if root.tag != q('document'):
        raise ValueError('Unsupported document namespace')
    if root.xpath('//w:ins | //w:del | //w:pPrChange | //w:sdt', namespaces=NS):
        raise ValueError('Tracked changes and content controls require a later version')
    style_root = None
    if style_raw is not None:
        style_root = _parse(style_raw)
        if style_root.tag != q('styles'):
            # A styles part we cannot read must not be silently treated as absent.
            raise ValueError('word/styles.xml is not a WordprocessingML styles part')
    return Package(document=root, styles=style_root)


def load(path):
    return open_package(path).document

def paras(root):
    # Deliberately exclude table cells, textboxes, headers and footers.
    return root.xpath('/w:document/w:body/w:p', namespaces=NS)

def signature(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def style_of(paragraph):
    """The paragraph's own w:pStyle id, or None when it relies on the default style."""
    ppr = paragraph.find(q('pPr'))
    pstyle = None if ppr is None else ppr.find(q('pStyle'))
    return None if pstyle is None else pstyle.get(q('val'))


def inspect(path):
    package = open_package(path)
    root, style_root = package.document, package.styles
    records = []
    for i, p in enumerate(paras(root)):
        ppr = p.find(q('pPr'))
        attrs = {}
        for field, (tag, attr) in FIELDS.items():
            el = None if ppr is None else ppr.find(q(tag))
            attrs[field] = None if el is None else el.get(q(attr))
        resolved = styles_module.resolve(p, style_root)
        records.append({'paragraph_id': i,
                        'text': ''.join(p.xpath('.//w:t/text()', namespaces=NS)),
                        'style_id': style_of(p),
                        'style_chain': resolved['style_chain'],
                        'direct_properties_raw': attrs,
                        'effective_properties': resolved['properties'],
                        'resolution_caveats': resolved['caveats']})
    return {'source_sha256': signature(path),
            'has_styles_part': style_root is not None,
            'default_paragraph_style': styles_module.default_paragraph_style(style_root),
            'paragraphs': records,
            'warning': 'direct_properties_raw holds twips as written. effective_properties '
                       'resolve docDefaults, the w:basedOn style chain and direct formatting, '
                       'each with the layer that supplied it; a null value with origin '
                       '"application_default" means no layer set it, not zero. Numbering '
                       'indentation, table styles and page mapping are still unresolved.'}


def inspect_breaks(path):
    """Every explicit run break in top-level body paragraphs, with edit eligibility.

    Read-only. Identification is not a recommendation: a page break a writer inserted
    on purpose is reported exactly like an unwanted one, because only the user can say
    which it is.
    """
    package = open_package(path)
    root = package.document
    return structure_module.break_report(root, paras(root), signature(path))


def inspect_sections(path):
    """Section boundaries and start types. Read-only in this version; see structure.py."""
    package = open_package(path)
    root = package.document
    return structure_module.section_report(root, paras(root), signature(path))


def is_structural_operation(op):
    """Any operation that names itself, as opposed to a {paragraph_id, field, value}."""
    return isinstance(op, dict) and 'operation' in op


def is_break_operation(op):
    return is_structural_operation(op) and op.get('operation') == REMOVE_BREAK


def is_section_operation(op):
    return is_structural_operation(op) and op.get('operation') == CHANGE_SECTION_START


def plan_schema_version(plan):
    """The schema version a plan declares, defaulting to 1 for a pre-existing plan."""
    declared = plan.get('schema_version')
    if declared is None:
        return LEGACY_PLAN_SCHEMA_VERSION
    return declared


def break_operations(plan):
    return [op for op in (plan.get('operations') or []) if is_break_operation(op)]


def section_operations(plan):
    return [op for op in (plan.get('operations') or []) if is_section_operation(op)]


def structural_operations(plan):
    return [op for op in (plan.get('operations') or []) if is_structural_operation(op)]


def paragraph_operations(plan):
    return [op for op in (plan.get('operations') or [])
            if not is_structural_operation(op)]


def required_schema_version(plan):
    """The lowest schema version that covers the operations a plan carries."""
    if section_operations(plan):
        return PLAN_SCHEMA_VERSION
    if break_operations(plan):
        return BREAK_PLAN_SCHEMA_VERSION
    return LEGACY_PLAN_SCHEMA_VERSION


def operation_kind(plan):
    """'paragraph_property', 'explicit_break_removal', 'section_start_change',
    'mixed' or 'none'.

    Used for reporting and telemetry. It is a label for a plan, never a permission.
    """
    breaks = break_operations(plan)
    sections = section_operations(plan)
    properties = paragraph_operations(plan)
    present = [bool(breaks), bool(sections), bool(properties)]
    if sum(present) > 1:
        return 'mixed'
    if breaks:
        return 'explicit_break_removal'
    if sections:
        return 'section_start_change'
    if properties:
        return 'paragraph_property'
    return 'none'


def resolve_section_targets(plan, root):
    """Map each section operation's target_id onto a live sectPr and its record.

    Identical in spirit to resolve_break_targets: ids are recomputed from this
    document and the hash the plan carries, so a plan from another revision resolves
    to nothing and is refused as stale rather than applied to whichever section now
    occupies that index.
    """
    records, elements = structure_module.locate_sections(
        root, paras(root), plan.get('source_sha256'))
    by_id = {r['target_id']: r for r in records if r.get('target_id')}
    return by_id, elements


def resolve_break_targets(plan, root):
    """Map each break operation's target_id onto a live element and its record.

    Target ids are recomputed from this document and the hash the plan carries, so a
    plan from another revision resolves to nothing and is refused as stale rather than
    applied to whatever element now occupies that position.
    """
    records, elements = structure_module.locate_breaks(
        root, plan.get('source_sha256'), paras(root))
    by_id = {r['target_id']: r for r in records if r.get('target_id')}
    return by_id, elements


def targeted_paragraph_ids(plan, root):
    """Body paragraph ids a plan may change, across both operation kinds."""
    ids = {op['paragraph_id'] for op in paragraph_operations(plan)}
    by_id, _ = resolve_break_targets(plan, root)
    for op in break_operations(plan):
        record = by_id.get(op.get('target_id'))
        if record is not None:
            ids.add(record['paragraph_id'])
    sections, _ = resolve_section_targets(plan, root)
    for op in section_operations(plan):
        record = sections.get(op.get('target_id'))
        # A body-level sectPr is not inside any paragraph, so it contributes no
        # paragraph id at all. That is reported honestly rather than papered over
        # with the last paragraph of the section, which is not what changes.
        if record is not None and record['defining_paragraph_id'] is not None:
            ids.add(record['defining_paragraph_id'])
    return sorted(ids)


def peers(path, paragraph_id):
    """Comparable paragraphs: same style elsewhere in the body, for "is this one odd?".

    Evidence for a diagnosis, not a rule. A deliberately different paragraph is allowed
    to differ, so this reports the distribution instead of naming a correct value.
    """
    report = inspect(path)
    records = report['paragraphs']
    if type(paragraph_id) is not int or not 0 <= paragraph_id < len(records):
        raise ValueError('Invalid selected paragraph')
    selected = records[paragraph_id]
    same_style = [r for r in records
                  if r['paragraph_id'] != paragraph_id and r['style_id'] == selected['style_id']]
    distribution = {}
    for field in styles_module.RESOLVED_FIELDS:
        counts = {}
        for r in same_style:
            key = json.dumps(r['effective_properties'][field]['value'])
            counts[key] = counts.get(key, 0) + 1
        distribution[field] = {
            'selected': selected['effective_properties'][field]['value'],
            # Most common peer value first; keys are JSON so None and 0 stay distinct.
            'peer_values': [{'value': json.loads(k), 'count': v}
                            for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]}
    return {'source_sha256': report['source_sha256'], 'paragraph_id': paragraph_id,
            'style_id': selected['style_id'], 'peer_count': len(same_style),
            'comparison': distribution,
            'warning': 'Differing from peers is evidence of a fault, not proof. '
                       'With no peers there is no comparison, only the resolved values.'}

def _validate_break_operations(plan, root):
    """Check every break removal against the actual structure of this document.

    Four separate things are checked, because each of them is a different way for a
    removal to be wrong: the operation's own shape, that the id resolves at all (a
    stale or invented target does not), that the element really is the break type the
    caller expected, and that the break sits in a context this version supports.
    """
    ops = break_operations(plan)
    if not ops:
        return
    by_id, _ = resolve_break_targets(plan, root)
    seen = set()
    for op in ops:
        if set(op) != BREAK_OPERATION_KEYS:
            raise ValueError('A break operation needs exactly operation, target_id '
                             'and expected_break_type')
        if op['operation'] not in OPERATIONS:
            raise ValueError('Unsupported operation %r' % (op['operation'],))
        tid, expected = op['target_id'], op['expected_break_type']
        if not isinstance(tid, str) or not tid:
            raise ValueError('target_id must be a non-empty string')
        if tid in seen:
            raise ValueError('Conflicting duplicate operation')
        seen.add(tid)
        if expected not in structure_module.REMOVABLE_BREAK_TYPES:
            raise ValueError('expected_break_type must be one of %s'
                             % ', '.join(structure_module.REMOVABLE_BREAK_TYPES))
        record = by_id.get(tid)
        if record is None:
            # Either the id was never issued for this document, or it was issued
            # against a different revision. Both are refused the same way, and the
            # caller is told to re-inspect rather than guess.
            raise ValueError('Unknown or stale break target %r: re-inspect the '
                             'document and use a current target_id' % (tid,))
        if record['break_type'] != expected:
            raise ValueError('Break %r is a %s break, not the %s break the operation '
                             'expected' % (tid, record['break_type'], expected))
        if not record['edit_eligible']:
            raise ValueError('Break %r cannot be removed: %s'
                             % (tid, record['ineligible_reason']))


def _validate_section_operations(plan, root):
    """Check every section-start change against the actual structure of this document.

    The same four questions a break removal is asked, plus one more that only applies
    here: a section change is never combined with another operation in one plan. Each
    is a separate judgement about a separate part of the document, and keeping a
    section plan to exactly one operation is what lets `apply` verify afterwards that
    the *entire* document is byte-identical apart from the one attribute.
    """
    ops = section_operations(plan)
    if not ops:
        return
    if len(ops) > 1:
        raise ValueError('At most one section-start change may be applied at a time; '
                         'each section is approved separately')
    if len(plan.get('operations') or []) != 1:
        raise ValueError('A section-start change is applied on its own: it cannot be '
                         'combined with another operation in the same plan')
    by_id, _ = resolve_section_targets(plan, root)
    op = ops[0]
    if set(op) != SECTION_OPERATION_KEYS:
        raise ValueError('A section operation needs exactly operation, target_id, '
                         'expected_start_type and new_start_type')
    if op['operation'] != CHANGE_SECTION_START:        # pragma: no cover - defensive
        raise ValueError('Unsupported operation %r' % (op['operation'],))
    tid = op['target_id']
    if not isinstance(tid, str) or not tid:
        raise ValueError('target_id must be a non-empty string')
    expected, new = op['expected_start_type'], op['new_start_type']
    if (expected, new) not in structure_module.SUPPORTED_SECTION_START_CHANGES:
        raise ValueError(
            'Unsupported section-start change %r -> %r. This version changes only %s.'
            % (expected, new, ', '.join('%s -> %s' % pair for pair in
                                        structure_module.SUPPORTED_SECTION_START_CHANGES)))
    record = by_id.get(tid)
    if record is None:
        # Either the id was never issued for this document, or it was issued against
        # a different revision or a different start type. All are refused the same
        # way, and the caller is told to re-inspect rather than guess.
        raise ValueError('Unknown or stale section target %r: re-inspect the document '
                         'and use a current target_id' % (tid,))
    if record['start_type'] != expected:               # pragma: no cover - defensive
        # The start type is inside the id digest, so a mismatch cannot normally reach
        # here. Checked anyway: the expectation is the caller's, not the server's.
        raise ValueError('Section %r starts with %r, not the %r the operation expected'
                         % (tid, record['start_type'], expected))
    if not record['edit_eligible']:
        raise ValueError('Section %r cannot be changed: %s'
                         % (tid, record['ineligible_reason']))


def validate(plan, root, style_root=None):
    if set(plan) not in ({'source_sha256', 'operations'},
                         {'source_sha256', 'operations', 'schema_version'}):
        raise ValueError('Expected source_sha256 and operations, with an optional '
                         'schema_version')
    declared = plan_schema_version(plan)
    if declared not in SUPPORTED_PLAN_SCHEMA_VERSIONS:
        raise ValueError('Unsupported plan schema_version %r' % (declared,))
    ops = plan['operations']
    if not isinstance(ops, list) or len(ops) > 12:
        raise ValueError('Expected at most 12 operations')
    for op in ops:
        if is_structural_operation(op) and op.get('operation') not in OPERATIONS:
            raise ValueError('Unsupported operation %r' % (op.get('operation'),))
    required = required_schema_version(plan)
    if declared < required:
        raise ValueError('%s operations require schema_version %d'
                         % ('Section-start' if section_operations(plan)
                            else 'Explicit-break', required))
    _validate_break_operations(plan, root)
    _validate_section_operations(plan, root)
    seen = set()
    for op in paragraph_operations(plan):
        if not isinstance(op, dict) or set(op) != PARAGRAPH_OPERATION_KEYS:
            raise ValueError('Invalid operation structure')
        idx, field, value = op['paragraph_id'], op['field'], op['value']
        if type(idx) is not int or not 0 <= idx < len(paras(root)):
            raise ValueError('Invalid paragraph id')
        if not isinstance(field, str) or field not in FIELDS:
            raise ValueError('Unsupported operation')
        if (idx, field) in seen:
            raise ValueError('Conflicting duplicate operation')
        seen.add((idx, field))
        if field.endswith('_pt'):
            if type(value) not in (int, float) or not 0 <= value <= 144:
                raise ValueError('Length must be finite and between 0 and 144 points')
        elif field == 'alignment':
            if value not in ('left', 'center', 'right', 'both'):
                raise ValueError('Unsupported alignment')
        elif type(value) is not bool:
            raise ValueError('Expected boolean')
        # Alternative measures (autospacing, line/char units) can override the requested
        # setting. Resolution looks through docDefaults and the style chain as well,
        # because an inherited afterAutospacing defeats a direct w:after just as surely.
        resolved = styles_module.resolve(paras(root)[idx], style_root)
        overridden_by = resolved['properties'].get(field, {}).get('overridden_by')
        if overridden_by:
            raise ValueError('Alternative measurement requires manual review: ' + ', '.join(overridden_by))

def _remove_breaks(plan, root):
    """Delete exactly the approved w:br elements. Returns one record per removal.

    Only the `w:br` element goes. The run that contained it stays, with its `w:rPr`,
    its text and any other children untouched, and an emptied run is left in place
    rather than tidied away: deleting a run or a paragraph would be a different change
    from the one that was approved.
    """
    ops = break_operations(plan)
    if not ops:
        return []
    by_id, elements = resolve_break_targets(plan, root)
    paragraphs = paras(root)
    planned = []
    for op in ops:
        record, element = by_id[op['target_id']], elements[op['target_id']]
        run = element.getparent()
        planned.append({
            'operation': REMOVE_BREAK,
            'target_id': op['target_id'],
            'break_type': record['break_type'],
            'paragraph_id': record['paragraph_id'],
            'run_index': record['run_index'],
            'break_index_in_run': record['break_index_in_run'],
            'child_index': list(run).index(element),
            'ordinal_in_paragraph': record['ordinal_in_paragraph'],
            'removed_element': 'w:br',
            'element': element,
        })
    # Indices are taken for every removal before any element is detached, so two
    # removals inside one run cannot shift each other's recorded position.
    for change in sorted(planned, key=lambda c: (c['paragraph_id'], -c['run_index'],
                                                 -c['child_index'])):
        element = change.pop('element')
        element.getparent().remove(element)
    for change in planned:
        change['style_id'] = style_of(paragraphs[change['paragraph_id']])
    return planned


def _insert_ordered(sectpr, tag):
    """Create `w:<tag>` at its CT_SectPr schema position and return it."""
    element = E.Element(q(tag))
    order = SECTPR_CHILD_ORDER
    position = next((i for i, child in enumerate(sectpr)
                     if E.QName(child).localname in order
                     and order.index(E.QName(child).localname) > order.index(tag)),
                    len(sectpr))
    sectpr.insert(position, element)
    return element


def _change_section_starts(plan, root):
    """Set exactly the approved section start types. Returns one record per change.

    Only the `w:val` of the section's `w:type` is written, and `w:type` is created at
    its schema position when the section relied on the documented `nextPage` default.
    No other child of the sectPr, no other sectPr, and nothing outside it is touched:
    the section count, its boundaries, its columns, page setup and every relationship
    the part declares are left exactly as they were.
    """
    ops = section_operations(plan)
    if not ops:
        return []
    by_id, elements = resolve_section_targets(plan, root)
    positions = {id(element): index
                 for index, element in enumerate(root.iter(q('sectPr')))}
    planned = []
    for op in ops:
        record, sectpr = by_id[op['target_id']], elements[op['target_id']]
        type_element = sectpr.find(q('type'))
        created = type_element is None
        if created:
            type_element = _insert_ordered(sectpr, 'type')
        planned.append({
            'operation': CHANGE_SECTION_START,
            'target_id': op['target_id'],
            'section_index': record['section_index'],
            'defined_in': record['defined_in'],
            'defining_paragraph_id': record['defining_paragraph_id'],
            'governs': record['governs'],
            'previous_start_type': record['start_type'],
            'previous_start_type_raw': record['start_type_raw'],
            'previous_start_type_was_default': record['start_type_is_default'],
            'new_start_type': op['new_start_type'],
            'created_type_element': created,
            'sectpr_document_index': positions[id(sectpr)],
            'first_paragraph_id': record['first_paragraph_id'],
            'last_paragraph_id': record['last_paragraph_id'],
            'columns': record['columns'],
        })
        type_element.set(q('val'), op['new_start_type'])
    return planned


def _verify_section_changes(before_root, after_root, section_changes):
    """Confirm the only difference in the whole document is the approved start type.

    This does not trust the code that made the change. It takes the document that was
    written, reverts exactly the approved attribute on a copy of it, and requires the
    result to serialise byte-for-byte identically to the input. Any other edit
    anywhere - another section, a paragraph, a run, a column declaration, a margin -
    survives the revert and fails the comparison.
    """
    before_sections = list(before_root.iter(q('sectPr')))
    after_sections = list(after_root.iter(q('sectPr')))
    if len(before_sections) != len(after_sections):
        raise ValueError('Unexpected content modification: the number of section '
                         'property blocks changed')
    reverted = copy.deepcopy(after_root)
    blocks = list(reverted.iter(q('sectPr')))
    for change in section_changes:
        index = change['sectpr_document_index']
        if not 0 <= index < len(blocks):               # pragma: no cover - defensive
            raise ValueError('Unexpected content modification: section index out of range')
        sectpr = blocks[index]
        type_element = sectpr.find(q('type'))
        if type_element is None or type_element.get(q('val')) != change['new_start_type']:
            raise ValueError('Unexpected content modification: the approved section '
                             'start type was not the value written')
        if change['created_type_element']:
            sectpr.remove(type_element)
        else:
            type_element.set(q('val'), change['previous_start_type_raw'])
    if E.tostring(reverted) != E.tostring(before_root):
        raise ValueError('Unexpected content modification: a section change altered '
                         'more than the approved start type')


def _run_children(paragraph):
    return [[E.tostring(child) for child in run]
            for run in paragraph.xpath('.//w:r', namespaces=NS)]


def _verify_break_removal(before_paragraph, after_paragraph, changes,
                          ignore_properties=False):
    """Confirm the only difference is the approved break elements being gone.

    The expected child list of every run is computed by dropping exactly the approved
    positions from the paragraph as it was, and is then compared with the paragraph as
    it is. Any other edit - a lost text node, reordered runs, altered run properties, a
    deleted run, a second break that was not approved - fails this comparison, so the
    narrowing is to the approved elements only and not to "changes inside runs".
    """
    expected = _run_children(before_paragraph)
    actual = _run_children(after_paragraph)
    if len(expected) != len(actual):
        raise ValueError('Unexpected content modification: run count changed')
    for change in changes:
        run_index, child_index = change['run_index'], change['child_index']
        if not 0 <= run_index < len(expected):             # pragma: no cover - defensive
            raise ValueError('Unexpected content modification: run index out of range')
        children = expected[run_index]
        if not 0 <= child_index < len(children):           # pragma: no cover - defensive
            raise ValueError('Unexpected content modification: break index out of range')
        removed = children[child_index]
        if b'<w:br' not in removed:                        # pragma: no cover - defensive
            raise ValueError('Unexpected content modification: target was not a w:br')
        children[child_index] = None
    expected = [[c for c in children if c is not None] for children in expected]
    if expected != actual:
        raise ValueError('Unexpected content modification: a run changed beyond the '
                         'approved break removal')
    if (before_paragraph.xpath('string(.)') != after_paragraph.xpath('string(.)')):
        raise ValueError('Unexpected content modification: paragraph text changed')
    if ignore_properties:
        # The paragraph also carries an approved property operation, so the caller
        # compares w:pPr itself. Skipping it here would otherwise hide that change.
        return
    ppr_before, ppr_after = before_paragraph.find(q('pPr')), after_paragraph.find(q('pPr'))
    if (ppr_before is None) != (ppr_after is None) or (
            ppr_before is not None and E.tostring(ppr_before) != E.tostring(ppr_after)):
        raise ValueError('Unexpected content modification: paragraph properties '
                         'changed during a break removal')


def apply(source, destination, plan):
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve() or destination.exists():
        raise ValueError('Choose a new output path; originals are never overwritten')
    package = open_package(source)
    root, style_root = package.document, package.styles
    validate(plan, root, style_root)
    if plan['source_sha256'] != signature(source):
        raise ValueError('Plan belongs to another document revision')
    before = copy.deepcopy(root)
    effective_before = {op['paragraph_id']: styles_module.effective_summary(
        styles_module.resolve(paras(root)[op['paragraph_id']], style_root))
        for op in paragraph_operations(plan)}
    changes, break_changes = [], _remove_breaks(plan, root)
    section_changes = _change_section_starts(plan, root)
    for op in paragraph_operations(plan):
        p = paras(root)[op['paragraph_id']]
        ppr = p.find(q('pPr'))
        if ppr is None:
            ppr = E.Element(q('pPr'))
            p.insert(0, ppr)
        tag, attr = FIELDS[op['field']]
        el = ppr.find(q(tag))
        if el is None:
            order = ['pStyle','keepNext','keepLines','pageBreakBefore','framePr','widowControl','numPr','suppressLineNumbers','pBdr','shd','tabs','suppressAutoHyphens','kinsoku','wordWrap','overflowPunct','topLinePunct','autoSpaceDE','autoSpaceDN','bidi','adjustRightInd','snapToGrid','spacing','ind','contextualSpacing','mirrorIndents','suppressOverlap','jc','textDirection','textAlignment','textboxTightWrap','outlineLvl','divId','cnfStyle','rPr','sectPr','pPrChange']
            el = E.Element(q(tag))
            position = next((i for i, child in enumerate(ppr)
                             if E.QName(child).localname in order and order.index(E.QName(child).localname) > order.index(tag)), len(ppr))
            ppr.insert(position, el)
        old = el.get(q(attr))
        value = op['value']
        encoded = str(round(value * 20)) if op['field'].endswith('_pt') else ('1' if value else '0') if type(value) is bool else value
        el.set(q(attr), encoded)
        resolved_after = styles_module.resolve(p, style_root)
        changes.append({**op, 'previous_raw': old, 'new_raw': encoded,
                        'effective_before': effective_before[op['paragraph_id']].get(op['field']),
                        'effective_after': resolved_after['properties'][op['field']]['value'],
                        'previous_origin': 'direct' if old is not None else 'inherited_or_default',
                        'style_chain': resolved_after['style_chain']})
    # Preserve all content and all untargeted paragraphs exactly at XML level.
    if section_changes:
        # A section plan carries exactly one operation (see _validate_section
        # _operations), so the whole-document revert comparison below is both
        # available and strictly stronger than the per-paragraph comparison: it
        # covers the body-level sectPr, which is not inside any paragraph at all.
        _verify_section_changes(before, root, section_changes)
    property_touched = {o['paragraph_id'] for o in paragraph_operations(plan)}
    break_touched = {}
    for change in break_changes:
        break_touched.setdefault(change['paragraph_id'], []).append(change)
    for i, (a, b) in enumerate(zip(paras(before), paras(root))):
        if section_changes:
            break
        if i in break_touched:
            # The strict byte comparison cannot apply to a paragraph a break was
            # removed from, so it is replaced - for this paragraph only - by an
            # equally strict check that *exactly* the approved elements are gone.
            _verify_break_removal(a, b, break_touched[i],
                                  ignore_properties=i in property_touched)
            if i not in property_touched:
                continue
            a, b = copy.deepcopy(a), copy.deepcopy(b)
            for p, changes_for in ((a, break_touched[i]), (b, ())):
                for change in sorted(changes_for, key=lambda c: (-c['run_index'],
                                                                 -c['child_index'])):
                    run = p.xpath('.//w:r', namespaces=NS)[change['run_index']]
                    run.remove(list(run)[change['child_index']])
        elif i in property_touched:
            a, b = copy.deepcopy(a), copy.deepcopy(b)
        if i in property_touched:
            for p in (a, b):
                ppr = p.find(q('pPr'))
                if ppr is not None:
                    p.remove(ppr)
        if E.tostring(a) != E.tostring(b):
            raise ValueError('Unexpected content modification')
    xml = E.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)
    # Exclusive creation avoids accidental overwriting, including races.
    with ZipFile(source) as src, destination.open('xb') as stream:
        with ZipFile(stream, 'w') as out:
            out.comment = src.comment
            for info in src.infolist():
                out.writestr(info, xml if info.filename == 'word/document.xml' else src.read(info.filename))
    return {'changes': changes, 'break_changes': break_changes,
            'section_changes': section_changes,
            'operation_kind': operation_kind(plan),
            'plan_schema_version': plan_schema_version(plan),
            'source_sha256': plan['source_sha256'],
            'output_sha256': signature(destination), 'structural_check': 'passed',
            'style_resolution': 'docDefaults and w:basedOn chain resolved; numbering and table styles not',
            'visual_check': 'not performed'}

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    scan = commands.add_parser('inspect'); scan.add_argument('source')
    near = commands.add_parser('peers'); near.add_argument('source'); near.add_argument('paragraph_id', type=int)
    brk = commands.add_parser('breaks'); brk.add_argument('source')
    sec = commands.add_parser('sections'); sec.add_argument('source')
    fix = commands.add_parser('apply'); fix.add_argument('source'); fix.add_argument('plan'); fix.add_argument('output')
    args = parser.parse_args()
    if args.command == 'inspect':
        result = inspect(args.source)
    elif args.command == 'peers':
        result = peers(args.source, args.paragraph_id)
    elif args.command == 'breaks':
        result = inspect_breaks(args.source)
    elif args.command == 'sections':
        result = inspect_sections(args.source)
    else:
        result = apply(args.source, args.output, json.loads(Path(args.plan).read_text()))
    print(json.dumps(result, indent=2))
