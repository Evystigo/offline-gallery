import pytest

from gallery import db, tags


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    for i, name in enumerate(["a.jpg", "b.jpg", "c.jpg"], 1):
        c.execute(
            "INSERT INTO media (id, rel_path, root, filename, type, size, mtime, added_at) "
            "VALUES (?, ?, '/r', ?, 'photo', 1, 1, 1)",
            (i, name, name),
        )
    c.commit()
    yield c
    c.close()


def test_parse_names():
    assert tags.parse_names(" Beach , beach,  sun  set,,") == ["Beach", "sun set"]


def test_add_and_list(conn):
    tags.add_tags(conn, [1, 2], ["beach", "Sun"])
    assert tags.tags_for(conn, 1) == ["beach", "Sun"]
    assert tags.tags_for(conn, 3) == []


def test_tag_names_case_insensitive(conn):
    tags.add_tags(conn, [1], ["Beach"])
    tags.add_tags(conn, [2], ["beach"])
    assert tags.all_tags(conn) == [("Beach", 2)]


def test_remove_is_tombstone_and_readd_restores(conn):
    tags.add_tags(conn, [1], ["x"], now=10)
    tags.remove_tag(conn, [1], "x", now=20)
    assert tags.tags_for(conn, 1) == []
    row = conn.execute("SELECT deleted, updated_at FROM media_tags").fetchone()
    assert (row["deleted"], row["updated_at"]) == (1, 20)
    tags.add_tags(conn, [1], ["x"], now=30)
    assert tags.tags_for(conn, 1) == ["x"]


def test_counts_for_selection(conn):
    tags.add_tags(conn, [1, 2], ["both"])
    tags.add_tags(conn, [1], ["one"])
    assert tags.tag_counts_for(conn, [1, 2, 3]) == {"both": 2, "one": 1}
    assert tags.tag_counts_for(conn, []) == {}


def test_rename_and_merge(conn):
    tags.add_tags(conn, [1, 2], ["old"])
    tags.add_tags(conn, [2, 3], ["new"])
    tags.rename_tag(conn, "old", "new", now=50)
    assert tags.all_tags(conn) == [("new", 3)]
    assert tags.tags_for(conn, 1) == ["new"]


def test_rename_plain(conn):
    tags.add_tags(conn, [1], ["old"])
    tags.rename_tag(conn, "old", "fresh")
    assert tags.tags_for(conn, 1) == ["fresh"]


def test_delete_tag(conn):
    tags.add_tags(conn, [1, 2], ["gone"])
    tags.delete_tag(conn, "gone")
    assert tags.all_tags(conn) == []


def test_rename_empty_rejected(conn):
    with pytest.raises(ValueError):
        tags.rename_tag(conn, "a", "  ")
