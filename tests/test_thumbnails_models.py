import posixpath
import time

import pytest
from PIL import Image

from gallery import db, models, thumbnails


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


def add(conn, rel, kind="photo"):
    conn.execute(
        "INSERT INTO media (rel_path, root, album, filename, type, size, mtime, added_at) VALUES (?,?,?,?,?,1,1,?)",
        (rel, "/r", posixpath.dirname(rel), rel.split("/")[-1], kind, time.time()),
    )
    return conn.execute("SELECT id FROM media WHERE rel_path = ?", (rel,)).fetchone()["id"]


def test_image_thumbnail_generated_and_cached(tmp_path):
    src = tmp_path / "big.png"
    Image.new("RGB", (1000, 500), "red").save(src)
    out = tmp_path / "cache"
    out.mkdir()
    p = thumbnails.get_thumbnail(src, "photo", 1.0, out)
    assert p and p.exists()
    with Image.open(p) as im:
        assert max(im.size) == thumbnails.THUMB_SIZE
    assert thumbnails.get_thumbnail(src, "photo", 1.0, out) == p


def test_corrupt_image_returns_none(tmp_path):
    src = tmp_path / "bad.jpg"
    src.write_bytes(b"not an image")
    out = tmp_path / "cache"
    out.mkdir()
    assert thumbnails.get_thumbnail(src, "photo", 1.0, out) is None


def test_filters_and_unsorted(conn):
    a = add(conn, "a.jpg")
    add(conn, "al/b.jpg")  # inside an album folder
    add(conn, "c.mp4", "video")
    conn.execute("INSERT INTO tags (name) VALUES ('x')")
    conn.execute("INSERT INTO media_tags VALUES (?, 1, 1, 0)", (a,))
    names = lambda **kw: {r["rel_path"] for r in models.list_media(conn, **kw)}
    assert names(media_type="video") == {"c.mp4"}
    assert names(unsorted=True) == {"c.mp4"}
    # a tombstoned tag no longer counts
    conn.execute("UPDATE media_tags SET deleted = 1")
    assert names(unsorted=True) == {"a.jpg", "c.mp4"}


def test_absent_media_hidden(conn):
    add(conn, "a.jpg")
    conn.execute("UPDATE media SET present = 0")
    assert models.list_media(conn) == []


def test_settings_roundtrip(conn):
    assert models.get_setting(conn, "k", "d") == "d"
    models.set_setting(conn, "k", "v1")
    models.set_setting(conn, "k", "v2")
    assert models.get_setting(conn, "k") == "v2"
