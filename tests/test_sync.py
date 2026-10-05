import json
import os
import time

import pytest

from gallery import albums, db, renaming, scanner, search, sync, tags

FILES = {"1.jpg": b"one", "2.jpg": b"twotwo", "3.mp4": b"three33"}


class Machine:
    """A simulated machine: its own library folder and its own database."""

    def __init__(self, tmp_path, name, files=FILES):
        self.root = tmp_path / name / "lib"
        self.root.mkdir(parents=True)
        for rel, content in files.items():
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
        self.root = self.root.resolve()
        self.conn = db.connect(tmp_path / f"{name}.db")
        self.rescan()

    def rescan(self):
        scanner.scan_root(self.conn, self.root)
        sync.after_scan(self.conn)

    def id(self, rel):
        return self.conn.execute("SELECT id FROM media WHERE rel_path = ?", (rel,)).fetchone()["id"]

    def tags(self, rel):
        return tags.tags_for(self.conn, self.id(rel))

    def rels(self):
        return sorted(r["rel_path"] for r in self.conn.execute("SELECT rel_path FROM media WHERE present = 1"))

    def move(self, rel, album, now=None):
        albums.create_album(self.conn, self.root, album)
        res = albums.move_to_album(self.conn, [self.id(rel)], self.root, album, now=now)
        assert res.moved == 1, res.skipped


@pytest.fixture
def a(tmp_path):
    return Machine(tmp_path, "a")


@pytest.fixture
def b(tmp_path):
    return Machine(tmp_path, "b")


def data(media=(), moves=(), mid="other"):
    return {"format": "gallery-sync", "version": 1, "machine_id": mid, "exported_at": 1, "media": list(media), "moves": list(moves)}


def rec(path, size, *tag_specs):
    return {"path": path, "size": size, "tags": [{"name": n, "updated_at": t, "deleted": d} for n, t, d in tag_specs]}


def mv(src, dst, size, at):
    return {"from": src, "to": dst, "size": size, "at": at}


# -- export ----------------------------------------------------------------------

def test_export_contents(a):
    tags.add_tags(a.conn, [a.id("1.jpg")], ["sun"], now=5)
    tags.add_tags(a.conn, [a.id("2.jpg")], ["gone"], now=5)
    tags.remove_tag(a.conn, [a.id("2.jpg")], "gone", now=6)
    a.move("3.mp4", "Trips", now=7)
    out = sync.build_export(a.conn, now=100)
    assert (out["format"], out["version"]) == ("gallery-sync", 1)
    by_path = {m["path"]: m for m in out["media"]}
    assert set(by_path) == {"1.jpg", "2.jpg"}  # untagged files are not exported
    assert by_path["1.jpg"]["tags"] == [{"name": "sun", "updated_at": 5, "deleted": False}]
    assert by_path["2.jpg"]["tags"][0]["deleted"] is True  # tombstones travel
    assert out["moves"] == [{"from": "3.mp4", "to": "Trips/3.mp4", "size": 7, "at": 7}]
    assert "machine_id" in out and str(a.root) not in json.dumps(out)  # no absolute paths


def test_export_omits_files_missing_from_this_machine(a):
    tags.add_tags(a.conn, [a.id("1.jpg")], ["sun"])
    (a.root / "1.jpg").unlink()
    a.rescan()
    assert sync.build_export(a.conn)["media"] == []


def test_export_prunes_old_moves(a):
    a.move("1.jpg", "X", now=1)
    a.move("2.jpg", "X", now=time.time())
    moves = sync.build_export(a.conn)["moves"]
    assert [m["from"] for m in moves] == ["2.jpg"]


def test_write_export_is_atomic_and_named_per_machine(a, tmp_path):
    folder = tmp_path / "proton" / ".gallery-sync"
    p = sync.write_export(a.conn, folder)
    assert p.name == f"gallery-sync.{sync.machine_id(a.conn)}.json"
    assert [f.name for f in folder.iterdir()] == [p.name]  # no temp file left behind
    assert json.loads(p.read_text())["machine_id"] == sync.machine_id(a.conn)


# -- tags --------------------------------------------------------------------------

