import os
from pathlib import Path

import pytest

from gallery import albums, db, renaming, scanner, tags


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture
def lib(tmp_path, conn):
    root = tmp_path / "lib"
    (root / "Trips").mkdir(parents=True)
    for rel, data in {
        "IMG_1.jpg": b"1", "IMG_2.PNG": b"22", "clip.mp4": b"333", "Trips/a.jpg": b"4444", "Trips/b.jpg": b"55555",
    }.items():
        (root / rel).write_bytes(data)
    scanner.scan_root(conn, root)
    return root.resolve()


def mid(conn, rel):
    return conn.execute("SELECT id FROM media WHERE rel_path = ?", (rel,)).fetchone()["id"]


def present(conn):
    return sorted(r["rel_path"] for r in conn.execute("SELECT rel_path FROM media WHERE present = 1"))


def on_disk(lib):
    return sorted(p.relative_to(lib).as_posix() for p in lib.rglob("*") if p.is_file())


def ids(conn, *rels):
    return [mid(conn, r) for r in rels]


# -- naming -----------------------------------------------------------------------------

def test_numbered_names_keep_each_files_extension():
    assert renaming.numbered_names(["a.jpg", "b.PNG", "c.mp4"], "file") == ["file1.jpg", "file2.PNG", "file3.mp4"]
    assert renaming.numbered_names(["a.jpg", "b.jpg"], "x", start=9, digits=3) == ["x009.jpg", "x010.jpg"]
    assert renaming.numbered_names(["a.jpg"], "solo", number=False) == ["solo.jpg"]
    assert renaming.numbered_names(["noext"], "f") == ["f1"]


@pytest.mark.parametrize("bad", ["", " a.jpg", "a.jpg ", ".hidden.jpg", "a/b.jpg", "a\\b.jpg", "a:b.jpg", "a?.jpg", "CON.jpg", "nul", "end.", "x" * 250 + ".jpg"])
def test_invalid_file_names_rejected(bad):
    with pytest.raises(renaming.RenameError):
        renaming.validate_filename(bad)


def test_valid_names_accepted():
    for ok in ("file1.jpg", "my photo (2).png", "ü-ñ.mp4", "a.b.c.jpg", "no_ext"):
        assert renaming.validate_filename(ok) == ok


# -- the headline use case ---------------------------------------------------------------------

def test_mass_rename_to_file1_file2_and_so_on(conn, lib):
    sel = ids(conn, "IMG_1.jpg", "IMG_2.PNG", "clip.mp4")
    tags.add_tags(conn, [sel[0]], ["sun"])
    plan = renaming.build_plan(conn, sel, "file")
    assert plan.ok and [i.new_rel for i in plan.items] == ["file1.jpg", "file2.PNG", "file3.mp4"]
    assert renaming.apply_plan(conn, plan, now=5) == 3
    assert on_disk(lib) == ["Trips/a.jpg", "Trips/b.jpg", "file1.jpg", "file2.PNG", "file3.mp4"]
    assert present(conn) == on_disk(lib)
    assert (lib / "file1.jpg").read_bytes() == b"1" and (lib / "file3.mp4").read_bytes() == b"333"
    assert tags.tags_for(conn, sel[0]) == ["sun"]  # same row, so tags and its album stay
    assert conn.execute("SELECT filename FROM media WHERE id = ?", (sel[1],)).fetchone()[0] == "file2.PNG"
    log = [tuple(r) for r in conn.execute("SELECT from_path, to_path, size, moved_at FROM move_log ORDER BY id")]
    assert log == [("IMG_1.jpg", "file1.jpg", 1, 5), ("IMG_2.PNG", "file2.PNG", 2, 5), ("clip.mp4", "file3.mp4", 3, 5)]


def test_files_in_different_albums_are_numbered_together_but_stay_in_their_folder(conn, lib):
    plan = renaming.build_plan(conn, ids(conn, "IMG_1.jpg", "Trips/a.jpg", "Trips/b.jpg"), "pic", start=10, digits=3)
    renaming.apply_plan(conn, plan)
    assert on_disk(lib) == ["IMG_2.PNG", "Trips/pic011.jpg", "Trips/pic012.jpg", "clip.mp4", "pic010.jpg"]
    assert conn.execute("SELECT album FROM media WHERE rel_path = 'Trips/pic011.jpg'").fetchone()[0] == "Trips"


