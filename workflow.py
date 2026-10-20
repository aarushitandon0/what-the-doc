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
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

from lxml import etree as E

import engine
import observability
import render as render_module

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
    record('runs_preserved', _runs(before.document) == _runs(after.document),
           'run count and run properties are identical')
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

    targeted = sorted({op['paragraph_id'] for op in plan['operations']})
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
    targets = sorted({op['paragraph_id'] for op in plan['operations']})
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
    with observability.span('candidate.create'):
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
        trace.set(outcome='failed' if failures else 'passed',
                  error_code='invalid_operation' if failures else None)
    report['invariants'] = checks
    report['invariant_failures'] = failures
    stage('validated')

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
