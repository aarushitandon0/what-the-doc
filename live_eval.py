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

import break_fixtures
import diagnosis
import engine
import eval_fixtures
import fixtures
import gemma_adapter
import pipeline
import section_fixtures

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


# A supplementary set, scored separately so the ten-case denominator above never
# changes. These are the five structural situations first probed one run each in
# TEST_RESULTS.md section 17.3 (break selected, break ambiguous, section supported,
# section intentional, section refused). Their expectations were fixed before any run
# in this harness and are not adjusted to fit an answer.
BREAKS = 'breaks'
SECTION_SUPPORTED = 'section_supported'
SECTION_INTENTIONAL = 'section_intentional'
SECTION_ODD_PAGE = 'section_odd_page'

STRUCTURAL_CASES = [
    {'id': 'break_selected_unwanted',
     'document': BREAKS,
     'paragraph_id': 2,
     'select_break': 'first_of_two',
     'complaint': 'There is a page break here I did not want and it leaves a half-empty '
                  'page. Please take out the break I selected.',
     'expect_statuses': ['proposed'],
     'expect_operation': {'operation': engine.REMOVE_BREAK, 'target': 'selected',
                          'expected_break_type': 'page'},
     'why': 'the person selected one explicit page break and called it unwanted'},

    {'id': 'break_ambiguous_two_in_paragraph',
     'document': BREAKS,
     'paragraph_id': 2,
     'select_break': None,
     'complaint': 'There is a page break in this paragraph that should not be here.',
     'expect_statuses': ['needs_clarification'],
     'expect_operation': None,
     'why': 'two removable page breaks in the paragraph, none selected, and the '
            'complaint does not say which: only a question is safe'},

    {'id': 'section_supported_selected',
     'document': SECTION_SUPPORTED,
     'paragraph_id': 0,
     'select_section': 'eligible',
     'complaint': 'Part Two should carry on down the same page instead of starting a '
                  'whole new one. Please make the section I selected continuous.',
     'expect_statuses': ['proposed'],
     'expect_operation': {'operation': engine.CHANGE_SECTION_START, 'target': 'selected',
                          'expected_start_type': 'nextPage',
                          'new_start_type': 'continuous'},
     'why': 'an eligible nextPage section the person selected and asked to continue'},

    {'id': 'section_intentional_unrelated_complaint',
     'document': SECTION_INTENTIONAL,
     'paragraph_id': 0,
     'select_section': 'eligible',
     'complaint': 'The spacing in this report looks uneven to me.',
     'expect_statuses': ['no_change', 'unsupported', 'needs_clarification'],
     'expect_operation': None,
     'why': 'an eligible but deliberate appendix break, and a complaint about '
            'something else: eligibility is not a reason to change it'},

    {'id': 'section_odd_page_refused',
     'document': SECTION_ODD_PAGE,
     'paragraph_id': 0,
     'select_section': None,
     'complaint': 'This section leaves a blank page before it. Stop it starting on a '
                  'new page.',
     'expect_statuses': ['unsupported', 'no_change'],
     'expect_operation': None,
     'why': 'an oddPage start the engine refuses outright; nothing is offered'},
]

EVIDENCE_MARKERS = ('evidence',)


def classify(result, expect_repair):
    """One failure category per graded run, so different failures are never pooled.

    pass                 the run passed under the case's scoring rule
    schema_failure       the answer was unusable for a reason other than its evidence
    evidence_failure     the answer was refused for its evidence (unsupplied,
                         malformed, duplicated or missing citations)
    incorrect_refusal    a repair was expected and the model abstained
    wrong_abstention     an abstention was right but it was the wrong kind (for
                         example no_change where a question was needed)
    unsafe_proposal      the model proposed an operation that was not the expected
                         one (any operation, when none was expected); `blocked`
                         records whether the system stopped it before review
    runtime_failure      timeout, unavailable model or renderer failure
    """
    if result.get('passed'):
        return 'pass'
    code = result.get('error_code')
    operations = result.get('operations') or []
    if code == diagnosis.INVALID_RESPONSE:
        message = (result.get('error') or '').lower()
        if any(marker in message for marker in EVIDENCE_MARKERS):
            return 'evidence_failure'
        return 'schema_failure'
    if operations and not result.get('operation_correct'):
        return 'unsafe_proposal'
    if result.get('forbidden_fields_touched'):
        return 'unsafe_proposal'
    if code in (diagnosis.MODEL_TIMEOUT, diagnosis.MODEL_UNAVAILABLE,
                diagnosis.RENDER_FAILURE):
        return 'runtime_failure'
    status = result.get('status')
    if status in pipeline.ABSTAINED:
        return 'incorrect_refusal' if expect_repair else 'wrong_abstention'
    return 'other_failure'


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
        'error': report.get('error'),
        'evidence': (found or {}).get('evidence'),
        'candidate_built': report.get('candidate') is not None,
        'passed': bool(schema_valid and status_correct and operation_correct
                       and not forbidden_touched
                       and preservation is not False),
    }



