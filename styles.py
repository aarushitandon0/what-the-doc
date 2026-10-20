"""Effective paragraph-property resolution for OOXML.

Word layers paragraph formatting. From weakest to strongest this prototype resolves:

    1. w:docDefaults/w:pPrDefault        (styles.xml)
    2. the paragraph style chain         (w:basedOn walked parent-first)
    3. direct w:pPr on the paragraph     (document.xml)

Every resolved value therefore carries the layer that supplied it, so a diagnosis can
say "48pt comes from style Heading1", not merely "spacing looks wrong".

Deliberately NOT resolved: numbering (w:numPr) indentation, table-style conditional
formatting, theme-driven and linked-style effects, and the document settings part.
Where one of those can change the answer the resolver records a caveat instead of
guessing. An unset property is reported as origin "application_default" with value
None: Word's own built-in fallback is not restated here as a number.
"""
from lxml import etree as E

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS = {'w': W}
MAX_STYLE_DEPTH = 32
TWIPS_PER_POINT = 20

# field -> (element localname, attribute localname, kind)
RESOLVED_FIELDS = {
    'space_before_pt': ('spacing', 'before', 'twips'),
    'space_after_pt': ('spacing', 'after', 'twips'),
    'left_indent_pt': ('ind', 'left', 'twips'),
    'alignment': ('jc', 'val', 'alignment'),
    'page_break_before': ('pageBreakBefore', 'val', 'toggle'),
    'keep_with_next': ('keepNext', 'val', 'toggle'),
    # Context a spacing complaint cannot be judged without.
    'contextual_spacing': ('contextualSpacing', 'val', 'toggle'),
    'outline_level': ('outlineLvl', 'val', 'integer'),
}
# Attributes that silently override a resolved field if present anywhere in the chain.
OVERRIDES = {
    'space_before_pt': ('spacing', ('beforeLines', 'beforeAutospacing')),
    'space_after_pt': ('spacing', ('afterLines', 'afterAutospacing')),
    'left_indent_pt': ('ind', ('start', 'startChars', 'leftChars')),
}
ALIGNMENT_ALIASES = {'start': 'left', 'end': 'right'}
TOGGLE_FALSE = {'0', 'false', 'off'}


def q(name):
    return '{' + W + '}' + name


def _toggle(raw):
    # An on/off element is true by its presence; only an explicit falsy w:val disables it.
    return False if raw is not None and raw.strip().lower() in TOGGLE_FALSE else True


def _decode(kind, raw):
    """Typed value for a raw attribute string, or None when it is unusable."""
    if kind == 'toggle':
        return _toggle(raw)
    if raw is None:
        return None
    if kind == 'twips':
        try:
            return int(raw) / TWIPS_PER_POINT
        except ValueError:
            return None
    if kind == 'integer':
        try:
            return int(raw)
        except ValueError:
            return None
    if kind == 'alignment':
        return ALIGNMENT_ALIASES.get(raw, raw)
    return raw


def _read(ppr, field):
    """(raw, value) for one field of one w:pPr, or (None, None) when absent here."""
    if ppr is None:
        return None, None
    tag, attr, kind = RESOLVED_FIELDS[field]
    el = ppr.find(q(tag))
    if el is None:
        return None, None
    raw = el.get(q(attr))
    if kind != 'toggle' and raw is None:
        return None, None
    return raw, _decode(kind, raw)


def style_index(styles_root):
    """styleId -> w:style element, for paragraph styles only."""
    if styles_root is None:
        return {}
    index = {}
    for style in styles_root.xpath('/w:styles/w:style[@w:type="paragraph"]', namespaces=NS):
        style_id = style.get(q('styleId'))
        if style_id is not None and style_id not in index:
            index[style_id] = style
    return index


def default_paragraph_style(styles_root):
    """The styleId Word applies to a paragraph carrying no w:pStyle, if declared."""
    if styles_root is None:
        return None
    for style in styles_root.xpath('/w:styles/w:style[@w:type="paragraph"][@w:default]', namespaces=NS):
        if _toggle(style.get(q('default'))):
            return style.get(q('styleId'))
    return None


