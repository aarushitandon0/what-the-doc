"""Independent verification against Microsoft Word, where Word is available.

LibreOffice renders the preview, but the user opens the file in Word, and Word is also
the authority on what a paragraph's formatting actually resolves to. This module asks
Word directly and compares its answers with the resolver in styles.py.

It is Windows-and-Word-only and entirely optional: every caller must treat an
unavailable result as "not verified", never as verified. Word is opened read-only with
macros force-disabled and is always quit without saving; the script refuses to run at
all if Word is already open, so it cannot close a document the user has in front of them.
"""
import json
import subprocess
import sys
from pathlib import Path

import engine

SCRIPT = Path(__file__).with_name('word_check.ps1')
TIMEOUT_SECONDS = 180
# Fields Word reports as effective values, mapped to this project's names.
COMPARABLE = ('space_before_pt', 'space_after_pt', 'left_indent_pt',
              'alignment', 'page_break_before', 'keep_with_next')


def available():
    return sys.platform == 'win32' and SCRIPT.exists()


def observe(path, paragraph_ids=(), pdf=None):
    """Ask Word what it sees. Returns {'available': False, 'reason': ...} if it cannot."""
    if not available():
        return {'available': False, 'reason': 'Microsoft Word automation needs Windows'}
    command = ['powershell.exe', '-NoProfile', '-NonInteractive',
               '-ExecutionPolicy', 'Bypass', '-File', str(SCRIPT),
               '-Path', str(Path(path).resolve())]
    if paragraph_ids:
        command += ['-Paragraphs', ','.join(str(i) for i in paragraph_ids)]
    if pdf:
        command += ['-Pdf', str(Path(pdf).resolve())]
    try:
        done = subprocess.run(command, capture_output=True, timeout=TIMEOUT_SECONDS,
                              stdin=subprocess.DEVNULL, check=False)
    except subprocess.TimeoutExpired:
        return {'available': False, 'reason': 'Word did not respond within %ss' % TIMEOUT_SECONDS}
    text = (done.stdout or b'').decode('utf-8', 'replace').strip().splitlines()
    for line in reversed(text):
        try:
            return json.loads(line)
        except ValueError:
            continue
    return {'available': False,
            'reason': 'No JSON from Word automation (exit %s) %s'
                      % (done.returncode, (done.stderr or b'').decode('utf-8', 'replace')[:300])}


def compare_with_resolver(path, paragraph_ids, tolerance=0.05):
    """Check styles.py against Word for the given paragraphs.

    A field is only compared where both sides state a value. Where this project reports
    application_default (nothing set it) Word still reports its own built-in number, so
    that pair is recorded as not_compared rather than counted as a disagreement.
    """
    observed = observe(path, paragraph_ids)
    if not observed.get('available'):
        return {'available': False, 'reason': observed.get('reason'), 'agreements': [], 'disagreements': []}
    resolved = engine.inspect(path)['paragraphs']
    agreements, disagreements, skipped = [], [], []
    for paragraph_id in paragraph_ids:
        word_values = observed['paragraphs'][str(paragraph_id)]
        ours = resolved[paragraph_id]['effective_properties']
        for field in COMPARABLE:
            mine, theirs = ours[field]['value'], word_values[field]
            entry = {'paragraph_id': paragraph_id, 'field': field,
                     'resolver': mine, 'word': theirs, 'origin': ours[field]['origin']}
            if mine is None:
                skipped.append(dict(entry, why='unset here; Word reports its built-in default'))
            elif isinstance(mine, bool) or isinstance(theirs, bool):
                (agreements if bool(mine) == bool(theirs) else disagreements).append(entry)
            elif isinstance(mine, (int, float)) and isinstance(theirs, (int, float)):
                (agreements if abs(mine - theirs) <= tolerance else disagreements).append(entry)
            else:
                (agreements if mine == theirs else disagreements).append(entry)
    return {'available': True, 'pages': observed.get('pages'),
            'paragraph_count': observed.get('paragraph_count'),
            'compatibility_mode': observed.get('compatibility_mode'),
            'agreements': agreements, 'disagreements': disagreements, 'not_compared': skipped,
            'summary': '%s fields agree, %s disagree, %s not comparable'
                       % (len(agreements), len(disagreements), len(skipped))}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Compare styles.py resolution with Word.')
    parser.add_argument('source')
    parser.add_argument('paragraph_ids', nargs='*', type=int)
    args = parser.parse_args()
    ids = args.paragraph_ids or list(range(len(engine.inspect(args.source)['paragraphs'])))
    print(json.dumps(compare_with_resolver(args.source, ids), indent=2))
