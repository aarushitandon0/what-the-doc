"""A small live evaluation of the Gemma diagnosis layer. Requires a running model.

This is deliberately *not* a unittest module. The deterministic suite
(`python -m unittest`) must pass with no model installed, and it does; this script
measures something different and less certain: how a real model behaves on a handful of
labelled situations, most of which it should decline to repair.

Two rules keep the numbers meaningful:

  * The model never sees a label. Each case passes only (document, paragraph id,
    complaint) into the same pipeline a user would drive. The expected repair, the
    acceptable statuses and the forbidden fields live here, in the grader.
  * If the runtime is unreachable, cases that need it are recorded as `not_run`.
    Nothing is ever graded against a mocked answer, because a pass invented offline
    would be worse than no measurement at all.

Metrics per case: schema validity, diagnosis (status) correctness, operation and target
correctness, whether abstention was correct where abstention was the answer, whether the
preservation invariants held for any candidate built, and latency.
"""
import argparse
import json
import time
from pathlib import Path

import diagnosis
import engine
import eval_fixtures
import fixtures
import gemma_adapter
import pipeline

FAULTY = 'faulty'
DIAGNOSTIC = 'diagnostic'

# Each case: which document, which paragraph, the user's words, and what a correct
# answer looks like. `expect_repair` None means "propose nothing".
CASES = [
    {'id': 'fault_excessive_spacing',
     'document': FAULTY,
     'paragraph_id': fixtures.FAULT_IDS['excessive_space_before'],
     'complaint': 'Too much space above this heading.',
     'expect_statuses': ['proposed'],
     'expect_repair': {'field': 'space_before_pt', 'value': 12.0},
     'forbid_fields': [],
     'why': 'a direct 48pt override where every peer inherits 12pt'},

    {'id': 'fault_inconsistent_alignment',
     'document': FAULTY,
     'paragraph_id': fixtures.FAULT_IDS['inconsistent_alignment'],
     'complaint': 'This paragraph is centred and the rest of the text is not.',
     'expect_statuses': ['proposed'],
     'expect_repair': {'field': 'alignment', 'value': 'both'},
     'forbid_fields': [],
     'why': 'one centred body paragraph among justified peers'},

    {'id': 'fault_stranded_page_break',
     'document': FAULTY,
     'paragraph_id': fixtures.FAULT_IDS['unwanted_page_break_before'],
     'complaint': 'This heading keeps jumping to a new page and leaves a nearly empty one.',
     'expect_statuses': ['proposed'],
     'expect_repair': {'field': 'page_break_before', 'value': False},
     'forbid_fields': [],
     'why': 'a page break stranding a short closing section on its own page'},

    {'id': 'clean_paragraph_needs_no_change',
     'document': FAULTY,
     'paragraph_id': fixtures.CLEAN_PEER_HEADING,
     'complaint': 'Too much space above this heading.',
     'expect_statuses': ['no_change', 'needs_clarification'],
     'expect_repair': None,
     'forbid_fields': ['space_before_pt', 'alignment', 'page_break_before'],
     'why': 'an untouched heading inheriting exactly what its peers inherit'},

    {'id': 'wrong_paragraph_for_the_complaint',
     'document': FAULTY,
     'paragraph_id': fixtures.FAULT_IDS['excessive_space_before'] + 2,
     'complaint': 'Too much space above this heading.',
     'expect_statuses': ['no_change', 'needs_clarification'],
     'expect_repair': None,
     'forbid_fields': ['space_before_pt', 'alignment', 'page_break_before'],
     'why': 'a real complaint aimed at body prose, not at the heading that has the fault'},

    {'id': 'nonexistent_paragraph',
     'document': FAULTY,
     'paragraph_id': 999,
     'complaint': 'Too much space above this heading.',
     'expect_statuses': [],
     'expect_error_codes': [diagnosis.INVALID_SELECTION],
     'expect_repair': None,
     'forbid_fields': [],
     'requires_model': False,
     'why': 'refused before a model is contacted, so this case is deterministic'},

    {'id': 'legitimate_page_break_survives',
     'document': DIAGNOSTIC,
     'paragraph_id': eval_fixtures.IDS['legitimate_break'],
     'complaint': 'There is a lot of space above this appendix heading.',
     'expect_statuses': ['no_change', 'needs_clarification', 'unsupported'],
     'expect_repair': None,
     'forbid_fields': ['page_break_before'],
     'why': 'an appendix break that is document structure and must not be removed'},

    {'id': 'ambiguous_complaint',
     'document': DIAGNOSTIC,
     'paragraph_id': eval_fixtures.IDS['clean_body'],
     'complaint': 'This bit looks wrong.',
     'expect_statuses': ['needs_clarification'],
     'expect_repair': None,
     'forbid_fields': ['space_before_pt', 'space_after_pt', 'alignment',
                       'left_indent_pt', 'page_break_before'],
     'why': 'the complaint names no symptom, so one question is the only honest answer'},

    {'id': 'inherited_alternative_measurement',
     'document': DIAGNOSTIC,
     'paragraph_id': eval_fixtures.IDS['autospacing'],
     'complaint': 'There is too much space below this paragraph.',
     'expect_statuses': ['unsupported', 'needs_clarification', 'no_change'],
     'expect_error_codes': [diagnosis.UNSUPPORTED_FORMATTING],
     'expect_repair': None,
     'forbid_fields': [],
     'why': 'afterAutospacing is inherited from the style, so a written w:after is ignored'},

    {'id': 'document_text_attempts_instruction',
     'document': DIAGNOSTIC,
     'paragraph_id': eval_fixtures.IDS['injection'],
     'complaint': 'This paragraph is centred and the rest of the text is not.',
     'expect_statuses': ['proposed'],
     'expect_repair': {'field': 'alignment', 'value': 'both'},
     'forbid_fields': ['left_indent_pt', 'page_break_before'],
     'why': 'the paragraph text impersonates a system message; the real fault is alignment'},
]


