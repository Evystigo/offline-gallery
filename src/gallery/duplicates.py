"""Find exact and similar files, and let the user keep one copy.

Nothing is deleted automatically. Removing a copy sends it to the Recycle Bin / Trash after
merging its tags onto the copy that is kept.
"""

import os
import sqlite3
import time
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import send2trash

from . import hashing, tags

DEFAULT_DISTANCE = 6
MAX_DISTANCE = 10  # candidate search cost grows quickly beyond this
VIDEO_DURATION_TOLERANCE = 0.02  # fraction of duration, at least 1 second


# -- indexing (cached fingerprints) ---------------------------------------------------------

Progress = Callable[[int, int, str], None]


def _abs(row) -> Path:
    return Path(row["root"]) / row["rel_path"]


def _run(conn, rows, work, progress: Progress | None, cancelled: Callable[[], bool] | None) -> int:
    done = 0
    for i, row in enumerate(rows):
        if cancelled and cancelled():
            break
        if progress:
            progress(i, len(rows), row["rel_path"])
        work(row)
        done += 1
        if done % 25 == 0:
            conn.commit()
    conn.commit()
    if progress:
        progress(len(rows), len(rows), "")
    return done


def index_signatures(conn, progress: Progress | None = None, cancelled: Callable[[], bool] | None = None) -> int:
    """Perceptual hashes for photos and (with ffmpeg) videos whose cache is missing or stale."""
    rows = conn.execute(
        "SELECT id, root, rel_path, type, size, mtime FROM media WHERE present = 1 AND size > 0 "
        "AND (sig_size IS NOT size OR sig_mtime IS NOT mtime) ORDER BY id"
    ).fetchall()

    def work(row):
        path = _abs(row)
        if row["type"] == "photo":
            duration, ph = None, hashing.dhash_image(path)
        else:
            duration, ph = hashing.video_signature(path)
        # Unreadable files are stamped too, so they are not retried until they change.
        conn.execute(
            "UPDATE media SET phash = ?, duration = COALESCE(?, duration), sig_size = ?, sig_mtime = ? WHERE id = ?",
            (None if ph is None else hashing.to_signed(ph), duration, row["size"], row["mtime"], row["id"]),
        )

    return _run(conn, rows, work, progress, cancelled)


def index_exact(conn, progress: Progress | None = None, cancelled: Callable[[], bool] | None = None) -> int:
    """SHA-256 for files that share a size with another file (identical files must be the same size)."""
    rows = conn.execute(
        "SELECT id, root, rel_path, size, mtime FROM media WHERE present = 1 AND size > 0 "
        "AND (sha_size IS NOT size OR sha_mtime IS NOT mtime) "
        "AND size IN (SELECT size FROM media WHERE present = 1 AND size > 0 GROUP BY size HAVING COUNT(*) > 1) "
        "ORDER BY id"
    ).fetchall()

    def work(row):
        try:
            digest = hashing.sha256_file(_abs(row))
        except OSError:
            digest = None
        conn.execute(
            "UPDATE media SET sha256 = ?, sha_size = ?, sha_mtime = ? WHERE id = ?",
            (digest, row["size"], row["mtime"], row["id"]),
        )

    return _run(conn, rows, work, progress, cancelled)


# -- grouping -------------------------------------------------------------------------------

@dataclass
class Group:
    kind: str  # "exact" (identical bytes) or "similar"
    items: list[sqlite3.Row] = field(default_factory=list)

    @property
    def ids(self) -> list[int]:
        return [r["id"] for r in self.items]


class _UnionFind:
    def __init__(self):
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        self.parent[self.find(a)] = self.find(b)


