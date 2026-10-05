"""Search: query model, query-string parser, and SQL builder.

Query-string syntax (space separated, all terms ANDed together):
    #beach            must have tag "beach"
    #beach|sun        must have "beach" OR "sun"
    -#nsfw            must NOT have tag "nsfw"
    #"new york"       quotes allow spaces in tag names
    type:photo        photo | video
    album:Trips/2024  in that album folder (or inside it); quote names with spaces
    after:2024-01-31  file modified on/after this day   (also before:YYYY-MM-DD)
    sunset            anything else matches the filename/path
"""

import shlex
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta

SORTS = {
    "newest": "m.mtime DESC, m.id DESC",
    "oldest": "m.mtime ASC, m.id ASC",
    "name": "m.filename ASC, m.id ASC",
}

_LIVE_TAG = (
    "EXISTS (SELECT 1 FROM media_tags mt JOIN tags t ON t.id = mt.tag_id "
    "WHERE mt.media_id = m.id AND mt.deleted = 0 AND t.name IN ({marks}))"
)


@dataclass
class Query:
    require: list[list[str]] = field(default_factory=list)  # AND of OR-groups
    exclude: list[str] = field(default_factory=list)
    text: list[str] = field(default_factory=list)
    media_type: str | None = None
    album: str | None = None
    after: float | None = None
    before: float | None = None
    unsorted: bool = False


def _day(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"Bad date “{value}”, expected YYYY-MM-DD") from None


def parse_query(text: str) -> Query:
    q = Query()
    try:
        tokens = shlex.split(text)
    except ValueError:  # unbalanced quote: fall back to plain splitting
        tokens = text.replace('"', "").split()
    for tok in tokens:
        low = tok.lower()
        if tok.startswith("-#") and len(tok) > 2:
            q.exclude.append(" ".join(tok[2:].split()))
        elif tok.startswith("#") and len(tok) > 1:
            group = [" ".join(n.split()) for n in tok[1:].split("|")]
            group = [n for n in group if n]
            if group:
                q.require.append(group)
        elif low.startswith("type:"):
            value = low[5:]
            if value not in ("photo", "video"):
                raise ValueError("type: must be photo or video")
            q.media_type = value
        elif low.startswith("album:") and len(tok) > 6:
            q.album = tok[6:]
        elif low.startswith("after:"):
            q.after = _day(tok[6:]).timestamp()
        elif low.startswith("before:"):
            q.before = (_day(tok[7:]) + timedelta(days=1)).timestamp()
        else:
            q.text.append(tok)
    return q


def _like(term: str) -> str:
    esc = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


def search(conn: sqlite3.Connection, q: Query, sort: str = "newest") -> list[sqlite3.Row]:
    where = ["m.present = 1"]
    params: list = []
    for group in q.require:
        where.append(_LIVE_TAG.format(marks=",".join("?" * len(group))))
        params += group
    if q.exclude:
        where.append("NOT " + _LIVE_TAG.format(marks=",".join("?" * len(q.exclude))))
        params += q.exclude
    for term in q.text:
        where.append("m.rel_path LIKE ? ESCAPE '\\'")
        params.append(_like(term))
    if q.media_type:
        where.append("m.type = ?")
        params.append(q.media_type)
    if q.album:  # the album folder or anything nested inside it
        where.append("(m.album = ? OR lower(substr(m.album, 1, length(?) + 1)) = lower(?) || '/')")
        params += [q.album, q.album, q.album]
    if q.after is not None:
        where.append("m.mtime >= ?")
        params.append(q.after)
    if q.before is not None:
        where.append("m.mtime < ?")
        params.append(q.before)
    if q.unsorted:  # no tags and not in any album folder
        where.append("m.album = ''")
        where.append("NOT EXISTS (SELECT 1 FROM media_tags t WHERE t.media_id = m.id AND t.deleted = 0)")
    sql = f"SELECT m.* FROM media m WHERE {' AND '.join(where)} ORDER BY {SORTS[sort]}"
    return conn.execute(sql, params).fetchall()
