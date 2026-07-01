#!/usr/bin/env python3
"""Vendor a pinned Hermes Agent source snapshot into the LAOS repository."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "config" / "hermes_vendor.json"


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("vendor manifest must be an object")
    return value


def _request(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "LAOS-Hermes-Vendor/1"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def _latest_sha(upstream: str, branch: str) -> str:
    data = json.loads(_request(f"https://api.github.com/repos/{upstream}/commits/{branch}"))
    sha = data.get("sha")
    if not isinstance(sha, str) or len(sha) != 40:
        raise RuntimeError("GitHub did not return a commit SHA")
    return sha


def _selected(path: PurePosixPath, includes: list[str], excludes: list[str]) -> bool:
    text = path.as_posix()
    included = any(text == item or text.startswith(item.rstrip("/") + "/") for item in includes)
    excluded = any(text == item or text.startswith(item.rstrip("/") + "/") for item in excludes)
    return included and not excluded


def _source_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file() and item.name != "_UPSTREAM.json"):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _extract(archive: bytes, destination: Path, manifest: dict) -> int:
    includes = list(manifest.get("include", []))
    excludes = list(manifest.get("exclude", []))
    copied = 0
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        for member in bundle.getmembers():
            parts = PurePosixPath(member.name).parts
            if len(parts) < 2 or not member.isfile():
                continue
            relative = PurePosixPath(*parts[1:])
            if ".." in relative.parts or not _selected(relative, includes, excludes):
                continue
            source = bundle.extractfile(member)
            if source is None:
                continue
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read())
            target.chmod(member.mode & 0o777)
            copied += 1
    if copied == 0:
        raise RuntimeError("vendor selection copied no files")
    return copied


def _metadata_path(root: Path) -> Path:
    return root / "_UPSTREAM.json"


def command_check(manifest: dict) -> dict:
    upstream = manifest["upstream"]
    latest = _latest_sha(upstream, manifest.get("track_branch", "main"))
    destination = REPO_ROOT / manifest["destination"]
    installed = None
    metadata = _metadata_path(destination)
    if metadata.exists():
        installed = json.loads(metadata.read_text(encoding="utf-8")).get("commit")
    installed = installed or manifest.get("pinned_ref")
    return {
        "upstream": upstream,
        "installed": installed,
        "latest": latest,
        "update_available": installed != latest,
    }


def command_sync(manifest: dict, ref: str | None) -> dict:
    upstream = manifest["upstream"]
    commit = ref or _latest_sha(upstream, manifest.get("track_branch", "main"))
    archive = _request(f"https://codeload.github.com/{upstream}/tar.gz/{commit}")
    archive_hash = hashlib.sha256(archive).hexdigest()
    destination = REPO_ROOT / manifest["destination"]

    with tempfile.TemporaryDirectory(prefix="laos-hermes-") as temp_dir:
        staged = Path(temp_dir) / "hermes_agent"
        staged.mkdir()
        copied = _extract(archive, staged, manifest)
        source_hash = _source_hash(staged)
        _metadata_path(staged).write_text(
            json.dumps(
                {
                    "upstream": upstream,
                    "commit": commit,
                    "archive_sha256": archive_hash,
                    "source_tree_sha256": source_hash,
                    "excluded": manifest.get("exclude", []),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        replacement = destination.with_name(destination.name + ".new")
        if replacement.exists():
            shutil.rmtree(replacement)
        shutil.copytree(staged, replacement)
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(replacement, destination)

    return {
        "upstream": upstream,
        "commit": commit,
        "destination": str(destination.relative_to(REPO_ROOT)),
        "files": copied,
        "archive_sha256": archive_hash,
        "source_tree_sha256": source_hash,
    }


def command_verify(manifest: dict) -> dict:
    destination = REPO_ROOT / manifest["destination"]
    metadata = _metadata_path(destination)
    license_path = destination / manifest.get("license_file", "LICENSE")
    if not destination.is_dir() or not metadata.is_file() or not license_path.is_file():
        raise RuntimeError("Hermes vendor snapshot is incomplete")
    recorded = json.loads(metadata.read_text(encoding="utf-8"))
    actual = _source_hash(destination)
    expected = recorded.get("source_tree_sha256")
    return {
        "destination": str(destination.relative_to(REPO_ROOT)),
        "commit": recorded.get("commit"),
        "expected_tree_sha256": expected,
        "actual_tree_sha256": actual,
        "valid": actual == expected,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    sync = commands.add_parser("sync")
    sync.add_argument("--ref")
    commands.add_parser("verify")
    args = parser.parse_args(argv)

    manifest = _load(args.manifest)
    if args.command == "check":
        result = command_check(manifest)
    elif args.command == "sync":
        result = command_sync(manifest, args.ref)
    else:
        result = command_verify(manifest)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("valid", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
