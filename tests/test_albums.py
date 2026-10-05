import pytest

from gallery import albums, db, scanner, search, tags


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture
def lib(tmp_path, conn):
    root = tmp_path / "lib"
    root.mkdir()
    (root / "1.jpg").write_bytes(b"one")
    (root / "2.jpg").write_bytes(b"two")
    (root / "3.mp4").write_bytes(b"three")
    scanner.scan_root(conn, root)
    return root.resolve()


def mid(conn, rel):
    return conn.execute("SELECT id FROM media WHERE rel_path = ?", (rel,)).fetchone()["id"]


def paths(conn):
    return sorted(r["rel_path"] for r in conn.execute("SELECT rel_path FROM media WHERE present = 1"))


def in_album(conn, name):
    return sorted(r["rel_path"] for r in search.search(conn, search.Query(album=name)))


# -- create -----------------------------------------------------------------

def test_create_nested_makes_directories_and_records(conn, lib):
    a = albums.create_album(conn, lib, "Trips/2024")
    assert (lib / "Trips" / "2024").is_dir()
    assert a.path == "Trips/2024"
    assert [x.path for x in albums.list_albums(conn)] == ["Trips", "Trips/2024"]


def test_create_reuses_existing_folder_case_insensitively(conn, lib):
    a = albums.create_album(conn, lib, "Trips")
    b = albums.create_album(conn, lib, "trips/Summer")
    assert b.path == "Trips/Summer"  # parent keeps its stored spelling
    assert albums.create_album(conn, lib, "TRIPS").id == a.id
    assert sorted(p.name for p in lib.iterdir() if p.is_dir()) == ["Trips"]


@pytest.mark.parametrize("bad", ["", "   ", "..", "a/../b", ".hidden", "a:b", "x?", "CON", "nul.txt", "end.", "a" * 200])
def test_invalid_names_rejected(conn, lib, bad):
    with pytest.raises(albums.AlbumError):
        albums.create_album(conn, lib, bad)
    assert [p for p in lib.iterdir() if p.is_dir()] == []


def test_create_in_missing_root(conn, tmp_path):
    with pytest.raises(albums.AlbumError):
        albums.create_album(conn, tmp_path / "nope", "A")


# -- move -------------------------------------------------------------------

def test_move_moves_the_file_and_keeps_tags(conn, lib):
    m = mid(conn, "1.jpg")
    tags.add_tags(conn, [m], ["sun"])
    albums.create_album(conn, lib, "Trips")
    res = albums.move_to_album(conn, [m], lib, "Trips", now=99)
    assert (res.moved, res.skipped) == (1, [])
    assert not (lib / "1.jpg").exists()
    assert (lib / "Trips" / "1.jpg").read_bytes() == b"one"
    row = conn.execute("SELECT rel_path, album FROM media WHERE id = ?", (m,)).fetchone()
    assert (row["rel_path"], row["album"]) == ("Trips/1.jpg", "Trips")
    assert tags.tags_for(conn, m) == ["sun"]  # same row, so tags stay attached
    log = conn.execute("SELECT from_path, to_path, size, moved_at FROM move_log").fetchall()
    assert [tuple(r) for r in log] == [("1.jpg", "Trips/1.jpg", 3, 99)]


def test_move_back_to_root(conn, lib):
    m = mid(conn, "1.jpg")
    albums.create_album(conn, lib, "Trips")
    albums.move_to_album(conn, [m], lib, "Trips")
    res = albums.move_to_album(conn, [m], lib, "")
    assert res.moved == 1
    assert (lib / "1.jpg").exists()
    assert paths(conn) == ["1.jpg", "2.jpg", "3.mp4"]


def test_move_never_overwrites(conn, lib):
    albums.create_album(conn, lib, "Trips")
    (lib / "Trips" / "1.jpg").write_bytes(b"different file")
    res = albums.move_to_album(conn, [mid(conn, "1.jpg")], lib, "Trips")
    assert res.moved == 0 and "already exists" in res.skipped[0][1]
    assert (lib / "1.jpg").read_bytes() == b"one"
    assert (lib / "Trips" / "1.jpg").read_bytes() == b"different file"
    assert conn.execute("SELECT COUNT(*) FROM move_log").fetchone()[0] == 0


