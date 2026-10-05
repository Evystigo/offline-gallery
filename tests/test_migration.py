import sqlite3

from gallery import db

V1_SCHEMA = """
CREATE TABLE media (
    id INTEGER PRIMARY KEY, rel_path TEXT NOT NULL UNIQUE COLLATE NOCASE, root TEXT NOT NULL,
    filename TEXT NOT NULL COLLATE NOCASE, type TEXT NOT NULL, size INTEGER NOT NULL, mtime REAL NOT NULL,
    width INTEGER, height INTEGER, duration REAL, present INTEGER NOT NULL DEFAULT 1, added_at REAL NOT NULL
);
CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE);
CREATE TABLE media_tags (
    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    updated_at REAL NOT NULL, deleted INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (media_id, tag_id)
);
CREATE TABLE albums (id INTEGER PRIMARY KEY, name TEXT, created_at REAL, updated_at REAL, deleted INTEGER);
CREATE TABLE album_media (album_id INTEGER, media_id INTEGER, updated_at REAL, deleted INTEGER);
CREATE TABLE pending_sync (id INTEGER PRIMARY KEY, rel_path TEXT, kind TEXT, name TEXT, updated_at REAL, deleted INTEGER);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO media (id, rel_path, root, filename, type, size, mtime, added_at) VALUES
    (1, 'a.jpg', '/r', 'a.jpg', 'photo', 1, 1, 1),
    (2, 'Trips/2024/b.jpg', '/r', 'b.jpg', 'photo', 1, 1, 1);
INSERT INTO tags VALUES (1, 'sun');
INSERT INTO media_tags VALUES (2, 1, 5, 0);
INSERT INTO settings VALUES ('roots', '["/r"]');
PRAGMA user_version = 1;
"""


def test_v1_database_migrates_to_folder_albums(tmp_path):
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript(V1_SCHEMA)
    raw.close()

    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    albums = {r["rel_path"]: r["album"] for r in conn.execute("SELECT rel_path, album FROM media")}
    assert albums == {"a.jpg": "", "Trips/2024/b.jpg": "Trips/2024"}
    # tags and settings survive; virtual album tables are gone
    assert conn.execute("SELECT deleted FROM media_tags WHERE media_id = 2").fetchone()["deleted"] == 0
    assert conn.execute("SELECT value FROM settings WHERE key = 'roots'").fetchone()
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"folders", "move_log"} <= tables and not ({"albums", "album_media"} & tables)
    conn.close()


def test_fresh_database_is_current_version(tmp_path):
    conn = db.connect(tmp_path / "new.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    conn.close()
