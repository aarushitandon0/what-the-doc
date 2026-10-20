"""One vertical slice: complaint -> Gemma diagnosis -> validated plan -> rendered candidate.

This module is the join between the diagnosis layer and the existing repair workflow,
and it deliberately adds no new powers to either side:

  * The diagnosis is validated by `diagnosis.check` and then, if it proposes anything,
    re-validated by `engine.validate` before a file is touched. Two independent checks,
    neither of which trusts the model.
  * A proposal creates a *candidate* through `workflow.repair`, which keeps the
    preservation invariants, the tamper detection and the parent+proposal candidate
    naming exactly as they were. The original is never written to.
  * The run ends at `awaiting_review` at best. Nothing here accepts a repair, and no
    status, explanation or changed-pixel ratio is treated as approval. Acceptance is a
    separate, explicit step a human takes after looking at the preview.
  * An abstention is reported as an abstention. `no_change`, `unsupported` and
    `needs_clarification` produce no candidate and are never called a repair.

Every failure carries one code from `diagnosis`, so "the model is not running" reads
differently from "the model answered something unusable" and from "LibreOffice died".
"""
import json
import time
from pathlib import Path

import diagnosis
import engine
import gemma_adapter
import observability
import render as render_module
import workflow

# Terminal outcomes. Only AWAITING_REVIEW has a candidate a user could accept.
AWAITING_REVIEW = 'awaiting_review'
FAILED = 'failed'
ABSTAINED = ('no_change', 'unsupported', 'needs_clarification')

LIVE = 'live'
FIXTURE = 'fixture'


def _validate_plan(source, plan):
    """engine.validate, with its failure kinds separated for the report.

    An all-ineffective plan is refused as `invalid_operation`: the model's own status
    stays `proposed` in the report, so a deterministic refusal here is never recorded
    as the model having abstained.
    """
    try:
        return gemma_adapter.prepare_proposal(source, plan)
    except ValueError as error:
        message = str(error)
        code = (diagnosis.UNSUPPORTED_FORMATTING
                if 'Alternative measurement' in message
                else diagnosis.INVALID_OPERATION)
        raise diagnosis.DiagnosisError(code, message)


def run(source, paragraph_id, complaint, work_dir, diagnoser=None, mode=LIVE,
        do_render=True, model=None, timeout=None, context=1):
    """Trace one finite repair; the API worker's enclosing trace is reused."""
    with observability.transaction('repair.execute',
                                   mode=FIXTURE if diagnoser is not None else mode,
                                   model=model) as trace:
        report = _run(source, paragraph_id, complaint, work_dir, diagnoser=diagnoser,
                      mode=mode, do_render=do_render, model=model, timeout=timeout,
                      context=context)
        trace.set(outcome=report['outcome'], error_code=report.get('error_code'))
        return report


def _run(source, paragraph_id, complaint, work_dir, diagnoser=None, mode=LIVE,
         do_render=True, model=None, timeout=None, context=1):
    """Diagnose one complaint and, if a repair is proposed, build a candidate to review.

    `diagnoser` is the injection point for deterministic tests: a callable
    (source, complaint, paragraph_id) -> (diagnosis, metadata). When it is supplied the
    report records mode "fixture", so a result produced without a model can never be
    read as a live one.
    """
    source, work_dir = Path(source), Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    if diagnoser is None:
        def diagnoser(path, text, selected):
            return gemma_adapter.diagnose(path, text, selected, model=model,
                                          timeout=timeout, context=context)
    else:
        mode = FIXTURE

    source_hash = engine.signature(source)
    report = {
        'mode': mode,
        'source': str(source),
        'source_sha256': source_hash,
        'paragraph_id': paragraph_id,
        # The user's own complaint is recorded because the result cannot be read
        # without it. Document text is not: the diagnosis cites ids and fields.
        'complaint': complaint,
        'stages': [],
        'renderer': render_module.renderer_status(),
        'runtime': gemma_adapter.runtime_status() if mode == LIVE else
                   {'available': False, 'reason': 'fixture mode: no model was contacted'},
        'diagnosis': None,
        'diagnosis_metadata': None,
        'plan': None,
        'candidate': None,
        'preview': None,
        'invariants': None,
        'error_code': None,
        'error': None,
        'accepted': False,
        'outcome': None,
    }

    def stage(name):
        report['stages'].append(name)

    def fail(code, message):
        report['error_code'] = code
        report['error'] = message
        report['outcome'] = FAILED
        stage(FAILED)
        return _finish(report, work_dir)

    stage('selected')
    started = time.monotonic()
    try:
        with observability.span('diagnosis.execute') as trace:
            found, metadata = diagnoser(source, complaint, paragraph_id)
            trace.set(model=(metadata or {}).get('model'))
    except diagnosis.DiagnosisError as error:
        report['diagnosis_metadata'] = {'latency_seconds': round(time.monotonic() - started, 3)}
        return fail(error.code, error.message)
    except RuntimeError as error:
        # An unconfigured model is a missing model, not a crash.
        report['diagnosis_metadata'] = {'latency_seconds': round(time.monotonic() - started, 3)}
        return fail(diagnosis.MODEL_UNAVAILABLE, str(error))
    report['diagnosis'] = found
    report['diagnosis_metadata'] = metadata
    stage('diagnosed')

    if found['status'] in ABSTAINED:
        # An abstention is a complete, honest answer. It produces no candidate, so
        # there is nothing here that could be mistaken for a finished repair.
        report['outcome'] = found['status']
        report['note'] = ('No repair was proposed, so no candidate exists and nothing '
                          'can be accepted.')
        stage(found['status'])
        return _finish(report, work_dir)

    plan = diagnosis.plan_of(found)
    report['plan'] = plan
    # Record the raw proposal's redundant writes even if the plan is refused or
    # preparation removes them. The model's diagnosis is retained without alteration.
    try:
        report['ineffective_operations'] = gemma_adapter.ineffective_operations(source, plan)
    except Exception:                                  # pragma: no cover - defensive
        report['ineffective_operations'] = None
    try:
        plan = _validate_plan(source, plan)
    except diagnosis.DiagnosisError as error:
        return fail(error.code, error.message)
    report['plan'] = plan
    stage('plan_validated')

    try:
        repair_report = workflow.repair(source, plan, work_dir, do_render=do_render)
    except render_module.RendererUnavailable as error:
        return fail(diagnosis.RENDER_FAILURE, str(error))
    except render_module.RenderFailed as error:
        return fail(diagnosis.RENDER_FAILURE, str(error))
    except ValueError as error:
        return fail(diagnosis.INVALID_OPERATION, str(error))

    report['candidate'] = repair_report['candidate']
    report['candidate_sha256'] = repair_report['candidate_sha256']
    report['candidate_reused'] = repair_report['candidate_reused']
    report['invariants'] = repair_report['invariants']
    report['invariant_failures'] = repair_report['invariant_failures']
    report['visual_comparison'] = repair_report.get('visual_comparison')
    report['changes'] = (repair_report.get('apply') or {}).get('changes')
    report['preview'] = {
        'original_pages': (repair_report.get('original_render') or {}).get('pages'),
        'candidate_pages': (repair_report.get('candidate_render') or {}).get('pages'),
        'original_pdf': (repair_report.get('original_render') or {}).get('pdf'),
        'candidate_pdf': (repair_report.get('candidate_render') or {}).get('pdf'),
    }
    report['workflow_stages'] = repair_report['stages']
    stage('candidate_built')

    if repair_report['invariant_failures']:
        return fail(diagnosis.INVALID_OPERATION,
                    'preservation invariants failed: '
                    + ', '.join(repair_report['invariant_failures']))

    report['outcome'] = AWAITING_REVIEW
    report['note'] = ('A candidate exists for review. It is not accepted: open it in '
                      'Word, compare it with the original, and accept it as a separate '
                      'step. The original file is untouched and is the undo path.')
    stage(AWAITING_REVIEW)
    return _finish(report, work_dir)


