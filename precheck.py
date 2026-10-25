"""A deterministic check that runs before the model is asked.

Measured behaviour (TEST_RESULTS.md section 18): when a person complains about spacing
and the paragraph has no spacing of its own, the small model still proposes a change.
Those answers are wrong - there is nothing to repair on that paragraph - and they are
refused or, worse, reach review. The setting is not a fault of the paragraph: it is
inherited from a style, and every paragraph using that style has the same.

This module answers exactly that one situation, and nothing else. It never proposes a
change, so it cannot put a wrong edit in front of anyone: its only possible answer is
"no_change", with the evidence it read. In every other situation it returns None and
the model is asked as before. It is deliberately narrow:

  * the complaint must be plainly about space above OR below (not both), and must not
    mention anything else (page, break, alignment, indent, ...);
  * the paragraph's own value for that spacing must not be direct formatting;
  * nothing else on the paragraph can explain a gap: no page-break-before setting, no
    explicit break, no automatic-spacing override, and the neighbouring paragraph's
    facing spacing is not direct formatting either.

If any of that is uncertain it declines, and the model decides.
"""
import re

import diagnosis
import engine

RULE = 'inherited_spacing'

_SPACE = re.compile(r'\b(space|spacing|spaced|gap|gaps|whitespace|white space)\b')
_ABOVE = re.compile(r'\b(above|before|over|top|preceding)\b')
_BELOW = re.compile(r'\b(below|after|under|beneath|underneath|bottom|following)\b')
# Anything that points at a different kind of fault means this rule has no business
# answering. Stems, not whole words, so "aligned"/"alignment"/"centred" all match.
_OTHER = re.compile(r'(page|break|column|align|indent|centre|center|justif|left|right|'
                    r'font|bold|italic|size|colou?r|line|row|table|section|number|'
                    r'bullet|list|margin|jump|hidden|missing)')


def _direction(complaint):
    text = complaint.lower()
    if not _SPACE.search(text) or _OTHER.search(text):
        return None
    above, below = bool(_ABOVE.search(text)), bool(_BELOW.search(text))
    if above == below:      # neither, or both: not clear which gap is meant
        return None
    return 'space_before_pt' if above else 'space_after_pt'


def _source_sentence(where, entry):
    """Where the value comes from, as one sentence."""
    origin, value = entry.get('origin'), entry.get('value')
    amount = ('%g pt' % value) if isinstance(value, (int, float)) else None
    if origin and origin.startswith('style:') and amount:
        return ('The space %s it is %s and comes from the %s style, which every '
                'paragraph with that style shares.' % (where, amount, origin[6:]))
    if origin == 'doc_defaults' and amount:
        return ("The space %s it is %s and comes from the document's default settings, "
                "which every paragraph shares." % (where, amount))
    return ("Nothing sets the space %s it, so Word's built-in default applies." % where)


def inherited_spacing(source, paragraph_id, complaint, break_target_id=None,
                      section_target_id=None):
    """A validated no_change diagnosis, or None to let the model decide."""
    if break_target_id or section_target_id:
        return None
    field = _direction(complaint or '')
    if field is None:
        return None
    inspection = engine.inspect(source)
    records = inspection['paragraphs']
    if not 0 <= paragraph_id < len(records):
        return None
    own = records[paragraph_id]['effective_properties']
    entry = own.get(field)
    if not entry or entry.get('origin') == 'direct' or entry.get('overridden_by'):
        return None
    # Another setting on this paragraph can open a gap by itself.
    if own.get('page_break_before', {}).get('origin') == 'direct':
        return None
    if own.get('page_break_before', {}).get('value'):
        return None
    if any(b.get('paragraph_id') == paragraph_id
           for b in engine.inspect_breaks(source).get('breaks', [])):
        return None
    # The gap between two paragraphs is the larger of the two neighbours' settings, so
    # a directly formatted neighbour could be the real cause. Leave that to the model.
    neighbour = paragraph_id - 1 if field == 'space_before_pt' else paragraph_id + 1
    facing = 'space_after_pt' if field == 'space_before_pt' else 'space_before_pt'
    if 0 <= neighbour < len(records):
        other = records[neighbour]['effective_properties'].get(facing, {})
        if other.get('origin') == 'direct' or other.get('overridden_by'):
            return None

    where = 'above' if field == 'space_before_pt' else 'below'
    explanation = ('This paragraph has no spacing of its own. %s Changing only this '
                   'paragraph would make it differ from the others. To change it '
                   'everywhere, edit that style in Word.' % _source_sentence(where, entry))
    raw = {'source_sha256': inspection['source_sha256'],
           'explanation': explanation,
           'evidence': [{'paragraph_id': paragraph_id, 'field': field}],
           'operations': [], 'status': 'no_change', 'question': None}
    # The same validator a model answer goes through, so the shape cannot drift.
    return diagnosis.check(raw, paragraph_id, inspection['source_sha256'],
                           {paragraph_id})