def test_move_batch_reports_partial_failures(conn, lib):
    albums.create_album(conn, lib, "A")
    (lib / "A" / "2.jpg").write_bytes(b"x")
    ids = [mid(conn, "1.jpg"), mid(conn, "2.jpg"), mid(conn, "3.mp4")]
    res = albums.move_to_album(conn, ids, lib, "A")
    assert res.moved == 2
    assert [n for n, _ in res.skipped] == ["2.jpg"]


def test_move_to_same_album_is_a_noop(conn, lib):
    albums.create_album(conn, lib, "A")
    m = mid(conn, "1.jpg")
    albums.move_to_album(conn, [m], lib, "A")
    res = albums.move_to_album(conn, [m], lib, "a")  # same album, different case
    assert (res.moved, res.skipped) == (0, [])
    assert conn.execute("SELECT COUNT(*) FROM move_log").fetchone()[0] == 1


def test_move_skips_file_gone_from_disk(conn, lib):
    albums.create_album(conn, lib, "A")
    (lib / "1.jpg").unlink()
    res = albums.move_to_album(conn, [mid(conn, "1.jpg")], lib, "A")
    assert res.moved == 0 and "not found" in res.skipped[0][1]


def test_move_skips_missing_flagged_rows(conn, lib):
    albums.create_album(conn, lib, "A")
    conn.execute("UPDATE media SET present = 0")
    res = albums.move_to_album(conn, [mid(conn, "1.jpg")], lib, "A")
    assert res.moved == 0 and "missing" in res.skipped[0][1]


def test_move_to_unknown_album_raises(conn, lib):
    with pytest.raises(albums.AlbumError):
        albums.move_to_album(conn, [mid(conn, "1.jpg")], lib, "Nope")
    assert (lib / "1.jpg").exists()


def test_move_across_roots_skipped(conn, lib, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "x.jpg").write_bytes(b"x")
    scanner.scan_root(conn, other)
    albums.create_album(conn, lib, "A")
    res = albums.move_to_album(conn, [mid(conn, "x.jpg")], lib, "A")
    assert res.moved == 0 and "different library folder" in res.skipped[0][1]
    assert (other / "x.jpg").exists()


def test_move_blocked_by_stale_record_leaves_file_alone(conn, lib):
    albums.create_album(conn, lib, "A")
    conn.execute(
        "INSERT INTO media (rel_path, root, album, filename, type, size, mtime, present, added_at) "
        "VALUES ('A/1.jpg', ?, 'A', '1.jpg', 'photo', 1, 1, 0, 1)",
        (str(lib),),
    )
    res = albums.move_to_album(conn, [mid(conn, "1.jpg")], lib, "A")
    assert res.moved == 0 and "older record" in res.skipped[0][1]
    assert (lib / "1.jpg").exists()


# -- scanner / listing --------------------------------------------------------

def test_scan_discovers_existing_and_empty_folders(conn, tmp_path):
    root = tmp_path / "r"
    (root / "Trips" / "2024").mkdir(parents=True)
    (root / "Empty").mkdir()
    (root / ".hidden").mkdir()
    (root / "Trips" / "2024" / "a.jpg").write_bytes(b"a")
    scanner.scan_root(conn, root)
    listed = {a.path: a.count for a in albums.list_albums(conn)}
    assert listed == {"Empty": 0, "Trips": 1, "Trips/2024": 1}  # parent counts include children
    assert conn.execute("SELECT album FROM media").fetchone()["album"] == "Trips/2024"


def test_vanished_folder_is_flagged_not_listed(conn, tmp_path):
    root = tmp_path / "r"
    (root / "Gone").mkdir(parents=True)
    scanner.scan_root(conn, root)
    (root / "Gone").rmdir()
    scanner.scan_root(conn, root)
    assert albums.list_albums(conn) == []


def test_album_filter_includes_nested_and_unsorted_means_root_and_untagged(conn, lib):
    albums.create_album(conn, lib, "Trips/2024")
    albums.move_to_album(conn, [mid(conn, "1.jpg")], lib, "Trips/2024")
    albums.move_to_album(conn, [mid(conn, "Trips/2024/1.jpg")], lib, "Trips/2024")
    assert in_album(conn, "Trips") == ["Trips/2024/1.jpg"]
    assert in_album(conn, "trips/2024") == ["Trips/2024/1.jpg"]
    assert in_album(conn, "Trip") == []  # prefix of a name is not a match
    unsorted = [r["rel_path"] for r in search.search(conn, search.Query(unsorted=True), "name")]
    assert unsorted == ["2.jpg", "3.mp4"]
    tags.add_tags(conn, [mid(conn, "2.jpg")], ["x"])
    assert [r["rel_path"] for r in search.search(conn, search.Query(unsorted=True))] == ["3.mp4"]


