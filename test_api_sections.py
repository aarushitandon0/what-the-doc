"""The section-start slice at the HTTP boundary: inspection, selection, approval.

Every diagnosis here is an **injected fixture**, labelled as such in each response.
These tests establish what the interface does in each state; they establish nothing
about model quality. No model, no Sentry and no listening server is contacted.

A section change can alter the layout of every page after it, so rendering is the one
thing it cannot be reviewed without. The tests that need a renderer are skipped rather
than faked when LibreOffice is absent, and the test that proves the *refusal* when
rendering did not happen needs no renderer at all.
"""
import unittest

import engine
import render as render_module
import section_fixtures
from test_api import ApiBase, api


def section_change(target_id, source_sha256):
    """A well-formed diagnosis proposing one section-start change."""
    return {'status': 'proposed',
            'explanation': 'The second part is asked to continue on the same page '
                           'rather than starting a new one.',
            'evidence': [{'section_id': target_id}],
            'source_sha256': source_sha256,
            'operations': [{'operation': 'change_section_start',
                            'target_id': target_id,
                            'expected_start_type': 'nextPage',
                            'new_start_type': 'continuous'}],
            'question': None}


class SectionApiBase(ApiBase):
    def setUp(self):
        super().setUp()
        self.paths = {}
        for name in ('supported_next_page', 'three_sections', 'odd_page_start',
                     'differing_geometry', 'single_section'):
            path = self.root / (name + '.docx')
            section_fixtures.BUILDERS[name](path)
            self.paths[name] = path

    def document(self, name='supported_next_page'):
        response = self.upload(self.paths[name])
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def sections(self, document_id):
        response = self.client.get('/api/documents/%s/sections' % document_id)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def eligible(self, document_id, index=0):
        found = [s for s in self.sections(document_id)['sections']
                 if s['edit_eligible']]
        self.assertTrue(found, 'no eligible section over HTTP')
        return found[index]


class SectionInspectionEndpointTests(SectionApiBase):

    def test_the_endpoint_issues_a_target_only_for_a_convertible_section(self):
        record = self.document()
        body = self.sections(record['document_id'])
        self.assertEqual(body['section_count'], 2)
        self.assertTrue(body['editable'])
        self.assertEqual(len(body['editable_target_ids']), 1)
        self.assertEqual(body['supported_start_changes'],
                         [{'from': 'nextPage', 'to': 'continuous'}])
        first, second = body['sections']
        self.assertFalse(first['edit_eligible'])
        self.assertIn('no previous section', first['ineligible_reason'])
        self.assertTrue(second['edit_eligible'])
        self.assertEqual(second['supported_start_changes'], ['continuous'])

    def test_an_unconvertible_document_says_so_rather_than_offering_a_target(self):
        for name, fragment in (('odd_page_start', 'odd-page'),
                               ('differing_geometry', 'w:pgSz'),
                               ('single_section', 'no previous section')):
            record = self.document(name)
            body = self.sections(record['document_id'])
            self.assertFalse(body['editable'], name)
            self.assertEqual(body['editable_target_ids'], [], name)
            reasons = ' '.join(s['ineligible_reason'] or '' for s in body['sections'])
            differing = ' '.join(str(s['differing_page_properties'])
                                 for s in body['sections'])
            self.assertIn(fragment, reasons + ' ' + differing, name)

    def test_the_endpoint_never_reports_a_page_number(self):
        record = self.document()
        body = self.sections(record['document_id'])
        self.assertIn('not page numbers', body['warning'])
        for section in body['sections']:
            self.assertFalse([k for k in section if 'page_number' in k])

    def test_an_unknown_document_is_a_404(self):
        response = self.client.get('/api/documents/%s/sections' % ('a' * 32))
        self.assertEqual(response.status_code, 404)


