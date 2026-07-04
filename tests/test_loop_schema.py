import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "schemas" / "loop-run.schema.json"


class LoopRunSchemaTests(unittest.TestCase):
    def test_loop_run_schema_is_valid_json_and_declares_v2_contract(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(schema["properties"]["schema_version"]["const"], 1)
        self.assertEqual(schema["properties"]["contract_version"]["const"], 2)
        self.assertEqual(
            schema["properties"]["contract_id"]["const"], "laos.loop-run.v2"
        )
        self.assertFalse(schema["additionalProperties"])

    def test_loop_run_schema_requires_all_runtime_contract_fields(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        required = set(schema["required"])
        expected = {
            "schema_version",
            "contract_version",
            "contract_id",
            "run_id",
            "idempotency_key",
            "task_sha256",
            "result_sha256",
            "outcome",
            "candidate_ids",
            "generated_candidate_ids",
            "approved_policy_ids",
            "policy_suggestion_ids",
            "reflection_path",
            "policy_suggestions_path",
            "reflection_status",
            "policy_status",
            "candidate_status",
            "review_required",
            "approved_policy_count",
            "status",
        }

        self.assertEqual(required, expected)

    def test_partition_provenance_is_optional_for_legacy_v2_but_complete_when_present(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

        self.assertEqual(
            schema["properties"]["workspace"]["enum"], ["personal", "work"]
        )
        self.assertNotIn("workspace", schema["required"])
        self.assertNotIn("project", schema["required"])
        partition_rule = schema["allOf"][0]
        self.assertEqual(
            set(partition_rule["then"]["required"]), {"workspace", "project"}
        )


if __name__ == "__main__":
    unittest.main()
