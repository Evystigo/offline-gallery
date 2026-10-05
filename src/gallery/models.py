"""Query helpers over the database."""

import sqlite3

from .search import Query, search


def list_media(
    conn: sqlite3.Connection,
    media_type: str | None = None,
    unsorted: bool = False,
    sort: str = "newest",
) -> list[sqlite3.Row]:
    """Media present on disk. `unsorted` = no live tags and no live album membership."""
    return search(conn, Query(media_type=media_type, unsorted=unsorted), sort)


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    with conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
