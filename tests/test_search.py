import posixpath
from datetime import datetime

import pytest

from gallery import db, tags
from gallery.search import Query, parse_query, search


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    day = lambda d: datetime(2024, 1, d, 12).timestamp()
    rows = [
        (1, "trips/beach1.jpg", "photo", day(5)),
        (2, "trips/beach2.jpg", "photo", day(10)),
        (3, "trips/city_100%.mp4", "video", day(15)),
        (4, "misc/other.png", "photo", day(20)),
        (5, "loose.jpg", "photo", day(25)),  # directly in the library root
    ]
    for i, rel, kind, mtime in rows:
        c.execute(
            "INSERT INTO media (id, rel_path, root, album, filename, type, size, mtime, added_at) "
            "VALUES (?, ?, '/r', ?, ?, ?, 1, ?, 1)",
            (i, rel, posixpath.dirname(rel), rel.split("/")[-1], kind, mtime),
        )
    c.commit()
    tags.add_tags(c, [1, 2], ["beach"])
    tags.add_tags(c, [1], ["sun"])
    tags.add_tags(c, [3], ["new york", "sun"])
    tags.add_tags(c, [2], ["nsfw"])
    yield c
    c.close()


def ids(conn, text, **kw):
    return [r["id"] for r in search(conn, parse_query(text), "oldest", **kw)]


def test_parse():
    q = parse_query('#beach #a|b -#nsfw #"new york" type:video album:Trip after:2024-01-02 foo')
    assert q.require == [["beach"], ["a", "b"], ["new york"]]
    assert q.exclude == ["nsfw"]
    assert (q.media_type, q.album, q.text) == ("video", "Trip", ["foo"])
    assert q.after is not None


def test_parse_errors():
    with pytest.raises(ValueError):
        parse_query("type:gif")
    with pytest.raises(ValueError):
        parse_query("after:yesterday")


def test_unbalanced_quote_does_not_crash():
    assert parse_query('#"beach').require == [["beach"]]


def test_and(conn):
    assert ids(conn, "#beach #sun") == [1]


def test_or_group(conn):
    assert ids(conn, "#beach|nsfw") == [1, 2]
    assert ids(conn, "#sun|nsfw") == [1, 2, 3]


def test_not(conn):
    assert ids(conn, "#beach -#nsfw") == [1]
    assert ids(conn, "-#sun") == [2, 4, 5]


def test_tag_with_space_and_case(conn):
    assert ids(conn, '#"NEW YORK"') == [3]


def test_removed_tag_not_matched(conn):
    tags.remove_tag(conn, [1], "beach")
    assert ids(conn, "#beach") == [2]


def test_text_matches_path_and_escapes_wildcards(conn):
    assert ids(conn, "beach") == [1, 2]
    assert ids(conn, "trips") == [1, 2, 3]
    assert ids(conn, "100%") == [3]
    assert ids(conn, "_") == [3]  # only city_100% contains a literal underscore


def test_type_and_dates(conn):
    assert ids(conn, "type:video") == [3]
    assert ids(conn, "after:2024-01-10 before:2024-01-15") == [2, 3]


def test_unsorted_flag(conn):
    # untagged AND directly in the root: "misc/other.png" is in an album folder, so it is not unsorted
    assert [r["id"] for r in search(conn, Query(unsorted=True))] == [5]
    tags.add_tags(conn, [5], ["x"])
    assert search(conn, Query(unsorted=True)) == []


def test_album_filter(conn):
    assert ids(conn, "album:misc") == [4]
    assert ids(conn, "album:TRIPS") == [1, 2, 3]
    assert ids(conn, "album:mis") == []  # whole folder names only
    assert ids(conn, "album:trips #beach") == [1, 2]