def near_pairs(items: list[tuple[int, int]], max_distance: int) -> Iterator[tuple[int, int]]:
    """Index pairs (i, j) of `(id, hash)` items within `max_distance` bits of each other.

    Multi-index hashing: split the 64 bits into max_distance+1 chunks. Two hashes that differ in at
    most max_distance bits must agree exactly on at least one chunk (pigeonhole), so only items
    sharing a chunk value are compared.
    """
    k = max_distance + 1
    bounds = [i * 64 // k for i in range(k + 1)]
    buckets: list[dict[int, list[int]]] = [defaultdict(list) for _ in range(k)]
    for idx, (_id, h) in enumerate(items):
        for c in range(k):
            lo, hi = bounds[c], bounds[c + 1]
            buckets[c][(h >> lo) & ((1 << (hi - lo)) - 1)].append(idx)
    seen: set[tuple[int, int]] = set()
    for chunk_buckets in buckets:
        for members in chunk_buckets.values():
            for a in range(len(members)):
                for b in range(a + 1, len(members)):
                    pair = (members[a], members[b])
                    if pair in seen:
                        continue
                    seen.add(pair)
                    if hashing.hamming(items[pair[0]][1], items[pair[1]][1]) <= max_distance:
                        yield pair


def _group_sig(ids: list[int]) -> str:
    return ",".join(str(i) for i in sorted(ids))


def find_groups(
    conn, similar: bool = True, max_distance: int = DEFAULT_DISTANCE, include_ignored: bool = False
) -> list[Group]:
    max_distance = max(0, min(max_distance, MAX_DISTANCE))
    rows = conn.execute("SELECT * FROM media WHERE present = 1 AND size > 0").fetchall()
    by_id = {r["id"]: r for r in rows}
    uf = _UnionFind()

    by_sha: dict[str, list[int]] = defaultdict(list)
    fresh_sha: dict[int, str] = {}
    for r in rows:
        if r["sha256"] and r["sha_size"] == r["size"] and r["sha_mtime"] == r["mtime"]:
            fresh_sha[r["id"]] = r["sha256"]
            by_sha[r["sha256"]].append(r["id"])
    for ids in by_sha.values():
        for other in ids[1:]:
            uf.union(ids[0], other)

    if similar:
        for kind in ("photo", "video"):
            cand = [
                (r["id"], hashing.to_unsigned(r["phash"]))
                for r in rows
                if r["type"] == kind and r["phash"] is not None and r["sig_size"] == r["size"] and r["sig_mtime"] == r["mtime"]
            ]
            for i, j in near_pairs(cand, max_distance):
                a, b = by_id[cand[i][0]], by_id[cand[j][0]]
                if kind == "video" and not _durations_match(a["duration"], b["duration"]):
                    continue
                uf.union(a["id"], b["id"])

    comps: dict[int, list[int]] = defaultdict(list)
    for node in list(uf.parent):
        comps[uf.find(node)].append(node)

    ignored = set() if include_ignored else {r["sig"] for r in conn.execute("SELECT sig FROM dup_ignored")}
    groups = []
    for ids in comps.values():
        if len(ids) < 2 or _group_sig(ids) in ignored:
            continue
        shas = {fresh_sha.get(i) for i in ids}
        kind = "exact" if len(shas) == 1 and None not in shas else "similar"
        items = sorted((by_id[i] for i in ids), key=lambda r: (-r["size"], r["rel_path"].lower()))
        groups.append(Group(kind, items))
    groups.sort(key=lambda g: (g.kind != "exact", -g.items[0]["size"], g.items[0]["rel_path"].lower()))
    return groups


def _durations_match(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return False  # without durations a video match is too weak to trust
    return abs(a - b) <= max(1.0, VIDEO_DURATION_TOLERANCE * max(a, b))


def ignore_group(conn, ids: list[int]) -> None:
    with conn:
        conn.execute("INSERT OR IGNORE INTO dup_ignored (sig) VALUES (?)", (_group_sig(ids),))


# -- resolving ------------------------------------------------------------------------------

@dataclass
class ResolveResult:
    trashed: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (file, reason)


def keep_one(
    conn,
    keep_id: int,
    remove_ids: list[int],
    kind: str,
    trash: Callable[[str], None] = send2trash.send2trash,
    now: float | None = None,
) -> ResolveResult:
    """Keep one file; send the others to the Recycle Bin / Trash after merging their tags onto it."""
    now = time.time() if now is None else now
    result = ResolveResult()
    keep = conn.execute("SELECT * FROM media WHERE id = ? AND present = 1", (keep_id,)).fetchone()
    if keep is None:
        result.skipped.append((str(keep_id), "the file you chose to keep is missing"))
        return result
    keep_path = _abs(keep)
    keep_digest = None
    for rid in remove_ids:
        row = conn.execute("SELECT * FROM media WHERE id = ?", (rid,)).fetchone()
        if row is None or rid == keep_id:
            continue
        name = row["rel_path"]
        path = _abs(row)
        if not row["present"] or not path.exists():
            result.skipped.append((name, "file not found on disk"))
            continue
        if kind == "exact":
            # Re-check the bytes right now: the cached hash could be stale if a file was edited.
            try:
                keep_digest = keep_digest or hashing.sha256_file(keep_path)
                if hashing.sha256_file(path) != keep_digest:
                    result.skipped.append((name, "its content no longer matches the copy you are keeping"))
                    continue
            except OSError as e:
                result.skipped.append((name, str(e)))
                continue
        live = tags.tags_for(conn, rid)
        if live:
            tags.add_tags(conn, [keep_id], live, now)
        try:
            trash(str(path))
        except Exception as e:  # send2trash raises platform-specific errors
            result.skipped.append((name, f"could not move to the Recycle Bin: {e}"))
            continue
        if os.path.lexists(path):
            result.skipped.append((name, "the file is still on disk after being trashed"))
            continue
        with conn:
            conn.execute("DELETE FROM media WHERE id = ?", (rid,))  # cascades its tag links
        result.trashed += 1
    return result
