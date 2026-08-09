from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import stat
import uuid
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - the supported production target is POSIX.
    fcntl = None


_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_HANDOFF_BYTES = 4 * 1024 * 1024


def _validate_project_slug(value):
    if not isinstance(value, str) or not _SLUG.fullmatch(value):
        raise ValueError("blocked: project_slug_required")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or len(path.parts) != 1:
        raise ValueError("blocked: project_slug_required")
    return value


def _validate_sha256(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("expected_sha256 must be a lowercase SHA256")
    value = value.lower()
    if _SHA256.fullmatch(value) is None:
        raise ValueError("expected_sha256 must be a lowercase SHA256")
    return value


def _canonical_json_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _project_slug_from_frontmatter(text):
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return None
    try:
        end = lines.index("---", 1)
    except ValueError:
        return None
    for line in lines[1:end]:
        if ":" not in line or line.startswith(" "):
            continue
        key, raw = line.split(":", 1)
        if key.strip() != "project_slug":
            continue
        raw = raw.strip()
        if raw.startswith('"'):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, str) else None
        return raw or None
    return None


def _validate_root(root):
    try:
        supplied = Path(root).expanduser()
    except TypeError as exc:
        raise ValueError("invalid handoff root") from exc
    try:
        supplied_info = os.lstat(supplied)
    except OSError as exc:
        raise ValueError("invalid handoff root") from exc
    if stat.S_ISLNK(supplied_info.st_mode) or not stat.S_ISDIR(
        supplied_info.st_mode
    ):
        raise ValueError("invalid handoff root")
    try:
        canonical = supplied.resolve(strict=True)
        canonical_info = os.lstat(canonical)
    except OSError as exc:
        raise ValueError("invalid handoff root") from exc
    if stat.S_ISLNK(canonical_info.st_mode) or not stat.S_ISDIR(
        canonical_info.st_mode
    ):
        raise ValueError("invalid handoff root")
    return canonical


def _relative_parts(root, path):
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError("invalid handoff path") from exc
    if ".." in relative.parts:
        raise ValueError("invalid handoff path")
    return relative.parts