def test_order_given_is_the_numbering_order(conn, lib):
    renaming.apply_plan(conn, renaming.build_plan(conn, ids(conn, "clip.mp4", "IMG_1.jpg"), "n"))
    assert (lib / "n1.mp4").read_bytes() == b"333" and (lib / "n2.jpg").read_bytes() == b"1"


# -- collisions and swaps -------------------------------------------------------------------------

def test_chain_and_swap_inside_a_batch_work(conn, lib):
    renaming.apply_plan(conn, renaming.build_plan(conn, ids(conn, "IMG_1.jpg", "Trips/a.jpg"), "file"))  # file1.jpg, Trips/file2.jpg
    # now rename so that names cross over: the .jpg files get file2 and file1
    a, b = mid(conn, "file1.jpg"), mid(conn, "Trips/file2.jpg")
    plan = renaming.build_named_plan(conn, [(a, "file2.jpg"), (b, "file1.jpg")])
    assert plan.ok  # different folders, so no clash
    renaming.apply_plan(conn, plan)
    assert (lib / "file2.jpg").read_bytes() == b"1" and (lib / "Trips" / "file1.jpg").read_bytes() == b"4444"


def test_true_swap_in_one_folder(conn, lib):
    (lib / "x.jpg").write_bytes(b"X")
    (lib / "y.jpg").write_bytes(b"YY")
    scanner.scan_root(conn, lib)
    x, y = mid(conn, "x.jpg"), mid(conn, "y.jpg")
    plan = renaming.build_named_plan(conn, [(x, "y.jpg"), (y, "x.jpg")])
    assert plan.ok
    renaming.apply_plan(conn, plan)
    assert (lib / "y.jpg").read_bytes() == b"X" and (lib / "x.jpg").read_bytes() == b"YY"
    assert conn.execute("SELECT rel_path FROM media WHERE id = ?", (x,)).fetchone()[0] == "y.jpg"
    assert not [p for p in on_disk(lib) if "gallery-rename" in p]


def test_collision_with_a_file_outside_the_batch_refuses_everything(conn, lib):
    (lib / "file2.jpg").write_bytes(b"precious")
    scanner.scan_root(conn, lib)
    plan = renaming.build_plan(conn, ids(conn, "IMG_1.jpg", "clip.mp4"), "file")  # would need file1.jpg + file2.mp4 -> fine
    assert plan.ok
    plan = renaming.build_plan(conn, ids(conn, "IMG_1.jpg", "Trips/a.jpg"), "file")  # file1.jpg ok, Trips/file2.jpg ok
    assert plan.ok
    (lib / "file1.jpg").write_bytes(b"also precious")
    plan = renaming.build_plan(conn, ids(conn, "IMG_1.jpg", "IMG_2.PNG"), "file")
    assert not plan.ok and "file1.jpg already exists" in plan.problems[0]
    with pytest.raises(renaming.RenameError):
        renaming.apply_plan(conn, plan)
    assert (lib / "IMG_1.jpg").exists() and (lib / "IMG_2.PNG").exists()  # nothing happened
    assert (lib / "file1.jpg").read_bytes() == b"also precious"


def test_stale_database_record_blocks_the_name(conn, lib):
    conn.execute(
        "INSERT INTO media (rel_path, root, album, filename, type, size, mtime, present, added_at) "
        "VALUES ('file1.jpg', ?, '', 'file1.jpg', 'photo', 1, 1, 0, 1)", (str(lib),)
    )
    plan = renaming.build_plan(conn, ids(conn, "IMG_1.jpg"), "file")
    assert not plan.ok and "older record" in plan.problems[0]


def test_case_only_rename_is_allowed(conn, lib):
    plan = renaming.build_named_plan(conn, [(mid(conn, "IMG_1.jpg"), "img_1.jpg")])
    assert plan.ok
    renaming.apply_plan(conn, plan)
    assert [p for p in on_disk(lib) if p.lower() == "img_1.jpg"] == ["img_1.jpg"]


