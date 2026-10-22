import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import os
from unittest.mock import patch
from argparse import Namespace

spec = importlib.util.spec_from_file_location('has', Path(__file__).resolve().parents[1] / 'tools/scrape_to_has.py')
has = importlib.util.module_from_spec(spec)
spec.loader.exec_module(has)


class FakeHub:
    def __init__(self, failure=None, status='confirmed'):
        self.calls = []
        self.failure = failure
        self.status = status

    def call(self, path, payload=None):
        self.calls.append((path, payload))
        if path.startswith('/agents/me/posts'):
            return {'posts': []}
        if path == self.failure:
            raise OSError('connection lost')
        if path.endswith('request-authorization'):
            return {'authorizationId': 'auth'}
        if path.endswith('create'):
            return {'challengeId': 'challenge', 'challenge': {'question': '2+2'}}
        return {'status': self.status, 'result': {'post': {'id': 'post'}}}


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = has.connect(Path(self.tmp.name) / 'queue.db')
        self.addCleanup(self.db.close)
        self.job = {'title': 'Engineer', 'company': 'Acme', 'url': 'https://example.org/jobs/1', 'fit': 'high', 'private_notes': 'SECRET'}
        for url, pair, public in has.validate({'jobs': [self.job], 'complete': True}, ['high']):
            self.db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)', (url, pair, json.dumps(public), 'queued', '{}', has.now()))
        self.db.commit()

    def row(self):
        return self.db.execute('SELECT * FROM jobs').fetchone()

    def publish(self, hub):
        has.publish(self.db, hub, {'community_public_id': 'jobs'}, self.row(), lambda _: '4')

    def test_pipeline_and_no_duplicate(self):
        hub = FakeHub()
        self.publish(hub)
        self.assertEqual(self.row()['stage'], 'confirmed')
        self.publish(hub)
        self.assertEqual(len(hub.calls), 3)
        self.assertNotIn('SECRET', json.dumps(hub.calls))

    def test_each_job_has_its_own_topic_in_configured_community(self):
        second = dict(self.job, title='Designer', url='https://example.org/jobs/2')
        for url, pair, public in has.validate({'jobs': [second], 'complete': True}, ['high']):
            self.db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)',
                            (url, pair, json.dumps(public), 'queued', '{}', has.now()))
        self.db.commit()
        hub = FakeHub()
        config = {'community_public_id': 'employment-freelance-opportunities'}
        for row in self.db.execute('SELECT * FROM jobs').fetchall():
            has.publish(self.db, hub, config, row, lambda _: '4')
        requests = [payload for path, payload in hub.calls if path.endswith('request-authorization')]
        self.assertEqual(requests, [{'communityPublicId': config['community_public_id'], 'type': 'topic'}] * 2)
        posts = [payload for path, payload in hub.calls if path.endswith('/create')]
        self.assertEqual(len(posts), 2)
        self.assertNotEqual(posts[0]['title'], posts[1]['title'])
        for post in posts:
            self.assertEqual(post['content'].count('Candidatura:'), 1)

    def test_lost_confirmation_not_republished(self):
        hub = FakeHub('/posts/confirm')
        with self.assertRaises(OSError):
            self.publish(hub)
        self.assertEqual(self.row()['stage'], 'confirm_uncertain')
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            self.publish(hub)
        self.assertEqual(len(hub.calls), 4)

    def test_rejected_never_marked_published(self):
        with self.assertRaisesRegex(ValueError, 'rejected'):
            self.publish(FakeHub(status='rejected'))
        self.assertEqual(self.row()['stage'], 'challenged')

    def test_challenge_solver_failure_resumes(self):
        hub = FakeHub()
        def fail(_):
            raise ValueError('solver unavailable')
        with self.assertRaises(ValueError):
            has.publish(self.db, hub, {'community_public_id': 'jobs'}, self.row(), fail)
        self.assertEqual(self.row()['stage'], 'challenged')
        self.publish(hub)
        self.assertEqual(len(hub.calls), 3)

    def test_dedup_and_filter(self):
        batch = {'complete': True, 'jobs': [self.job, dict(self.job, url='https://example.com/2'), dict(self.job, title='Other', fit='low')]}
        self.assertEqual(len(has.validate(batch, ['high'])), 1)

    def test_incomplete_collection_fails(self):
        with self.assertRaises(ValueError):
            has.validate({'jobs': [], 'complete': False}, ['high'])

    def test_nullable_canonical_dates_and_closed_jobs(self):
        batch = {'jobs': [dict(self.job, posted_date=None, deadline=None),
                          dict(self.job, url='https://example.org/closed', status='expired')], 'complete': True}
        jobs = has.validate(batch, ['high'])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0][2]['deadline'], '')

    def test_next_step_rejects_unrelated_endpoint(self):
        with self.assertRaises(ValueError):
            has.next_step({'next_step': {'endpoint': 'https://evil.test', 'method': 'POST'}}, '/posts/create', {})

    def test_redirect_is_blocked(self):
        self.assertIsNone(has.NoRedirect().redirect_request(None, None, None, None, None, None))

    def test_rate_limit_preserves_stage_and_cooldown(self):
        class Limited(FakeHub):
            def call(self, path, payload):
                raise has.HubError(429, 86400)
        with self.assertRaises(has.HubError):
            self.publish(Limited())
        self.assertEqual(self.row()['stage'], 'queued')
        self.assertGreater(float(self.db.execute("SELECT value FROM meta WHERE key='retry_after'").fetchone()[0]), has.time.time() + 86000)


