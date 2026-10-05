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
    return next(panel.list.item(i) for i in range(panel.list.count()) if panel.list.item(i).data(0x100) == path)


def test_dropping_an_album_requests_a_move(app, conn, lib):
    albums.create_album(conn, lib, "A")
    albums.create_album(conn, lib, "B")
    panel = AlbumPanel(conn, lambda: [str(lib)])
    got = []
    panel.album_move_requested.connect(lambda src, dst: got.append((src, dst)))
    a, b = item(panel, "A"), item(panel, "B")
    panel.list.emit_drop(b, [a])
    panel.list.emit_drop(panel.list.item(0), [a])  # "All media" row = top level
    assert got == [(a.data(0x101), b.data(0x101)), (a.data(0x101), None)]


def test_pointless_drops_are_ignored(app, conn, lib):
    albums.create_album(conn, lib, "A")
    panel = AlbumPanel(conn, lambda: [str(lib)])
    got = []
    panel.album_move_requested.connect(lambda *args: got.append(args))
    a = item(panel, "A")
    panel.list.emit_drop(a, [a])  # onto itself
    panel.list.emit_drop(None, [a])  # onto empty space
    panel.list.emit_drop(a, [])  # nothing dragged
    panel.list.emit_drop(a, [panel.list.item(0)])  # the "All media" row is not an album
    assert got == []
    assert not panel.list.item(0).flags() & panel.list.item(0).flags().ItemIsDragEnabled


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