def _finish(report, work_dir):
    assert report['accepted'] is False, 'the pipeline never accepts a repair'
    (work_dir / 'pipeline.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


def _summary(report):
    lines = ['mode:     %s' % report['mode'],
             'outcome:  %s' % report['outcome']]
    found = report['diagnosis']
    if found:
        lines.append('status:   %s' % found['status'])
        lines.append('says:     %s' % found['explanation'])
        if found['question']:
            lines.append('asks:     %s' % found['question'])
        if found['evidence']:
            lines.append('evidence: ' + ', '.join('p%d.%s' % (e['paragraph_id'], e['field'])
                                                  for e in found['evidence']))
        for op in found['operations']:
            lines.append('proposes: paragraph %d %s -> %r'
                         % (op['paragraph_id'], op['field'], op['value']))
    if report['error_code']:
        lines.append('error:    [%s] %s' % (report['error_code'], report['error']))
    metadata = report['diagnosis_metadata'] or {}
    if metadata.get('model'):
        lines.append('model:    %s (%.1fs)' % (metadata['model'],
                                               metadata.get('latency_seconds') or 0))
    if report['candidate']:
        lines.append('candidate: %s' % report['candidate'])
    preview = report.get('preview') or {}

    def images(pages):
        return ', '.join(page['image'] for page in pages)

    if preview.get('original_pages'):
        lines.append('before:   %s' % images(preview['original_pages']))
    if preview.get('candidate_pages'):
        lines.append('after:    %s' % images(preview['candidate_pages']))
    comparison = report.get('visual_comparison')
    if comparison:
        lines.append('pages:    %s -> %s (review signal, not approval)'
                     % (comparison.get('before_pages'), comparison.get('after_pages')))
    lines.append('accepted: False (acceptance is a separate, human step)')
    return '\n'.join(lines)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(
        description='Diagnose one paragraph complaint with local Gemma and build a '
                    'candidate to review. Never accepts or overwrites anything.')
    parser.add_argument('source', help='path to the .docx')
    parser.add_argument('paragraph_id', type=int,
                        help='top-level body paragraph id, as `engine.py inspect` reports')
    parser.add_argument('complaint', help='what looks wrong, in the user\'s own words')
    parser.add_argument('work_dir', nargs='?', default='work/pipeline',
                        help='where the candidate, previews and pipeline.json go')
    parser.add_argument('--no-render', action='store_true',
                        help='skip LibreOffice; previews are then reported as skipped')
    parser.add_argument('--model', help='override GEMMA_MODEL for this run')
    parser.add_argument('--timeout', type=float, default=None,
                        help='seconds to wait for the model (default %s)'
                             % gemma_adapter.DEFAULT_TIMEOUT)
    parser.add_argument('--context', type=int, default=1,
                        help='paragraphs of context either side of the selection')
    parser.add_argument('--json', action='store_true', help='print the full report')
    args = parser.parse_args()
    result = run(args.source, args.paragraph_id, args.complaint, args.work_dir,
                 do_render=not args.no_render, model=args.model, timeout=args.timeout,
                 context=args.context)
    print(json.dumps(result, indent=2) if args.json else _summary(result))
    raise SystemExit(0 if result['outcome'] != FAILED else 1)