def test_tags_roundtrip_between_machines(a, b, tmp_path):
    tags.add_tags(a.conn, [a.id("1.jpg")], ["sun", "beach"])
    folder = tmp_path / "shared"
    sync.write_export(a.conn, folder)
    files, errors = sync.read_folder(b.conn, folder)
    assert errors == [] and len(files) == 1
    res = sync.import_files(b.conn, files)
    assert res.tags_changed == 2
    assert b.tags("1.jpg") == ["beach", "sun"]
    assert b.tags("2.jpg") == []  # no record -> stays unsorted
    assert [r["rel_path"] for r in search.search(b.conn, search.Query(unsorted=True), "name")] == ["2.jpg", "3.mp4"]


def test_own_file_is_ignored(a, tmp_path):
    sync.write_export(a.conn, tmp_path / "s")
    assert sync.read_folder(a.conn, tmp_path / "s") == ([], [])


def test_last_writer_wins(b):
    tags.add_tags(b.conn, [b.id("1.jpg")], ["x"], now=10)
    sync.import_files(b.conn, [data([rec("1.jpg", 3, ("x", 5, True))])])  # older removal: ignored
    assert b.tags("1.jpg") == ["x"]
    sync.import_files(b.conn, [data([rec("1.jpg", 3, ("x", 20, True))])])  # newer removal: wins
    assert b.tags("1.jpg") == []
    sync.import_files(b.conn, [data([rec("1.jpg", 3, ("x", 15, False))])])  # older re-add: ignored
    assert b.tags("1.jpg") == []
    sync.import_files(b.conn, [data([rec("1.jpg", 3, ("x", 30, False))])])
    assert b.tags("1.jpg") == ["x"]


def test_removal_of_unknown_tag_leaves_tombstone_that_blocks_older_add(b):
    sync.import_files(b.conn, [data([rec("1.jpg", 3, ("x", 20, True))])])
    sync.import_files(b.conn, [data([rec("1.jpg", 3, ("x", 10, False))], mid="third")])
    assert b.tags("1.jpg") == []


def test_import_is_idempotent(b):
    d = data([rec("1.jpg", 3, ("x", 5, False))])
    assert sync.import_files(b.conn, [d]).tags_changed == 1
    assert sync.import_files(b.conn, [d]).tags_changed == 0


def test_tag_names_merge_case_insensitively(b):
    tags.add_tags(b.conn, [b.id("1.jpg")], ["Beach"], now=1)
    sync.import_files(b.conn, [data([rec("2.jpg", 6, ("beach", 2, False))])])
    assert tags.all_tags(b.conn) == [("Beach", 2)]


# -- files not on this machine ------------------------------------------------------------

def test_records_for_missing_files_are_held_then_attach_when_file_appears(b):
    res = sync.import_files(b.conn, [data([rec("new/9.jpg", 4, ("trip", 5, False))])])
    assert res.held_for_missing_files == 1 and res.tags_changed == 0
    assert b.conn.execute("SELECT COUNT(*) FROM pending_sync").fetchone()[0] == 1
    (b.root / "new").mkdir()
    (b.root / "new" / "9.jpg").write_bytes(b"1234")
    b.rescan()
    assert b.tags("new/9.jpg") == ["trip"]
    assert b.conn.execute("SELECT COUNT(*) FROM pending_sync").fetchone()[0] == 0


def test_held_record_keeps_newest_version(b):
    sync.import_files(b.conn, [data([rec("z.jpg", 1, ("t", 5, False))])])
    sync.import_files(b.conn, [data([rec("z.jpg", 1, ("t", 9, True))])])
    sync.import_files(b.conn, [data([rec("z.jpg", 1, ("t", 7, False))])])
    row = b.conn.execute("SELECT updated_at, deleted FROM pending_sync").fetchone()
    assert (row["updated_at"], row["deleted"]) == (9, 1)


def test_same_name_and_size_in_another_folder_still_matches(b):
    sync.import_files(b.conn, [data([rec("Trips/1.jpg", 3, ("sun", 5, False))])], apply_moves=False)
    assert b.tags("1.jpg") == ["sun"]


