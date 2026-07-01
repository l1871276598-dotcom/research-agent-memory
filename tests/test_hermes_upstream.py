import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_hermes_updates", REPO_ROOT / "tools" / "check_hermes_updates.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class HermesUpstreamTests(unittest.TestCase):
    def test_component_map_keeps_model_backends_and_excludes_rejected_features(self):
        config = json.loads(
            (REPO_ROOT / "config" / "hermes_upstream_components.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertFalse(config["policy"]["vendor_full_repository"])
        self.assertFalse(config["policy"]["run_external_hermes"])
        self.assertEqual(config["policy"]["excluded_feature_numbers"], [12, 21])

        components = {component["number"]: component for component in config["components"]}
        self.assertIn(2, components)
        self.assertEqual(components[2]["id"], "model_backends")
        self.assertEqual(components[2]["status"], "foundation_integrated")
        self.assertFalse(set(components) & {12, 21})

    def test_path_matching_accepts_files_and_subtrees(self):
        self.assertTrue(MODULE._matches("agent/background_review.py", ["agent/background_review.py"]))
        self.assertTrue(MODULE._matches("gateway/telegram.py", ["gateway/"]))
        self.assertFalse(MODULE._matches("cron/jobs.py", ["gateway/"]))

    def test_security_and_breaking_risk_take_precedence(self):
        self.assertEqual(
            MODULE._risk("tools/approval.py", "modified", "fix security token leak"),
            "security",
        )
        self.assertEqual(MODULE._risk("agent/example.py", "removed", "cleanup"), "breaking")

    def test_mark_reviewed_updates_only_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "components.json"
            path.write_text(
                json.dumps(
                    {
                        "components": [],
                        "last_reviewed_commit": "a" * 40,
                        "unchanged": {"value": 1},
                    }
                ),
                encoding="utf-8",
            )
            result = MODULE._mark_reviewed(path, "b" * 40)
            updated = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(result["previous"], "a" * 40)
            self.assertEqual(updated["last_reviewed_commit"], "b" * 40)
            self.assertEqual(updated["unchanged"], {"value": 1})


if __name__ == "__main__":
    unittest.main()