def grade_structural(case, report, selected_target):
    """Score a structural case. Same shape of record as `grade`, own matching rule.

    A structural repair names a server-issued target id rather than a paragraph field,
    so "the operation is correct" means: exactly one operation, of the expected kind,
    on the target the person selected, with the expected type fields - and a candidate
    that reached review with its invariants intact.
    """
    found = report.get('diagnosis')
    error_code = report.get('error_code')
    operations = (found or {}).get('operations') or []
    schema_valid = error_code != diagnosis.INVALID_RESPONSE
    status = (found or {}).get('status')
    status_correct = bool(status and status in case['expect_statuses'])
    expected = case['expect_operation']
    if expected is None:
        operation_correct = not operations and report.get('candidate') is None
    else:
        want = {k: v for k, v in expected.items() if k != 'target'}
        want['target_id'] = selected_target
        operation_correct = (len(operations) == 1
                             and all(operations[0].get(k) == v for k, v in want.items()))
    invariants = report.get('invariants')
    preservation = None if invariants is None else not report.get('invariant_failures')
    repaired = report.get('outcome') == pipeline.AWAITING_REVIEW
    metadata = report.get('diagnosis_metadata') or {}
    passed = bool(schema_valid and status_correct and operation_correct
                  and preservation is not False
                  and (expected is None or repaired))
    return {
        'case': case['id'], 'why': case['why'], 'paragraph_id': case['paragraph_id'],
        'ran': True, 'outcome': report.get('outcome'), 'status': status,
        'error_code': error_code, 'error': report.get('error'),
        'schema_valid': schema_valid, 'status_correct': status_correct,
        'operation_correct': operation_correct,
        'abstention_correct': (not operations) if expected is None else None,
        'forbidden_fields_touched': [], 'preservation_ok': preservation,
        'pages': {'before': (report.get('visual_comparison') or {}).get('before_pages'),
                  'after': (report.get('visual_comparison') or {}).get('after_pages')},
        'latency_seconds': metadata.get('latency_seconds'),
        'prompt_tokens': metadata.get('prompt_eval_count'),
        'output_tokens': metadata.get('eval_count'),
        'explanation': (found or {}).get('explanation'),
        'question': (found or {}).get('question'),
        'evidence': (found or {}).get('evidence'),
        'operations': operations, 'candidate_built': report.get('candidate') is not None,
        'passed': passed,
    }


def not_run(case, reason):
    return {'case': case['id'], 'why': case['why'],
            'paragraph_id': case['paragraph_id'], 'ran': False,
            'result': 'not_run', 'reason': reason, 'passed': None}


def build_documents(work_dir):
    work_dir.mkdir(parents=True, exist_ok=True)
    built = {FAULTY: (work_dir/'sample-faulty.docx', fixtures.build),
             DIAGNOSTIC: (work_dir/'sample-diagnosis.docx', eval_fixtures.build),
             BREAKS: (work_dir/'sample-breaks.docx',
                      break_fixtures.build_manual_page_break),
             SECTION_SUPPORTED: (work_dir/'sample-section-supported.docx',
                                 section_fixtures.build_supported_next_page),
             SECTION_INTENTIONAL: (work_dir/'sample-section-intentional.docx',
                                   section_fixtures.build_intentional_section_break),
             SECTION_ODD_PAGE: (work_dir/'sample-section-odd-page.docx',
                                section_fixtures.build_odd_page_start)}
    documents = {}
    for name, (path, builder) in built.items():
        if not path.exists():
            builder(path)
        documents[name] = path
    return documents


