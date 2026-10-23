"""Before/after repair workflow: inspect, patch a copy, render both, compare, report.

Follows the stage flow in IDEATION.md section 7, and two rules that matter more than
the output format:

  * The original file is never written to. Its hash is taken before and after the run
    and compared, so "preserved" is verified rather than asserted.
  * A candidate is always generated from the immutable parent plus the proposal hash.
    Re-running the same proposal reuses that candidate instead of patching a patched
    copy, so a retry cannot apply the same change twice.

Structural invariants are re-checked here independently of engine.apply, because a
guarantee the patcher checks against its own in-memory tree is worth re-testing
against the bytes that actually landed on disk.

awaiting_review is the end state. Nothing in this module accepts a repair on the
user's behalf, and no changed-pixel ratio is treated as approval.
"""
import copy
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

from lxml import etree as E

import engine
import observability
import render as render_module
import structure as structure_module

NS = engine.NS


def proposal_hash(plan):
    """Stable identity for a proposal, so the same plan maps to the same candidate."""
    payload = json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(payload).hexdigest()


def _text(root):
    return ''.join(root.xpath('//w:t/text()', namespaces=NS))


def _runs(root):
    # Run properties carry bold/italic/font: a formatting repair must not disturb them.
    return [E.tostring(r) for r in root.xpath('//w:r', namespaces=NS)]


def _run_properties(root):
    """Just w:rPr per run, so bold/italic/font can be checked when a run's content changed."""
    return [E.tostring(r.find(engine.q('rPr'))) if r.find(engine.q('rPr')) is not None else None
            for r in root.xpath('//w:r', namespaces=NS)]


def _run_text(root):
    return [''.join(r.xpath('.//w:t/text()', namespaces=NS))
            for r in root.xpath('//w:r', namespaces=NS)]


def _break_census(root, source_hash):
    """How many removable breaks of each type sit at each (paragraph, run) position.

    A census rather than a set of target ids, on purpose. Removing the first of two
    breaks inside one run shifts the second one's index, which changes the id that
    break would be issued next time. Counting positions is therefore the identity that
    survives the edit, and it still pins every remaining break to its exact run.
    """
    records, _ = structure_module.locate_breaks(root, source_hash, engine.paras(root))
    census = {}
    for record in records:
        if record['kind'] != 'explicit_break':
            continue
        key = (record['paragraph_id'], record['run_index'], record['break_type'],
               record['edit_eligible'])
        census[key] = census.get(key, 0) + 1
    return census


def _approved_break_removals(source_root, plan):
    """Re-derive what the plan is allowed to remove, from the source, independently.

    `engine.apply` recorded what it did. This recomputes what it was *permitted* to
    do, from the plan and the original document, so the invariant check does not take
    the patcher's own account of its work as evidence.
    """
    by_id, _ = engine.resolve_break_targets(plan, source_root)
    removals = {}
    for op in engine.break_operations(plan):
        record = by_id.get(op.get('target_id'))
        if record is None:
            continue
        key = (record['paragraph_id'], record['run_index'], record['break_type'], True)
        removals[key] = removals.get(key, 0) + 1
    return removals


def _section_census(root, source_hash):
    """Every section's identity and its start type, in document order.

    Boundaries are the (defined_in, defining paragraph id) pairs, which is what a
    section *is* structurally. Recording them beside the start types means a change
    that moved, added, merged or removed a section fails the comparison even if the
    list of start types happened to come out the same length.
    """
    records, _ = structure_module.locate_sections(root, engine.paras(root), source_hash)
    return ([(r['defined_in'], r['defining_paragraph_id']) for r in records],
            [r['start_type'] for r in records])


def _section_properties_census(root):
    """Each sectPr serialised with its w:type removed, in document order.

    What is left is every section property the start-type change is *not* allowed to
    touch: columns, page size, margins, numbering, borders, headers and footers.
    """
    blocks = []
    for sectpr in root.iter(engine.q('sectPr')):
        copy_of = copy.deepcopy(sectpr)
        type_element = copy_of.find(engine.q('type'))
        if type_element is not None:
            copy_of.remove(type_element)
        blocks.append(E.tostring(copy_of))
    return blocks


def _check_section_invariants(record, before, after, plan, source_hash_before):
    """Preservation checks that exist only for a section-start change."""
    ops = engine.section_operations(plan)
    by_id, _ = engine.resolve_section_targets(plan, before.document)
    boundaries_before, starts_before = _section_census(before.document, source_hash_before)
    boundaries_after, starts_after = _section_census(after.document, source_hash_before)

    record('section_count_preserved', len(starts_before) == len(starts_after),
           'no section was added, merged, split or deleted')
    record('section_boundaries_preserved', boundaries_before == boundaries_after,
           'every section is still defined in the same place and ends with the same '
           'paragraph')

    expected = list(starts_before)
    applied = []
    for op in ops:
        target = by_id.get(op['target_id'])
        if target is None:                             # pragma: no cover - defensive
            continue
        index = target['section_index']
        if 0 <= index < len(expected) and expected[index] == op['expected_start_type']:
            expected[index] = op['new_start_type']
            applied.append(index)
    record('only_approved_section_start_changed',
           len(expected) == len(starts_after) and expected == starts_after,
           'start types differ from the original at exactly section(s) %s and nowhere '
           'else' % (applied or 'none'))
    record('section_properties_preserved',
           _section_properties_census(before.document) ==
           _section_properties_census(after.document),
           'every section property other than w:type - columns, page size, margins, '
           'numbering, borders, headers and footers - is byte-identical')


