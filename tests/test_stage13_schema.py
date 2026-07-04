import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "schemas" / "low-risk-candidate.schema.json"


class Stage13SchemaTests(unittest.TestCase):
    def test_contract_identity_threshold_and_closed_shape(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["schema_version"]["const"], 1)
        self.assertEqual(
            schema["properties"]["contract_id"]["const"],
            "laos.low-risk-candidate.v1",
        )
        self.assertEqual(
            schema["properties"]["minimum_independent_evidence"]["const"], 3
        )
        self.assertFalse(schema["additionalProperties"])

    def test_schema_requires_two_phase_candidate_state(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            set(schema["required"]),
            {
                "schema_version",
                "contract_id",
                "request_id",
                "policy_id",
                "policy_text",
                "workspace",
                "project",
                "minimum_independent_evidence",
                "evidence_run_ids",
                "evidence_fingerprints",
                "candidate_id",
                "candidate_path",
                "status",
                "applied",
            },
        )
        self.assertEqual(
            set(schema["properties"]["status"]["enum"]),
            {"pending_creation", "candidate"},
        )
        self.assertEqual(schema["properties"]["evidence_run_ids"]["minItems"], 3)
        self.assertEqual(
            schema["properties"]["evidence_fingerprints"]["minItems"], 3
        )


if __name__ == "__main__":
    unittest.main()
