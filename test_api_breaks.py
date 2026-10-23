"""The explicit-break slice at the HTTP boundary: inspection, selection, approval.

Every diagnosis here is an **injected fixture**, labelled as such in each response.
These tests establish what the interface does in each state; they establish nothing
about model quality. No model, no Sentry and no listening server is contacted.

Rendering is the one thing a break removal cannot be reviewed without, so the tests
that need it are skipped rather than faked when LibreOffice is absent.
"""
import unittest

import break_fixtures
import engine
import render as render_module
from test_api import ApiBase, api, proposal


def break_removal(target_id, paragraph_id, source_sha256, break_type='page'):
    """A well-formed diagnosis proposing one explicit-break removal."""
    return {'status': 'proposed',
            'explanation': 'This page break strands a nearly empty page.',
            'evidence': [{'paragraph_id': paragraph_id, 'target_id': target_id}],
            'source_sha256': source_sha256,
            'operations': [{'operation': 'remove_explicit_break',
                            'target_id': target_id,
                            'expected_break_type': break_type}],
            'question': None}


class BreakApiBase(ApiBase):
    def setUp(self):
        super().setUp()
        self.page_doc = self.root / 'page-breaks.docx'
        self.column_doc = self.root / 'column-breaks.docx'
        break_fixtures.build_manual_page_break(self.page_doc)
        break_fixtures.build_multicolumn(self.column_doc)
        self.two_breaks = break_fixtures.MANUAL_PAGE_BREAK_IDS['two_breaks_paragraph']

    def document(self, path=None):
        response = self.upload(path or self.page_doc)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def breaks(self, document_id):
        response = self.client.get('/api/documents/%s/breaks' % document_id)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def removable(self, document_id):
        return [b for b in self.breaks(document_id)['breaks'] if b['edit_eligible']]


class InspectionEndpointTests(BreakApiBase):

    def test_breaks_endpoint_reports_targets_and_eligibility(self):
        record = self.document()
        report = self.breaks(record['document_id'])
        self.assertEqual(report['source_sha256'], record['source_sha256'])
        self.assertEqual(report['removable_count'], 3)
        self.assertEqual(report['last_rendered_page_break_count'], 1)
        self.assertEqual(report['removable_break_types'], ['page', 'column'])
        ineligible = [b for b in report['breaks'] if not b['edit_eligible']]
        self.assertTrue(ineligible)
        for entry in ineligible:
            self.assertTrue(entry['ineligible_reason'],
                            'an unsupported break must say why it is unsupported')

    def test_two_breaks_in_one_paragraph_are_distinguishable_over_http(self):
        document_id = self.document()['document_id']
        here = [b for b in self.removable(document_id)
                if b['paragraph_id'] == self.two_breaks]
        self.assertEqual(len(here), 2)
        self.assertNotEqual(here[0]['target_id'], here[1]['target_id'])
        self.assertEqual([b['ordinal_in_paragraph'] for b in here], [1, 2])
        self.assertNotEqual(here[0]['text_before'], here[1]['text_before'])

    def test_column_and_page_breaks_are_reported_separately(self):
        document_id = self.document(self.column_doc)['document_id']
        kinds = sorted(b['break_type'] for b in self.removable(document_id))
        self.assertEqual(kinds, ['column', 'page'])

    def test_this_single_section_document_offers_no_section_change(self):
        # The multicolumn fixture has one section, which is therefore the first
        # section and never eligible: a section start describes how a section
        # follows the previous one. The endpoint still reports it in full.
        document_id = self.document(self.column_doc)['document_id']
        response = self.client.get('/api/documents/%s/sections' % document_id)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body['editable'])
        self.assertEqual(body['editable_target_ids'], [])
        self.assertEqual(body['section_count'], 1)
        self.assertEqual(body['sections'][0]['columns']['count'], 2)
        self.assertEqual(body['sections'][0]['start_type'], 'nextPage')
        self.assertTrue(body['sections'][0]['start_type_is_default'])
        self.assertIn('no previous section',
                      body['sections'][0]['ineligible_reason'])
        self.assertTrue(all(not s['edit_eligible'] for s in body['sections']))

    def test_the_only_section_change_offered_is_next_page_to_continuous(self):
        document_id = self.document(self.column_doc)['document_id']
        body = self.client.get('/api/documents/%s/sections' % document_id).json()
        self.assertEqual(body['supported_start_changes'],
                         [{'from': 'nextPage', 'to': 'continuous'}])
        self.assertIn('continuous', body['read_only_reason'])

    def test_both_endpoints_404_for_an_unknown_document(self):
        for path in ('breaks', 'sections'):
            response = self.client.get('/api/documents/%s/%s' % ('0' * 32, path))
            self.assertEqual(response.status_code, 404, response.text)
            self.assertEqual(response.json()['error_code'], api.NOT_FOUND)

    def test_a_traversal_attempt_never_reaches_the_filesystem(self):
        response = self.client.get('/api/documents/..%2F..%2Fetc/breaks')
        self.assertIn(response.status_code, (404, 422), response.text)