def style_chain(paragraph, styles_root):
    """Style ids weakest-first: the w:basedOn ancestry ending at the paragraph's own style.

    Returns (chain, caveats). A missing or self-referential w:basedOn target stops the
    walk and is reported rather than silently treated as "no inheritance".
    """
    index, caveats = style_index(styles_root), []
    ppr = paragraph.find(q('pPr'))
    pstyle = None if ppr is None else ppr.find(q('pStyle'))
    requested = None if pstyle is None else pstyle.get(q('val'))
    if requested is None:
        requested = default_paragraph_style(styles_root)
    elif requested not in index:
        caveats.append('Paragraph references undefined style %r; its contribution is unknown' % requested)
        return [], caveats
    chain, seen, current = [], set(), requested
    while current is not None and current in index:
        if current in seen:
            caveats.append('Style inheritance cycle at %r; resolution stopped there' % current)
            break
        if len(chain) >= MAX_STYLE_DEPTH:
            caveats.append('Style inheritance deeper than supported; resolution truncated')
            break
        seen.add(current)
        chain.append(current)
        based_on = index[current].find(q('basedOn'))
        nxt = None if based_on is None else based_on.get(q('val'))
        if nxt is not None and nxt not in index:
            caveats.append('Style %r is based on undefined style %r; inherited values are unknown' % (current, nxt))
            nxt = None
        current = nxt
    chain.reverse()  # weakest ancestor first, matching application order
    return chain, caveats


def _layers(paragraph, styles_root):
    """Ordered (origin, w:pPr) pairs, weakest first, that apply to this paragraph."""
    layers = []
    if styles_root is not None:
        for ppr in styles_root.xpath('/w:styles/w:docDefaults/w:pPrDefault/w:pPr', namespaces=NS):
            layers.append(('doc_defaults', ppr))
    index = style_index(styles_root)
    chain, caveats = style_chain(paragraph, styles_root)
    for style_id in chain:
        layers.append(('style:' + style_id, index[style_id].find(q('pPr'))))
    layers.append(('direct', paragraph.find(q('pPr'))))
    return layers, chain, caveats


def resolve(paragraph, styles_root):
    """Resolve one paragraph's effective properties with their provenance.

    properties[field] = {value, raw, origin, overridden_by?}
        origin "application_default" means no layer set it, so the value is None and
        Word's built-in fallback decides; the caller must not report that as 0.
    """
    layers, chain, caveats = _layers(paragraph, styles_root)
    properties = {}
    for field in RESOLVED_FIELDS:
        record = {'value': None, 'raw': None, 'origin': 'application_default'}
        for origin, ppr in layers:
            raw, value = _read(ppr, field)
            if raw is None and value is None:
                continue
            record = {'value': value, 'raw': raw, 'origin': origin}
        tag, attrs = OVERRIDES.get(field, (None, ()))
        hits = []
        for origin, ppr in layers:
            el = None if ppr is None else ppr.find(q(tag)) if tag else None
            if el is None:
                continue
            hits += [origin + ':' + attr for attr in attrs if q(attr) in el.attrib]
        if hits:
            # e.g. afterAutospacing wins over an explicit w:after, so the resolved
            # number is not what the reader actually sees on the page.
            record['overridden_by'] = hits
        properties[field] = record
    numbering = [origin for origin, ppr in layers if ppr is not None and ppr.find(q('numPr')) is not None]
    if numbering:
        caveats.append('Numbering (w:numPr via ' + ', '.join(numbering) +
                       ') can set indentation this resolver does not read; left_indent_pt may be incomplete')
    if styles_root is None:
        caveats.append('No word/styles.xml in package; only direct formatting could be resolved')
    return {'style_chain': chain, 'properties': properties, 'caveats': caveats}


def effective_summary(resolved):
    """Flat field -> value map for quick comparison between paragraphs."""
    return {field: record['value'] for field, record in resolved['properties'].items()}