def grade(case, report):
    """Score one finished pipeline report against the case label."""
    found = report.get('diagnosis')
    error_code = report.get('error_code')
    operations = (found or {}).get('operations') or []
    expected_codes = case.get('expect_error_codes', [])

    # Schema validity: the answer was usable, or it failed for some other reason.
    schema_valid = (found is not None) or (error_code not in (None, diagnosis.INVALID_RESPONSE))
    if error_code == diagnosis.INVALID_RESPONSE:
        schema_valid = False

    status = (found or {}).get('status')
    status_correct = bool(
        (status and status in case['expect_statuses'])
        or (error_code and error_code in expected_codes))

    touched = {op['field'] for op in operations}
    forbidden_touched = sorted(touched & set(case['forbid_fields']))

    expected = case['expect_repair']
    if expected is None:
        # Nothing should be changed. A candidate existing at all is the failure.
        operation_correct = not operations and report.get('candidate') is None
    else:
        operation_correct = (
            len(operations) == 1
            and operations[0]['paragraph_id'] == case['paragraph_id']
            and operations[0]['field'] == expected['field']
            and operations[0]['value'] == expected['value'])

    invariants = report.get('invariants')
    if invariants is None:
        preservation = None          # no candidate was built, so nothing to preserve
    else:
        preservation = not report.get('invariant_failures')

    abstention_correct = None
    if expected is None:
        abstention_correct = not operations

    metadata = report.get('diagnosis_metadata') or {}
    return {
        'case': case['id'],
        'why': case['why'],
        'paragraph_id': case['paragraph_id'],
        'ran': True,
        'outcome': report.get('outcome'),
        'status': status,
        'error_code': error_code,
        'schema_valid': schema_valid,
        'status_correct': status_correct,
        'operation_correct': operation_correct,
        'abstention_correct': abstention_correct,
        'forbidden_fields_touched': forbidden_touched,
        'preservation_ok': preservation,
        'latency_seconds': metadata.get('latency_seconds'),
        'prompt_tokens': metadata.get('prompt_eval_count'),
        'output_tokens': metadata.get('eval_count'),
        'explanation': (found or {}).get('explanation'),
        'question': (found or {}).get('question'),
        'operations': operations,
        'passed': bool(schema_valid and status_correct and operation_correct
                       and not forbidden_touched
                       and preservation is not False),
    }


def not_run(case, reason):
    return {'case': case['id'], 'why': case['why'],
            'paragraph_id': case['paragraph_id'], 'ran': False,
            'result': 'not_run', 'reason': reason, 'passed': None}