def _structural_selection(case, source):
    """The break or section id the interface would send for this case, if any."""
    break_id = section_id = None
    if case.get('select_break') == 'first_of_two':
        breaks = [b for b in engine.inspect_breaks(source)['breaks']
                  if b['paragraph_id'] == case['paragraph_id'] and b['edit_eligible']]
        break_id = next(b['target_id'] for b in breaks
                        if b['ordinal_in_paragraph'] == 1)
    if case.get('select_section') == 'eligible':
        section_id = [x for x in engine.inspect_sections(source)['sections']
                      if x['edit_eligible']][-1]['target_id']
    return break_id, section_id


def _fraction(numerator, denominator):
    return '%d/%d' % (numerator, denominator)


def aggregate(results):
    """Counts with denominators over graded runs. Pure, so it is unit-tested offline."""
    ran = [r for r in results if r['ran']]
    expects_repair = [r for r in ran if r['expect_repair']]
    expects_abstain = [r for r in ran if not r['expect_repair']
                       and not r.get('expect_error')]
    categories = {}
    for r in ran:
        categories[r['category']] = categories.get(r['category'], 0) + 1
    unsafe = [r for r in ran if r['category'] == 'unsafe_proposal']
    latencies = sorted(r['latency_seconds'] for r in ran if r.get('latency_seconds'))
    return {
        'runs_total': len(results), 'runs_graded': len(ran),
        'runs_not_run': len(results) - len(ran),
        'passed': _fraction(sum(1 for r in ran if r['passed']), len(ran)),
        'schema_valid': _fraction(sum(1 for r in ran if r.get('schema_valid')), len(ran)),
        'valid_diagnosis': _fraction(sum(1 for r in ran if r.get('status_correct')),
                                     len(ran)),
        'appropriate_abstention': _fraction(
            sum(1 for r in expects_abstain if r['passed']), len(expects_abstain)),
        'successful_safe_repair': _fraction(
            sum(1 for r in expects_repair if r['passed']
                and r.get('outcome') == pipeline.AWAITING_REVIEW
                and r.get('preservation_ok') is not False), len(expects_repair)),
        'categories': categories,
        'unsafe_proposals': len(unsafe),
        'unsafe_proposals_reaching_review': sum(
            1 for r in unsafe if r.get('outcome') == pipeline.AWAITING_REVIEW),
        'preservation_failures': sum(1 for r in ran if r.get('preservation_ok') is False),
        'latency_seconds_median': latencies[len(latencies)//2] if latencies else None,
        'latency_seconds_max': latencies[-1] if latencies else None,
    }


def main(work_dir, model=None, timeout=None, do_render=False, only=None, context=1,
         repeat=1, include=('ten',), echo=True, use_precheck=False):
    work_dir = Path(work_dir)
    documents = build_documents(work_dir)
    runtime = gemma_adapter.runtime_status()
    live = bool(runtime['available'] and runtime.get('model_installed'))
    model_name = model or runtime.get('configured_model')

    plan = []
    if 'ten' in include:
        plan += [('ten', c) for c in CASES if not only or c['id'] in only]
    if 'structural' in include:
        plan += [('structural', c) for c in STRUCTURAL_CASES
                 if not only or c['id'] in only]

    results, started = [], time.monotonic()
    for attempt in range(1, repeat + 1):
        for case_set, case in plan:
            needs_model = case.get('requires_model', True)
            if needs_model and not live:
                record = not_run(case, runtime.get('reason')
                                 or 'no live Gemma runtime reachable')
            else:
                case_dir = work_dir/case['id']/('run-%d' % attempt)
                source = documents[case['document']]
                if case_set == 'ten':
                    report = pipeline.run(source, case['paragraph_id'],
                                          case['complaint'], case_dir,
                                          mode=pipeline.LIVE, do_render=do_render,
                                          model=model, timeout=timeout,
                                          context=context, use_precheck=use_precheck)
                    record = grade(case, report)
                    expect_repair = case['expect_repair'] is not None
                else:
                    break_id, section_id = _structural_selection(case, source)
                    # A structural candidate cannot reach review without a rendering,
                    # so these cases always render.
                    report = pipeline.run(source, case['paragraph_id'],
                                          case['complaint'], case_dir,
                                          mode=pipeline.LIVE, do_render=True,
                                          model=model, timeout=timeout,
                                          context=context, break_target_id=break_id,
                                          section_target_id=section_id,
                                          use_precheck=use_precheck)
                    record = grade_structural(case, report, break_id or section_id)
                    expect_repair = case['expect_operation'] is not None
                record['category'] = classify(record, expect_repair)
                record['expect_repair'] = expect_repair
                record['expect_error'] = bool(case.get('expect_error_codes'))
            record['set'] = case_set
            record['attempt'] = attempt
            results.append(record)
            if echo:
                print('run %d  %-42s %-22s %s' % (
                    attempt, case['id'],
                    record.get('status') or '[%s]' % record.get('error_code'),
                    record.get('category', 'not_run')), flush=True)

    per_case = {}
    for r in results:
        entry = per_case.setdefault(r['case'], {'set': r['set'], 'runs': 0,
                                                'passed': 0, 'answers': []})
        entry['runs'] += 1
        entry['passed'] += 1 if r.get('passed') else 0
        entry['answers'].append(r.get('status') or r.get('error_code') or 'not_run')

    summary = {
        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'live': live,
        'runtime': runtime,
        'provenance': gemma_adapter.runtime_provenance(model_name),
        'model': model_name,
        'rendered': do_render,
        'context_paragraphs_either_side': context,
        'repeat': repeat,
        'sets': list(include),
        'ten_case': aggregate([r for r in results if r['set'] == 'ten']),
        'structural': aggregate([r for r in results if r['set'] == 'structural']),
        'per_case': per_case,
        'wall_clock_seconds': round(time.monotonic() - started, 1),
        'note': ('Counts are numerator/denominator over runs (cases x repeats) on one '
                 'machine with synthetic fixtures. The ten-case set and the structural '
                 'set are scored separately and never pooled. Cases marked not_run were '
                 'never graded: no mocked answer is ever counted as a pass.'),
        'results': results,
    }
    (work_dir/'live-eval.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    return summary


def table(summary):
    rows = ['| set | case | runs passed | answers |', '| --- | --- | --- | --- |']
    for name, entry in summary['per_case'].items():
        rows.append('| %s | %s | %d/%d | %s |' % (entry['set'], name, entry['passed'],
                                                  entry['runs'],
                                                  ', '.join(entry['answers'])))
    return '\n'.join(rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Grade the live Gemma diagnosis layer on labelled cases. '
                    'Cases needing a model are reported not_run when none is reachable.')
    parser.add_argument('work_dir', nargs='?', default='work/live-eval')
    parser.add_argument('--model', help='override GEMMA_MODEL for this run')
    parser.add_argument('--timeout', type=float, default=None)
    parser.add_argument('--render', action='store_true',
                        help='also render before/after for proposing ten-case runs (slow)')
    parser.add_argument('--case', action='append', dest='only',
                        help='run only this case id; repeatable')
    parser.add_argument('--context', type=int, default=1)
    parser.add_argument('--repeat', type=int, default=1,
                        help='run every selected case this many times')
    parser.add_argument('--structural', action='store_true',
                        help='also run the separately scored structural set')
    parser.add_argument('--with-precheck', action='store_true',
                        help='let the fixed spacing rule answer before the model (off by '
                             'default, so the numbers keep measuring the model alone)')
    parser.add_argument('--structural-only', action='store_true',
                        help='run only the structural set')
    args = parser.parse_args()
    include = (('structural',) if args.structural_only
               else ('ten', 'structural') if args.structural else ('ten',))
    result = main(args.work_dir, model=args.model, timeout=args.timeout,
                  do_render=args.render, only=args.only, context=args.context,
                  repeat=args.repeat, include=include,
                  use_precheck=args.with_precheck)
    print(table(result))
    print()
    print(json.dumps({k: v for k, v in result.items()
                      if k not in ('results', 'per_case')}, indent=2))
    if not result['live']:
        print('\nNo live Gemma runtime: some runs were recorded not_run, none simulated.')