class BreakSelectionTests(BreakApiBase):

    def test_a_selected_break_is_recorded_on_the_job(self):
        record = self.document()
        target = self.removable(record['document_id'])[0]
        started = self.start(record['document_id'], target['paragraph_id'],
                             'This page break leaves a nearly empty page.',
                             break_removal(target['target_id'], target['paragraph_id'],
                                           record['source_sha256']),
                             break_target_id=target['target_id'])
        self.assertEqual(started.status_code, 202, started.text)
        self.assertEqual(started.json()['break_target_id'], target['target_id'])

    def test_an_unknown_break_target_is_refused_before_anything_runs(self):
        record = self.document()
        started = self.start(record['document_id'], self.two_breaks, 'Remove this break.',
                             proposal(self.two_breaks, record['source_sha256']),
                             break_target_id='brk-0000000000000000')
        self.assertEqual(started.status_code, 409, started.text)
        self.assertEqual(started.json()['error_code'], api.INVALID_STATE)
        self.assertIn('Re-read GET /breaks', started.json()['detail'])

    def test_a_stale_break_target_from_another_document_is_refused(self):
        mine = self.document()
        theirs = self.document(self.column_doc)
        stolen = self.removable(theirs['document_id'])[0]['target_id']
        started = self.start(mine['document_id'], self.two_breaks, 'Remove this break.',
                             proposal(self.two_breaks, mine['source_sha256']),
                             break_target_id=stolen)
        self.assertEqual(started.status_code, 409, started.text)
        self.assertEqual(started.json()['error_code'], api.INVALID_STATE)

    def test_an_ineligible_break_cannot_be_selected(self):
        record = self.document()
        ineligible = [b for b in self.breaks(record['document_id'])['breaks']
                      if b['target_id'] and not b['edit_eligible']][0]
        started = self.start(record['document_id'], ineligible['paragraph_id'],
                            'Remove this break.',
                            proposal(ineligible['paragraph_id'], record['source_sha256']),
                            break_target_id=ineligible['target_id'])
        self.assertEqual(started.status_code, 400, started.text)
        self.assertEqual(started.json()['error_code'], 'unsupported_formatting')

    def test_a_break_in_another_paragraph_cannot_be_selected(self):
        record = self.document()
        target = [b for b in self.removable(record['document_id'])
                  if b['paragraph_id'] == self.two_breaks][0]
        started = self.start(record['document_id'], self.two_breaks + 1,
                             'Remove this break.',
                             proposal(self.two_breaks + 1, record['source_sha256']),
                             break_target_id=target['target_id'])
        self.assertEqual(started.status_code, 400, started.text)
        self.assertEqual(started.json()['error_code'], 'invalid_selection')

    def test_a_non_string_break_target_is_refused(self):
        record = self.document()
        started = self.start(record['document_id'], self.two_breaks, 'Remove this.',
                             proposal(self.two_breaks, record['source_sha256']),
                             break_target_id=7)
        self.assertEqual(started.status_code, 400, started.text)
        self.assertEqual(started.json()['error_code'], 'invalid_selection')

    def test_an_injected_diagnosis_naming_an_unsupplied_break_is_refused(self):
        """An injected answer has a different origin, never more power than a model's."""
        record = self.document()
        target = self.removable(record['document_id'])[0]
        other = [b for b in self.removable(record['document_id'])
                 if b['paragraph_id'] != target['paragraph_id']][0]
        started = self.start(record['document_id'], target['paragraph_id'],
                             'Remove the other one.',
                             break_removal(other['target_id'], target['paragraph_id'],
                                           record['source_sha256']),
                             break_target_id=target['target_id'])
        self.assertEqual(started.status_code, 400, started.text)
        self.assertEqual(started.json()['error_code'], 'invalid_response')