class SectionSelectionTests(SectionApiBase):

    def test_a_selected_section_is_recorded_on_the_job(self):
        record = self.document()
        target = self.eligible(record['document_id'])
        started = self.start(record['document_id'], 0,
                             'The second part should not start a new page.',
                             section_change(target['target_id'],
                                            record['source_sha256']),
                             section_target_id=target['target_id'])
        self.assertEqual(started.status_code, 202, started.text)
        self.assertEqual(started.json()['section_target_id'], target['target_id'])

    def test_an_unknown_section_target_is_refused_before_anything_runs(self):
        record = self.document()
        response = self.start(record['document_id'], 0, 'Fix the section.',
                              section_change('sec-0000000000000000',
                                             record['source_sha256']),
                              section_target_id='sec-0000000000000000')
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn('Re-read GET /sections', response.json()['detail'])

    def test_a_section_target_from_another_document_is_refused(self):
        other = self.document('three_sections')
        stale = self.eligible(other['document_id'])['target_id']
        record = self.document()
        response = self.start(record['document_id'], 0, 'Fix the section.',
                              section_change(stale, record['source_sha256']),
                              section_target_id=stale)
        self.assertEqual(response.status_code, 409, response.text)

    def test_an_ineligible_section_cannot_be_selected(self):
        record = self.document('odd_page_start')
        last = self.sections(record['document_id'])['sections'][-1]
        response = self.start(record['document_id'], 0, 'Fix the section.',
                              section_change(last['target_id'],
                                             record['source_sha256']),
                              section_target_id=last['target_id'])
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()['error_code'], 'unsupported_formatting')
        self.assertIn('odd-page', response.json()['detail'])

    def test_a_break_and_a_section_cannot_both_be_selected(self):
        record = self.document()
        target = self.eligible(record['document_id'])
        response = self.start(record['document_id'], 0, 'Fix it.',
                              section_change(target['target_id'],
                                             record['source_sha256']),
                              section_target_id=target['target_id'],
                              break_target_id='brk-0000000000000000')
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn('not both', response.json()['detail'])

    def test_a_non_string_section_target_is_refused(self):
        record = self.document()
        target = self.eligible(record['document_id'])
        response = self.start(record['document_id'], 0, 'Fix it.',
                              section_change(target['target_id'],
                                             record['source_sha256']),
                              section_target_id=17)
        self.assertEqual(response.status_code, 400, response.text)

    def test_an_injected_diagnosis_naming_an_unsupplied_section_is_refused(self):
        # The injected answer names a real, eligible section - but one the request
        # did not select, so it was never shown. That is a fabrication, and it is
        # refused on the same grounds a model's would be.
        record = self.document('three_sections')
        chosen, other = (self.eligible(record['document_id'], 0),
                         self.eligible(record['document_id'], 1))
        response = self.start(record['document_id'], 0, 'Fix the section.',
                              section_change(other['target_id'],
                                             record['source_sha256']),
                              section_target_id=chosen['target_id'])
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn('not supplied', response.json()['detail'])


class UnrenderedSectionTests(SectionApiBase):
    """No renderer needed: this is the refusal when rendering did not happen."""

    def test_an_unrendered_section_change_fails_with_render_failure(self):
        record = self.document()
        target = self.eligible(record['document_id'])
        started = self.start(record['document_id'], 0,
                             'The second part should not start a new page.',
                             section_change(target['target_id'],
                                            record['source_sha256']),
                             section_target_id=target['target_id'], render=False)
        self.assertEqual(started.status_code, 202, started.text)
        job = self.settled(started.json()['job_id'], timeout=120)
        self.assertEqual(job['state'], api.FAILED, job)
        self.assertEqual(job['error_code'], 'render_failure', job)
        self.assertTrue(job['rendering_required'])
        self.assertFalse(job['rendering_satisfied'])
        self.assertFalse(job['accepted'])
        # Nothing to accept and nothing to download.
        self.assertEqual(self.client.get(
            '/api/jobs/%s/download' % job['job_id']).status_code, 409)


@unittest.skipUnless(render_module.renderer_status()['available'],
                     'No LibreOffice renderer installed')
