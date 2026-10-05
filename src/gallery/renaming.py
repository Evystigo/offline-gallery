"""Rename files on disk (mass rename: "file1", "file2", ...).

A batch is validated as a whole and is all-or-nothing: if anything would collide, nothing is renamed.
Files are renamed through temporary names, so chains and swaps (file1 -> file2 while file2 -> file3)
work, and everything is rolled back if the disk or the database fails part-way. Tags stay attached
because the database row is updated in place. Every rename is logged in `move_log` so other
machines can follow it (see sync).
"""

import os
import posixpath
import sqlite3
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .albums import _RESERVED

_INVALID_CHARS = set('<>:"|?*/\\')
MAX_NAME = 200


class RenameError(Exception):
    """The batch could not be renamed; nothing was changed."""


@dataclass
class RenameItem:
    media_id: int
    root: str
    old_rel: str
    new_rel: str
    size: int


@dataclass
class RenamePlan:
    items: list[RenameItem] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    unchanged: int = 0  # files whose name would not change

    @property
    def ok(self) -> bool:
        return not self.problems


def validate_filename(name: str) -> str:
    """Return the name if it is safe as a single file name on Windows and Linux, else raise RenameError."""
    if not name or name != name.strip():
        raise RenameError("File names cannot be empty or start or end with a space")
    if name in (".", "..") or name.startswith("."):
        raise RenameError(f"“{name}”: names cannot start with a dot")
    if any(c in _INVALID_CHARS or ord(c) < 32 for c in name):
        raise RenameError(f"“{name}”: names cannot contain / \\ < > : \" | ? * or control characters")
    if name.endswith("."):
        raise RenameError(f"“{name}”: names cannot end with a dot")
    if name.split(".")[0].lower() in _RESERVED:
        raise RenameError(f"“{name}” is a reserved name on Windows")
    if len(name) > MAX_NAME:
        raise RenameError("That name is too long")
    return name


def numbered_names(filenames: list[str], base: str, start: int = 1, digits: int = 1, number: bool = True) -> list[str]:
    """base + number + the file's own extension, e.g. file1.jpg, file2.mp4."""
    out = []
    for i, filename in enumerate(filenames):
        ext = os.path.splitext(filename)[1]
        n = str(start + i).zfill(digits) if number else ""
        out.append(f"{base}{n}{ext}")
    return out


