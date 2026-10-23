"""Effective no-op filtering stays honest through the HTTP review boundary.

These tests inject diagnoses and inspect actual candidate DOCX bytes. They do not
contact a model, start a server, or render document pages.
"""
import hashlib
import json
from pathlib import Path
import unittest

import engine
import fixtures
from test_api import ApiBase, api, proposal


class EffectiveNoOpApiTests(ApiBase):
    def test_a_mixed_proposal_exposes_only_candidate_writes_and_keeps_approval_checks(self):
        document_id = self.uploaded_id()
        stored_source = self.app.state.store.document_path(document_id)
        original_bytes = stored_source.read_bytes()
        found = proposal(self.spacing_id, self.sample_sha)
        real = dict(found['operations'][0])
        redundant = {'paragraph_id': self.spacing_id,
                     'field': 'space_after_pt', 'value': 6.0}
        found['operations'].append(redundant)
        # Every changed property is cited, as diagnosis.check requires.
        found['evidence'].append({'paragraph_id': self.spacing_id,
                                  'field': 'space_after_pt'})
        started = self.start(document_id, self.spacing_id,
                             'Too much space above this heading.', found)
        self.assertEqual(started.status_code, 202, started.text)
        job_id = started.json()['job_id']
        job = self.settled(job_id)

        self.assertEqual(job['state'], api.AWAITING_REVIEW, job)
        self.assertEqual(job['mode'], api.FIXTURE)
        self.assertFalse(job['is_live_model_answer'])
        self.assertFalse(job['accepted'])
        self.assertEqual(job['diagnosis']['status'], 'proposed')
        self.assertEqual(job['proposal'],
                         {'source_sha256': self.sample_sha, 'operations': [real]})
        self.assertEqual(len(job['ineffective_operations']), 1)
        dropped = job['ineffective_operations'][0]
        self.assertEqual((dropped['paragraph_id'], dropped['field'],
                          dropped['current_value'], dropped['proposed_value'],
                          dropped['current_origin']),
                         (self.spacing_id, 'space_after_pt', 6.0, 6.0, 'style:Heading1'))
        private = self.app.state.store.jobs[job_id]['report']
        self.assertEqual(private['diagnosis']['operations'], [real, redundant])
        self.assertEqual(private['ineffective_operations'], job['ineffective_operations'])
        candidate_path = Path(private['candidate'])
        candidate = engine.inspect(candidate_path)['paragraphs'][self.spacing_id]
        self.assertEqual(candidate['effective_properties']['space_before_pt']['value'], 12.0)
        self.assertEqual(candidate['direct_properties_raw']['space_before_pt'], '240')
        inherited = candidate['effective_properties']['space_after_pt']
        self.assertEqual((inherited['value'], inherited['origin']), (6.0, 'style:Heading1'))
        self.assertIsNone(candidate['direct_properties_raw']['space_after_pt'])

        digest = job['candidate']['candidate_sha256']
        self.assertEqual(digest, engine.signature(candidate_path))
        self.assertFalse(job['candidate']['download_available'])
        self.assertEqual(self.client.get('/api/jobs/%s/download' % job_id).status_code, 409)
        wrong_hash = self.client.post('/api/jobs/%s/accept' % job_id,
                                      json={'candidate_sha256': 'f' * 64})
        self.assertEqual(wrong_hash.status_code, 409)
        self.assertEqual(wrong_hash.json()['error_code'], api.INVALID_STATE)
        accepted = self.client.post('/api/jobs/%s/accept' % job_id,
                                    json={'candidate_sha256': digest})
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()['state'], api.ACCEPTED)
        self.assertTrue(accepted.json()['decision']['revalidated'])
        self.assertEqual(accepted.json()['decision']['candidate_sha256'], digest)
        self.assertEqual(accepted.json()['proposal']['operations'], [real])
        download = self.client.get('/api/jobs/%s/download' % job_id)
        self.assertEqual(download.status_code, 200, download.text)
        self.assertEqual(download.content, candidate_path.read_bytes())
        self.assertEqual(hashlib.sha256(download.content).hexdigest(), digest)
        self.assertEqual(stored_source.read_bytes(), original_bytes)

        candidate_path.write_bytes(candidate_path.read_bytes() + b'tampered')
        refused = self.client.get('/api/jobs/%s/download' % job_id)
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(refused.json()['error_code'], api.CANDIDATE_INTEGRITY_FAILED)

    def test_an_all_inherited_equal_proposal_fails_without_review_or_download(self):
        document_id = self.uploaded_id()
        stored_source = self.app.state.store.document_path(document_id)
        original_bytes = stored_source.read_bytes()
        target = fixtures.CLEAN_PEER_HEADING
        found = proposal(target, self.sample_sha)
        started = self.start(document_id, target,
                             'Too much space above this heading.', found)
        self.assertEqual(started.status_code, 202, started.text)
        job_id = started.json()['job_id']
        job = self.settled(job_id)

        self.assertEqual(job['state'], api.FAILED, job)
        self.assertEqual(job['error_code'], 'invalid_operation')
        self.assertEqual(job['mode'], api.FIXTURE)
        self.assertFalse(job['is_live_model_answer'])
        self.assertEqual(job['diagnosis']['status'], 'proposed')
        self.assertEqual(job['proposal']['operations'], found['operations'])
        self.assertEqual(len(job['ineffective_operations']), 1)
        dropped = job['ineffective_operations'][0]
        self.assertEqual((dropped['paragraph_id'], dropped['field'],
                          dropped['current_value'], dropped['proposed_value'],
                          dropped['current_origin']),
                         (target, 'space_before_pt', 12.0, 12.0, 'style:Heading1'))
        self.assertIsNone(job['candidate'])
        self.assertFalse(job['accepted'])
        self.assertIsNone(job['decision'])
        self.assertFalse(job['preview']['rendered'])
        self.assertEqual(job['preview']['before_urls'], [])
        self.assertEqual(job['preview']['after_urls'], [])
        self.assertEqual(job['preview']['before_page_count'], 0)
        self.assertEqual(job['preview']['after_page_count'], 0)
        self.assertEqual(self.client.get('/api/jobs/%s/preview/after/0' % job_id).status_code,
                         404)
        download = self.client.get('/api/jobs/%s/download' % job_id)
        self.assertEqual(download.status_code, 409)
        self.assertEqual(download.json()['error_code'], api.INVALID_STATE)
        accept = self.client.post('/api/jobs/%s/accept' % job_id,
                                  json={'candidate_sha256': self.sample_sha})
        self.assertEqual(accept.status_code, 409)
        self.assertEqual(accept.json()['error_code'], api.INVALID_STATE)
        self.assertEqual(self.client.get('/api/jobs/%s' % job_id).json()['state'], api.FAILED)
        self.assertEqual(stored_source.read_bytes(), original_bytes)

        job_dir = self.app.state.store.job_dir(job_id)
        self.assertEqual(sorted(path.relative_to(job_dir).as_posix()
                                for path in job_dir.rglob('*') if path.is_file()),
                         ['pipeline.json'])
        saved = json.loads((job_dir / 'pipeline.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['diagnosis']['status'], 'proposed')
        self.assertEqual(saved['diagnosis']['operations'], found['operations'])
        self.assertIsNone(saved['candidate'])
        self.assertEqual(saved['ineffective_operations'], job['ineffective_operations'])


if __name__ == '__main__':
    unittest.main()
