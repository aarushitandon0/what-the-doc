"""The uploaded document must be viewable before anything is proposed.

A formatting fault does not exist in a document's text. A 48pt space above a heading
and a 12pt one read identically as paragraphs, so a list of extracted text cannot show
a reader what they complained about, and cannot be compared with a repair. These tests
cover the endpoints that render the original itself.

No model is contacted. The LibreOffice-backed test is skipped, and said to be skipped,
when no renderer is installed; it is never quietly treated as passing.
"""
import unittest

import render as render_module
from test_api import ApiBase


class DocumentPreviewTests(ApiBase):
    def test_a_missing_renderer_is_reported_rather_than_failing_the_upload(self):
        # An absent renderer is a configuration, not an error: the document is still
        # usable for a structural diagnosis, so the endpoint answers 200 and says why
        # there are no pages.
        self.environment(SOFFICE_PATH=str(self.root / 'no-such-soffice'))
        document_id = self.uploaded_id()
        body = self.client.get('/api/documents/%s/preview' % document_id).json()
        self.assertFalse(body['rendered'], body)
        self.assertEqual(body['page_count'], 0, body)
        self.assertEqual(body['urls'], [], body)
        self.assertFalse(body['renderer_available'], body)
        self.assertIn('LibreOffice', body['unavailable_reason'], body)
        page = self.client.get('/api/documents/%s/preview/0' % document_id)
        self.assertEqual(page.status_code, 404, page.text)

    def test_an_unknown_document_has_no_preview(self):
        self.assertEqual(
            self.client.get('/api/documents/%s/preview' % ('a' * 32)).status_code, 404)
        # A traversal attempt is not 32 hex characters and never reaches the disk.
        self.assertEqual(
            self.client.get('/api/documents/..%2F..%2Fetc/preview').status_code, 404)

    def test_the_original_bytes_are_served_unchanged_for_the_browser_preview(self):
        # The browser draws the instant preview from these bytes, so they must be the
        # stored original exactly, and must not depend on LibreOffice being installed.
        self.environment(SOFFICE_PATH=str(self.root / 'no-such-soffice'))
        document_id = self.uploaded_id()
        stored = self.root / 'store' / 'documents' / document_id / 'original.docx'
        self.assertTrue(stored.is_file(), stored)
        response = self.client.get('/api/documents/%s/original' % document_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('wordprocessingml.document', response.headers['content-type'])
        self.assertEqual(response.content, stored.read_bytes())

    def test_an_unknown_document_has_no_original(self):
        self.assertEqual(
            self.client.get('/api/documents/%s/original' % ('a' * 32)).status_code, 404)
        self.assertEqual(
            self.client.get('/api/documents/..%2F..%2Fetc/original').status_code, 404)

    @unittest.skipUnless(render_module.find_soffice(),
                         'LibreOffice is not installed; document rendering is skipped')
    def test_the_original_renders_to_page_images_that_are_served_once(self):
        document_id = self.uploaded_id()
        body = self.client.get('/api/documents/%s/preview' % document_id).json()
        self.assertTrue(body['rendered'], body)
        self.assertGreaterEqual(body['page_count'], 1, body)
        self.assertEqual(len(body['urls']), body['page_count'], body)
        self.assertIsNone(body['unavailable_reason'], body)

        page = self.client.get(body['urls'][0])
        self.assertEqual(page.status_code, 200, page.text)
        self.assertEqual(page.headers['content-type'], 'image/png')
        # A real PNG, not an error page rendered as one.
        self.assertTrue(page.content.startswith(b'\x89PNG\r\n\x1a\n'))

        self.assertEqual(self.client.get(body['urls'][-1]).status_code, 200)
        self.assertEqual(
            self.client.get('/api/documents/%s/preview/%d'
                            % (document_id, body['page_count'])).status_code, 404)

    @unittest.skipUnless(render_module.find_soffice(),
                         'LibreOffice is not installed; document rendering is skipped')
    def test_the_render_is_cached_so_a_second_reader_does_not_pay_for_it(self):
        # The original is never written to, so one render stays valid for its life.
        document_id = self.uploaded_id()
        first = self.client.get('/api/documents/%s/preview' % document_id).json()
        calls = []
        original_convert = render_module.docx_to_pdf
        render_module.docx_to_pdf = lambda *a, **k: (calls.append(1),
                                                     original_convert(*a, **k))[1]
        self.addCleanup(setattr, render_module, 'docx_to_pdf', original_convert)
        second = self.client.get('/api/documents/%s/preview' % document_id).json()
        self.assertEqual(second, first)
        self.assertEqual(calls, [], 'the document was rendered twice')


if __name__ == '__main__':
    unittest.main()
