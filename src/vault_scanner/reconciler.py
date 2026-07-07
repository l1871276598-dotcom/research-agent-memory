"""Phase 1 vault scanner — reconciler for tombstone, rename, and conflict resolution.

Contract: _system/contracts/03-scan-index-and-conflict-rules.md
"""

from .indexer import Indexer


def reconcile_manifest(indexer: Indexer, scanned_paths: set[str],
                      scan_completed: bool = False) -> list[dict]:
    """Reconcile the index_manifest after a scan pass.

    Steps:
      1. Same-ULID rename detection: duplicate note_ids → tombstone old path
      2. No-ID fingerprint rename detection (before tombstoning)
      3. Tombstone detection (only when scan_completed=True):
         active entries not in scanned_paths → tombstone

    scan_completed must be True for tombstone to fire.  This prevents
    accidental mass-tombstone when scan was interrupted, vault mount
    failed, or scanned_paths was incorrectly empty.

    Returns a list of change dicts describing what was done.
    """
    changes: list[dict] = []
    scanned_paths_set = set(scanned_paths)

    # ── Step 1: Same-ULID rename detection ─────────────────────────
    # scan_file upserts by relative_path, so a file with id: X moved
    # from path A to path B creates a new active row at B with note_id X.
    # The old row at A is now a duplicate note_id — tombstone it.
    manifest = indexer.get_all_active()
    by_note_id: dict[str, list[dict]] = {}
    for e in manifest:
        if e["status"] == "active":
            by_note_id.setdefault(e["note_id"], []).append(e)

    for note_id, entries in by_note_id.items():
        if len(entries) > 1:
            # We have multiple active paths for the same note_id.
            # The entry at scanned_path is the new location; others are stale.
            for entry in entries:
                if entry["relative_path"] not in scanned_paths_set:
                    indexer.update_status(entry["relative_path"], "tombstone")
                    changes.append({
                        "type": "rename",
                        "note_id": note_id,
                        "old_path": entry["relative_path"],
                        "new_path": [e["relative_path"] for e in entries
                                     if e["relative_path"] != entry["relative_path"]][0],
                    })

    # ── Step 2: No-ID fingerprint rename detection ─────────────────
    # Run BEFORE tombstoning so old-path entries are still active.
    # Strategy: find fingerprints where EXACTLY ONE non-scanned active entry
    # and EXACTLY ONE scanned active entry share that fingerprint (with
    # different note_ids). This eliminates ambiguity on both sides.
    manifest = indexer.get_all_active()

    # Partition by scanned vs not-scanned
    not_scanned_fp: dict[str, list[dict]] = {}
    scanned_fp: dict[str, list[dict]] = {}
    for e in manifest:
        fp = e.get("rename_fingerprint")
        if not fp:
            continue
        in_scanned = e["relative_path"] in scanned_paths_set
        if in_scanned:
            scanned_fp.setdefault(fp, []).append(e)
        else:
            not_scanned_fp.setdefault(fp, []).append(e)

    for fp, src_entries in list(not_scanned_fp.items()):
        if len(src_entries) != 1:
            continue  # ambiguous: multiple old entries, can't tell which one moved
        dst_entries = scanned_fp.get(fp, [])
        if len(dst_entries) != 1:
            continue  # ambiguous: multiple scanned entries match

        src = src_entries[0]
        dst = dst_entries[0]

        # Must have different note_ids (same note_id means same-ULID handled above)
        if src["note_id"] == dst["note_id"]:
            continue

        # It's a clean 1:1 fingerprint match — merge
        old_path = src["relative_path"]
        new_path = dst["relative_path"]

        indexer.merge_onto_path(
            old_path=old_path,
            new_path=new_path,
            mtime=dst["mtime"],
            size=dst["size"],
            evidence_hash=dst["evidence_hash"],
            indexed_at=dst.get("indexed_at", ""),
        )

        changes.append({
            "type": "rename_fingerprint",
            "note_id": src["note_id"],
            "old_path": old_path,
            "new_path": new_path,
        })

        # Remove from further consideration
        del not_scanned_fp[fp]
        if fp in scanned_fp:
            del scanned_fp[fp]

    # Report any remaining scanned entries with ambiguous fingerprint matches
    for fp, dst_entries in scanned_fp.items():
        src_entries = not_scanned_fp.get(fp, [])
        if len(dst_entries) == 1 and len(src_entries) > 1:
            # Multiple non-scanned sources for one scanned entry — ambiguous
            changes.append({
                "type": "rename_fingerprint_ambiguous",
                "note_id": dst_entries[0]["note_id"],
                "relative_path": dst_entries[0]["relative_path"],
                "candidate_count": len(src_entries),
            })
        elif len(dst_entries) > 1:
            # Multiple scanned entries share a fingerprint
            for de in dst_entries:
                changes.append({
                    "type": "rename_fingerprint_ambiguous",
                    "note_id": de["note_id"],
                    "relative_path": de["relative_path"],
                    "candidate_count": len(src_entries) if src_entries else 0,
                })

    # ── Step 3: Tombstone detection ────────────────────────────────
    if not scan_completed:
        return changes

    manifest = indexer.get_all_active()
    for entry in manifest:
        if entry["relative_path"] not in scanned_paths_set and entry["status"] == "active":
            indexer.update_status(entry["relative_path"], "tombstone")
            changes.append({
                "type": "tombstone",
                "note_id": entry["note_id"],
                "relative_path": entry["relative_path"],
            })

    return changes
