"""Public (hosted demo) mode: the protections that stand in for accounts.

No model is contacted. Jobs are created from injected diagnoses, which public mode
refuses; the tests that need a job switch fixture injection back on *after* building
the app, so they exercise visitor scoping without weakening the refusal test.
"""
import tempfile
import time
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
    import api
except ImportError as _error:                      # pragma: no cover - optional dep
    raise unittest.SkipTest('fastapi is not installed: %s' % _error)

from test_api import ApiBase, proposal

ALICE = 'a' * 32
BOB = 'b' * 32


class PublicBase(ApiBase):
    def setUp(self):
        super().setUp()
        self.app = api.create_app(root=self.root / 'public-store', public=True)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.addCleanup(self.app.state.executor.shutdown, True)

    def as_visitor(self, key):
        self.client.headers['X-WTD-Visitor'] = key

    def job_for(self, key):
        self.as_visitor(key)
        document_id = self.uploaded_id()
        started = self.start(document_id, self.spacing_id, 'Too much space above.',
                             proposal(self.spacing_id, self.sample_sha))
        self.assertEqual(started.status_code, 202, started.text)
        return self.settled(started.json()['job_id'])


class PublicModeTests(PublicBase):
    def test_health_reports_public_mode_and_fixture_mode_is_forced_off(self):
        app = api.create_app(root=self.root / 'forced', public=True,
                             allow_fixture_mode=True)
        self.addCleanup(app.state.executor.shutdown, True)
        with TestClient(app) as client:
            body = client.get('/api/health').json()
        self.assertTrue(body['public_mode'])
        self.assertFalse(body['fixture_mode_allowed'])

    def test_an_injected_diagnosis_is_refused(self):
        document_id = self.uploaded_id()
        response = self.start(document_id, self.spacing_id, 'Too much space.',
                              proposal(self.spacing_id, self.sample_sha))
        self.assertEqual(response.json()['error_code'], api.FIXTURE_MODE_DISABLED)

    def test_the_job_list_shows_only_the_callers_own_jobs(self):
        self.app.state.allow_fixture_mode = True
        mine = self.job_for(ALICE)
        theirs = self.job_for(BOB)
        self.as_visitor(ALICE)
        listed = [j['job_id'] for j in self.client.get('/api/jobs').json()['jobs']]
        self.assertEqual(listed, [mine['job_id']])
        self.as_visitor(BOB)
        listed = [j['job_id'] for j in self.client.get('/api/jobs').json()['jobs']]
        self.assertEqual(listed, [theirs['job_id']])

    def test_a_request_without_a_valid_visitor_key_lists_nothing(self):
        self.app.state.allow_fixture_mode = True
        self.job_for(ALICE)
        for key in (None, 'not-hex', ''):
            self.client.headers.pop('X-WTD-Visitor', None)
            if key is not None:
                self.as_visitor(key)
            self.assertEqual(self.client.get('/api/jobs').json()['jobs'], [])

    def test_local_mode_still_lists_every_job(self):
        local = api.create_app(root=self.root / 'local', allow_fixture_mode=True)
        self.addCleanup(local.state.executor.shutdown, True)
        self.client = TestClient(local)
        self.addCleanup(self.client.close)
        self.job_for(ALICE)
        self.as_visitor(BOB)
        self.assertEqual(len(self.client.get('/api/jobs').json()['jobs']), 1)


class LimitTests(PublicBase):
    def test_uploads_are_rate_limited_per_client_address(self):
        self.app.state.limiter = api.RateLimiter({'upload': (2, 3600), 'job': (5, 3600)})
        codes = [self.upload().status_code for _ in range(3)]
        self.assertEqual(codes[:2], [201, 201])
        third = self.upload()
        self.assertEqual(third.status_code, 429)
        self.assertEqual(third.json()['error_code'], api.RATE_LIMITED)
        self.assertIn('Retry-After', third.headers)
        # A different forwarded address has its own allowance.
        other = self.client.post('/api/demo/document',
                                 headers={'X-Forwarded-For': '203.0.113.9'})
        self.assertEqual(other.status_code, 201, other.text)

    def test_the_limiter_window_slides(self):
        limiter = api.RateLimiter({'job': (1, 60)})
        self.assertTrue(limiter.allow('job', 'x', now=0)[0])
        self.assertFalse(limiter.allow('job', 'x', now=30)[0])
        self.assertTrue(limiter.allow('job', 'x', now=61)[0])

    def test_a_full_queue_refuses_new_jobs_with_busy(self):
        document_id = self.uploaded_id()
        for n in range(api.PUBLIC_MAX_PENDING):
            self.app.state.store.jobs['%032x' % n] = {
                'job_id': '%032x' % n, 'state': api.RUNNING, 'document_id': document_id,
                'created_at': time.time()}
        response = self.start(document_id, self.spacing_id, 'Too much space.', None)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()['error_code'], api.BUSY)


class RetentionTests(PublicBase):
    def test_old_documents_and_finished_jobs_are_deleted_and_running_ones_kept(self):
        self.app.state.allow_fixture_mode = True
        done = self.job_for(ALICE)
        store = self.app.state.store
        running_doc = self.uploaded_id()
        store.jobs['f' * 32] = {'job_id': 'f' * 32, 'state': api.RUNNING,
                                'document_id': running_doc, 'created_at': 0}
        later = time.time() + api.PUBLIC_RETENTION_SECONDS + 10
        self.app.state.sweep(now=later)
        self.assertNotIn(done['job_id'], store.jobs)
        self.assertNotIn(done['document_id'], store.documents)
        self.assertFalse(store.document_path(done['document_id']).exists())
        self.assertFalse(store.job_dir(done['job_id']).exists())
        self.assertIn('f' * 32, store.jobs)
        self.assertIn(running_doc, store.documents)
        self.assertTrue(store.document_path(running_doc).exists())

    def test_recent_work_survives_a_sweep(self):
        self.app.state.allow_fixture_mode = True
        done = self.job_for(ALICE)
        self.app.state.sweep()
        self.assertIn(done['job_id'], self.app.state.store.jobs)


class StaticFrontendTests(unittest.TestCase):
    def test_a_built_frontend_is_served_at_root_and_api_routes_still_win(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / 'dist'
            dist.mkdir()
            (dist / 'index.html').write_text('<!doctype html><title>wtd</title>')
            app = api.create_app(root=Path(tmp) / 'store', public=True, static_dir=dist)
            self.addCleanup(app.state.executor.shutdown, True)
            with TestClient(app) as client:
                page = client.get('/')
                self.assertEqual(page.status_code, 200)
                self.assertIn('<title>wtd</title>', page.text)
                self.assertEqual(client.get('/api/health').json()['service'], 'what-the-doc')


if __name__ == '__main__':
    unittest.main()
