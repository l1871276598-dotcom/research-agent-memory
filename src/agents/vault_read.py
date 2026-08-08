"""Vault read agent — fd-rooted vault file read (GP3-01 / S8).

Reads a note from the vault using an fd-rooted traversal so the validated
directory chain IS the opened directory chain. Python's ``os.open(path, flags,
dir_fd=...)`` + ``O_NOFOLLOW`` on every component closes the symlink swap race
that a pathname-based lstat→stat→open sequence leaves open:

    open trusted vault root directory fd
    for each path component:
        os.open(component, O_DIRECTORY|O_NOFOLLOW, dir_fd=parent_fd)
        fstat; require directory
    final component:
        os.open(name, O_RDONLY|O_NOFOLLOW, dir_fd=parent_fd)
        require regular file
    read from that exact fd

O_NOFOLLOW rejects BOTH a final-component symlink and an intermediate
directory symlink. The path is never re-resolved after the fd chain is built,
so an attacker cannot swap a validated pathname to point outside the vault.

The agent is an ingress primitive for vault evidence: it never creates
candidates, reviews, or activates memory.
"""

import os
import stat

from .base import BaseAgent


class VaultReadError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# R1-GP3/R2-GP3 (Round 4): O_NOFOLLOW/O_DIRECTORY must NOT silently degrade to
# 0 — that would fail open (a platform without O_NOFOLLOW would follow
# symlinks). If either is missing, vault.read is entirely unavailable
# (fail-closed) rather than running without the symlink guarantee.
if getattr(os, "O_NOFOLLOW", None) is None or getattr(os, "O_DIRECTORY", None) is None:
    raise RuntimeError("vault.read requires O_NOFOLLOW and O_DIRECTORY (unsupported platform)")

_NOFOLLOW = os.O_NOFOLLOW
_O_DIRECTORY = os.O_DIRECTORY
_MAX_NOTE_BYTES = 512 * 1024

# A note-relative path must be reject-all: no "." / ".." / NUL / backslash /
# empty segments / absolute path / drive or URL scheme. No aliases are
# canonicalized — the relative path passed to each dir_fd open is exactly the
# caller-supplied segments, so there is no locator representation split.


def _split_relative(relative_path: str) -> list[str]:
    if not isinstance(relative_path, str) or not relative_path:
        raise VaultReadError("invalid_note_path", "note path must be a non-empty string")
    if "\x00" in relative_path or "\\" in relative_path:
        raise VaultReadError("invalid_note_path", "note path must not contain NUL or backslash")
    if relative_path.startswith("/") or os.path.isabs(relative_path):
        raise VaultReadError("invalid_note_path", "note path must be relative")
    segments = relative_path.split("/")
    for segment in segments:
        if segment in (".", ".."):
            raise VaultReadError("note_path_escape", "note path must not contain traversal segments")
        if not segment:
            raise VaultReadError("invalid_note_path", "note path must not contain empty segments")
    return segments


