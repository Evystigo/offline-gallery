"""Headless checks for the album drag/drop plumbing and the rename dialog."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from gallery import albums, db, renaming, scanner
from gallery.ui.album_panel import AlbumPanel
from gallery.ui.rename_dialog import RenameDialog


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture
def lib(tmp_path, conn):
    root = tmp_path / "lib"
    root.mkdir()
    for n in ("IMG_1.jpg", "IMG_2.jpg", "IMG_3.png"):
        (root / n).write_bytes(n.encode())
    scanner.scan_root(conn, root)
    return root.resolve()


def item(panel, path):
    return next(i for i in panel._all_items() if i.data(0, 0x100) == path)


def test_dropping_an_album_requests_a_move(app, conn, lib):
    albums.create_album(conn, lib, "A")
    albums.create_album(conn, lib, "B")
    panel = AlbumPanel(conn, lambda: [str(lib)])
    got = []
    panel.album_move_requested.connect(lambda src, dst: got.append((src, dst)))
    a, b = item(panel, "A"), item(panel, "B")
    panel.list.emit_drop(b, [a])
    panel.list.emit_drop(panel.list.topLevelItem(0), [a])  # "All media" row = top level
    assert got == [(a.data(0, 0x101), b.data(0, 0x101)), (a.data(0, 0x101), None)]


def test_pointless_drops_are_ignored(app, conn, lib):
    albums.create_album(conn, lib, "A")
    panel = AlbumPanel(conn, lambda: [str(lib)])
    got = []
    panel.album_move_requested.connect(lambda *args: got.append(args))
    a = item(panel, "A")
    panel.list.emit_drop(a, [a])  # onto itself
    panel.list.emit_drop(None, [a])  # onto empty space
    panel.list.emit_drop(a, [])  # nothing dragged
    panel.list.emit_drop(a, [panel.list.topLevelItem(0)])  # the "All media" row is not an album
    assert got == []
    assert not panel.list.topLevelItem(0).flags() & panel.list.topLevelItem(0).flags().ItemIsDragEnabled


def test_nested_albums_are_collapsible_and_remember_their_state(app, conn, lib):
    albums.create_album(conn, lib, "Trips/2024/Summer")
    albums.create_album(conn, lib, "Work")
    panel = AlbumPanel(conn, lambda: [str(lib)])
    trips, y2024 = item(panel, "Trips"), item(panel, "Trips/2024")
    assert [panel.list.topLevelItem(i).data(0, 0x100) for i in range(panel.list.topLevelItemCount())] == [None, "Trips", "Work"]
    assert y2024.parent() is trips and item(panel, "Trips/2024/Summer").parent() is y2024
    assert not trips.isExpanded()  # collapsed by default

    trips.setExpanded(True)
    panel.reload()
    assert item(panel, "Trips").isExpanded() and not item(panel, "Trips/2024").isExpanded()
    assert AlbumPanel(conn, lambda: [str(lib)])._all_items()[1].isExpanded()  # survives a restart

    item(panel, "Trips").setExpanded(False)
    assert not AlbumPanel(conn, lambda: [str(lib)])._all_items()[1].isExpanded()


def test_selecting_a_hidden_album_expands_its_parents(app, conn, lib):
    albums.create_album(conn, lib, "Trips/2024")
    panel = AlbumPanel(conn, lambda: [str(lib)])
    got = []
    panel.album_selected.connect(got.append)
    panel.reload(select="Trips/2024")
    assert item(panel, "Trips").isExpanded() and panel.current_path() == "Trips/2024"
    assert got == ["Trips/2024"]


def test_collapsing_moves_a_hidden_selection_to_the_collapsed_album(app, conn, lib):
    albums.create_album(conn, lib, "Trips/2024/Summer")
    panel = AlbumPanel(conn, lambda: [str(lib)])
    panel.reload(select="Trips/2024/Summer")
    got = []
    panel.album_selected.connect(got.append)
    item(panel, "Trips").setExpanded(False)
    assert panel.current_path() == "Trips" and got == ["Trips"]

    panel.expand_all()
    assert all(i.isExpanded() for i in panel._all_items() if i.childCount())
    panel.list.setCurrentItem(item(panel, "Trips/2024/Summer"))
    panel.expand_all(False)
    assert not any(i.isExpanded() for i in panel._all_items()) and panel.current_path() == "Trips"


def ids(conn):
    return [r["id"] for r in conn.execute("SELECT id FROM media ORDER BY rel_path")]


def ok_button(dialog):
    return dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)


def test_rename_dialog_previews_and_enables_ok(app, conn, lib):
    d = RenameDialog(conn, ids(conn))
    assert not ok_button(d).isEnabled()  # nothing typed yet
    d.base.setText("file")
    d.update_preview()
    assert [d.preview.item(i).text().split("   →   ")[1] for i in range(3)] == ["file1.jpg", "file2.jpg", "file3.png"]
    assert ok_button(d).isEnabled() and ok_button(d).text() == "Rename 3 files"
    d.digits.setValue(3)
    d.start.setValue(7)
    d.update_preview()
    assert d.preview.item(0).text().endswith("file007.jpg")
    assert not d.number.isEnabled()  # several files always get numbers


def test_rename_dialog_blocks_bad_names_and_collisions(app, conn, lib):
    d = RenameDialog(conn, ids(conn))
    d.base.setText("bad:name")
    d.update_preview()
    assert not ok_button(d).isEnabled() and "cannot contain" in d.message.text()
    (lib / "file2.jpg").write_bytes(b"in the way")
    scanner.scan_root(conn, lib)
    d.media_ids = [r["id"] for r in conn.execute("SELECT id FROM media WHERE rel_path LIKE 'IMG_%' ORDER BY rel_path")]
    d.base.setText("file")
    d.update_preview()
    assert not ok_button(d).isEnabled() and "file2.jpg already exists" in d.message.text()


def test_single_file_can_take_an_exact_name(app, conn, lib):
    one = ids(conn)[:1]
    d = RenameDialog(conn, one)
    assert d.number.isEnabled()
    d.base.setText("holiday")
    d.number.setChecked(False)
    d.update_preview()
    assert d.preview.item(0).text().endswith("holiday.jpg") and ok_button(d).isEnabled()
    renaming.apply_plan(conn, d.final_plan())
    assert (lib / "holiday.jpg").exists()


def test_already_named_files_are_reported_not_renamed(app, conn, lib):
    d = RenameDialog(conn, ids(conn)[:1])
    d.number.setChecked(False)
    d.base.setText("IMG_1")
    d.update_preview()
    assert "already have that name" in d.message.text() and not ok_button(d).isEnabled()