def test_name_match_with_different_size_is_not_used(b):
    res = sync.import_files(b.conn, [data([rec("Trips/1.jpg", 999, ("sun", 5, False))])])
    assert b.tags("1.jpg") == [] and res.held_for_missing_files == 1


def test_ambiguous_name_match_is_not_used(tmp_path):
    m = Machine(tmp_path, "m", {"x/1.jpg": b"same", "y/1.jpg": b"same"})
    res = sync.import_files(m.conn, [data([rec("z/1.jpg", 4, ("sun", 5, False))])])
    assert m.tags("x/1.jpg") == [] and m.tags("y/1.jpg") == [] and res.held_for_missing_files == 1


# -- moves ------------------------------------------------------------------------------------

def test_move_is_applied_on_the_other_machine(a, b, tmp_path):
    tags.add_tags(a.conn, [a.id("1.jpg")], ["sun"], now=1)
    at = time.time() - 60  # recent: exports drop moves older than the retention window
    a.move("1.jpg", "Trips/2024", now=at)
    folder = tmp_path / "shared"
    sync.write_export(a.conn, folder)

    res = sync.sync_now(b.conn, folder)
    assert res.moves_applied == 1 and res.moves_skipped == []
    assert not (b.root / "1.jpg").exists() and (b.root / "Trips" / "2024" / "1.jpg").read_bytes() == b"one"
    assert b.tags("Trips/2024/1.jpg") == ["sun"]
    assert b.rels() == ["2.jpg", "3.mp4", "Trips/2024/1.jpg"]
    logged = b.conn.execute("SELECT from_path, to_path, size, moved_at FROM move_log").fetchall()
    assert [tuple(r) for r in logged] == [("1.jpg", "Trips/2024/1.jpg", 3, at)]  # same identity as on A


def test_move_replay_is_ignored(a, b, tmp_path):
    a.move("1.jpg", "T", now=time.time() - 60)
    folder = tmp_path / "shared"
    sync.write_export(a.conn, folder)
    assert sync.sync_now(b.conn, folder).moves_applied == 1
    assert (b.root / "T" / "1.jpg").exists()
    (b.root / "T" / "1.jpg").rename(b.root / "1.jpg")  # user deliberately moves it back on B
    b.rescan()
    res = sync.sync_now(b.conn, folder)  # A's old move record must not drag it into T again
    assert res.moves_applied == 0
    assert (b.root / "1.jpg").exists()


def test_move_skipped_when_it_is_a_different_file(a, tmp_path):
    other = Machine(tmp_path, "o", {"1.jpg": b"a completely different file"})
    res = sync.import_files(other.conn, [data(moves=[mv("1.jpg", "T/1.jpg", 3, 1)])])
    assert res.moves_applied == 0 and "different file" in res.moves_skipped[0]
    assert (other.root / "1.jpg").exists() and not (other.root / "T").exists()


def test_move_skipped_when_destination_occupied(b):
    (b.root / "T").mkdir()
    (b.root / "T" / "1.jpg").write_bytes(b"someone else")
    b.rescan()
    res = sync.import_files(b.conn, [data(moves=[mv("1.jpg", "T/1.jpg", 3, 1)])])
    assert res.moves_applied == 0 and "occupied" in res.moves_skipped[0]
    assert (b.root / "1.jpg").read_bytes() == b"one" and (b.root / "T" / "1.jpg").read_bytes() == b"someone else"


def test_move_for_file_not_on_this_machine_is_ignored(b):
    res = sync.import_files(b.conn, [data(moves=[mv("nope.jpg", "T/nope.jpg", 3, 1)])])
    assert (res.moves_applied, res.moves_skipped) == (0, [])
    assert not (b.root / "T").exists()


@pytest.mark.parametrize(
    "dst", ["../evil/1.jpg", "/abs/1.jpg", "C:/x/1.jpg", "T/../../1.jpg", "T\\1.jpg", "./1.jpg", "T//1.jpg", ".hid/1.jpg"]
)
def test_unsafe_move_paths_rejected(b, dst):
    res = sync.import_files(b.conn, [data(moves=[mv("1.jpg", dst, 3, 1)])])
    assert res.moves_applied == 0
    assert (b.root / "1.jpg").exists()
    assert not (b.root.parent / "evil").exists()