def test_album_with_sql_wildcard_characters(conn, lib):
    albums.create_album(conn, lib, "50%_off")
    albums.create_album(conn, lib, "50x")
    albums.move_to_album(conn, [mid(conn, "1.jpg")], lib, "50x")
    assert in_album(conn, "50%_off") == []
    assert [a.count for a in albums.list_albums(conn)] == [0, 1]


# -- rename / delete ----------------------------------------------------------

def test_rename_moves_folder_files_and_subfolders(conn, lib):
    albums.create_album(conn, lib, "Old/Sub")
    albums.move_to_album(conn, [mid(conn, "1.jpg")], lib, "Old")
    albums.move_to_album(conn, [mid(conn, "2.jpg")], lib, "Old/Sub")
    old_id = albums.list_albums(conn)[0].id
    assert albums.rename_album(conn, old_id, "New", now=7) == "New"
    assert (lib / "New" / "1.jpg").exists() and (lib / "New" / "Sub" / "2.jpg").exists()
    assert not (lib / "Old").exists()
    assert paths(conn) == ["3.mp4", "New/1.jpg", "New/Sub/2.jpg"]
    assert [a.path for a in albums.list_albums(conn)] == ["New", "New/Sub"]
    logged = {(r["from_path"], r["to_path"]) for r in conn.execute("SELECT * FROM move_log WHERE moved_at = 7")}
    assert logged == {("Old/1.jpg", "New/1.jpg"), ("Old/Sub/2.jpg", "New/Sub/2.jpg")}


def test_rename_into_existing_name_refused(conn, lib):
    a = albums.create_album(conn, lib, "A")
    albums.create_album(conn, lib, "B")
    with pytest.raises(albums.AlbumError):
        albums.rename_album(conn, a.id, "b")
    assert (lib / "A").is_dir() and (lib / "B").is_dir()


def test_rename_case_only_and_nested_name_rules(conn, lib):
    a = albums.create_album(conn, lib, "trip")
    assert albums.rename_album(conn, a.id, "Trip") == "Trip"
    assert [p.name for p in lib.iterdir() if p.is_dir()] == ["Trip"]
    with pytest.raises(albums.AlbumError):
        albums.rename_album(conn, a.id, "x/y")


# -- dragging an album into another album ----------------------------------------------------

def album_id(conn, path):
    return conn.execute("SELECT id FROM folders WHERE rel_path = ?", (path,)).fetchone()["id"]


def test_move_album_into_another_moves_everything_inside(conn, lib):
    albums.create_album(conn, lib, "Trips/2024")
    albums.create_album(conn, lib, "Archive")
    m1, m2 = mid(conn, "1.jpg"), mid(conn, "2.jpg")
    tags.add_tags(conn, [m1], ["sun"])
    albums.move_to_album(conn, [m1], lib, "Trips")
    albums.move_to_album(conn, [m2], lib, "Trips/2024")
    new = albums.move_album(conn, album_id(conn, "Trips"), album_id(conn, "Archive"), now=9)
    assert new == "Archive/Trips"
    assert not (lib / "Trips").exists()
    assert (lib / "Archive" / "Trips" / "1.jpg").exists() and (lib / "Archive" / "Trips" / "2024" / "2.jpg").exists()
    assert paths(conn) == ["3.mp4", "Archive/Trips/1.jpg", "Archive/Trips/2024/2.jpg"]
    assert [a.path for a in albums.list_albums(conn)] == ["Archive", "Archive/Trips", "Archive/Trips/2024"]
    assert tags.tags_for(conn, m1) == ["sun"]  # same rows, so tags stay attached
    logged = {(r["from_path"], r["to_path"]) for r in conn.execute("SELECT * FROM move_log WHERE moved_at = 9")}
    assert logged == {("Trips/1.jpg", "Archive/Trips/1.jpg"), ("Trips/2024/2.jpg", "Archive/Trips/2024/2.jpg")}
    assert in_album(conn, "Archive") == ["Archive/Trips/1.jpg", "Archive/Trips/2024/2.jpg"]