# ---------------------------------------------------------------------------
# Whole document: "there are page breaks I didn't want"
# ---------------------------------------------------------------------------
# Asking the model paragraph by paragraph is the wrong tool for this complaint. Measured
# on a real upload: of three removable page breaks, the per-paragraph scan removed one,
# refused the paragraph holding the other two, and on the heading proposed *adding* a
# page break. The breaks themselves are not a judgement call - engine.inspect_breaks
# lists every one, with whether this version can remove it - so the person's own words
# ("I didn't want them") plus that inventory is the whole answer.
#
# Still only a proposal: it goes through engine.validate, workflow.repair and every
# preservation invariant, is rendered before/after, and a person accepts it or not.

DOCUMENT_BREAKS_RULE = 'unwanted_page_breaks'
MAX_DOCUMENT_BREAK_REMOVALS = 12           # engine.validate's per-plan ceiling

# Deliberately not "new page": "this heading jumps to a new page" is usually
# keep-with-next or page-break-before on one paragraph, which the model handles.
_PAGE_BREAK = re.compile(r'(page[\s-]*breaks?|pagebreaks?|blank pages?|empty pages?)')
_COLUMN_BREAK = re.compile(r'column[\s-]*breaks?')
# A complaint asking for a break is the opposite request; leave it to the model.
_WANTS_MORE = re.compile(r'\b(add|insert|missing)\b')


def _break_types(complaint):
    text = (complaint or '').lower()
    if _WANTS_MORE.search(text):
        return ()
    types = []
    if _PAGE_BREAK.search(text):
        types.append('page')
    if _COLUMN_BREAK.search(text):
        types.append('column')
    return tuple(types)


def unwanted_page_breaks(source, complaint):
    """A whole-document proposal removing every removable break the complaint names.

    Returns (diagnosis, kept) or None when the complaint is not about unwanted page or
    column breaks, or there is nothing this version can remove. `kept` lists the breaks
    of the named kind that stay, each with the reason, so nothing is silently skipped.
    """
    types = _break_types(complaint)
    if not types:
        return None
    report = engine.inspect_breaks(source)
    named = [b for b in report.get('breaks', [])
             if b.get('kind') == 'explicit_break' and b.get('break_type') in types]
    removable = [b for b in named if b.get('edit_eligible') and b.get('target_id')]
    if not removable:
        return None
    removable = removable[:MAX_DOCUMENT_BREAK_REMOVALS]
    chosen = {b['target_id'] for b in removable}
    kept = [{'paragraph_id': b.get('paragraph_id'), 'break_type': b.get('break_type'),
             'text_after': (b.get('text_after') or '')[:60],
             'reason': b.get('ineligible_reason') or
                       'More than %d breaks: run the check again on the repaired '
                       'file to remove the rest.' % MAX_DOCUMENT_BREAK_REMOVALS}
            for b in named if b.get('target_id') not in chosen]
    paragraphs = sorted({b['paragraph_id'] for b in removable})
    noun = ' and '.join('%s break' % t for t in types)
    explanation = ('You said these breaks are unwanted. This removes all %d %s%s this '
                   'version can safely remove, in paragraph%s %s. Only the break '
                   'markers go: every word, run and style stays exactly as it was.'
                   % (len(removable), noun, '' if len(removable) == 1 else 's',
                      '' if len(paragraphs) == 1 else 's',
                      ', '.join(str(p) for p in paragraphs)))
    if kept:
        explanation += (' %d other %s stay%s, because this version cannot edit %s '
                         'safely.' % (len(kept), 'break' if len(kept) == 1 else 'breaks',
                                      's' if len(kept) == 1 else '',
                                      'it' if len(kept) == 1 else 'them'))
    found = {
        'status': 'proposed',
        'explanation': explanation,
        'evidence': [{'paragraph_id': b['paragraph_id'], 'target_id': b['target_id']}
                     for b in removable],
        'source_sha256': report['source_sha256'],
        'operations': [{'operation': engine.REMOVE_BREAK, 'target_id': b['target_id'],
                        'expected_break_type': b['break_type']} for b in removable],
        'question': None,
        'evidence_duplicates_removed': 0,
    }
    return found, kept
