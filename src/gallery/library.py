"""Library folders (roots). Always stored resolved, so every part of the app compares the same strings.

(The scanner stores media under the resolved path, while folder pickers return e.g. `C:/Users/x`.)
"""

import json
import sqlite3
from pathlib import Path

from . import models


class LibraryError(Exception):
    """A user-facing problem with a library folder."""


def normalize_root(path: str | Path) -> str:
    return str(Path(path).resolve())


def get_roots(conn: sqlite3.Connection) -> list[str]:
    try:
        stored = json.loads(models.get_setting(conn, "roots", "[]"))
    except ValueError:
        stored = []
    roots: list[str] = []
    for r in stored:
        n = normalize_root(r)
        if n not in roots:
            roots.append(n)
    return roots


def _save(conn, roots: list[str]) -> None:
    models.set_setting(conn, "roots", json.dumps(roots))


def add_root(conn: sqlite3.Connection, path: str | Path) -> str:
    """Add a library folder; returns its normalized path. Nested or overlapping folders are refused
    because the same file would then belong to two libraries."""
    root = normalize_root(path)
    if not Path(root).is_dir():
        raise LibraryError(f"Not a folder: {root}")
    new = Path(root)
    roots = get_roots(conn)
    for existing in roots:
        old = Path(existing)
        if old == new:
            raise LibraryError("That folder is already in your library.")
        if old in new.parents:
            raise LibraryError(f"That folder is already inside the library folder {existing}.")
        if new in old.parents:
            raise LibraryError(f"That folder contains the library folder {existing}. Remove it first.")
    roots.append(root)
    _save(conn, roots)
    return root


def remove_root(conn: sqlite3.Connection, path: str | Path) -> None:
    """Stop using a folder. Files on disk are untouched; its items are hidden, and their tags are
    kept so that adding the folder again restores everything."""
    root = normalize_root(path)
    _save(conn, [r for r in get_roots(conn) if r != root])
    with conn:
        conn.execute("UPDATE media SET present = 0 WHERE root = ?", (root,))
        conn.execute("UPDATE folders SET present = 0 WHERE root = ?", (root,))


def forget_roots(conn: sqlite3.Connection) -> None:
    """Called at every launch: the library folder must be chosen again each time the app opens.
    Tags and albums are kept, so choosing the same folder brings everything back."""
    for root in get_roots(conn):
        remove_root(conn, root)
