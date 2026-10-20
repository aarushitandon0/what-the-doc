"""Tests for the rendering workflow.

The rendering cases are skipped, never faked, when LibreOffice is absent. A skipped
visual check must not be reportable as a passing one.
"""
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from zipfile import ZipFile

import pypdfium2 as pdfium

import engine
import fixtures
import render as render_module
import word_check
import workflow

RENDERER = render_module.find_soffice()
needs_renderer = unittest.skipIf(RENDERER is None, 'LibreOffice not installed')


def word_usable():
    """Word automation is only attempted when no Word session is already open."""
    if not word_check.available():
        return False
    try:
        import subprocess
        done = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq WINWORD.EXE'],
                              capture_output=True, timeout=30, check=False)
        return b'WINWORD.EXE' not in (done.stdout or b'')
    except Exception:
        return False


needs_word = unittest.skipUnless(word_usable(), 'Microsoft Word not available or already running')


def plan_for(source, paragraph_id, field, value):
    return {'source_sha256': engine.signature(source),
            'operations': [{'paragraph_id': paragraph_id, 'field': field, 'value': value}]}


class SampleDocumentTests(unittest.TestCase):
    """The generated fixture must be a document the rest of the pipeline can read."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name)/'sample.docx'
        fixtures.build(self.source)

    def test_sample_is_a_readable_package_with_the_expected_parts(self):
        with ZipFile(self.source) as z:
            names = z.namelist()
        for required in ('[Content_Types].xml', '_rels/.rels', 'word/_rels/document.xml.rels',
                         'word/document.xml', 'word/styles.xml', 'word/settings.xml'):
            self.assertIn(required, names)

    def test_every_injected_fault_is_visible_in_resolved_properties(self):
        report = engine.inspect(self.source)
        for paragraph_id, fault in fixtures.FAULTS.items():
            record = report['paragraphs'][paragraph_id]
            field = fault['expected_repair']['field']
            resolved = record['effective_properties'][field]
            # The fault is direct formatting, so it must resolve to the direct layer.
            self.assertEqual(resolved['origin'], 'direct', '%s on paragraph %s' % (field, paragraph_id))
            self.assertNotEqual(resolved['value'], fault['expected_repair']['value'])

    def test_clean_heading_inherits_spacing_from_the_style(self):
        # The untouched Heading1 is the peer the diagnosis compares against.
        record = (engine.inspect(self.source)['paragraphs'][fixtures.CLEAN_PEER_HEADING]
                  ['effective_properties']['space_before_pt'])
        self.assertEqual((record['value'], record['origin']), (12.0, 'style:Heading1'))

    def test_peer_evidence_separates_the_faulty_heading_from_its_peers(self):
        faulty = fixtures.FAULT_IDS['excessive_space_before']
        comparison = engine.peers(self.source, faulty)['comparison']['space_before_pt']
        self.assertEqual(comparison['selected'], 48.0)
        # Every other Heading1 inherits 12pt, so the faulty one stands alone.
        self.assertEqual(comparison['peer_values'], [{'value': 12.0, 'count': 3}])


class PngWriterTests(unittest.TestCase):
    def test_png_round_trips_pixels_and_skips_stride_padding(self):
        width, height, channels = 3, 2, 4          # RGBA input, padded rows
        stride = width * channels + 5
        rows = [bytes([10, 20, 30, 255, 40, 50, 60, 255, 70, 80, 90, 255]) + b'\x00' * 5,
                bytes([11, 21, 31, 255, 41, 51, 61, 255, 71, 81, 91, 255]) + b'\x00' * 5]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'out.png'
            render_module._write_png(path, width, height, stride, channels, b''.join(rows))
            blob = path.read_bytes()
        self.assertTrue(blob.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(struct.unpack('>2I', blob[16:24]), (width, height))
        self.assertEqual(blob[24:29], bytes([8, 2, 0, 0, 0]))   # 8-bit truecolour RGB
        start = blob.index(b'IDAT') + 4
        length = struct.unpack('>I', blob[start - 8:start - 4])[0]
        raw = zlib.decompress(blob[start:start + length])
        self.assertEqual(raw, b'\x00' + bytes([10, 20, 30, 40, 50, 60, 70, 80, 90])
                              + b'\x00' + bytes([11, 21, 31, 41, 51, 61, 71, 81, 91]))


class ComparisonTests(unittest.TestCase):
    """compare_pdfs must describe differences without judging them."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def make_pdf(self, name, sizes):
        path = Path(self.tmp.name)/name
        document = pdfium.PdfDocument.new()
        for width, height in sizes:
            document.new_page(width, height)
        document.save(str(path))
        document.close()
        return path

    def test_identical_renderings_report_no_changed_pixels(self):
        a = self.make_pdf('a.pdf', [(200, 300), (200, 300)])
        b = self.make_pdf('b.pdf', [(200, 300), (200, 300)])
        result = render_module.compare_pdfs(a, b)
        self.assertEqual(result['page_count_delta'], 0)
        self.assertEqual([p['changed_ratio'] for p in result['pages']], [0.0, 0.0])

    def test_extra_page_is_reported_as_added_not_as_a_failure(self):
        result = render_module.compare_pdfs(self.make_pdf('a.pdf', [(200, 300)]),
                                            self.make_pdf('b.pdf', [(200, 300), (200, 300)]))
        self.assertEqual(result['page_count_delta'], 1)
        self.assertEqual(result['pages'][1]['status'], 'added')
        self.assertNotIn('failed', json.dumps(result))

    def test_page_size_change_is_reported_instead_of_a_bogus_ratio(self):
        result = render_module.compare_pdfs(self.make_pdf('a.pdf', [(200, 300)]),
                                            self.make_pdf('b.pdf', [(400, 300)]))
        self.assertEqual(result['pages'][0]['status'], 'page_size_changed')
        self.assertNotIn('changed_ratio', result['pages'][0])


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name)/'sample.docx'
        fixtures.build(self.source)
        self.work = Path(self.tmp.name)/'work'
        self.plan = plan_for(self.source, 1, 'space_before_pt', 12)

    def run_repair(self, plan=None, do_render=False):
        return workflow.repair(self.source, plan or self.plan, self.work, do_render=do_render)

    def test_repair_reaches_awaiting_review_with_all_invariants_passing(self):
        report = self.run_repair()
        self.assertEqual(report['outcome'], 'awaiting_review')
        self.assertEqual(report['invariant_failures'], [])
        self.assertTrue(all(c['passed'] for c in report['invariants'].values()))
        self.assertEqual(report['stages'][-1], 'awaiting_review')

    def test_original_is_byte_identical_after_a_run(self):
        before = self.source.read_bytes()
        self.run_repair()
        self.assertEqual(self.source.read_bytes(), before)

    def test_retry_reuses_the_candidate_instead_of_patching_a_patched_copy(self):
        first = self.run_repair()
        candidate = Path(first['candidate'])
        patched_once = candidate.read_bytes()
        second = self.run_repair()
        self.assertTrue(second['candidate_reused'])
        self.assertEqual(second['candidate'], first['candidate'])
        self.assertEqual(candidate.read_bytes(), patched_once)
        self.assertEqual(second['candidate_sha256'], first['candidate_sha256'])

    def test_different_proposals_produce_different_candidates(self):
        first = self.run_repair()
        second = self.run_repair(plan_for(self.source, 1, 'space_before_pt', 6))
        self.assertNotEqual(first['candidate'], second['candidate'])

    def test_stale_plan_is_refused_before_any_candidate_exists(self):
        stale = dict(self.plan, source_sha256='0' * 64)
        with self.assertRaises(ValueError):
            self.run_repair(stale)
        self.assertEqual(list(self.work.glob('candidate-*.docx')), [])

    def test_invariants_catch_a_candidate_whose_text_was_altered(self):
        # Proves the checks can fail: a hand-tampered candidate must not pass review.
        self.run_repair()
        candidate = next(self.work.glob('candidate-*.docx'))
        tampered = Path(self.tmp.name)/'tampered.docx'
        with ZipFile(candidate) as src, ZipFile(tampered, 'w') as out:
            for info in src.infolist():
                payload = src.read(info.filename)
                if info.filename == 'word/document.xml':
                    payload = payload.replace(b'Introduction', b'Introductory')
                out.writestr(info, payload)
        checks, failures = workflow.check_invariants(
            self.source, tampered, self.plan, engine.signature(self.source))
        self.assertIn('text_preserved', failures)
        self.assertFalse(checks['text_preserved']['passed'])

    def test_report_is_written_and_states_that_nothing_is_accepted(self):
        report = self.run_repair()
        saved = json.loads((self.work/'report.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['proposal_sha256'], report['proposal_sha256'])
        self.assertIn('Nothing is accepted yet', saved['reminder'])

    def test_visual_check_is_not_claimed_when_rendering_is_skipped(self):
        report = self.run_repair(do_render=False)
        self.assertIsNone(report['visual_comparison'])
        self.assertIn('not performed', report['visual_check'])
        self.assertIn('candidate_render_skipped', report['stages'])


@needs_renderer
class RenderedRepairTests(unittest.TestCase):
    """The real before/after proof: convert, rasterise and compare an actual document."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.source = Path(cls.tmp.name)/'sample.docx'
        fixtures.build(cls.source)
        cls.reports = {}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def rendered(self, paragraph_id, field, value):
        key = (paragraph_id, field, json.dumps(value))
        if key not in self.reports:
            self.reports[key] = workflow.repair(
                self.source, plan_for(self.source, paragraph_id, field, value),
                Path(self.tmp.name)/('run-%s-%s' % (paragraph_id, field)), do_render=True)
        return self.reports[key]

    def test_original_renders_to_a_multi_page_pdf_with_page_images(self):
        report = self.rendered(fixtures.FAULT_IDS['excessive_space_before'], 'space_before_pt', 12)
        pages = report['original_render']['pages']
        self.assertGreater(len(pages), 1)
        for page in pages:
            self.assertTrue(Path(page['image']).exists())
            self.assertGreater(Path(page['image']).stat().st_size, 1000)
        self.assertAlmostEqual(pages[0]['width_pt'], 595.0, delta=2)   # A4 portrait

    def test_removing_the_unwanted_page_break_reclaims_the_stranded_page(self):
        report = self.rendered(fixtures.FAULT_IDS['unwanted_page_break_before'],
                               'page_break_before', False)
        self.assertEqual(report['invariant_failures'], [])
        self.assertEqual(report['visual_comparison']['page_count_delta'], -1)

    def test_spacing_repair_changes_the_affected_page(self):
        report = self.rendered(fixtures.FAULT_IDS['excessive_space_before'], 'space_before_pt', 12)
        comparison = report['visual_comparison']
        # The heading sits on page 1, so page 1 must change.
        first = next(p for p in comparison['pages'] if p['page'] == 1)
        self.assertGreater(first['changed_ratio'], 0.0)

    def test_alignment_repair_is_visible_and_leaves_earlier_pages_alone(self):
        report = self.rendered(fixtures.FAULT_IDS['inconsistent_alignment'], 'alignment', 'both')
        comparison = report['visual_comparison']
        self.assertEqual(comparison['page_count_delta'], 0)
        changed = [p['page'] for p in comparison['pages'] if p.get('changed_ratio', 0) > 0.001]
        self.assertTrue(changed, 'a centred-to-justified repair must be visible')
        # Re-justifying a paragraph in a later section does not move page 1.
        self.assertNotIn(1, changed)

    def test_rendered_run_still_preserves_original_and_unrelated_parts(self):
        report = self.rendered(fixtures.FAULT_IDS['inconsistent_alignment'], 'alignment', 'both')
        self.assertTrue(report['invariants']['original_unmodified']['passed'])
        self.assertTrue(report['invariants']['unrelated_parts_byte_identical']['passed'])
        self.assertTrue(report['invariants']['styles_part_unmodified']['passed'])


@needs_word
class WordVerificationTests(unittest.TestCase):
    """Cross-check the resolver against the application that defines the answer.

    Word reports effective paragraph formatting, so where it states a value and we
    state one, they must match. Launching Word is slow, so this is one test covering
    the faulty paragraphs and the clean peer in a single session.
    """

    def test_word_agrees_with_resolved_properties(self):
        paragraph_ids = sorted(fixtures.FAULTS) + [fixtures.CLEAN_PEER_HEADING]
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'sample.docx'
            fixtures.build(source)
            result = word_check.compare_with_resolver(source, paragraph_ids)
        self.assertTrue(result['available'], result.get('reason'))
        self.assertEqual(result['disagreements'], [], result['summary'])
        self.assertGreater(len(result['agreements']), 0)
        # Everything we decline to state is a field nothing in the file sets.
        self.assertTrue(all(item['origin'] == 'application_default'
                            for item in result['not_compared']))

    def test_word_opens_the_sample_in_current_layout_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'sample.docx'
            fixtures.build(source)
            observed = word_check.observe(source)
        self.assertTrue(observed['available'], observed.get('reason'))
        self.assertEqual(observed['compatibility_mode'], 15)
        self.assertEqual(observed['paragraph_count'], len(fixtures.PARAGRAPHS))


if __name__ == '__main__':
    unittest.main()
