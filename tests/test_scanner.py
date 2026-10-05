import os

import pytest

from gallery import db, scanner


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    yield c
    c.close()


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "lib"
    (root / "trips" / "2024").mkdir(parents=True)
    (root / ".gallery-sync").mkdir()
    (root / "a.jpg").write_bytes(b"x")
    (root / "trips" / "2024" / "B.MP4").write_bytes(b"xx")
    (root / "trips" / "notes.txt").write_bytes(b"x")
    (root / "trips" / "raw.cr2").write_bytes(b"x")
    (root / ".gallery-sync" / "hidden.jpg").write_bytes(b"x")
    return root


def paths(conn):
    return {r["rel_path"]: r["type"] for r in conn.execute("SELECT rel_path, type FROM media")}


def test_media_type():
    assert scanner.media_type("x.JPG") == "photo"
    assert scanner.media_type("x.mkv") == "video"
    assert scanner.media_type("x.cr2") is None


def test_scan_finds_media_with_posix_relative_paths(conn, lib):
    res = scanner.scan_root(conn, lib)
    assert res.added == 2
    assert paths(conn) == {"a.jpg": "photo", "trips/2024/B.MP4": "video"}


def test_rescan_is_idempotent(conn, lib):
    scanner.scan_root(conn, lib)
    res = scanner.scan_root(conn, lib)
    assert (res.added, res.updated, res.missing) == (0, 0, 0)


def test_missing_files_flagged_not_deleted_and_restored(conn, lib):
    scanner.scan_root(conn, lib)
    (lib / "a.jpg").unlink()
    assert scanner.scan_root(conn, lib).missing == 1
    row = conn.execute("SELECT present FROM media WHERE rel_path = 'a.jpg'").fetchone()
    assert row["present"] == 0
    (lib / "a.jpg").write_bytes(b"x")
    assert scanner.scan_root(conn, lib).restored == 1


def test_modified_file_updates(conn, lib):
    scanner.scan_root(conn, lib)
    f = lib / "a.jpg"
    f.write_bytes(b"longer")
    os.utime(f, (1_000_000, 1_000_000))
    assert scanner.scan_root(conn, lib).updated == 1


def test_bad_root(conn, tmp_path):
    with pytest.raises(NotADirectoryError):
        scanner.scan_root(conn, tmp_path / "nope")
