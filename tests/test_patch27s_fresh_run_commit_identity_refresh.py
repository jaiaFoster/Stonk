import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.app_diagnostics_service import build_dev_status, build_latest_run_manifest, build_latest_profiles
from app.services.commit_identity_service import build_commit_identity
from app.services.developer_snapshot_service import build_developer_snapshot
from app.services.report_snapshot_service import ReportSnapshotRepository
from app.services.run_manifest_repository import RunManifestRepository, build_run_manifest


class Patch27SFreshRunCommitIdentityRefreshTests(unittest.TestCase):
    def test_stale_latest_run_manifest_after_deploy_is_labeled_expected(self):
        identity = build_commit_identity(
            {"git_commit": "old-run"},
            env={"RAILWAY_GIT_COMMIT_SHA": "new-deploy", "GIT_COMMIT": "new-deploy"},
        )

        self.assertEqual(identity["commit_identity_status"], "stale_run_manifest_after_deploy")
        self.assertTrue(identity["latest_run_predates_current_deploy"])
        self.assertTrue(identity["mismatch_expected_due_to_stale_run"])
        self.assertTrue(identity["fresh_run_needed_to_refresh_manifest"])
        self.assertFalse(identity["mismatch_requires_attention"])

    def test_true_app_build_mismatch_requires_attention(self):
        identity = build_commit_identity(
            {"git_commit": "old-run"},
            env={"RAILWAY_GIT_COMMIT_SHA": "app-commit", "GIT_COMMIT": "build-commit"},
        )

        self.assertEqual(identity["commit_identity_status"], "app_build_identity_mismatch")
        self.assertTrue(identity["app_build_identity_mismatch"])
        self.assertFalse(identity["mismatch_expected_due_to_stale_run"])
        self.assertTrue(identity["mismatch_requires_attention"])

    def test_fresh_run_manifest_aligns_to_current_deploy_commit(self):
        with patch.dict("os.environ", {"RAILWAY_GIT_COMMIT_SHA": "new-deploy", "GIT_COMMIT": "new-deploy"}, clear=True):
            manifest = build_run_manifest("run-1", "dev", "complete", "SUCCESS_COMPLETE", {}, {"sections_bytes": {}}, {}, {}, {})

        self.assertEqual(manifest["git_commit"], "new-deploy")
        self.assertEqual(manifest["commit_identity"]["commit_identity_status"], "fresh_run_identity_aligned")
        self.assertTrue(manifest["commit_identity"]["fresh_run_identity_aligned"])
        self.assertFalse(manifest["commit_identity"]["commit_identity_mismatch"])

    def test_diagnostics_surface_stale_manifest_without_provider_calls(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.sqlite3")
            with patch("app.services.app_diagnostics_service.RunManifestRepository", lambda: RunManifestRepository(path)), \
                 patch.dict("os.environ", {"RAILWAY_GIT_COMMIT_SHA": "new-deploy", "GIT_COMMIT": "new-deploy"}, clear=True):
                RunManifestRepository(path).save({"run_id": "run-1", "status": "complete", "git_commit": "old-run"})
                status = build_dev_status(run_lock={"held": False})
                manifest = build_latest_run_manifest()

        self.assertFalse(status["provider_calls_triggered"])
        self.assertFalse(manifest["provider_calls_triggered"])
        self.assertTrue(status["commit_identity"]["latest_run_manifest_stale_after_deploy"])
        self.assertTrue(manifest["commit_identity"]["fresh_run_needed_to_refresh_manifest"])

    def test_snapshot_and_profile_commit_identity_remain_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.sqlite3")
            reports = ReportSnapshotRepository(path)
            manifests = RunManifestRepository(path)
            reports.save_success("run-1", "dev", "payload", {"report_data": {"tradier_snapshot": {}}}, {}, {})
            manifests.save({"run_id": "run-1", "status": "complete", "git_commit": "old-run"})

            with patch("app.services.developer_snapshot_service.RunManifestRepository", lambda: RunManifestRepository(path)), \
                 patch("app.services.developer_snapshot_service.ReportSnapshotRepository", lambda: ReportSnapshotRepository(path)), \
                 patch.dict("os.environ", {"RAILWAY_GIT_COMMIT_SHA": "new-deploy", "GIT_COMMIT": "new-deploy"}, clear=True):
                snapshot = build_developer_snapshot("latest", reports, manifests)
                profiles = build_latest_profiles()

        self.assertTrue(snapshot["read_only"])
        self.assertFalse(snapshot["provider_calls_triggered"])
        self.assertFalse(profiles["provider_calls_triggered"])
        self.assertTrue(snapshot["commit_identity"]["mismatch_expected_due_to_stale_run"])


if __name__ == "__main__":
    unittest.main()