class RenderedSectionReviewTests(SectionApiBase):

    def reviewable(self, name='supported_next_page'):
        record = self.document(name)
        target = self.eligible(record['document_id'])
        started = self.start(record['document_id'], 0,
                             'The second part should not start a new page.',
                             section_change(target['target_id'],
                                            record['source_sha256']),
                             section_target_id=target['target_id'], render=True)
        self.assertEqual(started.status_code, 202, started.text)
        job = self.settled(started.json()['job_id'], timeout=600)
        # Name the cause when a rendered run does not reach review. A bare 'failed'
        # was once attributed to a renderer timeout that the log could not show.
        self.assertEqual(job['state'], api.AWAITING_REVIEW,
                         'job did not reach review: %s: %s'
                         % (job.get('error_code'), job.get('error_detail')))
        return record, target, job

    def test_a_rendered_section_change_reaches_review_and_downloads(self):
        record, target, job = self.reviewable()
        self.assertEqual(job['state'], api.AWAITING_REVIEW, job)
        self.assertFalse(job['accepted'], job)
        self.assertTrue(job['rendering_satisfied'], job)
        self.assertEqual(job['operation_kind'], 'section_start_change')
        self.assertEqual(job['plan_schema_version'], 3)
        self.assertTrue(job['preview']['rendered'])
        self.assertTrue(job['preview']['before_urls'])
        self.assertTrue(job['preview']['after_urls'])
        shown, = job['section_targets']
        self.assertEqual(shown['target_id'], target['target_id'])
        self.assertEqual(shown['start_type'], 'nextPage')
        self.assertEqual(shown['governs'], 'the final section of the document')

        accepted = self.client.post(
            '/api/jobs/%s/accept' % job['job_id'],
            json={'candidate_sha256':
                  job['candidate']['accept_requires_candidate_sha256']})
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()['state'], api.ACCEPTED)
        download = self.client.get('/api/jobs/%s/download' % job['job_id'])
        self.assertEqual(download.status_code, 200, download.text)
        self.assertTrue(download.content.startswith(b'PK'))

        downloaded = self.root / 'downloaded.docx'
        downloaded.write_bytes(download.content)
        after = engine.inspect_sections(downloaded)
        self.assertEqual([s['start_type'] for s in after['sections']],
                         ['nextPage', 'continuous'])
        self.assertEqual(after['section_count'], 2)
        # The original is untouched and still starts its second section on a page.
        before = engine.inspect_sections(self.paths['supported_next_page'])
        self.assertEqual([s['start_type'] for s in before['sections']],
                         ['nextPage', 'nextPage'])

    def test_the_download_is_byte_identical_to_the_accepted_candidate(self):
        record, target, job = self.reviewable()
        reviewed = job['candidate']['candidate_sha256']
        self.client.post('/api/jobs/%s/accept' % job['job_id'],
                         json={'candidate_sha256': reviewed})
        import hashlib
        content = self.client.get('/api/jobs/%s/download' % job['job_id']).content
        self.assertEqual(hashlib.sha256(content).hexdigest(), reviewed)

    def test_download_before_approval_is_refused(self):
        record, target, job = self.reviewable()
        self.assertEqual(job['state'], api.AWAITING_REVIEW)
        response = self.client.get('/api/jobs/%s/download' % job['job_id'])
        self.assertEqual(response.status_code, 409, response.text)

    def test_rejecting_keeps_the_section_and_offers_no_download(self):
        record, target, job = self.reviewable()
        rejected = self.client.post('/api/jobs/%s/reject' % job['job_id'],
                                    json={'reason': 'that page break is deliberate'})
        self.assertEqual(rejected.status_code, 200, rejected.text)
        self.assertEqual(rejected.json()['state'], api.REJECTED)
        self.assertFalse(rejected.json()['accepted'])
        self.assertEqual(self.client.get(
            '/api/jobs/%s/download' % job['job_id']).status_code, 409)
        self.assertEqual(
            [s['start_type'] for s in
             engine.inspect_sections(self.paths['supported_next_page'])['sections']],
            ['nextPage', 'nextPage'])

    def test_acceptance_requires_the_reviewed_candidate_hash(self):
        record, target, job = self.reviewable()
        wrong = self.client.post('/api/jobs/%s/accept' % job['job_id'],
                                 json={'candidate_sha256': 'f' * 64})
        self.assertEqual(wrong.status_code, 409, wrong.text)
        self.assertEqual(self.client.get(
            '/api/jobs/%s' % job['job_id']).json()['state'], api.AWAITING_REVIEW)

    def test_every_preservation_invariant_is_reported_and_passing(self):
        record, target, job = self.reviewable()
        self.assertEqual(job['invariant_failures'], [])
        for name in ('section_count_preserved', 'section_boundaries_preserved',
                     'only_approved_section_start_changed',
                     'section_properties_preserved', 'runs_preserved',
                     'text_preserved', 'unrelated_parts_byte_identical'):
            self.assertIn(name, job['invariants'])
            self.assertTrue(job['invariants'][name]['passed'], name)

    def test_the_page_count_is_reported_without_being_promised(self):
        record, target, job = self.reviewable()
        comparison = job['visual_comparison']
        self.assertIsInstance(comparison['before_pages'], int)
        self.assertIsInstance(comparison['after_pages'], int)
        self.assertEqual(comparison['page_count_delta'],
                         comparison['after_pages'] - comparison['before_pages'])


if __name__ == '__main__':
    unittest.main()