class UnrenderedBreakTests(BreakApiBase):
    """Without a rendering, a break removal must not become reviewable or acceptable."""

    def test_an_unrendered_break_removal_fails_with_render_failure(self):
        record = self.document()
        target = self.removable(record['document_id'])[0]
        started = self.start(record['document_id'], target['paragraph_id'],
                             'This page break leaves a nearly empty page.',
                             break_removal(target['target_id'], target['paragraph_id'],
                                           record['source_sha256']),
                             break_target_id=target['target_id'], render=False)
        self.assertEqual(started.status_code, 202, started.text)
        job = self.settled(started.json()['job_id'])
        self.assertEqual(job['state'], api.FAILED, job)
        self.assertEqual(job['error_code'], 'render_failure', job)
        self.assertTrue(job['rendering_required'], job)
        self.assertFalse(job['rendering_satisfied'], job)
        self.assertEqual(job['operation_kind'], 'explicit_break_removal', job)
        self.assertFalse(job['accepted'], job)
        # Nothing to approve, and the refusal does not depend on a disabled button.
        accept = self.client.post('/api/jobs/%s/accept' % job['job_id'],
                                  json={'candidate_sha256': 'f' * 64})
        self.assertEqual(accept.status_code, 409, accept.text)
        download = self.client.get('/api/jobs/%s/download' % job['job_id'])
        self.assertEqual(download.status_code, 409, download.text)
        self.assertEqual(engine.inspect_breaks(self.page_doc)['removable_count'], 3)

    def test_a_paragraph_repair_without_rendering_is_still_reviewable(self):
        """Existing behaviour is unchanged: the new rule applies to the new operation."""
        job = self.awaiting()
        self.assertEqual(job['state'], api.AWAITING_REVIEW, job)
        self.assertFalse(job['rendering_required'], job)
        self.assertTrue(job['rendering_satisfied'], job)
        self.assertEqual(job['operation_kind'], 'paragraph_property', job)


@unittest.skipUnless(render_module.find_soffice(),
                     'LibreOffice is not installed; a rendered review cannot be faked, '
                     'so this is reported as not run')
