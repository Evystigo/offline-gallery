"""Albums are real directories under a library root.

Adding media to an album moves the file into that folder. Every move is written to
`move_log` so other machines can follow it (see sync). Nothing here ever deletes or
overwrites a user file: collisions are skipped and reported.
"""

import os
import posixpath
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

_INVALID_CHARS = set('<>:"|?*')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


class AlbumError(Exception):
    """A user-facing problem (bad name, folder not empty, ...)."""


@dataclass
class Album:
    id: int
    root: str
    path: str  # posix, relative to root, e.g. "Trips/2024"
    count: int  # media present in this folder and its subfolders

    @property
    def name(self) -> str:
        return posixpath.basename(self.path)

    @property
    def depth(self) -> int:
        return self.path.count("/")


@dataclass
class MoveResult:
    moved: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (file, reason)


def normalize(name: str) -> str:
    return " ".join(name.split())


def validate_path(name: str) -> str:
    """Normalize a user-typed album path ('Trips/2024') and reject unsafe names."""
    parts = [normalize(p) for p in name.replace("\\", "/").split("/")]
    parts = [p for p in parts if p]
    if not parts:
        raise AlbumError("Album name cannot be empty")
    for p in parts:
        if p.startswith("."):
            raise AlbumError(f"“{p}”: album names cannot start with a dot")
        if any(c in _INVALID_CHARS or ord(c) < 32 for c in p):
            raise AlbumError(f"“{p}”: album names cannot contain < > : \" | ? * or control characters")
        if p.endswith("."):
            raise AlbumError(f"“{p}”: album names cannot end with a dot")
        if p.split(".")[0].lower() in _RESERVED:
            raise AlbumError(f"“{p}” is a reserved name on Windows")
        if len(p) > 120:
            raise AlbumError("Album name is too long")
    return "/".join(parts)


def _folder_row(conn, root: str, rel: str):
    return conn.execute("SELECT * FROM folders WHERE root = ? AND rel_path = ?", (root, rel)).fetchone()


def list_albums(conn) -> list[Album]:
    """Present album folders, sorted by path; counts include subfolders."""
    rows = conn.execute(
        "SELECT f.id, f.root, f.rel_path, "
        " (SELECT COUNT(*) FROM media m WHERE m.root = f.root AND m.present = 1 AND "
        "  (m.album = f.rel_path OR lower(substr(m.album, 1, length(f.rel_path) + 1)) = lower(f.rel_path) || '/')) AS n "
        "FROM folders f WHERE f.present = 1 ORDER BY f.rel_path COLLATE NOCASE"
    )
    return [Album(r["id"], r["root"], r["rel_path"], r["n"]) for r in rows]


def create_album(conn, root: str | Path, name: str) -> Album:
    """Create the folder (and any missing parents). Reuses existing folders case-insensitively."""
    root = str(root)
    if not Path(root).is_dir():
        raise AlbumError(f"Library folder not found: {root}")
    wanted = validate_path(name).split("/")
    rel = ""
    for seg in wanted:
        probe = f"{rel}/{seg}" if rel else seg
        existing = _folder_row(conn, root, probe)  # NOCASE: finds the stored spelling
        rel = existing["rel_path"] if existing else probe
    target = Path(root) / rel
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise AlbumError(f"Could not create folder: {e}") from e
    with conn:
        parts = rel.split("/")
        for i in range(1, len(parts) + 1):
            conn.execute(
                "INSERT INTO folders (root, rel_path) VALUES (?, ?) "
                "ON CONFLICT(root, rel_path) DO UPDATE SET present = 1",
                (root, "/".join(parts[:i])),
            )
    row = _folder_row(conn, root, rel)
    return Album(row["id"], root, row["rel_path"], 0)


def move_to_album(
    conn, media_ids: Iterable[int], root: str | Path, album_path: str, now: float | None = None
) -> MoveResult:
    """Move files into the album folder (`''` = the library root, i.e. out of any album)."""
    now = time.time() if now is None else now
    root = str(root)
    if album_path:
        folder = _folder_row(conn, root, album_path)
        if folder is None or not folder["present"]:
            raise AlbumError(f"Album “{album_path}” does not exist")
        album_path = folder["rel_path"]  # stored spelling
    result = MoveResult()
    dest_dir = Path(root) / album_path
    for mid in media_ids:
        row = conn.execute("SELECT * FROM media WHERE id = ?", (mid,)).fetchone()
        if row is None:
            continue
        name = posixpath.basename(row["rel_path"])
        if not row["present"]:
            result.skipped.append((name, "file is missing from disk"))
            continue
        if row["root"] != root:
            result.skipped.append((name, "it is in a different library folder"))
            continue
        if row["album"].lower() == album_path.lower():
            continue  # already there
        src = Path(root) / row["rel_path"]
        dest = dest_dir / name
        new_rel = posixpath.join(album_path, name)
        if not src.exists():
            result.skipped.append((name, "file not found on disk"))
        elif os.path.lexists(dest):
            result.skipped.append((name, "a file with that name already exists in the destination"))
        elif conn.execute("SELECT 1 FROM media WHERE rel_path = ? AND id != ?", (new_rel, mid)).fetchone():
            result.skipped.append((name, "an older record already uses that path"))
        else:
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
                os.rename(src, dest)
            except OSError as e:
                result.skipped.append((name, str(e)))
                continue
            try:
                with conn:
                    conn.execute("UPDATE media SET rel_path = ?, album = ? WHERE id = ?", (new_rel, album_path, mid))
                    conn.execute(
                        "INSERT INTO move_log (from_path, to_path, size, moved_at) VALUES (?, ?, ?, ?)",
                        (row["rel_path"], new_rel, row["size"], now),
                    )
            except sqlite3.Error as e:
                os.rename(dest, src)  # keep disk and database in agreement
                result.skipped.append((name, f"database error: {e}"))
                continue
            result.moved += 1
    return result


