import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.app_diagnostics_service import build_dev_status, build_latest_run_manifest
from app.services.commit_identity_service import build_commit_identity
from app.services.developer_snapshot_service import build_developer_snapshot
from app.services.report_snapshot_service import ReportSnapshotRepository
from app.services.run_manifest_repository import RunManifestRepository, build_run_manifest


class Patch27RCommitIdentityReconciliationTests(unittest.TestCase):
    def test_commit_identity_agrees_when_sources_match(self):
        identity = build_commit_identity(
            {"git_commit": "abc", "git_branch": "main", "deploy_label": "dep"},
            env={"RAILWAY_GIT_COMMIT_SHA": "abc", "RAILWAY_GIT_BRANCH": "main", "RAILWAY_DEPLOYMENT_ID": "dep"},
        )
        self.assertFalse(identity["commit_identity_mismatch"])
        self.assertEqual(identity["source_of_truth"], "abc")
        self.assertEqual(identity["source_of_truth_field"], "run_manifest_git_commit")

    def test_commit_identity_reports_mismatch_without_failure(self):
        identity = build_commit_identity(
            {"git_commit": "run-commit"},
            env={"RAILWAY_GIT_COMMIT_SHA": "app-commit", "GIT_COMMIT": "build-commit"},
        )
        self.assertTrue(identity["commit_identity_mismatch"])
        self.assertEqual(identity["app_git_commit"], "app-commit")
        self.assertEqual(identity["build_git_commit"], "build-commit")
        self.assertEqual(identity["run_manifest_git_commit"], "run-commit")
        self.assertEqual(identity["source_of_truth"], "run-commit")
        self.assertTrue(identity["app_build_identity_mismatch"])
        self.assertTrue(identity["mismatch_requires_attention"])

    def test_stale_run_manifest_after_deploy_is_expected_not_failure(self):
        identity = build_commit_identity(
            {"git_commit": "old-run"},
            env={"RAILWAY_GIT_COMMIT_SHA": "new-deploy", "GIT_COMMIT": "new-deploy"},
        )
        self.assertTrue(identity["commit_identity_mismatch"])
        self.assertFalse(identity["app_build_identity_mismatch"])
        self.assertTrue(identity["latest_run_manifest_stale_after_deploy"])
        self.assertTrue(identity["latest_run_predates_current_deploy"])
        self.assertTrue(identity["mismatch_expected_due_to_stale_run"])
        self.assertTrue(identity["fresh_run_needed_to_refresh_manifest"])
        self.assertEqual(identity["fresh_run_expected_manifest_commit"], "new-deploy")
        self.assertEqual(identity["commit_identity_status"], "stale_run_manifest_after_deploy")
        self.assertFalse(identity["mismatch_requires_attention"])

    def test_fresh_run_alignment_clears_expected_mismatch(self):
        identity = build_commit_identity(
            {"git_commit": "new-deploy"},
            env={"RAILWAY_GIT_COMMIT_SHA": "new-deploy", "GIT_COMMIT": "new-deploy"},
        )
        self.assertFalse(identity["commit_identity_mismatch"])
        self.assertFalse(identity["latest_run_manifest_stale_after_deploy"])
        self.assertFalse(identity["fresh_run_needed_to_refresh_manifest"])
        self.assertTrue(identity["fresh_run_identity_aligned"])
        self.assertEqual(identity["commit_identity_status"], "fresh_run_identity_aligned")

    def test_app_build_mismatch_is_distinct_from_stale_run_manifest(self):
        identity = build_commit_identity(
            {"git_commit": "run-commit"},
            env={"RAILWAY_GIT_COMMIT_SHA": "app-commit", "GIT_COMMIT": "build-commit"},
        )
        self.assertTrue(identity["app_build_identity_mismatch"])
        self.assertFalse(identity["latest_run_manifest_stale_after_deploy"])
        self.assertFalse(identity["mismatch_expected_due_to_stale_run"])
        self.assertTrue(identity["mismatch_requires_attention"])
        self.assertEqual(identity["commit_identity_status"], "app_build_identity_mismatch")

    def test_missing_commit_metadata_degrades_to_unknown(self):
        identity = build_commit_identity({}, env={})
        self.assertEqual(identity["source_of_truth"], "unknown")
        self.assertEqual(identity["app_git_commit"], "unknown")
        self.assertFalse(identity["commit_identity_mismatch"])
        self.assertEqual(identity["commit_identity_status"], "unknown_commit_identity")
        self.assertFalse(identity["provider_calls_triggered"])
        self.assertTrue(identity["read_only"])

    def test_status_uses_source_of_truth_and_exposes_source_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.sqlite3")
            with patch("app.services.app_diagnostics_service.RunManifestRepository", lambda: RunManifestRepository(path)), \
                 patch.dict("os.environ", {"RAILWAY_GIT_COMMIT_SHA": "old", "GIT_COMMIT": "new"}, clear=True):
                RunManifestRepository(path).save({"run_id": "run-1", "status": "complete", "git_commit": "new"})
                status = build_dev_status(run_lock={"held": False})

        self.assertEqual(status["git_commit"], "new")
        self.assertTrue(status["commit_identity_mismatch"])
        self.assertEqual(status["commit_identity"]["app_git_commit"], "old")
        self.assertEqual(status["commit_identity"]["run_manifest_git_commit"], "new")
        self.assertTrue(status["commit_identity"]["app_build_identity_mismatch"])
        self.assertFalse(status["provider_calls_triggered"])

    def test_manifest_and_snapshot_include_commit_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.sqlite3")
            reports = ReportSnapshotRepository(path)
            manifests = RunManifestRepository(path)
            reports.save_success("run-1", "dev", "payload", {"report_data": {"tradier_snapshot": {}}}, {}, {})
            manifests.save({"run_id": "run-1", "status": "complete", "git_commit": "run-commit"})
            with patch("app.services.app_diagnostics_service.RunManifestRepository", lambda: RunManifestRepository(path)), \
                 patch.dict("os.environ", {"RAILWAY_GIT_COMMIT_SHA": "app-commit"}, clear=True):
                manifest = build_latest_run_manifest()
                snapshot = build_developer_snapshot("latest", reports, manifests)

        self.assertEqual(manifest["commit_identity"]["source_of_truth"], "run-commit")
        self.assertEqual(snapshot["commit_identity"]["source_of_truth"], "run-commit")
        self.assertEqual(snapshot["git_commit"], "run-commit")

    def test_new_run_manifest_records_explicit_commit_identity(self):
        with patch.dict("os.environ", {"RAILWAY_GIT_COMMIT_SHA": "commit-a", "RAILWAY_GIT_BRANCH": "main"}, clear=True):
            manifest = build_run_manifest("run-1", "dev", "complete", "SUCCESS_COMPLETE", {}, {"sections_bytes": {}}, {}, {}, {})
        self.assertEqual(manifest["git_commit"], "commit-a")
        self.assertEqual(manifest["commit_identity"]["source_of_truth"], "commit-a")
        self.assertFalse(manifest["commit_identity"]["commit_identity_mismatch"])


if __name__ == "__main__":
    unittest.main()
