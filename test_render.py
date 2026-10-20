"""Injected subprocess failures, distinct from the real LibreOffice rendering tests.

The boundary wrapper launches a real Python helper with subprocess.run. It does not
mock docx_to_pdf or its result, and does not claim to reproduce LibreOffice internals.
"""
from contextlib import contextmanager
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import pypdfium2 as pdfium

import fixtures
import render


_HELPER = '''
from pathlib import Path
import shutil
import sys
import time

mode, template, fail_on = sys.argv[1:4]
arguments = sys.argv[4:]
source = Path(arguments[-1])
phase = 'candidate' if source.stem.startswith('candidate-') else 'original'
if fail_on != 'all' and fail_on != phase:
    mode = 'valid'
target = Path(arguments[arguments.index('--outdir') + 1]) / (source.stem + '.pdf')
if mode == 'timeout':
    time.sleep(10)
elif mode == 'missing':
    pass
elif mode == 'empty':
    target.write_bytes(b'')
elif mode == 'invalid':
    target.write_bytes(b'This is not a PDF')
elif mode == 'exit_with_pdf':
    shutil.copyfile(template, target)
    print('injected conversion failure', file=sys.stderr)
    sys.exit(23)
elif mode == 'exit':
    print('injected conversion failure', file=sys.stderr)
    sys.exit(23)
else:
    shutil.copyfile(template, target)
'''


@contextmanager
def injected_renderer(root, mode, fail_on=None):
    """Exercise production conversion through a controlled, real child process."""
    with tempfile.TemporaryDirectory(prefix='renderer-helper-', dir=root) as tmp:
        helper = Path(tmp) / 'convert.py'
        helper.write_text(_HELPER, encoding='utf-8')
        template = Path(tmp) / 'template.pdf'
        with pdfium.PdfDocument.new() as document:
            document.new_page(200, 300)
            document.save(str(template))
        run_process = subprocess.run
        calls = []

        def launch(command, **kwargs):
            calls.append(command)
            # Preserve production arguments and subprocess options; only substitute
            # the controlled renderer executable at this boundary.
            return run_process([sys.executable, str(helper), mode, str(template),
                                fail_on or 'all', *command[1:]], **kwargs)

        settle = 0 if mode in ('missing', 'empty') else 2
        with patch.object(render, 'find_soffice', return_value=sys.executable), \
                patch.object(render.subprocess, 'run', side_effect=launch), \
                patch.object(render, 'SETTLE_SECONDS', settle):
            yield calls


class RendererBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'sample.docx'
        fixtures.build(self.source)
        self.before = self.source.read_bytes()
        self.output = self.root / 'output'

    def convert(self, mode, **kwargs):
        with injected_renderer(self.root, mode):
            return render.docx_to_pdf(self.source, self.output, **kwargs)

    def test_missing_executable_remains_renderer_unavailable(self):
        with patch.dict('os.environ', {'SOFFICE_PATH': str(self.root / 'absent')}):
            with self.assertRaises(render.RendererUnavailable):
                render.docx_to_pdf(self.source, self.output)

    def test_nonzero_exit_reports_the_actual_exit_code(self):
        with self.assertRaisesRegex(render.RenderFailed, r'exit 23.*injected conversion failure'):
            self.convert('exit')
        self.assertFalse((self.output / 'sample.pdf').exists())
        self.assertEqual(self.source.read_bytes(), self.before)

    def test_nonzero_exit_is_refused_even_if_it_wrote_a_valid_pdf(self):
        with self.assertRaisesRegex(render.RenderFailed, 'exit 23'):
            self.convert('exit_with_pdf')
        self.assertFalse((self.output / 'sample.pdf').exists())

    def test_a_real_subprocess_timeout_is_reported_and_stopped(self):
        with self.assertRaisesRegex(render.RenderFailed, 'exceeded 0.2s and was stopped') as caught:
            self.convert('timeout', timeout=0.2)
        self.assertIsInstance(caught.exception.__cause__, subprocess.TimeoutExpired)

    def test_successful_exit_without_output_is_a_failure(self):
        with self.assertRaisesRegex(render.RenderFailed, 'No PDF produced'):
            self.convert('missing')

    def test_empty_output_is_distinct_from_missing_output(self):
        with self.assertRaisesRegex(render.RenderFailed, 'Empty PDF produced'):
            self.convert('empty')

    def test_non_pdf_output_is_normalized_to_render_failed(self):
        with self.assertRaisesRegex(render.RenderFailed, 'Invalid PDF output') as caught:
            self.convert('invalid')
        self.assertIsInstance(caught.exception.__cause__, pdfium.PdfiumError)
        self.assertFalse((self.output / 'sample.pdf').exists())

    def test_a_failed_retry_cannot_reuse_or_overwrite_an_old_pdf(self):
        prior = self.convert('valid')
        before = prior.read_bytes()
        with self.assertRaisesRegex(render.RenderFailed, 'No PDF produced'):
            self.convert('missing')
        self.assertEqual(prior.read_bytes(), before)

    def test_an_existing_but_unlaunchable_renderer_is_a_render_failure(self):
        # A directory passes existence discovery but cannot be launched. This is an
        # actual OS error, not a fabricated subprocess result.
        with patch.dict('os.environ', {'SOFFICE_PATH': str(self.root)}):
            with self.assertRaisesRegex(render.RenderFailed, 'Could not launch') as caught:
                render.docx_to_pdf(self.source, self.output)
        self.assertIsInstance(caught.exception.__cause__, OSError)

    def test_valid_fresh_output_is_published_and_staging_is_removed(self):
        with injected_renderer(self.root, 'valid') as calls:
            target = render.docx_to_pdf(self.source, self.output)
        self.assertEqual(target, self.output / 'sample.pdf')
        with pdfium.PdfDocument(str(target)) as document:
            self.assertEqual(len(document), 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-1], str(self.source))
        self.assertIn('--headless', calls[0])
        self.assertEqual(list(self.output.iterdir()), [target])
        self.assertEqual(self.source.read_bytes(), self.before)


if __name__ == '__main__':
    unittest.main()