def test_moving_and_renaming_in_one_step_is_not_applied(b):
    res = sync.import_files(b.conn, [data(moves=[mv("1.jpg", "T/renamed.jpg", 3, 1)])])
    assert res.moves_applied == 0 and "moving and renaming" in res.moves_skipped[0]
    assert (b.root / "1.jpg").exists() and not (b.root / "T").exists()


# -- renames and folder moves ---------------------------------------------------------------------

def rename(machine, pairs, now=None):
    plan = renaming.build_named_plan(machine.conn, [(machine.id(old), new) for old, new in pairs])
    assert plan.ok, plan.problems
    renaming.apply_plan(machine.conn, plan, now=now if now is not None else time.time() - 60)


def test_rename_is_applied_on_the_other_machine(a, b, tmp_path):
    tags.add_tags(a.conn, [a.id("1.jpg")], ["sun"], now=1)
    at = time.time() - 60
    rename(a, [("1.jpg", "file1.jpg"), ("2.jpg", "file2.jpg"), ("3.mp4", "file3.mp4")], now=at)
    folder = tmp_path / "shared"
    sync.write_export(a.conn, folder)
    res = sync.sync_now(b.conn, folder)
    assert res.moves_applied == 3 and res.moves_skipped == []
    assert sorted(p.name for p in b.root.iterdir()) == ["file1.jpg", "file2.jpg", "file3.mp4"]
    assert (b.root / "file1.jpg").read_bytes() == b"one"  # the right file got the right name
    assert b.tags("file1.jpg") == ["sun"]
    assert b.rels() == ["file1.jpg", "file2.jpg", "file3.mp4"]
    assert sorted(r["moved_at"] for r in b.conn.execute("SELECT moved_at FROM move_log")) == [at] * 3  # same identity as on A


def test_rename_replay_is_ignored(a, b, tmp_path):
    rename(a, [("1.jpg", "file1.jpg")])
    folder = tmp_path / "shared"
    sync.write_export(a.conn, folder)
    assert sync.sync_now(b.conn, folder).moves_applied == 1
    (b.root / "file1.jpg").rename(b.root / "mine.jpg")  # B deliberately picks its own name afterwards
    b.rescan()
    assert sync.sync_now(b.conn, folder).moves_applied == 0
    assert (b.root / "mine.jpg").exists()


def test_rename_skipped_for_a_different_file_or_an_occupied_name(b):
    res = sync.import_files(b.conn, [data(moves=[mv("1.jpg", "n.jpg", 999, 1)])])  # wrong size: not the same file
    assert res.moves_applied == 0 and "different file" in res.moves_skipped[0]
    (b.root / "n.jpg").write_bytes(b"someone else")
    b.rescan()
    res = sync.import_files(b.conn, [data(moves=[mv("1.jpg", "n.jpg", 3, 2)])])
    assert res.moves_applied == 0 and "occupied" in res.moves_skipped[0]
    assert (b.root / "1.jpg").read_bytes() == b"one" and (b.root / "n.jpg").read_bytes() == b"someone else"


def test_proton_renamed_it_first_so_tags_follow_via_the_record(a, b, tmp_path):
    tags.add_tags(b.conn, [b.id("1.jpg")], ["local"], now=1)
    tags.add_tags(a.conn, [a.id("1.jpg")], ["remote"], now=2)
    rename(a, [("1.jpg", "file1.jpg")])
    folder = tmp_path / "shared"
    sync.write_export(a.conn, folder)
    (b.root / "1.jpg").rename(b.root / "file1.jpg")  # the sync service renamed it on B first
    b.rescan()
    assert b.tags("file1.jpg") == []  # a plain rescan cannot know it is the same file
    sync.sync_now(b.conn, folder)
    assert b.tags("file1.jpg") == ["local", "remote"]
    assert b.conn.execute("SELECT COUNT(*) FROM media WHERE rel_path = '1.jpg'").fetchone()[0] == 0  # stale row folded away


