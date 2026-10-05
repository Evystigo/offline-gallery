"""SQLite schema and connection handling.

The database lives in the OS app-data dir, never in the synced media folder.

Albums are real directories under a library root: a media row's `album` is the
folder it sits in (posix, relative to the root; '' = directly in the root).
"""

import os
import posixpath
import sqlite3
import sys
from pathlib import Path

SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE media (
    id        INTEGER PRIMARY KEY,
    rel_path  TEXT NOT NULL UNIQUE COLLATE NOCASE,
    root      TEXT NOT NULL,
    album     TEXT NOT NULL DEFAULT '' COLLATE NOCASE,
    filename  TEXT NOT NULL COLLATE NOCASE,
    type      TEXT NOT NULL CHECK (type IN ('photo', 'video')),
    size      INTEGER NOT NULL,
    mtime     REAL NOT NULL,
    width     INTEGER,
    height    INTEGER,
    duration  REAL,
    present   INTEGER NOT NULL DEFAULT 1,
    added_at  REAL NOT NULL,
    -- duplicate detection caches; valid only while *_size/*_mtime match size/mtime
    sha256    TEXT,
    sha_size  INTEGER,
    sha_mtime REAL,
    phash     INTEGER,  -- 64-bit dHash stored as signed int
    sig_size  INTEGER,
    sig_mtime REAL
);
CREATE INDEX idx_media_filename ON media(filename);
CREATE INDEX idx_media_album ON media(album);
CREATE INDEX idx_media_sha ON media(sha256);

CREATE TABLE tags (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE media_tags (
    media_id   INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    tag_id     INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    updated_at REAL NOT NULL,
    deleted    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (media_id, tag_id)
);
CREATE INDEX idx_media_tags_tag ON media_tags(tag_id);

-- Album folders found on disk (including empty ones).
CREATE TABLE folders (
    id       INTEGER PRIMARY KEY,
    root     TEXT NOT NULL,
    rel_path TEXT NOT NULL COLLATE NOCASE,
    present  INTEGER NOT NULL DEFAULT 1,
    UNIQUE (root, rel_path)
);

-- Files this app moved between albums; exported so other machines can follow.
CREATE TABLE move_log (
    id        INTEGER PRIMARY KEY,
    from_path TEXT NOT NULL,
    to_path   TEXT NOT NULL,
    size      INTEGER NOT NULL,
    moved_at  REAL NOT NULL
);

CREATE TABLE pending_sync (
    id         INTEGER PRIMARY KEY,
    rel_path   TEXT NOT NULL COLLATE NOCASE,
    name       TEXT NOT NULL COLLATE NOCASE,
    updated_at REAL NOT NULL,
    deleted    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (rel_path, name)
);

CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Duplicate groups the user dismissed ("not duplicates"); sig = sorted member ids.
CREATE TABLE dup_ignored (
    sig TEXT PRIMARY KEY
);
"""

# v1 had virtual albums (albums/album_media tables). Albums are now folders, so
# those tables are dropped; each media row's album is derived from its path.
MIGRATE_1_TO_2 = """
BEGIN;
ALTER TABLE media ADD COLUMN album TEXT NOT NULL DEFAULT '' COLLATE NOCASE;
UPDATE media SET album = dirname(rel_path);
CREATE INDEX idx_media_album ON media(album);
DROP TABLE album_media;
DROP TABLE albums;
DROP TABLE pending_sync;
CREATE TABLE folders (
    id       INTEGER PRIMARY KEY,
    root     TEXT NOT NULL,
    rel_path TEXT NOT NULL COLLATE NOCASE,
    present  INTEGER NOT NULL DEFAULT 1,
    UNIQUE (root, rel_path)
);
CREATE TABLE move_log (
    id        INTEGER PRIMARY KEY,
    from_path TEXT NOT NULL,
    to_path   TEXT NOT NULL,
    size      INTEGER NOT NULL,
    moved_at  REAL NOT NULL
);
CREATE TABLE pending_sync (
    id         INTEGER PRIMARY KEY,
    rel_path   TEXT NOT NULL COLLATE NOCASE,
    name       TEXT NOT NULL COLLATE NOCASE,
    updated_at REAL NOT NULL,
    deleted    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (rel_path, name)
);
PRAGMA user_version = 2;
COMMIT;
"""

MIGRATE_2_TO_3 = """
BEGIN;
ALTER TABLE media ADD COLUMN sha256 TEXT;
ALTER TABLE media ADD COLUMN sha_size INTEGER;
ALTER TABLE media ADD COLUMN sha_mtime REAL;
ALTER TABLE media ADD COLUMN phash INTEGER;
ALTER TABLE media ADD COLUMN sig_size INTEGER;
ALTER TABLE media ADD COLUMN sig_mtime REAL;
CREATE INDEX idx_media_sha ON media(sha256);
CREATE TABLE dup_ignored (sig TEXT PRIMARY KEY);
PRAGMA user_version = 3;
COMMIT;
"""

MIGRATIONS = {1: MIGRATE_1_TO_2, 2: MIGRATE_2_TO_3}


def app_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    path = base / "gallery"
    path.mkdir(parents=True, exist_ok=True)
    return path


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Open (creating/migrating if needed) the gallery database."""
    if path is None:
        path = app_data_dir() / "gallery.db"
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.create_function("dirname", 1, lambda p: posixpath.dirname(p), deterministic=True)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # the scan thread and UI thread share the file
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema v{version} is newer than this app (v{SCHEMA_VERSION})"
        )
    if version == 0:
        with conn:
            conn.executescript(SCHEMA)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        return
    while version < SCHEMA_VERSION:
        conn.executescript(MIGRATIONS[version])
        version = conn.execute("PRAGMA user_version").fetchone()[0]