def check_invariants(source, candidate, plan, source_hash_before):
    """Verify preservation against the files on disk. Returns (checks, failures)."""
    checks, failures = {}, []

    def record(name, ok, detail):
        checks[name] = {'passed': bool(ok), 'detail': detail}
        if not ok:
            failures.append(name)

    record('original_unmodified', engine.signature(source) == source_hash_before,
           'sha256 of the input file is unchanged by the run')

    before, after = engine.open_package(source), engine.open_package(candidate)
    record('text_preserved', _text(before.document) == _text(after.document),
           'every w:t character survives, in order')
    break_ops = engine.break_operations(plan)
    if not break_ops:
        # Unchanged for every paragraph-property repair: a run must be byte-identical.
        record('runs_preserved', _runs(before.document) == _runs(after.document),
               'run count and run properties are identical')
    else:
        # Narrowed for exactly the approved structural change, and only when the plan
        # contains one. The strict check above is not disabled globally, and the three
        # checks replacing it are not weaker in any other respect: run count, run
        # properties and per-run text must all still be identical, and the break
        # census must differ by precisely the approved removals.
        record('run_count_preserved',
               len(_runs(before.document)) == len(_runs(after.document)),
               'no run was added or deleted; a run emptied by a break removal is kept')
        record('run_properties_preserved',
               _run_properties(before.document) == _run_properties(after.document),
               'every w:rPr is identical, so bold, italic and font are untouched')
        record('run_text_preserved', _run_text(before.document) == _run_text(after.document),
               'every run holds the same text, so no text moved between runs')
        census_before = _break_census(before.document, source_hash_before)
        census_after = _break_census(after.document, source_hash_before)
        approved = _approved_break_removals(before.document, plan)
        expected = dict(census_before)
        for key, count in approved.items():
            expected[key] = expected.get(key, 0) - count
            if expected[key] <= 0:
                expected.pop(key)
        record('only_approved_breaks_removed', expected == census_after,
               'breaks by paragraph, run and type differ from the original by exactly '
               'the %d approved removal(s)' % sum(approved.values()))
        record('approved_break_count_matches',
               sum(approved.values()) == len(break_ops),
               'every break operation in the plan resolved to a break in the original')
    if engine.section_operations(plan):
        _check_section_invariants(record, before, after, plan, source_hash_before)
    record('paragraph_count_preserved',
           len(engine.paras(before.document)) == len(engine.paras(after.document)),
           'no body paragraph added or removed')
    record('styles_part_unmodified',
           (before.styles is None) == (after.styles is None) and
           (before.styles is None or E.tostring(before.styles) == E.tostring(after.styles)),
           'the repair overrides formatting on the paragraph, it does not edit shared styles')

    with ZipFile(source) as a, ZipFile(candidate) as b:
        names_a, names_b = a.namelist(), b.namelist()
        record('zip_members_preserved', names_a == names_b,
               'same parts, in the same order')
        differing = [n for n in names_a
                     if n in names_b and n != 'word/document.xml' and a.read(n) != b.read(n)]
        record('unrelated_parts_byte_identical', not differing,
               'only word/document.xml is rewritten' + (' (differs: %s)' % differing if differing else ''))

    targeted = engine.targeted_paragraph_ids(plan, before.document)
    changed = [i for i, (x, y) in enumerate(zip(engine.paras(before.document), engine.paras(after.document)))
               if E.tostring(x) != E.tostring(y)]
    record('only_targeted_paragraphs_changed', set(changed) <= set(targeted),
           'changed paragraph ids %s within targeted %s' % (changed, targeted))
    return checks, failures


