import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config
from app.main import app
from app.services.report_snapshot_service import ReportSnapshotRepository


class Patch25ESnapshotLoadingTests(unittest.TestCase):
    def test_dashboard_get_renders_snapshot_without_starting_pipeline(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "reports.sqlite3")
            with patch.object(config, "REPORT_SNAPSHOT_DB_PATH", path), patch.object(config, "RUN_TOKEN", "test-token"):
                ReportSnapshotRepository().save_success(
                    "run-1", "dev", "payload",
                    {"report_data": {"positions": [], "news": {}, "recommendations": [], "tradier_snapshot": {"_pipeline_status": {"mode": "dev", "steps": []}}, "log": []}},
                    {}, {},
                )
                with patch("app.main.run") as pipeline:
                    response = app.test_client().get("/?token=test-token")
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"cached server snapshot", response.data)
                pipeline.assert_not_called()


if __name__ == "__main__":
    unittest.main()
