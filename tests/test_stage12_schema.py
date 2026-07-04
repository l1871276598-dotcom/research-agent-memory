import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "schemas" / "policy-candidates.schema.json"


class Stage12SchemaTests(unittest.TestCase):
    def test_contract_identity_and_closed_shapes(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["schema_version"]["const"], 1)
        self.assertEqual(
            schema["properties"]["contract_id"]["const"],
            "laos.policy-candidates.v1",
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(schema["$defs"]["candidate"]["additionalProperties"])

    def test_required_fields_and_statuses_match_runtime(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            set(schema["required"]),
            {
                "schema_version",
                "contract_id",
                "run_id",
                "source_reflection_artifact_sha256",
                "candidates",
                "review_required",
                "applied",
            },
        )
        self.assertEqual(
            set(schema["$defs"]["candidate"]["required"]),
            {
                "policy_id",
                "text",
                "normalized_text",
                "effect",
                "subject",
                "status",
                "duplicate_of",
                "conflicts_with",
            },
        )
        self.assertEqual(
            set(schema["$defs"]["candidate"]["properties"]["status"]["enum"]),
            {"review_required", "duplicate", "conflicted"},
        )


if __name__ == "__main__":
    unittest.main()
