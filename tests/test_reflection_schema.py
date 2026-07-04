import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "schemas" / "reflection-result.schema.json"


class ReflectionResultSchemaTests(unittest.TestCase):
    def test_schema_declares_exact_reflection_contract(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(schema["properties"]["schema_version"]["const"], 1)
        self.assertEqual(
            schema["properties"]["contract_id"]["const"],
            "laos.reflection-result.v1",
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(schema["properties"]["reflection"]["additionalProperties"])

    def test_schema_requires_runtime_artifact_fields(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

        self.assertEqual(
            set(schema["required"]),
            {
                "schema_version",
                "contract_id",
                "run_id",
                "source_reflection_sha256",
                "reflection",
            },
        )
        self.assertEqual(
            set(schema["properties"]["reflection"]["required"]),
            {
                "run_id",
                "outcome",
                "what_happened",
                "what_worked",
                "what_failed",
                "likely_cause",
                "reusable_lesson",
                "evidence",
                "unknown_fields",
            },
        )


if __name__ == "__main__":
    unittest.main()