def test_move_album_to_top_level(conn, lib):
    albums.create_album(conn, lib, "A/B")
    albums.move_to_album(conn, [mid(conn, "1.jpg")], lib, "A/B")
    assert albums.move_album(conn, album_id(conn, "A/B"), None) == "B"
    assert (lib / "B" / "1.jpg").exists() and not (lib / "A" / "B").exists()
    assert [a.path for a in albums.list_albums(conn)] == ["A", "B"]


def test_move_album_noop_when_already_there(conn, lib):
    albums.create_album(conn, lib, "A/B")
    assert albums.move_album(conn, album_id(conn, "A/B"), album_id(conn, "A")) == "A/B"
    assert conn.execute("SELECT COUNT(*) FROM move_log").fetchone()[0] == 0


def test_move_album_into_itself_or_descendant_refused(conn, lib):
    albums.create_album(conn, lib, "A/B/C")
    a, b = album_id(conn, "A"), album_id(conn, "A/B")
    for dest in (a, b, album_id(conn, "A/B/C")):
        with pytest.raises(albums.AlbumError, match="itself"):
            albums.move_album(conn, a, dest)
    assert (lib / "A" / "B" / "C").is_dir()
    albums.create_album(conn, lib, "Ab")  # a name that merely starts with "A" is a legitimate destination
    assert albums.move_album(conn, a, album_id(conn, "Ab")) == "Ab/A"


def test_move_album_name_collision_refused(conn, lib):
    albums.create_album(conn, lib, "X/Trips")
    albums.create_album(conn, lib, "Trips")
    albums.move_to_album(conn, [mid(conn, "1.jpg")], lib, "Trips")
    with pytest.raises(albums.AlbumError, match="already has"):
        albums.move_album(conn, album_id(conn, "Trips"), album_id(conn, "X"))
    assert (lib / "Trips" / "1.jpg").exists() and (lib / "X" / "Trips").is_dir()


def test_move_album_blocked_by_a_file_with_the_same_name(conn, lib):
    albums.create_album(conn, lib, "Dest")
    albums.create_album(conn, lib, "Trips")
    (lib / "Dest" / "Trips").write_text("a plain file")  # not an album, but in the way
    with pytest.raises(albums.AlbumError):
        albums.move_album(conn, album_id(conn, "Trips"), album_id(conn, "Dest"))
    assert (lib / "Trips").is_dir() and (lib / "Dest" / "Trips").read_text() == "a plain file"


def test_move_album_between_roots_refused(conn, lib, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    albums.create_album(conn, other.resolve(), "Elsewhere")
    albums.create_album(conn, lib, "Mine")
    with pytest.raises(albums.AlbumError, match="different library"):
        albums.move_album(conn, album_id(conn, "Mine"), album_id(conn, "Elsewhere"))


def test_move_album_unknown_ids(conn, lib):
    a = albums.create_album(conn, lib, "A")
    with pytest.raises(albums.AlbumError):
        albums.move_album(conn, 9999, None)
    with pytest.raises(albums.AlbumError):
        albums.move_album(conn, a.id, 9999)


def test_delete_empty_album(conn, lib):
    a = albums.create_album(conn, lib, "A")
    albums.delete_album(conn, a.id)
    assert not (lib / "A").exists()
    assert albums.list_albums(conn) == []


def test_delete_refuses_non_empty_and_keeps_files(conn, lib):
    a = albums.create_album(conn, lib, "A")
    albums.move_to_album(conn, [mid(conn, "1.jpg")], lib, "A")
    with pytest.raises(albums.AlbumError):
        albums.delete_album(conn, a.id)
    assert (lib / "A" / "1.jpg").exists()
    assert [x.path for x in albums.list_albums(conn)] == ["A"]


def test_delete_refuses_folder_with_non_media_files(conn, lib):
    a = albums.create_album(conn, lib, "A")
    (lib / "A" / "notes.txt").write_text("keep me")
    with pytest.raises(albums.AlbumError):
        albums.delete_album(conn, a.id)
    assert (lib / "A" / "notes.txt").exists()
