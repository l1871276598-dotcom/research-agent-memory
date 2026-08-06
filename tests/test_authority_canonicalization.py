import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "src"
if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

from review.authority import canonical_bytes


class AuthorityCanonicalizationTests(unittest.TestCase):
    def test_authority_canonical_bytes_are_sorted_compact_and_newline_free(self):
        raw = canonical_bytes({"z": 1, "a": "记忆"})

        self.assertEqual(raw, b'{"a":"\\u8bb0\\u5fc6","z":1}')
        self.assertFalse(raw.endswith(b"\n"))


if __name__ == "__main__":
    unittest.main()
