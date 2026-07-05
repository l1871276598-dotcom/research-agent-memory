import json
import unittest

import test_developer_bridge_adapter as adapter_tests


class DeveloperBridgeAdapterConflictTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        adapter_tests.DeveloperBridgeAdapterTests.setUpClass()
        cls.adapter = adapter_tests.DeveloperBridgeAdapterTests()

    @classmethod
    def tearDownClass(cls):
        adapter_tests.DeveloperBridgeAdapterTests.tearDownClass()

    def test_changed_content_for_final_checkpoint_fails_closed(self):
        checkpoint_id = "developer-bridge-conflict-checkpoint"
        first = self.adapter.capture(checkpoint_id=checkpoint_id)
        self.assertEqual(first.returncode, 0)
        original = json.loads(first.stdout)["result"]

        conflict = self.adapter.capture(
            checkpoint_id=checkpoint_id,
            assistant_response="Changed assistant text must not replace the original.",
        )
        self.adapter.assert_failure(conflict, "invalid_request")

        restored = self.adapter.invoke(
            {
                "operation": "session_get",
                "arguments": {"session_id": original["session_id"]},
            }
        )
        self.assertEqual(restored.returncode, 0)
        messages = json.loads(restored.stdout)["result"]["messages"]
        self.assertEqual(len(messages), 2)
        self.assertEqual(
            [message["content"] for message in messages],
            ["Preserve this exact user text.", "Preserve this exact assistant text."],
        )


if __name__ == "__main__":
    unittest.main()
