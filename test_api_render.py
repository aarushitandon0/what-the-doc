"""Renderer failures must remain failed jobs at the HTTP approval boundary.

Diagnoses are labelled fixtures. A real Python child process produces or withholds
the PDF output, while the conversion validation and PDF rasterisation stay real.
No model, LibreOffice installation, or listening HTTP server is required.
"""
import unittest

from test_api import ApiBase, api, proposal
from test_render import injected_renderer


class RendererFailureApiTests(ApiBase):
    def failed_render(self, mode, fail_on=None):
        document_id = self.uploaded_id()
        original = self.app.state.store.document_path(document_id)
        before = original.read_bytes()
        with injected_renderer(self.root, mode, fail_on=fail_on) as commands:
            started = self.start(
                document_id, self.spacing_id, 'Too much space above this heading.',
                proposal(self.spacing_id, self.sample_sha), render=True)
            self.assertEqual(started.status_code, 202, started.text)
            job = self.settled(started.json()['job_id'])

        self.assertTrue(commands, 'the renderer subprocess was never called')
        self.assertEqual(job['state'], api.FAILED, job)
        self.assertEqual(job['error_code'], 'render_failure', job)
        self.assertEqual(job['mode'], api.FIXTURE, job)
        self.assertFalse(job['is_live_model_answer'], job)
        self.assertIsNone(job['candidate'], job)
        self.assertFalse(job['accepted'], job)
        self.assertIsNone(job['decision'], job)
        self.assertIsNone(job['visual_comparison'], job)
        preview = job['preview']
        self.assertTrue(preview['renderer_available'], preview)
        self.assertFalse(preview['rendered'], preview)
        self.assertEqual(preview['before_page_count'], 0, preview)
        self.assertEqual(preview['after_page_count'], 0, preview)
        self.assertEqual(preview['before_urls'], [], preview)
        self.assertEqual(preview['after_urls'], [], preview)

        job_id = job['job_id']
        accept = self.client.post('/api/jobs/%s/accept' % job_id,
                                  json={'candidate_sha256': 'f' * 64})
        self.assertEqual(accept.status_code, 409, accept.text)
        self.assertEqual(accept.json()['error_code'], api.INVALID_STATE)
        download = self.client.get('/api/jobs/%s/download' % job_id)
        self.assertEqual(download.status_code, 409, download.text)
        self.assertEqual(download.json()['error_code'], api.INVALID_STATE)
        for side in ('before', 'after'):
            response = self.client.get('/api/jobs/%s/preview/%s/0' % (job_id, side))
            self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(original.read_bytes(), before)
        self.assertEqual(self.sample.read_bytes(), before)
        # Refused decisions must leave the failed job unchanged.
        refreshed = self.client.get('/api/jobs/%s' % job_id).json()
        self.assertEqual(refreshed['state'], api.FAILED, refreshed)
        self.assertFalse(refreshed['accepted'], refreshed)
        self.assertIsNone(refreshed['decision'], refreshed)
        return job, commands

    def test_missing_original_pdf_fails_without_a_reviewable_candidate(self):
        job, commands = self.failed_render('missing')
        self.assertEqual(len(commands), 1)
        self.assertIn('No PDF produced', job['error_detail'])

    def test_invalid_original_pdf_fails_without_a_reviewable_candidate(self):
        job, commands = self.failed_render('invalid')
        self.assertEqual(len(commands), 1)
        self.assertIn('Invalid PDF output', job['error_detail'])

    def test_candidate_render_failure_hides_the_partial_before_preview(self):
        job, commands = self.failed_render('exit', fail_on='candidate')
        self.assertEqual(len(commands), 2)
        self.assertIn('Conversion exited unsuccessfully', job['error_detail'])
        job_root = self.app.state.store.job_dir(job['job_id'])
        # The original was genuinely converted and rasterised before the second
        # conversion failed. Its partial output must not be served as a review.
        self.assertTrue((job_root / 'original' / 'original.pdf').is_file())
        self.assertTrue((job_root / 'original' / 'original-page01.png').is_file())
        self.assertTrue(list(job_root.glob('candidate-*.docx')))


if __name__ == '__main__':
    unittest.main()
