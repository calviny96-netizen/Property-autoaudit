"""Dashboard-triggered deploy: state is read from files, so it survives the API restarting itself."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

import deploy


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.script = self.root / 'deploy.sh'
        self.state = self.root / 'state'
        env = patch.dict(os.environ, {'XM_DEPLOY_SCRIPT': str(self.script), 'XM_DEPLOY_STATE_DIR': str(self.state)})
        env.start()
        self.addCleanup(env.stop)

    def write_script(self, body):
        self.script.write_text('#!/usr/bin/env bash\n' + body + '\n')

    def wait_until_done(self):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            current = deploy.status()
            if current['status'] != 'running':
                return current
            time.sleep(.05)
        self.fail('deploy did not finish')

    def test_disabled_without_script(self):
        with patch.dict(os.environ, {'XM_DEPLOY_SCRIPT': ''}):
            self.assertEqual(deploy.status(), {'enabled': False})
            with self.assertRaises(HTTPException) as raised:
                deploy.start()
            self.assertEqual(raised.exception.status_code, 404)
        # Configured but missing on disk counts as disabled too.
        self.assertEqual(deploy.status(), {'enabled': False})

    def test_idle_before_first_run(self):
        self.write_script('true')
        current = deploy.status()
        self.assertTrue(current['enabled'])
        self.assertEqual(current['status'], 'idle')
        self.assertEqual(current['log'], '')
        self.assertIsNone(current['started_at'])

    def test_successful_run_records_log_and_exit_code(self):
        self.write_script("printf '\\033[1;32m✓ beres\\033[0m\\n'")
        deploy.start()
        current = self.wait_until_done()
        self.assertEqual(current['status'], 'success')
        self.assertEqual(current['exit_code'], 0)
        self.assertEqual(current['log'].strip(), '✓ beres')
        self.assertIsNotNone(current['started_at'])
        self.assertIsNotNone(current['finished_at'])

    def test_failed_run_keeps_stderr_and_exit_code(self):
        self.write_script('echo gagal >&2; exit 3')
        deploy.start()
        current = self.wait_until_done()
        self.assertEqual(current['status'], 'failed')
        self.assertEqual(current['exit_code'], 3)
        self.assertIn('gagal', current['log'])

    def test_second_start_is_rejected_while_running(self):
        release = self.root / 'release'
        self.write_script(f'echo mulai; while [ ! -e "{release}" ]; do sleep 0.05; done')
        deploy.start()
        try:
            self.assertEqual(deploy.status()['status'], 'running')
            with self.assertRaises(HTTPException) as raised:
                deploy.start()
            self.assertEqual(raised.exception.status_code, 409)
        finally:
            release.touch()
        self.assertEqual(self.wait_until_done()['status'], 'success')

    def test_new_run_clears_previous_result(self):
        self.write_script('echo pertama; exit 1')
        deploy.start()
        self.assertEqual(self.wait_until_done()['status'], 'failed')
        self.write_script('echo kedua')
        deploy.start()
        current = self.wait_until_done()
        self.assertEqual(current['status'], 'success')
        self.assertNotIn('pertama', current['log'])

    def test_run_without_result_is_reported_as_interrupted(self):
        self.write_script('true')
        self.state.mkdir()
        (self.state / 'deploy.started').write_text('')
        current = deploy.status()
        self.assertEqual(current['status'], 'failed')
        self.assertIsNone(current['exit_code'])

    def test_script_does_not_inherit_api_secrets(self):
        self.write_script('echo "pw=[${POSTGRES_PASSWORD:-}] node=[${NODE_ENV:-}] path=${PATH:+ada}"')
        with patch.dict(os.environ, {'POSTGRES_PASSWORD': 'rahasia', 'NODE_ENV': 'production'}):
            deploy.start()
            current = self.wait_until_done()
        self.assertIn('pw=[] node=[] path=ada', current['log'])


if __name__ == '__main__':
    unittest.main()
