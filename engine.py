"""Constrained DOCX paragraph-format patcher. No model required.

Only word/document.xml is changed. Other ZIP member payloads are preserved.
Inspection resolves effective paragraph properties through docDefaults and the
paragraph style chain (see styles.py); rendering comparison lives in render.py.
Structural guarantees are checked here, visual ones only by rendering.
"""
import argparse
import copy
import hashlib
import json
from collections import namedtuple
from pathlib import Path
from zipfile import ZipFile, BadZipFile
from lxml import etree as E

import styles as styles_module

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS = {'w': W}
MAX_BYTES = 20 * 1024 * 1024
MAX_EXPANDED = 80 * 1024 * 1024
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

def validate(plan, root, style_root=None):
    if set(plan) != {'source_sha256', 'operations'}:
        raise ValueError('Expected source_sha256 and operations only')
    ops = plan['operations']
    if not isinstance(ops, list) or len(ops) > 12:
        raise ValueError('Expected at most 12 operations')
    seen = set()
    for op in ops:
        if not isinstance(op, dict) or set(op) != {'paragraph_id', 'field', 'value'}:
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
        for op in plan['operations']}
    changes = []
    for op in plan['operations']:
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
    touched = {o['paragraph_id'] for o in plan['operations']}
    for i, (a, b) in enumerate(zip(paras(before), paras(root))):
        if i in touched:
            a, b = copy.deepcopy(a), copy.deepcopy(b)
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
    return {'changes': changes, 'source_sha256': plan['source_sha256'],
            'output_sha256': signature(destination), 'structural_check': 'passed',
            'style_resolution': 'docDefaults and w:basedOn chain resolved; numbering and table styles not',
            'visual_check': 'not performed'}

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    scan = commands.add_parser('inspect'); scan.add_argument('source')
    near = commands.add_parser('peers'); near.add_argument('source'); near.add_argument('paragraph_id', type=int)
    fix = commands.add_parser('apply'); fix.add_argument('source'); fix.add_argument('plan'); fix.add_argument('output')
    args = parser.parse_args()
    if args.command == 'inspect':
        result = inspect(args.source)
    elif args.command == 'peers':
        result = peers(args.source, args.paragraph_id)
    else:
        result = apply(args.source, args.output, json.loads(Path(args.plan).read_text()))
    print(json.dumps(result, indent=2))
