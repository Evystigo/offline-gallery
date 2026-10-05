"""Scan library roots and keep the `media` table in step with the disk."""

import os
import posixpath
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".heic", ".heif"}
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".wmv", ".flv", ".mpg", ".mpeg", ".3gp"}


@dataclass
class ScanResult:
    added: int = 0
    updated: int = 0
    missing: int = 0
    restored: int = 0


def media_type(path: str | Path) -> str | None:
    ext = os.path.splitext(path)[1].lower()
    if ext in PHOTO_EXTS:
        return "photo"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def _walk(root: Path, folders: set[str]):
    """Yield media files under root; collect every non-hidden folder (as posix rel paths)."""
    for dirpath, dirnames, filenames in os.walk(root):
        # Skip hidden dirs (includes our own .gallery-sync folder)
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        if rel_dir != ".":
            folders.add(rel_dir)
        for name in filenames:
            if name.startswith("."):
                continue
            kind = media_type(name)
            if kind is None:
                continue
            full = Path(dirpath) / name
            try:
                st = full.stat()
            except OSError:
                continue
            rel = full.relative_to(root).as_posix()
            yield rel, name, kind, st.st_size, st.st_mtime


def scan_root(conn: sqlite3.Connection, root: Path | str) -> ScanResult:
    """Scan one library root. Files that vanished are flagged absent, never deleted,
    so their tags survive a temporarily unavailable drive or sync folder."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)
    root_s = str(root)
    result = ScanResult()
    now = time.time()
    seen: set[str] = set()
    folders: set[str] = set()

    with conn:
        existing = {
            r["rel_path"].lower(): r
            for r in conn.execute("SELECT id, rel_path, size, mtime, present FROM media WHERE root = ?", (root_s,))
        }
        for rel, name, kind, size, mtime in _walk(root, folders):
            key = rel.lower()
            seen.add(key)
            row = existing.get(key)
            if row is None:
                try:
                    conn.execute(
                        "INSERT INTO media (rel_path, root, album, filename, type, size, mtime, added_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (rel, root_s, posixpath.dirname(rel), name, kind, size, mtime, now),
                    )
                except sqlite3.IntegrityError:
                    continue  # same rel_path already owned by another root
                result.added += 1
            else:
                if not row["present"]:
                    result.restored += 1
                if not row["present"] or row["size"] != size or row["mtime"] != mtime:
                    conn.execute(
                        "UPDATE media SET size = ?, mtime = ?, present = 1 WHERE id = ?",
                        (size, mtime, row["id"]),
                    )
                    if row["present"]:
                        result.updated += 1
        for key, row in existing.items():
            if key not in seen and row["present"]:
                conn.execute("UPDATE media SET present = 0 WHERE id = ?", (row["id"],))
                result.missing += 1
        _sync_folders(conn, root_s, folders)
    return result


def _sync_folders(conn: sqlite3.Connection, root: str, folders: set[str]) -> None:
    """Mirror the on-disk folder set into `folders` (vanished folders are flagged, not deleted)."""
    wanted = {f.lower(): f for f in folders}
    known = {r["rel_path"].lower(): r for r in conn.execute("SELECT id, rel_path, present FROM folders WHERE root = ?", (root,))}
    for key, rel in wanted.items():
        row = known.get(key)
        if row is None:
            conn.execute("INSERT INTO folders (root, rel_path) VALUES (?, ?)", (root, rel))
        elif not row["present"]:
            conn.execute("UPDATE folders SET present = 1 WHERE id = ?", (row["id"],))
    for key, row in known.items():
        if key not in wanted and row["present"]:
            conn.execute("UPDATE folders SET present = 0 WHERE id = ?", (row["id"],))
