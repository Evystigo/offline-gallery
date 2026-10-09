import ast
import json
from pathlib import Path

import pytest

from gallery import albums, db, library, models, scanner, tags


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


def test_roots_are_stored_resolved_even_when_given_forward_slashes(conn, tmp_path):
    folder = tmp_path / "Photos"
    folder.mkdir()
    forward = str(folder).replace("\\", "/")  # what QFileDialog returns on Windows
    root = library.add_root(conn, forward)
    assert root == str(folder.resolve())
    assert library.get_roots(conn) == [root]


def test_legacy_unresolved_entries_are_normalized_on_read(conn, tmp_path):
    folder = tmp_path / "P"
    folder.mkdir()
    models.set_setting(conn, "roots", json.dumps([str(folder).replace("\\", "/"), str(folder)]))
    assert library.get_roots(conn) == [str(folder.resolve())]  # and duplicates collapse


def test_album_created_from_stored_root_matches_scanner_folders(conn, tmp_path):
    """Regression: a root typed differently from the scanner's resolved path made duplicate albums."""
    folder = tmp_path / "Photos"
    folder.mkdir()
    (folder / "a.jpg").write_bytes(b"x")
    root = library.add_root(conn, str(folder).replace("\\", "/"))
    albums.create_album(conn, library.get_roots(conn)[0], "Trips")
    scanner.scan_root(conn, root)
    assert [a.path for a in albums.list_albums(conn)] == ["Trips"]
    media = conn.execute("SELECT id, root FROM media").fetchone()
    res = albums.move_to_album(conn, [media["id"]], library.get_roots(conn)[0], "Trips")
    assert res.moved == 1  # not refused as "a different library folder"


def test_add_root_rejects_bad_overlapping_and_duplicate_folders(conn, tmp_path):
    outer = tmp_path / "outer"
    (outer / "inner").mkdir(parents=True)
    with pytest.raises(library.LibraryError):
        library.add_root(conn, tmp_path / "missing")
    library.add_root(conn, outer)
    with pytest.raises(library.LibraryError, match="already in your library"):
        library.add_root(conn, outer)
    with pytest.raises(library.LibraryError, match="already inside"):
        library.add_root(conn, outer / "inner")
    other = tmp_path / "parent"
    (other / "sub").mkdir(parents=True)
    library.add_root(conn, other / "sub")
    with pytest.raises(library.LibraryError, match="contains"):
        library.add_root(conn, other)


def test_remove_root_hides_items_but_keeps_tags_and_files(conn, tmp_path):
    folder = tmp_path / "P"
    (folder / "A").mkdir(parents=True)
    (folder / "A" / "a.jpg").write_bytes(b"x")
    root = library.add_root(conn, folder)
    scanner.scan_root(conn, root)
    mid = conn.execute("SELECT id FROM media").fetchone()["id"]
    tags.add_tags(conn, [mid], ["keep"])
    library.remove_root(conn, folder)
    assert library.get_roots(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM media WHERE present = 1").fetchone()[0] == 0
    assert albums.list_albums(conn) == []
    assert (folder / "A" / "a.jpg").exists()  # files are never touched
    library.add_root(conn, folder)
    scanner.scan_root(conn, root)
    assert tags.tags_for(conn, mid) == ["keep"]  # same row came back, tag intact
    assert [a.path for a in albums.list_albums(conn)] == ["A"]


def test_forget_roots_clears_every_folder_but_keeps_tags(conn, tmp_path):
    folders = [tmp_path / "P", tmp_path / "Q"]
    for f in folders:
        f.mkdir()
        (f / "a.jpg").write_bytes(f.name.encode())
        scanner.scan_root(conn, library.add_root(conn, f))
    mid = conn.execute("SELECT id FROM media ORDER BY root").fetchone()["id"]
    tags.add_tags(conn, [mid], ["keep"])
    library.forget_roots(conn)
    assert library.get_roots(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM media WHERE present = 1").fetchone()[0] == 0
    scanner.scan_root(conn, library.add_root(conn, folders[0]))
    assert tags.tags_for(conn, mid) == ["keep"]


def test_app_never_imports_network_modules():
    """Privacy guarantee: nothing in the app can talk to the network."""
    banned = {"socket", "ssl", "urllib", "http", "requests", "httpx", "aiohttp", "ftplib", "smtplib", "xmlrpc", "websocket"}
    offenders = []
    for py in (Path(__file__).parent.parent / "src" / "gallery").rglob("*.py"):
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            offenders += [f"{py.name}: {n}" for n in names if n.split(".")[0] in banned or n.startswith("PySide6.QtNetwork")]
    assert offenders == []