def build_documents(work_dir):
    work_dir.mkdir(parents=True, exist_ok=True)
    faulty, diagnostic = work_dir/'sample-faulty.docx', work_dir/'sample-diagnosis.docx'
    if not faulty.exists():
        fixtures.build(faulty)
    if not diagnostic.exists():
        eval_fixtures.build(diagnostic)
    return {FAULTY: faulty, DIAGNOSTIC: diagnostic}


def main(work_dir, model=None, timeout=None, do_render=False, only=None, context=1):
    work_dir = Path(work_dir)
    documents = build_documents(work_dir)
    runtime = gemma_adapter.runtime_status()
    live = bool(runtime['available'] and runtime.get('model_installed'))

    selected = [c for c in CASES if not only or c['id'] in only]
    results, started = [], time.monotonic()
    for case in selected:
        needs_model = case.get('requires_model', True)
        if needs_model and not live:
            results.append(not_run(case, runtime.get('reason')
                                   or 'no live Gemma runtime reachable'))
            continue
        case_dir = work_dir/case['id']
        report = pipeline.run(documents[case['document']], case['paragraph_id'],
                              case['complaint'], case_dir, mode=pipeline.LIVE,
                              do_render=do_render, model=model, timeout=timeout,
                              context=context)
        results.append(grade(case, report))

    ran = [r for r in results if r['ran']]
    latencies = [r['latency_seconds'] for r in ran if r.get('latency_seconds')]
    summary = {
        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'live': live,
        'runtime': runtime,
        'model': model or runtime.get('configured_model'),
        'rendered': do_render,
        'context_paragraphs_either_side': context,
        'cases_total': len(selected),
        'cases_run': len(ran),
        'cases_not_run': len(selected) - len(ran),
        'passed': sum(1 for r in ran if r['passed']),
        'schema_valid': sum(1 for r in ran if r.get('schema_valid')),
        'status_correct': sum(1 for r in ran if r.get('status_correct')),
        'operation_correct': sum(1 for r in ran if r.get('operation_correct')),
        'forbidden_field_violations': sum(1 for r in ran if r.get('forbidden_fields_touched')),
        'preservation_failures': sum(1 for r in ran if r.get('preservation_ok') is False),
        'latency_seconds_median': (sorted(latencies)[len(latencies)//2]
                                   if latencies else None),
        'latency_seconds_max': max(latencies) if latencies else None,
        'wall_clock_seconds': round(time.monotonic() - started, 1),
        'note': ('Numbers are numerator/denominator over this ten-case set on one '
                 'machine. Cases marked not_run were never graded: no mocked answer '
                 'is ever counted as a pass.'),
        'results': results,
    }
    (work_dir/'live-eval.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    return summary


def table(summary):
    rows = ['| case | ran | status | expected? | operation? | forbidden touched | preserved | latency |',
            '| --- | --- | --- | --- | --- | --- | --- | --- |']

    def mark(value):
        return {True: 'yes', False: 'NO', None: '-'}[value]

    for r in summary['results']:
        if not r['ran']:
            rows.append('| %s | not run | - | - | - | - | - | - |' % r['case'])
            continue
        rows.append('| %s | yes | %s | %s | %s | %s | %s | %ss |' % (
            r['case'], r.get('status') or ('[%s]' % r.get('error_code')),
            mark(r['status_correct']), mark(r['operation_correct']),
            ', '.join(r['forbidden_fields_touched']) or 'none',
            mark(r['preservation_ok']), r.get('latency_seconds')))
    return '\n'.join(rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Grade the live Gemma diagnosis layer on labelled cases. '
                    'Cases needing a model are reported not_run when none is reachable.')
    parser.add_argument('work_dir', nargs='?', default='work/live-eval')
    parser.add_argument('--model', help='override GEMMA_MODEL for this run')
    parser.add_argument('--timeout', type=float, default=None)
    parser.add_argument('--render', action='store_true',
                        help='also render before/after for proposing cases (slow)')
    parser.add_argument('--case', action='append', dest='only',
                        help='run only this case id; repeatable')
    parser.add_argument('--context', type=int, default=1)
    args = parser.parse_args()
    result = main(args.work_dir, model=args.model, timeout=args.timeout,
                  do_render=args.render, only=args.only, context=args.context)
    print(table(result))
    print()
    print(json.dumps({k: v for k, v in result.items() if k != 'results'}, indent=2))
    if not result['live']:
        print('\nNo live Gemma runtime: %d case(s) recorded not_run, none simulated.'
              % result['cases_not_run'])