@unittest.skipUnless(os.name == "posix", "Daily runner uses Linux file locking")
class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.addCleanup(patch.stopall)
        patch.object(has, 'ROOT', self.root).start()
        self.hub = FakeHub()
        patch.object(has, 'Hub', return_value=self.hub).start()
        patch.object(has, 'heartbeat').start()
        patch.object(has, 'codex', return_value={'answer': '4'}).start()
        self.config = {'enabled': False, 'community_public_id': 'employment-freelance-opportunities',
                       'credentials': '.agenticspace/credentials.json'}
        (self.root / 'config.json').write_text(json.dumps(self.config))
        self.args = Namespace(config='config.json', once=True, dry_run=False, queue_only=False,
                              input=self.root / has.BATCH)
        self.args.input.parent.mkdir(parents=True)
        self.batch = {'complete': True, 'notes': '', 'jobs': [
            {'title': 'Engineer', 'company': 'Acme', 'url': 'https://example.org/1', 'fit': 'high'}]}
        self.args.input.write_text(json.dumps(self.batch))

    def test_manual_run_works_while_schedule_disabled_and_consumes_handoff(self):
        self.assertEqual(has.run(self.args), 0)
        self.assertFalse(self.args.input.exists())
        with has.connect(self.root / 'automation/state/queue.sqlite3') as db:
            self.assertEqual(db.execute('SELECT stage FROM jobs').fetchone()[0], 'confirmed')
        self.assertFalse(json.loads((self.root / 'config.json').read_text())['enabled'])

    def test_disabled_timer_never_publishes(self):
        self.args.once = False
        self.assertEqual(has.run(self.args), 0)
        self.assertEqual(self.hub.calls, [])
        self.assertTrue(self.args.input.exists())

    def test_queue_survives_failure_after_handoff_acknowledgement(self):
        self.hub.failure = '/posts/confirm'
        with self.assertRaises(OSError):
            has.run(self.args)
        self.assertFalse(self.args.input.exists())
        with has.connect(self.root / 'automation/state/queue.sqlite3') as db:
            self.assertEqual(db.execute('SELECT stage FROM jobs').fetchone()[0], 'confirm_uncertain')

    def test_queue_only_never_collects(self):
        self.args.queue_only = True
        self.args.input = None
        with patch.object(has, 'collect', side_effect=AssertionError('unexpected collection')):
            self.assertEqual(has.run(self.args), 0)

    def test_collect_resumes_persisted_batch_before_reading_configuration(self):
        self.assertEqual(has.collect({}), self.batch)

    def test_incomplete_setup_is_blocked(self):
        self.args.input.unlink()
        search = self.root / 'search.md'
        search.write_text('Priority 1: [YOUR_PRIMARY_ROLE_TYPE]')
        with self.assertRaisesRegex(ValueError, 'Finish /setup'):
            has.collect({'search_instructions': 'search.md'})


if __name__ == '__main__':
    unittest.main()