def repair(source, plan, work_dir, do_render=True):
    """Run one proposal end to end and return a report. Never modifies `source`."""
    source, work_dir = Path(source), Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    source_hash_before = engine.signature(source)
    report = {'stages': [], 'source': str(source), 'source_sha256': source_hash_before,
              'proposal_sha256': proposal_hash(plan)}

    def stage(name, **extra):
        report['stages'].append(name)
        report.update(extra)

    inspection = engine.inspect(source)
    if plan.get('source_sha256') != source_hash_before:
        raise ValueError('Plan belongs to another document revision')
    source_root = engine.open_package(source).document
    targets = engine.targeted_paragraph_ids(plan, source_root)
    report['operation_kind'] = engine.operation_kind(plan)
    report['plan_schema_version'] = engine.plan_schema_version(plan)
    if engine.section_operations(plan):
        sections, _ = engine.resolve_section_targets(plan, source_root)
        report['section_targets'] = [dict(sections.get(op['target_id']) or {})
                                     for op in engine.section_operations(plan)]
    if engine.break_operations(plan):
        by_id, _ = engine.resolve_break_targets(plan, source_root)
        # The nearby text stays: a reviewer cannot tell which of several breaks is
        # being removed without it. It lives in this on-disk report exactly as the
        # user's complaint already does, and never reaches telemetry.
        report['break_targets'] = [dict(by_id.get(op['target_id']) or {})
                                   for op in engine.break_operations(plan)]
    stage('inspected', inspection={
        'paragraph_count': len(inspection['paragraphs']),
        'has_styles_part': inspection['has_styles_part'],
        'targets': [{'paragraph_id': i,
                     'style_chain': inspection['paragraphs'][i]['style_chain'],
                     'effective_before': {f: r['value'] for f, r in
                                          inspection['paragraphs'][i]['effective_properties'].items()}}
                    for i in targets if i < len(inspection['paragraphs'])]})

    renderer = render_module.renderer_status()
    report['renderer'] = renderer
    original_pdf = None
    if do_render and renderer['available']:
        with observability.span('render.original'):
            original_pdf = render_module.docx_to_pdf(source, work_dir / 'original')
            report['original_render'] = {
                'pdf': str(original_pdf),
                'pages': render_module.pdf_pages(original_pdf, work_dir / 'original', 'original')}
        stage('original_rendered')
    else:
        stage('original_render_skipped')

    # Candidate identity = immutable parent + proposal. A retry reuses it rather than
    # patching an already patched file.
    candidate = work_dir / ('candidate-%s-%s.docx' % (source_hash_before[:8], report['proposal_sha256'][:8]))
    kind = engine.operation_kind(plan)
    break_types = sorted({t.get('break_type') for t in (report.get('break_targets') or [])
                          if t.get('break_type')})
    with observability.span('candidate.create', operation_kind=kind,
                            break_type=break_types[0] if len(break_types) == 1 else None):
        report['candidate_reused'] = candidate.exists()
        report['apply'] = None if report['candidate_reused'] else engine.apply(source, candidate, plan)
        report['candidate'] = str(candidate)
        report['candidate_sha256'] = engine.signature(candidate)
    stage('proposed')

    if do_render and renderer['available']:
        with observability.span('render.candidate'):
            candidate_pdf = render_module.docx_to_pdf(candidate, work_dir / 'candidate')
            report['candidate_render'] = {
                'pdf': str(candidate_pdf),
                'pages': render_module.pdf_pages(candidate_pdf, work_dir / 'candidate', 'candidate')}
            report['visual_comparison'] = render_module.compare_pdfs(original_pdf, candidate_pdf)
        stage('candidate_rendered')
    else:
        stage('candidate_render_skipped')
        report['visual_comparison'] = None

    with observability.span('preservation.check') as trace:
        checks, failures = check_invariants(source, candidate, plan, source_hash_before)
        trace.set(outcome='failed' if failures else 'passed', operation_kind=kind,
                  error_code='invalid_operation' if failures else None)
    report['invariants'] = checks
    report['invariant_failures'] = failures
    stage('validated')

    # An explicit-break removal changes document structure, so it is not offered for
    # review unless *both* renderings actually succeeded. This is enforced here, on the
    # server, for every caller: a disabled button in one interface is not a rule.
    # Paragraph-property repairs keep their existing behaviour, which permits a
    # structural-only review when no renderer is installed.
    # A section-start change alters document structure exactly as a break removal
    # does, and it can change the layout of every page after it, so it carries the
    # same server-side rule: no review without both renderings.
    rendering_required = bool(engine.structural_operations(plan))
    rendered_both = bool(report.get('original_render') and report.get('candidate_render'))
    report['rendering_required'] = rendering_required
    report['rendering_satisfied'] = rendered_both or not rendering_required
    if rendering_required and not rendered_both:
        report['render_requirement_failure'] = (
            'An explicit-break removal requires a successful rendering of both the '
            'original and the candidate before it can be reviewed. '
            + ('The renderer is unavailable.' if not renderer['available']
               else 'Rendering was skipped for this run.'))
        report['invariant_failures'] = failures = list(failures) + ['rendering_required']
        report['invariants'] = dict(checks, rendering_required={
            'passed': False, 'detail': report['render_requirement_failure']})

    report['outcome'] = 'awaiting_review' if not failures else 'failed'
    report['visual_check'] = ('rendered for review' if report.get('visual_comparison')
                              else 'not performed: no renderer available')
    report['reminder'] = ('Nothing is accepted yet. Open the candidate in Word before '
                          'trusting it; the rendered pages approximate Word, and the '
                          'original stays available as the undo path.')
    stage(report['outcome'])
    (work_dir / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Apply a plan and render before/after.')
    parser.add_argument('source')
    parser.add_argument('plan')
    parser.add_argument('work_dir')
    parser.add_argument('--no-render', action='store_true')
    args = parser.parse_args()
    result = repair(args.source, json.loads(Path(args.plan).read_text()), args.work_dir,
                    do_render=not args.no_render)
    print(json.dumps(result, indent=2))
