"""Milestone B walkthrough on the synthetic sample document.

For each labelled fault: show the resolved evidence a diagnosis would rely on, apply
only the single expected repair, render before and after, and report what changed.

This is a known-answer harness, so "the repair worked" means the resolved property
moved to the expected value, the invariants held and the rendering changed where it
should. It says nothing about a model's ability to pick that repair unaided, and the
rendered pages approximate Word rather than reproduce it.

    python demo.py work/demo            # renders if LibreOffice is available
    python demo.py work/demo --no-render
"""
import argparse
import json
from pathlib import Path

import engine
import fixtures
import render as render_module
import word_check
import workflow


def evidence(source, paragraph_id, field):
    """What the diagnosis layer gets to see: resolved value, its origin, and peers."""
    record = engine.inspect(source)['paragraphs'][paragraph_id]
    resolved = record['effective_properties'][field]
    comparison = engine.peers(source, paragraph_id)['comparison'][field]
    return {'text_preview': record['text'][:60].strip(),
            'style_chain': record['style_chain'],
            'resolved': {'value': resolved['value'], 'origin': resolved['origin']},
            'peers': comparison['peer_values'],
            'caveats': record['resolution_caveats']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('work_dir', nargs='?', default='work/demo')
    parser.add_argument('--no-render', action='store_true')
    parser.add_argument('--word', action='store_true',
                        help='also open the documents in Microsoft Word and compare '
                             'its resolved formatting and page count with ours')
    args = parser.parse_args()

    work = Path(args.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    source = work/'sample-faulty.docx'
    fixtures.build(source)
    status = render_module.renderer_status()

    print('Sample document : %s' % source)
    print('Source sha256   : %s' % engine.signature(source))
    print('Renderer        : %s' % (status['binary'] or 'not available - visual checks will be skipped'))

    summary = []
    for paragraph_id, fault in sorted(fixtures.FAULTS.items()):
        field = fault['expected_repair']['field']
        value = fault['expected_repair']['value']
        print('\n' + '=' * 78)
        print('Paragraph %s  %s' % (paragraph_id, fault['fault']))
        print('User says       : "%s"' % fault['symptom'])
        print('Injected        : %s' % fault['injected'])
        found = evidence(source, paragraph_id, field)
        peers_text = ', '.join('%r x%s' % (p['value'], p['count']) for p in found['peers']) or 'none'
        print('Paragraph text  : %s' % (found['text_preview'] or '(empty)'))
        print('Style chain     : %s' % (' -> '.join(found['style_chain']) or 'none'))
        print('Resolved %-7s: %r from %s' % (field, found['resolved']['value'], found['resolved']['origin']))
        print('Same-style peers: %s' % peers_text)
        if found['caveats']:
            print('Caveats         : %s' % '; '.join(found['caveats']))
        print('Repair applied  : %s -> %r' % (field, value))

        plan = {'source_sha256': engine.signature(source),
                'operations': [{'paragraph_id': paragraph_id, 'field': field, 'value': value}]}
        report = workflow.repair(source, plan, work/('fault-%s' % paragraph_id),
                                 do_render=not args.no_render)

        resolved_after = engine.inspect(report['candidate'])['paragraphs'][paragraph_id][
            'effective_properties'][field]
        reached = resolved_after['value'] == value
        print('Resolved after  : %r from %s  (expected %r: %s)'
              % (resolved_after['value'], resolved_after['origin'], value,
                 'reached' if reached else 'NOT REACHED'))
        print('Invariants      : %s' % ('all passed' if not report['invariant_failures']
                                        else 'FAILED ' + ', '.join(report['invariant_failures'])))
        comparison = report['visual_comparison']
        if comparison:
            changed = [(p['page'], p.get('changed_ratio'), p['status']) for p in comparison['pages']]
            print('Pages           : %s -> %s (delta %+d)'
                  % (comparison['before_pages'], comparison['after_pages'],
                     comparison['page_count_delta']))
            for page, ratio, state in changed:
                detail = ('%.3f%% pixels changed' % (ratio * 100)) if ratio is not None else state
                print('  page %-2s       : %s' % (page, detail))
        else:
            print('Pages           : not rendered')
        print('Outcome         : %s' % report['outcome'])
        word = None
        if args.word:
            word = word_check.compare_with_resolver(report['candidate'], [paragraph_id])
            if word['available']:
                print('Word check      : %s; Word paginates the candidate to %s pages'
                      % (word['summary'], word['pages']))
                for item in word['disagreements']:
                    print('  DISAGREEMENT  : %s resolver=%r word=%r (origin %s)'
                          % (item['field'], item['resolver'], item['word'], item['origin']))
            else:
                print('Word check      : unavailable (%s)' % word['reason'])

        summary.append({'paragraph_id': paragraph_id, 'fault': fault['fault'],
                        'expected_repair_reached': reached,
                        'invariants_passed': not report['invariant_failures'],
                        'page_count_delta': comparison['page_count_delta'] if comparison else None,
                        'rendered': bool(comparison),
                        'word_agreements': word['summary'] if word and word['available'] else None,
                        'word_disagreements': len(word['disagreements']) if word and word['available'] else None,
                        'word_pages': word['pages'] if word and word['available'] else None,
                        'libreoffice_pages': comparison['after_pages'] if comparison else None,
                        'candidate': report['candidate']})

    print('\n' + '=' * 78)
    print(json.dumps({'summary': summary,
                      'original_sha256_after_all_runs': engine.signature(source),
                      'visual_verification': 'rendered for review' if not args.no_render and status['available']
                                             else 'skipped: no renderer',
                      'word_verification': ('Word opened each candidate and its resolved formatting '
                                            'was compared with ours; page counts above are each '
                                            "application's own pagination")
                                           if args.word else
                                           'not performed; pass --word, or open the candidates in Word'},
                     indent=2))
    (work/'demo-summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