def test_unchanged_names_are_skipped(conn, lib):
    plan = renaming.build_named_plan(conn, [(mid(conn, "IMG_1.jpg"), "IMG_1.jpg")])
    assert (plan.items, plan.unchanged, plan.problems) == ([], 1, [])
    assert renaming.apply_plan(conn, plan) == 0


def test_invalid_base_name_reported_once(conn, lib):
    plan = renaming.build_plan(conn, ids(conn, "IMG_1.jpg", "IMG_2.PNG", "clip.mp4"), "bad:name")
    assert len(plan.problems) == 3 or len(set(plan.problems)) == len(plan.problems)  # no flood of duplicates
    assert not plan.ok and on_disk(lib)[0] == "IMG_1.jpg"


def test_several_files_cannot_share_one_unnumbered_name(conn, lib):
    plan = renaming.build_plan(conn, ids(conn, "IMG_1.jpg", "IMG_2.PNG"), "same", number=False)
    assert not plan.ok
    single = renaming.build_plan(conn, ids(conn, "IMG_1.jpg"), "holiday", number=False)
    assert single.ok and single.items[0].new_rel == "holiday.jpg"


def test_missing_file_is_a_problem(conn, lib):
    (lib / "IMG_1.jpg").unlink()
    scanner.scan_root(conn, lib)
    plan = renaming.build_plan(conn, ids(conn, "IMG_1.jpg"), "f")
    assert not plan.ok and "missing" in plan.problems[0]


# -- failure handling ---------------------------------------------------------------------------------

def test_disk_failure_midway_rolls_everything_back(conn, lib, monkeypatch):
    plan = renaming.build_plan(conn, ids(conn, "IMG_1.jpg", "IMG_2.PNG", "clip.mp4"), "file")
    real, calls = os.rename, {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 5:  # fails during the second phase
            raise PermissionError("file is in use")
        return real(src, dst)

    monkeypatch.setattr(os, "rename", flaky)
    with pytest.raises(renaming.RenameError, match="Nothing was changed"):
        renaming.apply_plan(conn, plan)
    monkeypatch.setattr(os, "rename", real)
    assert on_disk(lib) == ["IMG_1.jpg", "IMG_2.PNG", "Trips/a.jpg", "Trips/b.jpg", "clip.mp4"]
    assert "IMG_1.jpg" in present(conn) and conn.execute("SELECT COUNT(*) FROM move_log").fetchone()[0] == 0


def test_database_failure_restores_files(conn, lib, monkeypatch):
    plan = renaming.build_plan(conn, ids(conn, "IMG_1.jpg", "clip.mp4"), "file")
    conn.execute("CREATE TRIGGER boom BEFORE INSERT ON move_log BEGIN SELECT RAISE(ABORT, 'disk full'); END")
    with pytest.raises(renaming.RenameError, match="files restored"):
        renaming.apply_plan(conn, plan)
    assert (lib / "IMG_1.jpg").exists() and (lib / "clip.mp4").exists() and not (lib / "file1.jpg").exists()
    assert "IMG_1.jpg" in present(conn)  # the row rolled back too


def test_file_deleted_after_planning(conn, lib):
    plan = renaming.build_plan(conn, ids(conn, "IMG_1.jpg"), "f")
    (lib / "IMG_1.jpg").unlink()
    with pytest.raises(renaming.RenameError, match="no longer on disk"):
        renaming.apply_plan(conn, plan)


def test_rename_media_single(conn, lib):
    assert renaming.rename_media(conn, mid(conn, "clip.mp4"), "movie.mp4") == 1
    assert (lib / "movie.mp4").exists()


def test_rename_then_album_move_still_work_together(conn, lib):
    albums.create_album(conn, lib, "A")
    renaming.apply_plan(conn, renaming.build_plan(conn, ids(conn, "IMG_1.jpg"), "new"))
    res = albums.move_to_album(conn, [mid(conn, "new1.jpg")], lib, "A")
    assert res.moved == 1 and (lib / "A" / "new1.jpg").exists()