def rename_album(conn, album_id: int, new_name: str, now: float | None = None) -> str:
    """Rename a folder in place (same parent). Returns the new path."""
    now = time.time() if now is None else now
    folder = conn.execute("SELECT * FROM folders WHERE id = ?", (album_id,)).fetchone()
    if folder is None:
        raise AlbumError("Album not found")
    leaf = validate_path(new_name)
    if "/" in leaf:
        raise AlbumError("Rename only changes the album's own name; use “/” when creating nested albums")
    old, root = folder["rel_path"], folder["root"]
    parent = posixpath.dirname(old)
    new = posixpath.join(parent, leaf)
    if new == old:
        return old
    case_only = new.lower() == old.lower()
    if not case_only and (_folder_row(conn, root, new) or os.path.lexists(Path(root) / new)):
        raise AlbumError(f"An album named “{leaf}” already exists here")
    return _relocate_album(conn, folder, new, now)


def move_album(conn, album_id: int, dest_id: int | None, now: float | None = None) -> str:
    """Move an album, with everything inside it, into another album (`dest_id`) or, with None, to the
    top level of its library folder. Returns the album's new path."""
    now = time.time() if now is None else now
    folder = conn.execute("SELECT * FROM folders WHERE id = ?", (album_id,)).fetchone()
    if folder is None:
        raise AlbumError("Album not found")
    old, root = folder["rel_path"], folder["root"]
    new_parent = ""
    if dest_id is not None:
        dest = conn.execute("SELECT * FROM folders WHERE id = ? AND present = 1", (dest_id,)).fetchone()
        if dest is None:
            raise AlbumError("The destination album does not exist")
        if dest["root"] != root:
            raise AlbumError("Albums cannot be moved between different library folders")
        new_parent = dest["rel_path"]
        if new_parent.lower() == old.lower() or new_parent.lower().startswith(old.lower() + "/"):
            raise AlbumError("An album cannot be moved into itself or one of its own sub-albums")
    leaf = posixpath.basename(old)
    new = posixpath.join(new_parent, leaf)
    if new.lower() == old.lower():
        return old  # already there
    if _folder_row(conn, root, new) or os.path.lexists(Path(root) / new):
        where = f"“{new_parent}”" if new_parent else "the top level"
        raise AlbumError(f"{where} already has an album or file named “{leaf}”")
    return _relocate_album(conn, folder, new, now)


def _relocate_album(conn, folder, new: str, now: float) -> str:
    """Rename/move an album folder (and everything in it) to the relative path `new`."""
    old, root = folder["rel_path"], folder["root"]
    try:
        os.rename(Path(root) / old, Path(root) / new)
    except OSError as e:
        raise AlbumError(f"Could not move folder: {e}") from e

    in_tree = "(album = :old OR lower(substr(album, 1, length(:old) + 1)) = lower(:old) || '/')"
    p = {"old": old, "new": new, "cut": len(old) + 1, "root": root, "now": now}
    try:
        with conn:
            conn.execute(
                "INSERT INTO move_log (from_path, to_path, size, moved_at) "
                f"SELECT rel_path, :new || substr(rel_path, :cut), size, :now FROM media "
                f"WHERE root = :root AND present = 1 AND {in_tree}",
                p,
            )
            conn.execute(
                "UPDATE media SET rel_path = :new || substr(rel_path, :cut), album = :new || substr(album, :cut) "
                f"WHERE root = :root AND {in_tree}",
                p,
            )
            conn.execute(
                "UPDATE folders SET rel_path = :new || substr(rel_path, :cut) WHERE root = :root AND "
                "(rel_path = :old OR lower(substr(rel_path, 1, length(:old) + 1)) = lower(:old) || '/')",
                p,
            )
    except sqlite3.Error as e:
        os.rename(Path(root) / new, Path(root) / old)
        raise AlbumError(f"Database error, move undone: {e}") from e
    return new


def delete_album(conn, album_id: int) -> None:
    """Remove an EMPTY album folder. Folders that still hold anything are refused."""
    folder = conn.execute("SELECT * FROM folders WHERE id = ?", (album_id,)).fetchone()
    if folder is None:
        return
    path = Path(folder["root"]) / folder["rel_path"]
    try:
        if path.exists():
            os.rmdir(path)  # fails unless completely empty
    except OSError:
        raise AlbumError(
            f"“{folder['rel_path']}” is not empty. Move its files out (and delete sub-albums) first."
        ) from None
    with conn:
        conn.execute("DELETE FROM folders WHERE id = ?", (album_id,))