def test_album_folder_move_is_followed_file_by_file(a, b, tmp_path):
    albums.create_album(a.conn, a.root, "Trips")
    albums.create_album(a.conn, a.root, "Archive")
    a.move("1.jpg", "Trips", now=time.time() - 120)
    a.move("2.jpg", "Trips", now=time.time() - 119)
    sync.write_export(a.conn, tmp_path / "shared")
    sync.sync_now(b.conn, tmp_path / "shared")  # B now mirrors Trips/
    folder_id = a.conn.execute("SELECT id FROM folders WHERE rel_path = 'Trips'").fetchone()["id"]
    dest_id = a.conn.execute("SELECT id FROM folders WHERE rel_path = 'Archive'").fetchone()["id"]
    albums.move_album(a.conn, folder_id, dest_id, now=time.time() - 60)  # drag Trips into Archive on A
    sync.write_export(a.conn, tmp_path / "shared")
    res = sync.sync_now(b.conn, tmp_path / "shared")
    assert res.moves_applied == 2
    assert (b.root / "Archive" / "Trips" / "1.jpg").exists() and (b.root / "Archive" / "Trips" / "2.jpg").exists()
    assert b.rels() == ["3.mp4", "Archive/Trips/1.jpg", "Archive/Trips/2.jpg"]


def test_a_swap_cannot_be_replayed_one_by_one_but_loses_nothing(a, b, tmp_path):
    rename(a, [("1.jpg", "tmp1.jpg")])
    rename(a, [("2.jpg", "1.jpg"), ("tmp1.jpg", "2.jpg")])  # 1.jpg and 2.jpg trade names on A
    sync.write_export(a.conn, tmp_path / "shared")
    res = sync.sync_now(b.conn, tmp_path / "shared")
    assert (b.root / "1.jpg").exists() and (b.root / "2.jpg").exists()  # nothing overwritten or lost
    assert sorted(p.stat().st_size for p in b.root.iterdir()) == [3, 6, 7]
    assert isinstance(res.moves_skipped, list)


def test_malformed_move_records_do_not_crash(b):
    bad = [None, 5, {}, {"from": "1.jpg"}, mv("1.jpg", "T/1.jpg", "3", 1), mv("1.jpg", "T/1.jpg", 3, "now")]
    res = sync.import_files(b.conn, [data(moves=bad)])
    assert res.moves_applied == 0 and (b.root / "1.jpg").exists()


def test_chained_moves_apply_in_time_order(b):
    moves = [mv("T/1.jpg", "U/1.jpg", 3, 20), mv("1.jpg", "T/1.jpg", 3, 10)]  # listed out of order
    res = sync.import_files(b.conn, [data(moves=moves)])
    assert res.moves_applied == 2
    assert (b.root / "U" / "1.jpg").exists()


def test_apply_moves_can_be_switched_off(a, b, tmp_path):
    tags.add_tags(a.conn, [a.id("1.jpg")], ["sun"])
    a.move("1.jpg", "T")
    folder = tmp_path / "shared"
    sync.write_export(a.conn, folder)
    res = sync.sync_now(b.conn, folder, apply_moves=False)
    assert res.moves_applied == 0 and (b.root / "1.jpg").exists()
    assert b.tags("1.jpg") == ["sun"]  # tags still follow via the name+size match


def test_dry_run_changes_nothing(a, b, tmp_path):
    tags.add_tags(a.conn, [a.id("2.jpg")], ["sun"])
    a.move("1.jpg", "T")
    d = sync.build_export(a.conn)
    d["machine_id"] = "other"
    d["media"].append(rec("gone.jpg", 1, ("x", 1, False)))
    before = (b.rels(), b.conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0])
    res = sync.import_files(b.conn, [d], dry_run=True)
    assert (res.moves_applied, res.tags_changed, res.held_for_missing_files) == (1, 1, 1)
    assert (b.rels(), b.conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]) == before
    assert (b.root / "1.jpg").exists() and not (b.root / "T").exists()
    assert b.conn.execute("SELECT COUNT(*) FROM pending_sync").fetchone()[0] == 0


# -- moves made outside the app (Explorer / Proton Drive) ---------------------------------------