def _read_vault_note_fd_rooted(vault_root: str, relative_path: str, before_read=None) -> str:
    """Read one vault note through an fd-rooted traversal.

    Returns the note content as UTF-8 text. Fails closed (VaultReadError) on
    any symlink at any level, any non-directory intermediate, any non-regular
    final file, or any mutation detected between open and read.
    before_read (test seam): an optional callable invoked after the final fd is
    opened and before the content is read, so tests can deterministically
    simulate a concurrent in-place writer on the same inode.
    """
    if not isinstance(vault_root, str) or not vault_root or not os.path.isabs(vault_root):
        raise VaultReadError("invalid_vault_root", "vault root must be an absolute directory")

    root_info = os.lstat(vault_root)
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise VaultReadError("vault_root_symlink", "vault root must be a real directory")

    root_fd = os.open(vault_root, os.O_RDONLY | _O_DIRECTORY | _NOFOLLOW)
    try:
        root_fd_stat = os.fstat(root_fd)
        if not stat.S_ISDIR(root_fd_stat.st_mode):
            raise VaultReadError("vault_root_symlink", "vault root must be a directory")

        segments = _split_relative(relative_path)
        parent_fd = root_fd
        opened_fds = []  # parents opened during traversal (close after)
        final_fd = None  # GP6-04: tracked so every path closes it (incl. exceptions)
        try:
            for index, segment in enumerate(segments):
                is_final = index == len(segments) - 1
                flags = os.O_RDONLY | _NOFOLLOW
                if not is_final:
                    flags |= _O_DIRECTORY
                try:
                    fd = os.open(segment, flags, dir_fd=parent_fd)
                except OSError as exc:
                    if exc.errno == getattr(os, "ELOOP", 40):
                        raise VaultReadError("note_path_symlink", "note path must not traverse a symbolic link") from exc
                    if exc.errno == getattr(os, "ENOENT", 2):
                        raise VaultReadError("note_missing", "note does not exist") from exc
                    raise VaultReadError("note_unreadable", "note could not be opened") from exc

                if is_final:
                    final_fd = fd
                else:
                    info = os.fstat(fd)
                    if not stat.S_ISDIR(info.st_mode):
                        os.close(fd)
                        raise VaultReadError("note_not_directory", "intermediate path component is not a directory")
                    opened_fds.append(fd)
                    parent_fd = fd

            # Read from the exact final fd.
            before = os.fstat(final_fd)
            if not stat.S_ISREG(before.st_mode):
                raise VaultReadError("note_not_file", "note must be a regular file")
            if before.st_size > _MAX_NOTE_BYTES:
                raise VaultReadError("note_too_large", "note exceeds the size limit")
            if before_read is not None:
                before_read(final_fd, before)
            chunks = []
            total = 0
            while True:
                chunk = os.read(final_fd, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > _MAX_NOTE_BYTES:
                    raise VaultReadError("note_too_large", "note exceeds the size limit")
            after = os.fstat(final_fd)
            # S8b (Round 6): stable-content snapshot. The fd cannot change
            # identity mid-read (same open descriptor), so dev/ino never differ
            # — that check alone cannot catch an in-place modification of the
            # SAME inode during the read. Compare size and nanosecond
            # mtime/ctime as CHANGE DETECTORS (not generation guarantees): if
            # the file was written while we read, these differ and we fail
            # closed rather than minting an evidence artifact from a torn read.
            before_mtime = getattr(before, "st_mtime_ns", int(before.st_mtime * 1_000_000_000))
            after_mtime = getattr(after, "st_mtime_ns", int(after.st_mtime * 1_000_000_000))
            before_ctime = getattr(before, "st_ctime_ns", int(before.st_ctime * 1_000_000_000))
            after_ctime = getattr(after, "st_ctime_ns", int(after.st_ctime * 1_000_000_000))
            if (
                before.st_ino != after.st_ino
                or before.st_dev != after.st_dev
                or before.st_size != after.st_size
                or before_mtime != after_mtime
                or before_ctime != after_ctime
            ):
                raise VaultReadError("note_changed", "note changed during read")
            return b"".join(chunks).decode("utf-8")
        finally:
            # GP6-04: close final_fd unconditionally (no leak on exception).
            if final_fd is not None:
                try:
                    os.close(final_fd)
                except OSError:
                    pass
            for fd in opened_fds:
                try:
                    os.close(fd)
                except OSError:
                    pass
    finally:
        os.close(root_fd)


class VaultReadAgent(BaseAgent):
    agent_id = "vault_read_agent"
    handles = ["vault.read"]

    def __init__(self, trusted_vault_root):
        # GP4-01: the vault root is a Core-side authority, injected at
        # construction from administrator configuration (LAOS_VAULT_ROOT). A
        # caller can NEVER supply a vault_root — doing so would turn vault.read
        # into arbitrary file read. The agent accepts only relative_path.
        # An empty trusted_vault_root means vault evidence is not configured;
        # the agent still constructs (so registry wiring is stable) but every
        # run() fails closed.
        if trusted_vault_root is None:
            raise ValueError("trusted vault root must be a string")
        if trusted_vault_root and not os.path.isabs(trusted_vault_root):
            raise ValueError("trusted vault root must be an absolute path")
        self.trusted_vault_root = trusted_vault_root

    def run(self, task, context):
        values = task.get("input")
        if not isinstance(values, dict):
            raise ValueError("task.input must be an object")
        # Exact input: ONLY relative_path. A caller-supplied vault_root (or any
        # other field) is rejected — the trusted root comes from construction.
        if set(values) != {"relative_path"}:
            raise ValueError("task.input must contain exactly relative_path")
        relative_path = values["relative_path"]
        if not isinstance(relative_path, str) or not relative_path:
            raise ValueError("relative_path must be a non-empty string")
        if not self.trusted_vault_root:
            raise ValueError("vault.read is not configured (no trusted vault root)")
        try:
            content = _read_vault_note_fd_rooted(self.trusted_vault_root, relative_path)
        except VaultReadError as exc:
            raise ValueError(str(exc)) from exc
        return self.result(
            task,
            {"content": content, "relative_path": relative_path},
            candidates=[],
        )


__all__ = ["VaultReadAgent", "VaultReadError", "_read_vault_note_fd_rooted"]
