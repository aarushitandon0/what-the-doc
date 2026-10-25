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
