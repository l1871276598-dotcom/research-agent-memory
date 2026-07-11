"""Phase 1: Read-only vault scanner — first vertical slice tests.

Contract: _system/contracts/03-scan-index-and-conflict-rules.md

Vertical slice:
  Scan a single Markdown file
  → Compute evidence_hash (SHA-256, UTF-8, LF only)
  → Parse frontmatter (YAML)
  → Write to read-only index (SQLite)
  → Re-scan unchanged file → skip
"""

import hashlib
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "src"

if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

from vault_scanner.indexer import Indexer, PARSER_VERSION, export_receipt_records
from vault_scanner.scanner import scan_file


def _normalize_for_evidence(content: str) -> bytes:
    """evidence_hash normalization: UTF-8, LF only. Full file, no field exclusions."""
    return content.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _compute_evidence_hash(content: str) -> str:
    return hashlib.sha256(_normalize_for_evidence(content)).hexdigest()


class ScannerFirstSliceTests(unittest.TestCase):
    """Test the first vertical slice: scan → hash → parse → index → skip."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name) / "vault"
        self.state = Path(self.temp.name) / "state"
        self.vault.mkdir()
        self.state.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def _write_note(self, rel_path: str, content: str) -> Path:
        full = self.vault / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return full

    def _sample_note(self) -> str:
        return (
            "---\n"
            'schema_version: 1\n'
            'type: project\n'
            'lifecycle: active\n'
            'source: human\n'
            "created: 2026-07-07\n"
            "updated: 2026-07-07\n"
            "tags:\n"
            "  - drilling\n"
            "---\n"
            "\n"
            "# PDC Drilling Test\n"
            "\n"
            "This is a test note about [[PDC钻头]] and [[钻井液]].\n"
        )

    # ── evidence_hash ──────────────────────────────────────────────

    def test_evidence_hash_is_deterministic(self):
        content = self._sample_note()
        h1 = _compute_evidence_hash(content)
        h2 = _compute_evidence_hash(content)
        self.assertEqual(h1, h2)

    def test_evidence_hash_normalizes_crlf_to_lf(self):
        crlf = self._sample_note().replace("\n", "\r\n")
        lf = self._sample_note()
        self.assertEqual(_compute_evidence_hash(crlf), _compute_evidence_hash(lf))

    def test_evidence_hash_differs_when_content_differs(self):
        a = self._sample_note()
        b = a.replace("PDC Drilling Test", "PDC Drilling Modified")
        self.assertNotEqual(_compute_evidence_hash(a), _compute_evidence_hash(b))

    def test_evidence_hash_includes_updated_field(self):
        """Per contract: evidence_hash does NOT exclude updated."""
        a = self._sample_note()
        b = a.replace("updated: 2026-07-07", "updated: 2026-07-08")
        self.assertNotEqual(_compute_evidence_hash(a), _compute_evidence_hash(b))

    # ── frontmatter parse ─────────────────────────────────────────

    def test_parse_frontmatter_extracts_required_fields(self):
        from vault_scanner.parser import parse_frontmatter

        content = self._sample_note()
        fm, body = parse_frontmatter(content)

        self.assertEqual(fm["type"], "project")
        self.assertEqual(fm["lifecycle"], "active")
        self.assertEqual(fm["source"], "human")
        self.assertEqual(fm["created"], "2026-07-07")
        self.assertIn("PDC Drilling Test", body)

    def test_parse_frontmatter_extracts_wikilinks(self):
        from vault_scanner.parser import extract_wikilinks

        body = "This is about [[PDC钻头]] and [[钻井液]] and also [[PDC钻头]]."
        links = extract_wikilinks(body)
        self.assertEqual(set(links), {"PDC钻头", "钻井液"})

    def test_parse_frontmatter_quarantines_unclosed_delimiter(self):
        from vault_scanner.parser import parse_frontmatter, QuarantineError

        content = "---\ntype: project\nlifecycle: active\n\nBody without closing.\n"
        with self.assertRaises(QuarantineError):
            parse_frontmatter(content)

    # ── index write ────────────────────────────────────────────────

    def test_index_writes_manifest_row(self):
        from vault_scanner.scanner import scan_file
        from vault_scanner.indexer import Indexer

        content = self._sample_note()
        path = self._write_note("1-projects/test-note.md", content)

        db_path = self.state / "vault_index.sqlite"
        indexer = Indexer(db_path)
        indexer.init_db()

        result = scan_file(path, self.vault, indexer)

        self.assertEqual(result["status"], "indexed")
        self.assertEqual(result["relative_path"], "1-projects/test-note.md")
        self.assertEqual(result["evidence_hash"], _compute_evidence_hash(content))
        self.assertIsNotNone(result["note_id"])

        # Verify row in DB
        with closing(sqlite3.connect(str(db_path))) as conn:
            row = conn.execute(
                "SELECT relative_path, evidence_hash, status FROM index_manifest "
                "WHERE relative_path = ?",
                ("1-projects/test-note.md",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "1-projects/test-note.md")
        self.assertEqual(row[1], _compute_evidence_hash(content))
        self.assertEqual(row[2], "active")

    # ── skip on re-scan ────────────────────────────────────────────

    def test_rescan_unchanged_file_is_skipped(self):
        from vault_scanner.scanner import scan_file
        from vault_scanner.indexer import Indexer

        content = self._sample_note()
        path = self._write_note("1-projects/test-note.md", content)

        db_path = self.state / "vault_index.sqlite"
        indexer = Indexer(db_path)
        indexer.init_db()

        r1 = scan_file(path, self.vault, indexer)
        self.assertEqual(r1["status"], "indexed")

        r2 = scan_file(path, self.vault, indexer)
        self.assertEqual(r2["status"], "skipped")
        self.assertEqual(r2["reason"], "unchanged")

    # ── skip .icloud placeholder ───────────────────────────────────

    def test_skip_icloud_placeholder(self):
        from vault_scanner.scanner import scan_file
        from vault_scanner.indexer import Indexer

        path = self._write_note("1-projects/note.md.icloud", "")
        db_path = self.state / "vault_index.sqlite"
        indexer = Indexer(db_path)
        indexer.init_db()

        result = scan_file(path, self.vault, indexer)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "icloud_placeholder")

    # ── skip _system and .obsidian ─────────────────────────────────

    def test_skip_system_directory(self):
        from vault_scanner.scanner import scan_file
        from vault_scanner.indexer import Indexer

        content = self._sample_note()
        path = self._write_note("_system/contracts/test.md", content)
        db_path = self.state / "vault_index.sqlite"
        indexer = Indexer(db_path)
        indexer.init_db()

        result = scan_file(path, self.vault, indexer)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "excluded_directory")

    # ── skip symlink escape ──────────────────────────────────────

    def test_skip_symlink_pointing_outside_vault(self):
        """Symlink pointing outside vault_root → skipped with symlink_escape."""
        import os
        from vault_scanner.scanner import scan_file
        from vault_scanner.indexer import Indexer

        # Create a target file outside the vault
        outside_dir = Path(self.temp.name) / "outside"
        outside_dir.mkdir()
        outside_file = outside_dir / "secret.md"
        outside_file.write_text(self._sample_note(), encoding="utf-8")

        # Create a symlink inside vault pointing outside
        link_path = self.vault / "1-projects" / "link-to-outside.md"
        link_path.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(str(outside_file), str(link_path))

        db_path = self.state / "vault_index.sqlite"
        indexer = Indexer(db_path)
        indexer.init_db()

        result = scan_file(link_path, self.vault, indexer)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "symlink_escape")

    # ── Identity: frontmatter id-priority ──────────────────────────

    def test_frontmatter_id_used_as_note_id(self):
        from vault_scanner.scanner import scan_file
        from vault_scanner.indexer import Indexer
        from vault_scanner.scanner import _compute_rename_fingerprint

        content = (
            "---\n"
            "id: my-custom-id\n"
            "schema_version: 1\n"
            "type: project\n"
            "lifecycle: active\n"
            "created: 2026-07-07\n"
            "updated: 2026-07-07\n"
            "---\n"
            "\n"
            "Body text.\n"
        )
        path = self._write_note("1-projects/custom-id-note.md", content)

        db_path = self.state / "vault_index.sqlite"
        indexer = Indexer(db_path)
        indexer.init_db()

        result = scan_file(path, self.vault, indexer)

        self.assertEqual(result["status"], "indexed")
        self.assertEqual(result["note_id"], "my-custom-id")

        with closing(sqlite3.connect(str(db_path))) as conn:
            row = conn.execute(
                "SELECT note_id FROM index_manifest WHERE relative_path = ?",
                ("1-projects/custom-id-note.md",),
            ).fetchone()
        self.assertEqual(row[0], "my-custom-id")

    def test_no_id_does_not_write_back(self):
        from vault_scanner.scanner import scan_file
        from vault_scanner.indexer import Indexer

        content = self._sample_note()
        path = self._write_note("1-projects/no-id-note.md", content)

        db_path = self.state / "vault_index.sqlite"
        indexer = Indexer(db_path)
        indexer.init_db()

        result = scan_file(path, self.vault, indexer)
        self.assertEqual(result["status"], "indexed")

        # Read back the file; it must be unchanged
        after = path.read_text(encoding="utf-8")
        self.assertEqual(after, content)

    def test_last_indexed_hash_equals_evidence_hash(self):
        from vault_scanner.scanner import scan_file
        from vault_scanner.indexer import Indexer

        content = self._sample_note()
        path = self._write_note("1-projects/hash-match-note.md", content)

        db_path = self.state / "vault_index.sqlite"
        indexer = Indexer(db_path)
        indexer.init_db()

        result = scan_file(path, self.vault, indexer)
        self.assertEqual(result["status"], "indexed")

        with closing(sqlite3.connect(str(db_path))) as conn:
            row = conn.execute(
                "SELECT evidence_hash, last_indexed_hash FROM index_manifest "
                "WHERE relative_path = ?",
                ("1-projects/hash-match-note.md",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], result["evidence_hash"])
        self.assertEqual(row[1], row[0])

    def test_updated_affects_evidence_hash(self):
        from vault_scanner.scanner import scan_file, _compute_evidence_hash
        from vault_scanner.indexer import Indexer

        content_a = self._sample_note()
        content_b = content_a.replace("updated: 2026-07-07", "updated: 2026-07-08")

        ha = _compute_evidence_hash(content_a)
        hb = _compute_evidence_hash(content_b)
        self.assertNotEqual(ha, hb)

    def test_updated_does_not_affect_rename_fingerprint(self):
        from vault_scanner.scanner import _compute_rename_fingerprint

        content_a = (
            "---\n"
            "type: project\n"
            "updated: 2026-07-07\n"
            "---\n"
            "\n"
            "Body text.\n"
        )
        content_b = content_a.replace("updated: 2026-07-07", "updated: 2026-07-08")

        fp_a = _compute_rename_fingerprint(content_a)
        fp_b = _compute_rename_fingerprint(content_b)
        self.assertEqual(fp_a, fp_b)

    def test_trailing_whitespace_does_not_affect_rename_fingerprint(self):
        from vault_scanner.scanner import _compute_rename_fingerprint

        no_trail = (
            "---\n"
            "type: project\n"
            "updated: 2026-07-07\n"
            "---\n"
            "\n"
            "Body text.\n"
        )
        with_trail = (
            "---\n"
            "type: project  \n"
            "updated: 2026-07-07  \n"
            "---\n"
            "  \n"
            "Body text.  \n"
        )

        fp_no = _compute_rename_fingerprint(no_trail)
        fp_with = _compute_rename_fingerprint(with_trail)
        self.assertEqual(fp_no, fp_with)

    def test_generated_note_id_persists_across_rescans(self):
        from vault_scanner.scanner import scan_file
        from vault_scanner.indexer import Indexer

        content = self._sample_note()
        path = self._write_note("1-projects/persist-id-note.md", content)

        db_path = self.state / "vault_index.sqlite"
        indexer = Indexer(db_path)
        indexer.init_db()

        # First scan — generates a note_id
        r1 = scan_file(path, self.vault, indexer)
        self.assertEqual(r1["status"], "indexed")
        note_id_1 = r1["note_id"]
        self.assertIsNotNone(note_id_1)

        # Second scan — must reuse the same note_id
        r2 = scan_file(path, self.vault, indexer)
        self.assertEqual(r2["status"], "skipped")
        # On unchanged content, we skip and return no note_id;
        # verify by reading DB directly
        with closing(sqlite3.connect(str(db_path))) as conn:
            row = conn.execute(
                "SELECT note_id FROM index_manifest WHERE relative_path = ?",
                ("1-projects/persist-id-note.md",),
            ).fetchone()
        self.assertEqual(row[0], note_id_1)

        # Third scan — modify content slightly so it must re-index
        modified = content.replace("PDC Drilling Test", "PDC Drilling Modified")
        path.write_text(modified, encoding="utf-8")
        r3 = scan_file(path, self.vault, indexer)
        self.assertEqual(r3["status"], "indexed")
        self.assertEqual(r3["note_id"], note_id_1)

    # ── Schema & Quarantine (Vertical Slice 2) ─────────────────────

    def test_forbidden_fields_detected_and_quarantined(self):
        """Forbidden field 'verification_state' → quarantined."""
        from vault_scanner.scanner import scan_file
        from vault_scanner.indexer import Indexer

        content = (
            "---\n"
            "schema_version: 1\n"
            "type: project\n"
            "lifecycle: active\n"
            "created: 2026-07-07\n"
            "updated: 2026-07-07\n"
            "verification_state: pending\n"
            "---\n"
            "\n"
            "Should be quarantined.\n"
        )
        path = self._write_note("1-projects/forbidden-note.md", content)

        db_path = self.state / "vault_index.sqlite"
        indexer = Indexer(db_path)
        indexer.init_db()

        result = scan_file(path, self.vault, indexer)
        self.assertEqual(result["status"], "quarantined")
        self.assertIn("verification_state", result["reason"])

    def test_utf8_failure_quarantines(self):
        """Binary/invalid UTF-8 content → quarantined."""
        from vault_scanner.scanner import scan_file
        from vault_scanner.indexer import Indexer

        path = self.vault / "1-projects" / "binary-note.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xfe\x00\x01\x02")

        db_path = self.state / "vault_index.sqlite"
        indexer = Indexer(db_path)
        indexer.init_db()

        result = scan_file(path, self.vault, indexer)
        self.assertEqual(result["status"], "quarantined")
        self.assertEqual(result["reason"], "not_valid_utf8")

    def test_quarantine_preserves_previous_valid_index(self):
        """Scan valid file → scan with forbidden field → previous valid fields preserved."""
        from vault_scanner.scanner import scan_file
        from vault_scanner.indexer import Indexer

        valid_content = (
            "---\n"
            "schema_version: 1\n"
            "type: project\n"
            "lifecycle: active\n"
            "created: 2026-07-07\n"
            "updated: 2026-07-07\n"
            "---\n"
            "\n"
            "Valid note.\n"
        )
        forbidden_content = (
            "---\n"
            "schema_version: 1\n"
            "type: project\n"
            "lifecycle: active\n"
            "created: 2026-07-07\n"
            "updated: 2026-07-07\n"
            "verification_state: pending\n"
            "---\n"
            "\n"
            "Should be quarantined.\n"
        )
        path = self._write_note("1-projects/quarantine-preserve.md", valid_content)

        db_path = self.state / "vault_index.sqlite"
        indexer = Indexer(db_path)
        indexer.init_db()

        # First scan — valid
        r1 = scan_file(path, self.vault, indexer)
        self.assertEqual(r1["status"], "indexed")
        original_evidence = r1["evidence_hash"]

        with closing(sqlite3.connect(str(db_path))) as conn:
            row = conn.execute(
                "SELECT evidence_hash, last_indexed_hash, status FROM index_manifest "
                "WHERE relative_path = ?",
                ("1-projects/quarantine-preserve.md",),
            ).fetchone()
        self.assertEqual(row[0], original_evidence)
        self.assertEqual(row[1], original_evidence)
        self.assertEqual(row[2], "active")

        # Second scan — forbidden field, should quarantine but preserve
        path.write_text(forbidden_content, encoding="utf-8")
        r2 = scan_file(path, self.vault, indexer)
        self.assertEqual(r2["status"], "quarantined")

        with closing(sqlite3.connect(str(db_path))) as conn:
            row = conn.execute(
                "SELECT evidence_hash, last_indexed_hash, status, quarantine_reason "
                "FROM index_manifest WHERE relative_path = ?",
                ("1-projects/quarantine-preserve.md",),
            ).fetchone()
        # evidence_hash and last_indexed_hash must be preserved from the valid scan
        self.assertEqual(row[0], original_evidence)
        self.assertEqual(row[1], original_evidence)
        self.assertEqual(row[2], "quarantined")
        self.assertIsNotNone(row[3])
        self.assertIn("verification_state", row[3])

    def test_schema_version_too_high_quarantines(self):
        """schema_version: 999 exceeds supported version 1 → quarantined."""
        from vault_scanner.scanner import scan_file
        from vault_scanner.indexer import Indexer

        content = (
            "---\n"
            "schema_version: 999\n"
            "type: project\n"
            "lifecycle: active\n"
            "created: 2026-07-07\n"
            "updated: 2026-07-07\n"
            "---\n"
            "\n"
            "Schema version too high.\n"
        )
        path = self._write_note("1-projects/high-schema-note.md", content)

        db_path = self.state / "vault_index.sqlite"
        indexer = Indexer(db_path)
        indexer.init_db()

        result = scan_file(path, self.vault, indexer)
        self.assertEqual(result["status"], "quarantined")
        self.assertIn("schema_version", result["reason"])
        self.assertIn("999", result["reason"])
        self.assertIn("1", result["reason"])

    def test_required_fields_missing_quarantines(self):
        """Missing 'type' and 'created' required fields → quarantined."""
        from vault_scanner.scanner import scan_file
        from vault_scanner.indexer import Indexer

        content = (
            "---\n"
            "schema_version: 1\n"
            # Missing: type, lifecycle, created, updated
            "source: human\n"
            "---\n"
            "\n"
            "Missing required fields.\n"
        )
        path = self._write_note("1-projects/missing-fields-note.md", content)

        db_path = self.state / "vault_index.sqlite"
        indexer = Indexer(db_path)
        indexer.init_db()

        result = scan_file(path, self.vault, indexer)
        self.assertEqual(result["status"], "quarantined")
        self.assertIn("type", result["reason"])
        self.assertIn("lifecycle", result["reason"])
        self.assertIn("created", result["reason"])
        self.assertIn("updated", result["reason"])


class ReceiptSliceTests(unittest.TestCase):
    """Vertical Slice 4: Phase 1 Receipt Foundation."""

    RECEIPT_FIELDS = {
        "note_id", "relative_path", "evidence_hash", "rename_fingerprint",
        "parser_version", "schema_version", "record_state",
        "verification_state", "quarantine_reason", "indexed_at",
    }

    VALID_NOTE = (
        "---\n"
        "schema_version: 1\n"
        "type: project\n"
        "lifecycle: active\n"
        "created: 2026-07-07\n"
        "updated: 2026-07-07\n"
        "---\n"
        "\n"
        "Valid note about [[topic-a]].\n"
    )

    BAD_CONTENT = (
        "---\n"
        "schema_version: 1\n"
        "type: project\n"
        "lifecycle: active\n"
        "created: 2026-07-07\n"
        "updated: 2026-07-07\n"
        "verification_state: pending\n"
        "---\n"
        "\n"
        "Has forbidden field.\n"
    )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name) / "vault"
        self.state = Path(self.temp.name) / "state"
        self.vault.mkdir()
        self.state.mkdir()
        self.db_path = self.state / "vault_index.sqlite"
        self.indexer = Indexer(self.db_path)
        self.indexer.init_db()

    def tearDown(self):
        self.temp.cleanup()

    def _write_note(self, rel_path: str, content: str) -> Path:
        full = self.vault / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return full

    def test_index_manifest_has_all_receipt_fields(self):
        """Scan a file, then query manifest to verify each receipt field is present and non-null."""
        path = self._write_note("1-projects/receipt-test.md", self.VALID_NOTE)
        result = scan_file(path, self.vault, self.indexer)
        self.assertEqual(result["status"], "indexed")

        receipts = export_receipt_records(self.indexer)
        self.assertEqual(len(receipts), 1)
        r = receipts[0]

        self.assertEqual(set(r.keys()), self.RECEIPT_FIELDS)

        for key in self.RECEIPT_FIELDS:
            if key == "quarantine_reason":
                self.assertIsNone(r[key])
            else:
                self.assertIsNotNone(r[key], f"Field {key} is None")

        self.assertEqual(r["note_id"], result["note_id"])
        self.assertEqual(r["relative_path"], "1-projects/receipt-test.md")
        self.assertEqual(r["evidence_hash"], result["evidence_hash"])
        self.assertEqual(r["parser_version"], PARSER_VERSION)
        self.assertEqual(r["schema_version"], 1)
        self.assertEqual(r["record_state"], "active")
        self.assertEqual(r["verification_state"], "pending")
        self.assertIsNotNone(r["indexed_at"])

    def test_export_receipt_fields_composable(self):
        """Scan 2 files, call export_receipt_records, verify the returned list structure."""
        path_1 = self._write_note("1-projects/note-a.md", self.VALID_NOTE)
        r1 = scan_file(path_1, self.vault, self.indexer)
        self.assertEqual(r1["status"], "indexed")

        content_2 = self.VALID_NOTE.replace("topic-a", "topic-b")
        path_2 = self._write_note("2-areas/note-b.md", content_2)
        r2 = scan_file(path_2, self.vault, self.indexer)
        self.assertEqual(r2["status"], "indexed")

        receipts = export_receipt_records(self.indexer)
        self.assertEqual(len(receipts), 2)

        for r in receipts:
            self.assertEqual(set(r.keys()), self.RECEIPT_FIELDS)
            self.assertIsNotNone(r["note_id"])
            self.assertIsNotNone(r["relative_path"])
            self.assertIsNotNone(r["evidence_hash"])
            self.assertEqual(r["parser_version"], PARSER_VERSION)
            self.assertEqual(r["schema_version"], 1)
            self.assertEqual(r["record_state"], "active")
            self.assertEqual(r["verification_state"], "pending")
            self.assertIsNone(r["quarantine_reason"])
            self.assertIsNotNone(r["indexed_at"])

        paths = {r["relative_path"] for r in receipts}
        self.assertEqual(paths, {"1-projects/note-a.md", "2-areas/note-b.md"})

    def test_quarantine_receipt_record(self):
        """Scan -> receipt -> re-scan with bad content -> receipt shows quarantined."""
        path = self._write_note("1-projects/quar-receipt.md", self.VALID_NOTE)
        r1 = scan_file(path, self.vault, self.indexer)
        self.assertEqual(r1["status"], "indexed")
        original_evidence = r1["evidence_hash"]

        receipts_1 = export_receipt_records(self.indexer)
        self.assertEqual(len(receipts_1), 1)
        self.assertEqual(receipts_1[0]["record_state"], "active")
        self.assertEqual(receipts_1[0]["verification_state"], "pending")
        self.assertEqual(receipts_1[0]["evidence_hash"], original_evidence)
        self.assertIsNone(receipts_1[0]["quarantine_reason"])

        path.write_text(self.BAD_CONTENT, encoding="utf-8")
        r2 = scan_file(path, self.vault, self.indexer)
        self.assertEqual(r2["status"], "quarantined")

        receipts_2 = export_receipt_records(self.indexer)
        self.assertEqual(len(receipts_2), 1)
        r = receipts_2[0]

        self.assertEqual(set(r.keys()), self.RECEIPT_FIELDS)
        self.assertEqual(r["record_state"], "quarantined")
        self.assertEqual(r["verification_state"], "pending")
        self.assertEqual(r["evidence_hash"], original_evidence)
        self.assertIsNotNone(r["quarantine_reason"])
        self.assertIn("verification_state", r["quarantine_reason"])
        self.assertIsNotNone(r["note_id"])
        self.assertEqual(r["relative_path"], "1-projects/quar-receipt.md")
        self.assertEqual(r["parser_version"], PARSER_VERSION)
        self.assertEqual(r["schema_version"], 1)
        self.assertIsNotNone(r["indexed_at"])


class ReconciliationTests(unittest.TestCase):
    """Phase 1 vault scanner — Vertical Slice 3: Full Reconciliation.

    Tests tombstone, same-ULID rename, no-ID fingerprint rename,
    ambiguous fingerprint, and content+path change scenarios.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name) / "vault"
        self.state = Path(self.temp.name) / "state"
        self.vault.mkdir()
        self.state.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def _write_note(self, rel_path: str, content: str) -> Path:
        full = self.vault / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return full

    def _valid_note(self) -> str:
        return (
            "---\n"
            "schema_version: 1\n"
            "type: project\n"
            "lifecycle: active\n"
            "created: 2026-07-07\n"
            "updated: 2026-07-07\n"
            "---\n"
            "\n"
            "# Test Note\n"
            "\n"
            "Body content.\n"
        )

    # ── 3a: Tombstone detection ──────────────────────────────────

    def test_missing_file_becomes_tombstone(self):
        """Scan file A → delete file A → reconcile → A is tombstone."""
        from vault_scanner.scanner import scan_file
        from vault_scanner.indexer import Indexer
        from vault_scanner.reconciler import reconcile_manifest

        content = self._valid_note()
        path = self._write_note("1-projects/note-a.md", content)

        db_path = self.state / "vault_index.sqlite"
        indexer = Indexer(db_path)
        indexer.init_db()

        # Scan file A
        scan_file(path, self.vault, indexer)

        # "Delete" file A by not including it in scanned_paths
        scanned_paths: set[str] = set()

        changes = reconcile_manifest(indexer, scanned_paths, scan_completed=True)

        # Verify tombstone
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["type"], "tombstone")
        self.assertEqual(changes[0]["relative_path"], "1-projects/note-a.md")

        # Verify DB status
        with closing(sqlite3.connect(str(db_path))) as conn:
            row = conn.execute(
                "SELECT status FROM index_manifest WHERE relative_path = ?",
                ("1-projects/note-a.md",),
            ).fetchone()
        self.assertEqual(row[0], "tombstone")

    # ── 3b: Same-ULID rename ────────────────────────────────────

    def test_same_ulid_new_path_rename(self):
        """File with id: X at path A → scan → change path to B → reconcile shows path B."""
        from vault_scanner.scanner import scan_file
        from vault_scanner.indexer import Indexer
        from vault_scanner.reconciler import reconcile_manifest

        content_a = (
            "---\n"
            "id: my-fixed-id\n"
            "schema_version: 1\n"
            "type: project\n"
            "lifecycle: active\n"
            "created: 2026-07-07\n"
            "updated: 2026-07-07\n"
            "---\n"
            "\n"
            "Body at path A.\n"
        )
        path_a = self._write_note("1-projects/old-path.md", content_a)

        db_path = self.state / "vault_index.sqlite"
        indexer = Indexer(db_path)
        indexer.init_db()

        # Scan at path A
        scan_file(path_a, self.vault, indexer)

        # "Move" the file: write same id at new path (keep old on disk for scan)
        # Note: in real use, scan_file is called per file, so both old and new
        # files exist on disk simultaneously during a full vault scan.
        content_b = content_a.replace("Body at path A.", "Body at path B.")
        path_b = self._write_note("1-projects/new-path.md", content_b)

        # Scan at path B — since note_id is 'my-fixed-id', upsert replaces
        # the old-path row with the new-path row (note_id is PRIMARY KEY).
        scan_file(path_b, self.vault, indexer)

        scanned_paths = {"1-projects/new-path.md"}
        changes = reconcile_manifest(indexer, scanned_paths, scan_completed=True)

        # Should have exactly one rename change (tombstone the old path)
        rename_changes = [c for c in changes if c["type"] == "rename"]
        self.assertEqual(len(rename_changes), 1, msg=f"Got changes: {changes}")
        self.assertEqual(rename_changes[0]["note_id"], "my-fixed-id")
        self.assertEqual(rename_changes[0]["old_path"], "1-projects/old-path.md")
        self.assertEqual(rename_changes[0]["new_path"], "1-projects/new-path.md")

        # Verify DB: new-path is active with note_id, old-path is tombstone
        with closing(sqlite3.connect(str(db_path))) as conn:
            new_row = conn.execute(
                "SELECT note_id, status FROM index_manifest WHERE relative_path = ?",
                ("1-projects/new-path.md",),
            ).fetchone()
            old_row = conn.execute(
                "SELECT note_id, status FROM index_manifest WHERE relative_path = ?",
                ("1-projects/old-path.md",),
            ).fetchone()
        self.assertIsNotNone(new_row)
        self.assertEqual(new_row[0], "my-fixed-id")
        self.assertEqual(new_row[1], "active")
        self.assertIsNotNone(old_row)
        self.assertEqual(old_row[0], "my-fixed-id")
        self.assertEqual(old_row[1], "tombstone")

    # ── 3c: No-ID fingerprint rename ─────────────────────────────

    def test_no_id_fingerprint_rename(self):
        """File without id at path A → scan → move to path B → fingerprint matches."""
        from vault_scanner.scanner import scan_file
        from vault_scanner.indexer import Indexer
        from vault_scanner.reconciler import reconcile_manifest

        content = self._valid_note()
        path_a = self._write_note("1-projects/old-path-no-id.md", content)

        db_path = self.state / "vault_index.sqlite"
        indexer = Indexer(db_path)
        indexer.init_db()

        # Scan at path A
        r1 = scan_file(path_a, self.vault, indexer)
        self.assertEqual(r1["status"], "indexed")
        original_note_id = r1["note_id"]

        # "Move" the file (same content, new path)
        path_a.unlink()
        path_b = self._write_note("1-projects/new-path-no-id.md", content)

        # Scan at path B — generates a NEW note_id (no existing state at B)
        r2 = scan_file(path_b, self.vault, indexer)
        self.assertEqual(r2["status"], "indexed")
        new_note_id = r2["note_id"]
        self.assertNotEqual(new_note_id, original_note_id)

        scanned_paths = {"1-projects/new-path-no-id.md"}
        changes = reconcile_manifest(indexer, scanned_paths, scan_completed=True)

        # Should have a fingerprint rename
        fp_changes = [c for c in changes if c["type"] == "rename_fingerprint"]
        self.assertEqual(len(fp_changes), 1, msg=f"Got changes: {changes}")
        self.assertEqual(fp_changes[0]["old_path"], "1-projects/old-path-no-id.md")
        self.assertEqual(fp_changes[0]["new_path"], "1-projects/new-path-no-id.md")
        self.assertEqual(fp_changes[0]["note_id"], original_note_id)

        # Verify DB: old entry is gone, new path has original note_id
        with closing(sqlite3.connect(str(db_path))) as conn:
            new_row = conn.execute(
                "SELECT note_id, status FROM index_manifest WHERE relative_path = ?",
                ("1-projects/new-path-no-id.md",),
            ).fetchone()
            old_row = conn.execute(
                "SELECT note_id, status FROM index_manifest WHERE relative_path = ?",
                ("1-projects/old-path-no-id.md",),
            ).fetchone()
        self.assertIsNotNone(new_row)
        self.assertEqual(new_row[0], original_note_id)
        self.assertEqual(new_row[1], "active")
        self.assertIsNone(old_row)

    # ── 3d: Ambiguous fingerprint — no rename ────────────────────

    def test_no_id_fingerprint_ambiguous_no_rename(self):
        """Two files with same fingerprint at different paths → neither gets renamed."""
        from vault_scanner.scanner import scan_file
        from vault_scanner.indexer import Indexer
        from vault_scanner.reconciler import reconcile_manifest

        # Two files with intentionally identical content (same rename_fingerprint)
        content = (
            "---\n"
            "schema_version: 1\n"
            "type: project\n"
            "lifecycle: active\n"
            "created: 2026-07-07\n"
            "updated: 2026-07-07\n"
            "---\n"
            "\n"
            "Identical content.\n"
        )
        path_a = self._write_note("1-projects/file-a.md", content)
        path_b = self._write_note("1-projects/file-b.md", content)

        db_path = self.state / "vault_index.sqlite"
        indexer = Indexer(db_path)
        indexer.init_db()

        # Scan both files
        r_a = scan_file(path_a, self.vault, indexer)
        r_b = scan_file(path_b, self.vault, indexer)
        note_id_a = r_a["note_id"]
        note_id_b = r_b["note_id"]
        self.assertNotEqual(note_id_a, note_id_b)

        # Verify fingerprints are the same
        with closing(sqlite3.connect(str(db_path))) as conn:
            fp_a = conn.execute(
                "SELECT rename_fingerprint FROM index_manifest WHERE relative_path = ?",
                ("1-projects/file-a.md",),
            ).fetchone()[0]
            fp_b = conn.execute(
                "SELECT rename_fingerprint FROM index_manifest WHERE relative_path = ?",
                ("1-projects/file-b.md",),
            ).fetchone()[0]
        self.assertEqual(fp_a, fp_b)

        # Now "move" file-a to a new path (simulate rename scenario)
        path_a.unlink()
        path_c = self._write_note("1-projects/file-c.md", content)

        # Scan at new path — creates a 3rd entry with same fingerprint
        scan_file(path_c, self.vault, indexer)

        scanned_paths = {"1-projects/file-b.md", "1-projects/file-c.md"}
        changes = reconcile_manifest(indexer, scanned_paths, scan_completed=True)

        # Should NOT have a fingerprint rename (ambiguous — 2 candidates)
        fp_changes = [c for c in changes if c["type"] == "rename_fingerprint"]
        self.assertEqual(len(fp_changes), 0, msg=f"Expected no rename, got: {changes}")

        # Should see an ambiguous entry
        amb_changes = [c for c in changes if c["type"] == "rename_fingerprint_ambiguous"]
        self.assertGreaterEqual(len(amb_changes), 1)

        # file-b and file-c should be active, file-a should be tombstone
        with closing(sqlite3.connect(str(db_path))) as conn:
            rows = conn.execute(
                "SELECT relative_path, status FROM index_manifest ORDER BY relative_path"
            ).fetchall()
        statuses = {r[0]: r[1] for r in rows}
        self.assertEqual(statuses.get("1-projects/file-a.md"), "tombstone")
        self.assertEqual(statuses.get("1-projects/file-b.md"), "active")
        self.assertEqual(statuses.get("1-projects/file-c.md"), "active")

    # ── 3e: No-ID content+path changed — no auto-link ────────────

    def test_no_id_content_and_path_changed_no_auto_link(self):
        """File without id → change content + change path → new entry, old is tombstone."""
        from vault_scanner.scanner import scan_file
        from vault_scanner.indexer import Indexer
        from vault_scanner.reconciler import reconcile_manifest

        content_a = self._valid_note()
        path_a = self._write_note("1-projects/original-path.md", content_a)

        db_path = self.state / "vault_index.sqlite"
        indexer = Indexer(db_path)
        indexer.init_db()

        # Scan original
        r1 = scan_file(path_a, self.vault, indexer)
        self.assertEqual(r1["status"], "indexed")
        original_note_id = r1["note_id"]

        # Move + change content (different path, different content -> different fingerprint)
        path_a.unlink()
        content_b = content_a.replace("# Test Note", "# Completely Different Note")
        self.assertNotEqual(content_a, content_b)
        path_b = self._write_note("2-areas/new-path.md", content_b)

        # Scan at new path — generates new note_id since no existing state
        r2 = scan_file(path_b, self.vault, indexer)
        self.assertEqual(r2["status"], "indexed")
        new_note_id = r2["note_id"]

        scanned_paths = {"2-areas/new-path.md"}
        changes = reconcile_manifest(indexer, scanned_paths, scan_completed=True)

        # Should tombstone old path (no fingerprint match since content changed)
        tombstone_changes = [c for c in changes if c["type"] == "tombstone"]
        self.assertGreaterEqual(len(tombstone_changes), 1)
        tombstoned_paths = [c["relative_path"] for c in tombstone_changes]
        self.assertIn("1-projects/original-path.md", tombstoned_paths)

        # No fingerprint rename
        fp_changes = [c for c in changes if c["type"] == "rename_fingerprint"]
        self.assertEqual(len(fp_changes), 0)

        # Verify DB: new-path is active with new note_id, old-path is tombstone
        with closing(sqlite3.connect(str(db_path))) as conn:
            new_row = conn.execute(
                "SELECT note_id, status FROM index_manifest WHERE relative_path = ?",
                ("2-areas/new-path.md",),
            ).fetchone()
            old_row = conn.execute(
                "SELECT note_id, status FROM index_manifest WHERE relative_path = ?",
                ("1-projects/original-path.md",),
            ).fetchone()
        self.assertIsNotNone(new_row)
        self.assertEqual(new_row[0], new_note_id)
        self.assertEqual(new_row[1], "active")
        self.assertIsNotNone(old_row)
        self.assertEqual(old_row[0], original_note_id)
        self.assertEqual(old_row[1], "tombstone")
        self.assertNotEqual(original_note_id, new_note_id)

    # ── 3f: Empty scan without scan_completed must NOT tombstone ──

    def test_empty_scan_does_not_tombstone_when_not_completed(self):
        """scan_completed=False prevents tombstoning active entries."""
        from vault_scanner.scanner import scan_file
        from vault_scanner.indexer import Indexer
        from vault_scanner.reconciler import reconcile_manifest

        content = self._valid_note()
        path = self._write_note("1-projects/note-a.md", content)

        db_path = self.state / "vault_index.sqlite"
        indexer = Indexer(db_path)
        indexer.init_db()

        # Scan one file
        scan_file(path, self.vault, indexer)

        # Reconcile with empty scanned_paths but scan_completed=False
        changes = reconcile_manifest(indexer, set(), scan_completed=False)

        # No tombstone changes should be produced
        tombstone_changes = [c for c in changes if c["type"] == "tombstone"]
        self.assertEqual(len(tombstone_changes), 0)

        # Verify DB: file still active
        with closing(sqlite3.connect(str(db_path))) as conn:
            row = conn.execute(
                "SELECT status FROM index_manifest WHERE relative_path = ?",
                ("1-projects/note-a.md",),
            ).fetchone()
        self.assertEqual(row[0], "active")


if __name__ == "__main__":
    unittest.main()
