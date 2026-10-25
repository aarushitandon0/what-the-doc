"""Whole-document break repair: "there are page breaks I didn't want".

Answered by precheck.unwanted_page_breaks, not the model, so no model is contacted.
The candidate still goes through engine.validate, every preservation invariant, a
render and human acceptance, exactly like a single-break repair.
"""
import unittest

import break_fixtures
import engine
import precheck
import render as render_module
from test_api import ApiBase, api


class DocumentBreakRuleTests(unittest.TestCase):
    """Which complaints the rule answers. Anything else is left to the model."""

    def test_complaints_about_unwanted_breaks_are_recognised(self):
        for text in ('There are page breaks that i didnt want',
                     'Remove the page-breaks please', 'Why are there blank pages?'):
            self.assertEqual(precheck._break_types(text), ('page',), text)
        self.assertEqual(precheck._break_types('this column break splits the text'),
                         ('column',))

    def test_other_complaints_are_not(self):
        for text in ('Too much space above this heading',
                     'This heading keeps jumping to a new page',
                     'Please add a page break before the appendix',
                     'A page break is missing here'):
            self.assertEqual(precheck._break_types(text), (), text)


class DocumentBreakApiTests(ApiBase):
    def setUp(self):
        super().setUp()
        self.page_doc = self.root / 'page-breaks.docx'
        break_fixtures.build_manual_page_break(self.page_doc)
        response = self.upload(self.page_doc)
        self.assertEqual(response.status_code, 201, response.text)
        self.record = response.json()

    def start_document(self, complaint, **extra):
        body = {'document_id': self.record['document_id'], 'scope': 'document',
                'complaint': complaint, **extra}
        return self.client.post('/api/jobs', json=body)

    def test_a_complaint_that_is_not_about_breaks_is_refused_before_a_job_exists(self):
        before = len(self.client.get('/api/jobs').json()['jobs'])
        response = self.start_document('The heading is too far from the text.')
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()['error_code'], api.NO_DOCUMENT_REPAIR)
        self.assertEqual(len(self.client.get('/api/jobs').json()['jobs']), before)

    def test_asking_for_a_break_is_not_answered_by_removing_them(self):
        response = self.start_document('Please add a page break before the appendix')
        self.assertEqual(response.status_code, 422, response.text)

    def test_a_document_job_takes_no_injected_diagnosis_or_target(self):
        response = self.start_document('Unwanted page breaks',
                                       break_target_id='brk-0000000000000000')
        self.assertEqual(response.status_code, 400, response.text)

    def test_an_unknown_scope_is_refused(self):
        response = self.start_document('Unwanted page breaks', scope='everything')
        self.assertEqual(response.status_code, 400, response.text)


@unittest.skipUnless(render_module.find_soffice(),
                     'LibreOffice is not installed; a rendered review cannot be faked')
class RenderedDocumentBreakTests(DocumentBreakApiTests):

    def test_every_removable_page_break_goes_in_one_reviewed_candidate(self):
        page_breaks = [b for b in engine.inspect_breaks(self.page_doc)['breaks']
                       if b.get('break_type') == 'page' and b.get('edit_eligible')]
        self.assertGreater(len(page_breaks), 1, 'the fixture needs several page breaks')

        started = self.start_document('There are page breaks that i didnt want')
        self.assertEqual(started.status_code, 202, started.text)
        self.assertEqual(started.json()['scope'], 'document')
        job = self.settled(started.json()['job_id'], timeout=600)
        self.assertEqual(job['state'], api.AWAITING_REVIEW,
                         '%s: %s' % (job.get('error_code'), job.get('error_detail')))
        # A rule answered, and says so: this is never presented as a model answer.
        self.assertEqual(job['answered_by'], 'rule')
        self.assertFalse(job['is_live_model_answer'])
        self.assertFalse(job['accepted'])
        self.assertEqual(sorted(op['target_id'] for op in job['proposal']['operations']),
                         sorted(b['target_id'] for b in page_breaks))
        self.assertTrue(job['preview']['after_urls'])
        self.assertTrue(all(c['passed'] for c in job['invariants'].values()),
                        job['invariants'])

        accepted = self.client.post(
            '/api/jobs/%s/accept' % job['job_id'],
            json={'candidate_sha256': job['candidate']['accept_requires_candidate_sha256']})
        self.assertEqual(accepted.status_code, 200, accepted.text)
        download = self.client.get('/api/jobs/%s/download' % job['job_id'])
        self.assertEqual(download.status_code, 200, download.text)
        repaired = self.root / 'repaired.docx'
        repaired.write_bytes(download.content)
        left = [b for b in engine.inspect_breaks(repaired)['breaks']
                if b.get('break_type') == 'page' and b.get('edit_eligible')]
        self.assertEqual(left, [], 'every removable page break should be gone')
        # The original still has all of them: it is never written to.
        self.assertEqual(len([b for b in engine.inspect_breaks(self.page_doc)['breaks']
                              if b.get('break_type') == 'page' and b.get('edit_eligible')]),
                         len(page_breaks))


if __name__ == '__main__':
    unittest.main()
