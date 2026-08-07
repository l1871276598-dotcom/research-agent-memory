"""VaultReadAgent tests — fd-rooted traversal (GP3-01 / S8).

The fd-rooted traversal must reject symlinks at EVERY level (final component
and intermediate directory), reject traversal in the relative path, never read
a vault-outside file, and detect mutation on the opened descriptor. These are
the exact GP3-01 attack scenarios.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO / "src") not in sys.path:
    sys.path.insert(0, str(_REPO / "src"))

from agents.vault_read import VaultReadAgent, _read_vault_note_fd_rooted


class VaultReadAgentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.vault = base / "vault"
        self.vault.mkdir()
        (self.vault / "note.md").write_text("note body", encoding="utf-8")
        (self.vault / "sub").mkdir()
        (self.vault / "sub" / "nested.md").write_text("nested body", encoding="utf-8")

    def agent(self):
        return VaultReadAgent()

    def read(self, relative_path):
        return self.agent().run(
            {"type": "vault.read", "input": {"vault_root": str(self.vault), "relative_path": relative_path}},
            {},
        )["output"]["content"]

    # ---- happy path ----
    def test_reads_note(self):
        self.assertEqual(self.read("note.md"), "note body")

    def test_reads_nested_note_via_intermediate_dir(self):
        self.assertEqual(self.read("sub/nested.md"), "nested body")

    # ---- lexical traversal ----
    def test_rejects_traversal(self):
        for bad in ["../etc/passwd", "a/../../x", "./note.md", "a/../x", "\\..\\x", "/etc/passwd", "a//b", "a\\b", ".."]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.read(bad)

    def test_rejects_nul(self):
        with self.assertRaises(ValueError):
            self.read("a\0b")

    # ---- symlink attacks (GP3-01) ----
    def test_rejects_final_component_symlink(self):
        # A symlink planted as the note itself must be rejected.
        os.symlink("/etc/passwd", self.vault / "evil.md")
        with self.assertRaises(ValueError):
            self.read("evil.md")

    def test_rejects_intermediate_directory_symlink(self):
        # A symlink replacing an intermediate directory must be rejected.
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        (outside / "data.txt").write_text("secret", encoding="utf-8")
        os.symlink(outside, self.vault / "sub_link")
        with self.assertRaises(ValueError):
            self.read("sub_link/data.txt")

    def test_rejects_vault_root_symlink(self):
        real = Path(self.temporary.name) / "real_vault"
        real.mkdir()
        (real / "n.md").write_text("x", encoding="utf-8")
        link = Path(self.temporary.name) / "vault_link"
        os.symlink(real, link)
        # The vault root itself must be a real directory (lstat rejects symlink).
        with self.assertRaises(ValueError):
            _read_vault_note_fd_rooted(str(link), "n.md")

    def test_rejects_nonexistent(self):
        with self.assertRaises(ValueError):
            self.read("missing.md")

    # ---- exact input schema ----
    def test_rejects_extra_input_fields(self):
        with self.assertRaises(ValueError):
            self.agent().run(
                {"type": "vault.read", "input": {"vault_root": str(self.vault), "relative_path": "note.md", "extra": 1}},
                {},
            )

    def test_rejects_non_string_fields(self):
        with self.assertRaises(ValueError):
            self.agent().run(
                {"type": "vault.read", "input": {"vault_root": str(self.vault), "relative_path": 42}},
                {},
            )

    def test_never_creates_candidates_or_authority(self):
        result = self.agent().run(
            {"type": "vault.read", "input": {"vault_root": str(self.vault), "relative_path": "note.md"}},
            {},
        )
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["requires_review"], True)


# Direct function-level tests of the fd-rooted primitive (no agent wrapper).
class FdRootedReadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.vault = base / "vault"
        self.vault.mkdir()
        (self.vault / "f.txt").write_text("hello", encoding="utf-8")

    def test_fd_rooted_reads_regular_file(self):
        self.assertEqual(_read_vault_note_fd_rooted(str(self.vault), "f.txt"), "hello")

    def test_fd_rooted_rejects_final_symlink(self):
        os.symlink("/etc/passwd", self.vault / "link.txt")
        with self.assertRaises(Exception):
            _read_vault_note_fd_rooted(str(self.vault), "link.txt")


if __name__ == "__main__":
    unittest.main()