class RenderedBreakReviewTests(BreakApiBase):

    def reviewable(self, path=None, break_type='page', pick=0):
        record = self.document(path)
        targets = [b for b in self.removable(record['document_id'])
                   if b['break_type'] == break_type]
        target = targets[pick]
        started = self.start(record['document_id'], target['paragraph_id'],
                             'This break leaves the page looking wrong.',
                             break_removal(target['target_id'], target['paragraph_id'],
                                           record['source_sha256'], break_type),
                             break_target_id=target['target_id'], render=True)
        self.assertEqual(started.status_code, 202, started.text)
        return record, target, self.settled(started.json()['job_id'], timeout=600)

    def test_a_rendered_page_break_removal_reaches_review_and_downloads(self):
        record, target, job = self.reviewable()
        self.assertEqual(job['state'], api.AWAITING_REVIEW, job)
        self.assertFalse(job['accepted'], job)
        self.assertTrue(job['rendering_satisfied'], job)
        self.assertTrue(job['preview']['rendered'], job['preview'])
        self.assertTrue(job['preview']['before_urls'])
        self.assertTrue(job['preview']['after_urls'])
        # The review screen is told which break is going, with its nearby text.
        shown, = job['break_targets']
        self.assertEqual(shown['target_id'], target['target_id'])
        self.assertEqual(shown['break_type'], 'page')
        self.assertTrue(shown['text_before'] or shown['text_after'])
        # A page-count change is reported when there is one, and is not an approval.
        self.assertEqual(job['visual_comparison']['page_count_delta'], -1)

        accepted = self.client.post(
            '/api/jobs/%s/accept' % job['job_id'],
            json={'candidate_sha256': job['candidate']['accept_requires_candidate_sha256']})
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()['state'], api.ACCEPTED)
        download = self.client.get('/api/jobs/%s/download' % job['job_id'])
        self.assertEqual(download.status_code, 200, download.text)
        self.assertTrue(download.content.startswith(b'PK'))
        # The original is untouched and still holds all three removable breaks.
        self.assertEqual(engine.inspect_breaks(self.page_doc)['removable_count'], 3)

    def test_the_candidate_has_exactly_one_fewer_break(self):
        record, target, job = self.reviewable()
        candidate_path = self.root / 'downloaded.docx'
        accepted = self.client.post(
            '/api/jobs/%s/accept' % job['job_id'],
            json={'candidate_sha256': job['candidate']['candidate_sha256']})
        self.assertEqual(accepted.status_code, 200, accepted.text)
        candidate_path.write_bytes(
            self.client.get('/api/jobs/%s/download' % job['job_id']).content)
        after = engine.inspect_breaks(candidate_path)
        self.assertEqual(after['removable_count'], 2)
        self.assertEqual(
            ''.join(engine.open_package(self.page_doc).document.xpath(
                '//w:t/text()', namespaces=engine.NS)),
            ''.join(engine.open_package(candidate_path).document.xpath(
                '//w:t/text()', namespaces=engine.NS)))

    def test_a_rendered_column_break_removal_may_not_change_the_page_count(self):
        record, target, job = self.reviewable(self.column_doc, break_type='column')
        self.assertEqual(job['state'], api.AWAITING_REVIEW, job)
        self.assertEqual(job['break_targets'][0]['break_type'], 'column')
        # Reflow without a page-count change is a correct outcome, not a failure.
        self.assertEqual(job['visual_comparison']['page_count_delta'], 0)
        self.assertTrue(any(page.get('changed_ratio', 0) > 0
                            for page in job['visual_comparison']['pages']))

    def test_rejecting_keeps_the_break_and_offers_no_download(self):
        record, target, job = self.reviewable()
        rejected = self.client.post('/api/jobs/%s/reject' % job['job_id'],
                                    json={'reason': 'that break is deliberate'})
        self.assertEqual(rejected.status_code, 200, rejected.text)
        self.assertEqual(rejected.json()['state'], api.REJECTED)
        self.assertFalse(rejected.json()['accepted'])
        download = self.client.get('/api/jobs/%s/download' % job['job_id'])
        self.assertEqual(download.status_code, 409, download.text)
        # The legitimate break survives because nothing was approved.
        self.assertEqual(engine.inspect_breaks(self.page_doc)['removable_count'], 3)

    def test_acceptance_requires_the_reviewed_candidate_hash(self):
        record, target, job = self.reviewable()
        wrong = self.client.post('/api/jobs/%s/accept' % job['job_id'],
                                 json={'candidate_sha256': 'f' * 64})
        self.assertEqual(wrong.status_code, 409, wrong.text)
        self.assertEqual(wrong.json()['error_code'], api.INVALID_STATE)
        self.assertEqual(self.client.get('/api/jobs/%s' % job['job_id']).json()['state'],
                         api.AWAITING_REVIEW)

    def test_a_tampered_candidate_is_refused_at_acceptance(self):
        record, target, job = self.reviewable()
        candidate = self.root / 'store' / 'jobs'
        path = [p for p in candidate.rglob('candidate-*.docx')][0]
        path.write_bytes(path.read_bytes() + b'tamper')
        refused = self.client.post(
            '/api/jobs/%s/accept' % job['job_id'],
            json={'candidate_sha256': job['candidate']['candidate_sha256']})
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()['error_code'], api.CANDIDATE_INTEGRITY_FAILED)


if __name__ == '__main__':
    unittest.main()
