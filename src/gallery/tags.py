"""Tag operations. Removals are tombstones (deleted=1) so they propagate through sync."""

import sqlite3
import time
from collections.abc import Iterable


def normalize(name: str) -> str:
    return " ".join(name.split())


def parse_names(text: str) -> list[str]:
    """Split comma-separated user input into unique, normalized tag names."""
    seen: dict[str, str] = {}
    for part in text.split(","):
        n = normalize(part)
        if n:
            seen.setdefault(n.lower(), n)
    return list(seen.values())


def _tag_id(conn: sqlite3.Connection, name: str) -> int:
    conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
    return conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()["id"]


def add_tags(conn, media_ids: Iterable[int], names: Iterable[str], now: float | None = None) -> None:
    now = time.time() if now is None else now
    media_ids = list(media_ids)
    with conn:
        for name in names:
            name = normalize(name)
            if not name:
                continue
            tid = _tag_id(conn, name)
            for mid in media_ids:
                conn.execute(
                    "INSERT INTO media_tags (media_id, tag_id, updated_at, deleted) VALUES (?, ?, ?, 0) "
                    "ON CONFLICT(media_id, tag_id) DO UPDATE SET deleted = 0, updated_at = excluded.updated_at",
                    (mid, tid, now),
                )


def remove_tag(conn, media_ids: Iterable[int], name: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    with conn:
        conn.executemany(
            "UPDATE media_tags SET deleted = 1, updated_at = ? WHERE media_id = ? AND deleted = 0 "
            "AND tag_id = (SELECT id FROM tags WHERE name = ?)",
            [(now, mid, normalize(name)) for mid in media_ids],
        )


def tags_for(conn, media_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT t.name FROM media_tags mt JOIN tags t ON t.id = mt.tag_id "
        "WHERE mt.media_id = ? AND mt.deleted = 0 ORDER BY t.name",
        (media_id,),
    )
    return [r["name"] for r in rows]


def tag_counts_for(conn, media_ids: list[int]) -> dict[str, int]:
    """For a selection: tag name -> how many of the selected items carry it."""
    if not media_ids:
        return {}
    marks = ",".join("?" * len(media_ids))
    rows = conn.execute(
        f"SELECT t.name, COUNT(*) AS n FROM media_tags mt JOIN tags t ON t.id = mt.tag_id "
        f"WHERE mt.deleted = 0 AND mt.media_id IN ({marks}) GROUP BY t.id ORDER BY t.name",
        media_ids,
    )
    return {r["name"]: r["n"] for r in rows}


def all_tags(conn) -> list[tuple[str, int]]:
    """Every tag with at least one live use, with its usage count."""
    rows = conn.execute(
        "SELECT t.name, COUNT(*) AS n FROM tags t JOIN media_tags mt ON mt.tag_id = t.id "
        "WHERE mt.deleted = 0 GROUP BY t.id ORDER BY t.name"
    )
    return [(r["name"], r["n"]) for r in rows]


def delete_tag(conn, name: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    with conn:
        conn.execute(
            "UPDATE media_tags SET deleted = 1, updated_at = ? WHERE deleted = 0 "
            "AND tag_id = (SELECT id FROM tags WHERE name = ?)",
            (now, normalize(name)),
        )


def rename_tag(conn, old: str, new: str, now: float | None = None) -> None:
    """Rename `old` to `new`; if `new` already exists the two are merged."""
    now = time.time() if now is None else now
    old, new = normalize(old), normalize(new)
    if not new:
        raise ValueError("Tag name cannot be empty")
    if old.lower() == new.lower():
        if old != new:  # case-only change
            with conn:
                conn.execute("UPDATE tags SET name = ? WHERE name = ?", (new, old))
        return
    ids = [
        r["media_id"]
        for r in conn.execute(
            "SELECT mt.media_id FROM media_tags mt JOIN tags t ON t.id = mt.tag_id "
            "WHERE t.name = ? AND mt.deleted = 0",
            (old,),
        )
    ]
    add_tags(conn, ids, [new], now)
    delete_tag(conn, old, now)