def test_external_move_keeps_tags_after_rescan(b):
    tags.add_tags(b.conn, [b.id("1.jpg")], ["sun"], now=3)
    (b.root / "Moved").mkdir()
    (b.root / "1.jpg").rename(b.root / "Moved" / "1.jpg")
    b.rescan()
    assert b.rels() == ["2.jpg", "3.mp4", "Moved/1.jpg"]
    assert b.tags("Moved/1.jpg") == ["sun"]
    assert b.conn.execute("SELECT COUNT(*) FROM media WHERE rel_path = '1.jpg'").fetchone()[0] == 0


def test_relink_skips_ambiguous_matches(tmp_path):
    m = Machine(tmp_path, "m", {"a/1.jpg": b"same", "b/1.jpg": b"same"})
    tags.add_tags(m.conn, [m.id("a/1.jpg")], ["x"])
    (m.root / "a" / "1.jpg").unlink()
    (m.root / "c").mkdir()
    (m.root / "c" / "1.jpg").write_bytes(b"same")  # two present candidates (b/ and c/)
    m.rescan()
    assert m.tags("c/1.jpg") == [] and m.tags("b/1.jpg") == []


def test_proton_already_moved_it_then_sync_still_consistent(a, b, tmp_path):
    tags.add_tags(a.conn, [a.id("1.jpg")], ["sun"], now=1)
    a.move("1.jpg", "T", now=time.time() - 60)
    folder = tmp_path / "shared"
    sync.write_export(a.conn, folder)
    (b.root / "T").mkdir()
    (b.root / "1.jpg").rename(b.root / "T" / "1.jpg")  # the sync service moved it first
    b.rescan()
    res = sync.sync_now(b.conn, folder)
    assert res.moves_applied == 0 and res.moves_skipped == []
    assert b.rels() == ["2.jpg", "3.mp4", "T/1.jpg"] and b.tags("T/1.jpg") == ["sun"]


# -- bad input --------------------------------------------------------------------------------------

def test_bad_files_are_reported_and_good_ones_still_import(a, b, tmp_path):
    folder = tmp_path / "shared"
    tags.add_tags(a.conn, [a.id("1.jpg")], ["sun"])
    sync.write_export(a.conn, folder)
    (folder / "gallery-sync.broken.json").write_text("{not json")
    (folder / "gallery-sync.alien.json").write_text(json.dumps({"format": "other"}))
    (folder / "gallery-sync.future.json").write_text(json.dumps(data(mid="f") | {"version": 99}))
    (folder / "unrelated.json").write_text("{}")
    res = sync.sync_now(b.conn, folder)
    assert len(res.errors) == 3 and res.tags_changed == 1
    assert b.tags("1.jpg") == ["sun"]


def test_garbage_records_do_not_crash(b):
    junk = {"media": [None, 3, {"path": 5}, {"path": "1.jpg", "tags": "x"}, {"path": "1.jpg", "tags": [{"name": 4}, {"name": " ", "updated_at": 1}]}, {"path": "../x", "tags": [{"name": "t", "updated_at": 1, "deleted": False}]}]}
    res = sync.import_files(b.conn, [data(**junk)])
    assert res.tags_changed == 0 and b.tags("1.jpg") == []


def test_missing_sync_folder_reports_error(b, tmp_path):
    files, errors = sync.read_folder(b.conn, tmp_path / "nope")
    assert files == [] and "not found" in errors[0]


# -- convergence --------------------------------------------------------------------------------------

def test_two_machines_converge(a, b, tmp_path):
    folder = tmp_path / "shared"
    tags.add_tags(a.conn, [a.id("1.jpg")], ["from-a"], now=1)
    tags.add_tags(b.conn, [b.id("2.jpg")], ["from-b"], now=2)
    a.move("3.mp4", "Videos", now=time.time() - 60)
    sync.sync_now(a.conn, folder)
    sync.sync_now(b.conn, folder)
    sync.sync_now(a.conn, folder)
    for m in (a, b):
        assert m.tags("1.jpg") == ["from-a"] and m.tags("2.jpg") == ["from-b"]
        assert m.rels() == ["1.jpg", "2.jpg", "Videos/3.mp4"]
    tags.remove_tag(b.conn, [b.id("1.jpg")], "from-a", now=time.time() + 10)
    sync.sync_now(b.conn, folder)
    sync.sync_now(a.conn, folder)
    assert a.tags("1.jpg") == []