def build_plan(
    conn: sqlite3.Connection,
    media_ids: Iterable[int],
    base: str,
    start: int = 1,
    digits: int = 1,
    number: bool = True,
) -> RenamePlan:
    """Plan "base1.ext, base2.ext, ..." for the files, in the order given."""
    ids = list(media_ids)
    rows = {r["id"]: r for r in conn.execute(f"SELECT * FROM media WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else {}
    ordered = [rows[i] for i in ids if i in rows]
    if not number and len(ordered) > 1:
        plan = RenamePlan()
        plan.problems.append("Several files need numbers so that their names differ")
        return plan
    names = numbered_names([posixpath.basename(r["rel_path"]) for r in ordered], base.strip(), start, digits, number)
    return build_named_plan(conn, [(r["id"], n) for r, n in zip(ordered, names)])


def build_named_plan(conn: sqlite3.Connection, pairs: list[tuple[int, str]]) -> RenamePlan:
    """Plan explicit renames: [(media_id, new file name)]. The directory never changes."""
    plan = RenamePlan()
    batch_ids = {mid for mid, _ in pairs}
    candidates: list[RenameItem] = []
    for mid, new_name in pairs:
        row = conn.execute("SELECT * FROM media WHERE id = ?", (mid,)).fetchone()
        if row is None or not row["present"]:
            plan.problems.append(f"{new_name}: the file is missing")
            continue
        try:
            validate_filename(new_name)
        except RenameError as e:
            if str(e) not in plan.problems:
                plan.problems.append(str(e))
            continue
        old_rel = row["rel_path"]
        new_rel = posixpath.join(posixpath.dirname(old_rel), new_name)
        if new_rel == old_rel:
            plan.unchanged += 1
            continue
        candidates.append(RenameItem(mid, row["root"], old_rel, new_rel, row["size"]))

    sources = {os.path.normcase(str(Path(c.root) / c.old_rel)) for c in candidates}
    seen_targets: set[str] = set()
    for c in candidates:
        target = os.path.normcase(str(Path(c.root) / c.new_rel))
        name = posixpath.basename(c.new_rel)
        if c.new_rel.lower() in seen_targets:
            plan.problems.append(f"{name}: two files would get the same name")
            continue
        seen_targets.add(c.new_rel.lower())
        if os.path.lexists(Path(c.root) / c.new_rel) and target not in sources:
            plan.problems.append(f"{name} already exists in {posixpath.dirname(c.new_rel) or 'the library folder'}")
            continue
        clash = conn.execute("SELECT id FROM media WHERE rel_path = ?", (c.new_rel,)).fetchone()
        if clash is not None and clash["id"] not in batch_ids:
            plan.problems.append(f"{name}: an older record already uses that name")
            continue
        plan.items.append(c)
    return plan


def apply_plan(conn: sqlite3.Connection, plan: RenamePlan, now: float | None = None) -> int:
    """Perform the renames. Returns how many files were renamed; raises RenameError, changing nothing, on failure."""
    now = time.time() if now is None else now
    if plan.problems:
        raise RenameError("; ".join(plan.problems[:3]))
    items = plan.items
    if not items:
        return 0
    token = uuid.uuid4().hex[:8]
    srcs = [Path(it.root) / it.old_rel for it in items]
    dsts = [Path(it.root) / it.new_rel for it in items]
    tmps = [s.with_name(f".gallery-rename-{token}-{i}{s.suffix}") for i, s in enumerate(srcs)]
    for s in srcs:
        if not s.exists():
            raise RenameError(f"{s.name} is no longer on disk")

    at_tmp: list[int] = []  # indexes currently sitting at their temporary name
    at_dst: list[int] = []  # indexes already at their final name

    def rollback() -> None:
        for i in reversed(at_dst):
            _try_rename(dsts[i], tmps[i])
        for i in reversed(at_tmp + at_dst):
            _try_rename(tmps[i], srcs[i])

    try:
        for i in range(len(items)):
            os.rename(srcs[i], tmps[i])
            at_tmp.append(i)
        for i in range(len(items)):
            os.rename(tmps[i], dsts[i])
            at_tmp.remove(i)
            at_dst.append(i)
    except OSError as e:
        rollback()
        raise RenameError(f"Could not rename the files ({e}). Nothing was changed.") from e

    try:
        with conn:
            for it in items:  # park rows on unique placeholders first so swaps never violate UNIQUE(rel_path)
                # "<" cannot appear in a Windows file name, so a placeholder never matches a real path
                conn.execute("UPDATE media SET rel_path = ? WHERE id = ?", (f"<rename>-{it.media_id}", it.media_id))
            for it in items:
                conn.execute(
                    "UPDATE media SET rel_path = ?, filename = ? WHERE id = ?",
                    (it.new_rel, posixpath.basename(it.new_rel), it.media_id),
                )
                conn.execute(
                    "INSERT INTO move_log (from_path, to_path, size, moved_at) VALUES (?, ?, ?, ?)",
                    (it.old_rel, it.new_rel, it.size, now),
                )
    except sqlite3.Error as e:
        for i in reversed(at_dst):
            _try_rename(dsts[i], srcs[i])
        raise RenameError(f"Database error, files restored: {e}") from e
    return len(items)


def rename_media(conn: sqlite3.Connection, media_id: int, new_name: str, now: float | None = None) -> int:
    """Rename a single file (used when applying a rename that another machine made)."""
    return apply_plan(conn, build_named_plan(conn, [(media_id, new_name)]), now)


def _try_rename(src: Path, dst: Path) -> None:
    try:
        os.rename(src, dst)
    except OSError:
        pass  # best effort while undoing
