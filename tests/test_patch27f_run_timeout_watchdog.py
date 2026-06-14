import importlib
import threading
import time
import unittest
from unittest.mock import patch

from app import config
from app.providers import robinhood_provider
from app.services.app_diagnostics_service import build_dev_status

main = importlib.import_module("app.main")


def _result():
    return "payload", [], {}, [], {}, ["done"]


class Patch27FRunTimeoutWatchdogTests(unittest.TestCase):
    def setUp(self):
        self.old_lock = main.RUN_LOCK
        self.old_active = main.ACTIVE_JOB_ID
        self.old_jobs = dict(main.RUN_JOBS)
        main.RUN_LOCK = threading.Lock()
        main.ACTIVE_JOB_ID = None
        main.RUN_JOBS.clear()

    def tearDown(self):
        main._safe_release_lock(main.RUN_LOCK)
        main.RUN_LOCK = self.old_lock
        main.ACTIVE_JOB_ID = self.old_active
        main.RUN_JOBS.clear()
        main.RUN_JOBS.update(self.old_jobs)

    def test_robinhood_auth_request_deadline_breaks_infinite_prompt_poll(self):
        robinhood_provider._auth_deadline.value = time.monotonic() - 1
        try:
            with self.assertRaisesRegex(TimeoutError, "approval/login timed out"):
                robinhood_provider._bounded_auth_request_get("https://example.invalid")
        finally:
            robinhood_provider._auth_deadline.value = None

    def test_robinhood_login_timeout_returns_explicit_result(self):
        with patch.object(robinhood_provider.r, "login", side_effect=TimeoutError("Robinhood approval/login timed out after 1 seconds.")), \
             patch.object(robinhood_provider, "notify"), \
             patch.object(config, "ROBINHOOD_USERNAME", "user"), \
             patch.object(config, "ROBINHOOD_PASSWORD", "password"), \
             patch.object(config, "ROBINHOOD_LOGIN_TIMEOUT_SECONDS", 1):
            result = robinhood_provider.login_with_retry()
        self.assertFalse(result["success"])
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["status"], "auth_timeout")

    def test_robinhood_swallowed_timeout_return_is_not_treated_as_success(self):
        def swallowed_timeout(**kwargs):
            robinhood_provider._auth_deadline.timed_out = True
            return None

        with patch.object(robinhood_provider.r, "login", side_effect=swallowed_timeout), \
             patch.object(robinhood_provider, "notify"), \
             patch.object(config, "ROBINHOOD_USERNAME", "user"), \
             patch.object(config, "ROBINHOOD_PASSWORD", "password"), \
             patch.object(config, "ROBINHOOD_LOGIN_TIMEOUT_SECONDS", 1):
            result = robinhood_provider.login_with_retry()
        self.assertFalse(result["success"])
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["status"], "auth_timeout")

    def test_stale_run_rotates_lock_and_allows_retry(self):
        stale_lock = threading.Lock()
        stale_lock.acquire()
        main.RUN_LOCK = stale_lock
        main.ACTIVE_JOB_ID = "stale"
        main.RUN_JOBS["stale"] = {"status": "running", "created_at": 1.0, "started_at": 1.0}
        with patch.object(config, "RUN_STALE_TIMEOUT_SECONDS", 10):
            recovered = main._recover_stale_run_if_needed(now=20.0)
        self.assertTrue(recovered)
        self.assertEqual(main.RUN_JOBS["stale"]["status"], "timeout")
        self.assertEqual(main.RUN_JOBS["stale"]["timeout_reason"], "run_stale_timeout")
        self.assertTrue(main.RUN_JOBS["stale"]["retry_safe"])
        self.assertIsNone(main.ACTIVE_JOB_ID)
        self.assertFalse(main.RUN_LOCK.locked())
        main._safe_release_lock(stale_lock)

    def test_old_timed_out_worker_does_not_clobber_replacement_job(self):
        old_lock = threading.Lock()
        old_lock.acquire()
        main.RUN_JOBS["old"] = {"status": "timeout", "message": "timed out", "retry_safe": True}
        main.RUN_JOBS["new"] = {"status": "running"}
        main.ACTIVE_JOB_ID = "new"
        with patch.object(main, "run", return_value=_result()):
            main._run_job("old", "dev", old_lock)
        self.assertEqual(main.RUN_JOBS["old"]["status"], "timeout")
        self.assertEqual(main.ACTIVE_JOB_ID, "new")
        self.assertFalse(old_lock.locked())

    def test_normal_and_exception_runs_release_lock(self):
        for side_effect in (None, RuntimeError("boom")):
            lock = threading.Lock()
            lock.acquire()
            job_id = f"job-{side_effect is not None}"
            main.RUN_JOBS[job_id] = {"status": "running"}
            main.ACTIVE_JOB_ID = job_id
            with patch.object(main, "run", return_value=_result(), side_effect=side_effect):
                main._run_job(job_id, "dev", lock)
            self.assertFalse(lock.locked())
            self.assertIsNone(main.ACTIVE_JOB_ID)
            self.assertIn(main.RUN_JOBS[job_id]["status"], {"complete", "error"})

    def test_diagnostics_expose_lock_and_timeout_metadata_without_providers(self):
        jobs = {
            "timed": {
                "status": "timeout",
                "started_at": 1.0,
                "heartbeat_at": 2.0,
                "timeout_reason": "run_stale_timeout",
                "failed_stage": "background_run",
                "retry_safe": True,
            }
        }
        lock_state = {"held": False, "retry_safe": True, "timeout_reason": "run_stale_timeout"}
        with patch("app.services.app_diagnostics_service.RunManifestRepository") as manifests:
            manifests.return_value.latest.return_value = None
            status = build_dev_status(jobs, "timed", "booted", lock_state)
        self.assertEqual(status["active_run"]["timeout_reason"], "run_stale_timeout")
        self.assertTrue(status["active_run"]["retry_safe"])
        self.assertEqual(status["run_lock"], lock_state)
        self.assertFalse(status["provider_calls_triggered"])


if __name__ == "__main__":
    unittest.main()
