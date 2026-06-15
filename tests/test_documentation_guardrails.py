from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DocumentationGuardrailTests(unittest.TestCase):
    def test_readme_does_not_recommend_manual_trade_memory(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertNotIn("TRADE_MEMORY_ENABLED=true", text)
        self.assertNotIn("TRADE_MEMORY_DB_PATH", text)

    def test_manual_trade_memory_doc_is_marked_deprecated(self):
        text = (ROOT / "docs" / "pipeline_finalization_trade_memory_v1.md").read_text(encoding="utf-8")

        self.assertIn("Deprecated", text)
        self.assertIn("manual trade tracking is out of scope", text.lower())


if __name__ == "__main__":
    unittest.main()