def _fsync_directory(path):
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(
        os, "O_NOFOLLOW", 0
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError("handoff directory sync failed") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("invalid handoff path")
        os.fsync(descriptor)
    except OSError as exc:
        raise ValueError("handoff directory sync failed") from exc
    finally:
        os.close(descriptor)


def _ensure_safe_directory(root, path):
    current = root
    for part in _relative_parts(root, path):
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            try:
                os.mkdir(current, 0o700)
            except FileExistsError:
                pass
            except OSError as exc:
                raise ValueError("handoff directory creation failed") from exc
            try:
                info = os.lstat(current)
            except OSError as exc:
                raise ValueError("handoff directory creation failed") from exc
            _fsync_directory(current.parent)
        except OSError as exc:
            raise ValueError("invalid handoff path") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ValueError("invalid handoff path")


def _check_safe_ancestors(root, path):
    current = root
    for part in _relative_parts(root, path):
        current = current / part
        try:
            info = os.lstat(current)
        except OSError as exc:
            raise ValueError("invalid handoff path") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ValueError("invalid handoff path")


def _identity(info):
    return (
        info.st_dev,
        info.st_ino,
        stat.S_IFMT(info.st_mode),
        info.st_size,
        getattr(info, "st_mtime_ns", int(info.st_mtime * 1_000_000_000)),
        getattr(info, "st_ctime_ns", int(info.st_ctime * 1_000_000_000)),
    )


def _read_regular_file(root, path, *, missing_ok=False):
    _check_safe_ancestors(root, path.parent)
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise ValueError("invalid handoff target")
    except OSError as exc:
        raise ValueError("invalid handoff target") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ValueError("invalid handoff target")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError("invalid handoff target") from exc
    try:
        opened_before = os.fstat(descriptor)
        if not stat.S_ISREG(opened_before.st_mode):
            raise ValueError("invalid handoff target")
        chunks = []
        size = 0
        while True:
            chunk = os.read(
                descriptor,
                min(1024 * 1024, _MAX_HANDOFF_BYTES + 1 - size),
            )
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > _MAX_HANDOFF_BYTES:
                raise ValueError("handoff target is too large")
        opened_after = os.fstat(descriptor)
    finally:
        os.close(descriptor)

    try:
        after = os.lstat(path)
    except OSError as exc:
        raise ValueError("handoff target changed during read") from exc
    if not (
        _identity(before)
        == _identity(opened_before)
        == _identity(opened_after)
        == _identity(after)
    ):
        raise ValueError("handoff target changed during read")
    return b"".join(chunks)


def _write_temp(parent, content):
    temp = parent / f".handoff-{uuid.uuid4().hex}.tmp"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = None
    completed = False
    try:
        descriptor = os.open(temp, flags, 0o600)
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise OSError("short handoff write")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        if _read_regular_file(parent, temp) != content:
            raise ValueError("handoff staging readback failed")
        completed = True
        return temp
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError("handoff write failed") from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if not completed:
            _unlink_owned(temp)


def _unlink_owned(path):
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ValueError("handoff cleanup failed") from exc


@contextlib.contextmanager
def _handoff_lock(root, staging_dir):
    if fcntl is None:
        raise ValueError("handoff lock unavailable")
    _ensure_safe_directory(root, staging_dir)
    lock_path = staging_dir / ".handoff.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise ValueError("handoff lock unavailable") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("handoff lock unavailable")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except ValueError:
        os.close(descriptor)
        raise
    except OSError as exc:
        os.close(descriptor)
        raise ValueError("handoff lock unavailable") from exc

    try:
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _require_source_backed_project(root, project_slug, workspace):
    from memory import collect_validated_records
    from review.authority import AuthorityStore

    # GP10-04: a pending activation can produce a record at status=active
    # before the receipt is written. Raw collect_validated_records sees it;
    # AuthorityStore.authorize_active_records blocks it. Handoff must gate
    # through the Authority facade so a crash-window active state cannot
    # produce a permanent handoff side-effect.
    _, records, errors, _ = collect_validated_records(root)
    if errors:
        raise ValueError("handoff project validation failed")

    # Run active records through the Authority filter so pending-activation
    # projects are invisible. Fail closed on any Authority error.
    try:
        authority = AuthorityStore(root)
    except Exception:
        authority = None
    filtered = None
    if authority is not None:
        try:
            # authorize_active_records blocks when pending activation exists.
            # We don't need the returned records — just the gate check.
            _ = authority.authorize_active_records(records)
            # Also check for a pending activation that targets this project.
            pending = authority.pending_count()
            if pending > 0:
                # Check whether the pending activation targets the same project.
                from pathlib import Path as _Path
                import json as _json
                for p in _Path(authority.activations).iterdir():
                    if p.name.startswith(".tmp."):
                        continue
                    if p.suffix != ".json" or p.is_symlink() or not p.is_file():
                        continue
                    try:
                        data = _json.loads(p.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    for authorized in data.get("authorized_records", {}).values():
                        if authorized.get("workspace") == workspace and authorized.get("project") == project_slug:
                            raise ValueError("blocked: handoff project has pending activation")
        except ValueError:
            raise
        except Exception:
            pass  # Non-blocking Authority errors: let raw scan continue.

    for item in records:
        record = item["record"]
        if (
            record.get("type") == "project"
            and record.get("status") == "active"
            and record.get("project") == project_slug
        ):
            if record.get("workspace") == workspace:
                return
            raise ValueError("blocked: project_not_in_workspace")
    raise ValueError("blocked: project_slug_not_source_backed")


def _render_handoff(project_slug, content):
    text = (
        "---\nhandoff_schema: \"laos-handoff/v1\"\nproject_slug: "
        + json.dumps(project_slug, ensure_ascii=False)
        + "\n---\n\n"
        + content.rstrip()
        + "\n"
    )
    return text.encode("utf-8")


def _validate_existing(project_slug, raw):
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("invalid handoff target") from exc
    if _project_slug_from_frontmatter(text) != project_slug:
        raise ValueError("blocked: target_handoff_belongs_to_another_project")


def _publish_create(root, target, temp, rendered):
    try:
        os.link(temp, target, follow_symlinks=False)
    except FileExistsError as exc:
        raise ValueError("handoff target changed concurrently") from exc
    except OSError as exc:
        raise ValueError("handoff publication failed") from exc
    try:
        _unlink_owned(temp)
        _fsync_directory(target.parent)
        if _read_regular_file(root, target) != rendered:
            raise ValueError("handoff readback failed")
    except Exception:
        try:
            _unlink_owned(target)
            _fsync_directory(target.parent)
        except Exception:
            pass
        raise


def _publish_update(root, target, temp, rendered, previous_raw):
    current = _read_regular_file(root, target)
    if current != previous_raw:
        raise ValueError("handoff target sha256 mismatch")

    backup = target.parent / f".handoff-{uuid.uuid4().hex}.bak"
    try:
        os.link(target, backup, follow_symlinks=False)
        _fsync_directory(target.parent)
        if _read_regular_file(root, target) != previous_raw:
            raise ValueError("handoff target sha256 mismatch")
        os.replace(temp, target)
        _fsync_directory(target.parent)
        if _read_regular_file(root, target) != rendered:
            raise ValueError("handoff readback failed")
        _unlink_owned(backup)
        _fsync_directory(target.parent)
    except Exception:
        try:
            if backup.exists() and not backup.is_symlink():
                os.replace(backup, target)
                _fsync_directory(target.parent)
        except Exception:
            pass
        raise
    finally:
        if temp.exists() and not temp.is_symlink():
            try:
                _unlink_owned(temp)
            except ValueError:
                pass
        if backup.exists() and not backup.is_symlink():
            try:
                _unlink_owned(backup)
            except ValueError:
                pass


def update_project_handoff(
    root,
    project_slug,
    content,
    expected_sha256=None,
    *,
    workspace,
):
    project_slug = _validate_project_slug(project_slug)
    expected_sha256 = _validate_sha256(expected_sha256)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("handoff content must be a non-empty string")
    if workspace not in {"personal", "work"}:
        raise ValueError("workspace must be personal or work")

    root = _validate_root(root)
    legacy = root / "LAOS_HANDOFF.md"
    try:
        legacy_info = os.lstat(legacy)
    except FileNotFoundError:
        legacy_info = None
    except OSError as exc:
        raise ValueError("invalid legacy handoff") from exc
    if legacy_info is not None and stat.S_ISLNK(legacy_info.st_mode):
        raise ValueError("invalid legacy handoff")
    _require_source_backed_project(root, project_slug, workspace)

    target_dir = root / "projects" / project_slug
    staging_dir = root / "_staging" / project_slug
    target = target_dir / "handoff.md"

    with _handoff_lock(root, staging_dir):
        _ensure_safe_directory(root, target_dir)
        previous_raw = _read_regular_file(root, target, missing_ok=True)
        previous_sha256 = None
        if previous_raw is not None:
            _validate_existing(project_slug, previous_raw)
            previous_sha256 = hashlib.sha256(previous_raw).hexdigest()
            if expected_sha256 is None:
                raise ValueError(
                    "expected_sha256 is required for handoff update"
                )
            if previous_sha256 != expected_sha256:
                raise ValueError("handoff target sha256 mismatch")
        elif expected_sha256 is not None:
            raise ValueError("handoff target sha256 mismatch")

        rendered = _render_handoff(project_slug, content)
        if len(rendered) > _MAX_HANDOFF_BYTES:
            raise ValueError("handoff content is too large")
        try:
            rendered_text = rendered.decode("utf-8")
        except UnicodeDecodeError as exc:  # pragma: no cover - rendered bytes are UTF-8.
            raise ValueError("handoff validation failed") from exc
        if _project_slug_from_frontmatter(rendered_text) != project_slug:
            raise ValueError("handoff validation failed")

        temp = _write_temp(target_dir, rendered)
        try:
            if previous_raw is None:
                _publish_create(root, target, temp, rendered)
            else:
                _publish_update(root, target, temp, rendered, previous_raw)
        finally:
            if temp.exists() and not temp.is_symlink():
                try:
                    _unlink_owned(temp)
                except ValueError:
                    pass

        final_raw = _read_regular_file(root, target)
        if final_raw != rendered:
            raise ValueError("handoff readback failed")
        after_sha256 = hashlib.sha256(final_raw).hexdigest()
        receipt = {
            "project_slug": project_slug,
            "path": f"projects/{project_slug}/handoff.md",
            "status": "updated" if previous_sha256 else "created",
            "sha256": after_sha256,
            "previous_sha256": previous_sha256,
            "before_sha256": previous_sha256,
            "after_sha256": after_sha256,
            "expected_sha256": expected_sha256,
            "content_size": len(final_raw),
        }
        receipt["receipt_sha256"] = hashlib.sha256(
            _canonical_json_bytes(receipt)
        ).hexdigest()
        return receipt


__all__ = ["update_project_handoff"]
