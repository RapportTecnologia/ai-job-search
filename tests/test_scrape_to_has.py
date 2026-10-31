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


class HeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.db = has.connect(self.folder / 'queue.db')
        self.addCleanup(self.db.close)

    def test_optional_probe_auth_and_missing_route_do_not_block_heartbeat(self):
        class Hub:
            def call(self, path):
                if path == '/agents/me':
                    return {'data': {'id': 'ai-job-search'}}
                if path == '/auth/accounts':
                    raise has.HubError(401)
                if path == '/agents/me/posts?limit=50':
                    raise has.HubError(404)
                return {'data': {'ok': True}}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, _limit): return b'# heartbeat\n'

        class Opener:
            def open(self, *_args, **_kwargs): return Response()

        with patch.object(has, 'build_opener', return_value=Opener()):
            has.heartbeat(self.db, Hub(), self.folder)

        checks = json.loads((self.folder / 'heartbeat-checks.json').read_text())
        self.assertEqual(checks['/auth/accounts'], {'status': 'unavailable', 'http_status': 401})
        self.assertEqual(checks['/agents/me/posts?limit=50'], {'status': 'unavailable', 'http_status': 404})
        self.assertEqual(json.loads((self.folder / 'heartbeat-status.json').read_text())['status'], 'success')
        self.assertIsNotNone(self.db.execute("SELECT value FROM meta WHERE key='heartbeat'").fetchone())

    def test_agent_identity_auth_failure_still_stops_heartbeat(self):
        class Hub:
            def call(self, path):
                raise has.HubError(401)

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, _limit): return b'# heartbeat\n'

        class Opener:
            def open(self, *_args, **_kwargs): return Response()

        with patch.object(has, 'build_opener', return_value=Opener()):
            with self.assertRaises(has.HubError):
                has.heartbeat(self.db, Hub(), self.folder)
        self.assertEqual(json.loads((self.folder / 'heartbeat-status.json').read_text())['status'], 'error')


class CooldownTests(unittest.TestCase):
    def test_persisted_deadline_has_margin_and_waits_after_expiry(self):
        with tempfile.TemporaryDirectory() as folder:
            with has.connect(Path(folder) / 'queue.db') as db:
                before = has.time.time()
                deadline = has.persist_cooldown(db, 60)
                self.assertGreaterEqual(deadline, before + 60)
                with patch.object(has.time, 'time', return_value=deadline), patch.object(has.time, 'sleep') as sleep:
                    allowed, waited = has.wait_for_cooldown(db)
                self.assertTrue(allowed)
                self.assertEqual(waited, has.COOLDOWN_SAFETY_SECONDS + 1)
                sleep.assert_called_once_with(has.COOLDOWN_SAFETY_SECONDS + 1)
                self.assertIsNone(db.execute("SELECT value FROM meta WHERE key='retry_after'").fetchone())


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

    def test_write_429_waits_out_countdown_then_resumes_same_queue_item(self):
        real_call = self.hub.call
        rate_limited = {'once': False}

        def call_with_one_rate_limit(path, payload=None):
            if path == '/posts/request-authorization' and not rate_limited['once']:
                rate_limited['once'] = True
                raise has.HubError(429, 60)
            return real_call(path, payload)

        self.hub.call = call_with_one_rate_limit

        def fast_wait(db, already_waited=0):
            db.execute("DELETE FROM meta WHERE key='retry_after'")
            db.commit()
            return True, already_waited + 76

        with patch.object(has, 'wait_for_cooldown', side_effect=fast_wait):
            self.assertEqual(has.run(self.args), 0)
        with has.connect(self.root / 'automation/state/queue.sqlite3') as db:
            self.assertEqual(db.execute('SELECT stage FROM jobs').fetchone()[0], 'confirmed')
        self.assertEqual(json.loads((self.root / 'automation/state/last-run.json').read_text())['status'], 'success')

    def test_heartbeat_429_is_persisted_and_deferred_without_systemd_failure(self):
        too_long = 6 * 3600
        with patch.object(has, 'heartbeat', side_effect=has.HubError(429, too_long)):
            self.assertEqual(has.run(self.args), 0)
        with has.connect(self.root / 'automation/state/queue.sqlite3') as db:
            retry = db.execute("SELECT value FROM meta WHERE key='retry_after'").fetchone()
            self.assertIsNotNone(retry)
            self.assertGreater(float(retry[0]), has.time.time() + too_long - 1)
        status = json.loads((self.root / 'automation/state/last-run.json').read_text())
        self.assertEqual(status['status'], 'deferred')
        self.assertTrue(self.args.input.exists())

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

    def test_collect_archives_incomplete_batch_and_retries(self):
        self.batch['complete'] = False
        self.args.input.write_text(json.dumps(self.batch))
        (self.root / 'search.md').write_text('Search the configured roles and locations.')
        fresh = dict(self.batch, complete=True)
        with patch.object(has, 'codex', return_value=fresh) as codex:
            result = has.collect({'search_instructions': 'search.md'})
        self.assertEqual(result, fresh)
        codex.assert_called_once()
        self.assertFalse(self.args.input.exists())
        archives = list((self.args.input.parent / 'incomplete').glob('scrape-batch-*.json'))
        self.assertEqual(len(archives), 1)
        self.assertFalse(json.loads(archives[0].read_text())['complete'])

    def test_incomplete_setup_is_blocked(self):
        self.args.input.unlink()
        search = self.root / 'search.md'
        search.write_text('Priority 1: [YOUR_PRIMARY_ROLE_TYPE]')
        with self.assertRaisesRegex(ValueError, 'Finish /setup'):
            has.collect({'search_instructions': 'search.md'})


if __name__ == '__main__':
    unittest.main()
