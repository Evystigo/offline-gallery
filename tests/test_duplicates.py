import random
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from gallery import db, duplicates, hashing, scanner, tags

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not on PATH")


# -- helpers ---------------------------------------------------------------------------------

def scene(seed: int, size=(400, 300)) -> Image.Image:
    """A synthetic 'photo': random coloured shapes, so different seeds look different."""
    rng = random.Random(seed)
    im = Image.new("RGB", size, (rng.randrange(256), rng.randrange(256), rng.randrange(256)))
    d = ImageDraw.Draw(im)
    for _ in range(25):
        x, y = rng.randrange(size[0]), rng.randrange(size[1])
        w, h = rng.randrange(20, 150), rng.randrange(20, 120)
        box = [x, y, x + w, y + h]
        colour = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
        d.ellipse(box, fill=colour) if rng.random() < 0.5 else d.rectangle(box, fill=colour)
    return im


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


def add_row(conn, rel, *, size=100, kind="photo", sha=None, phash=None, duration=None, present=1):
    """Insert a media row with fresh caches (sha/phash are only stored when given)."""
    ph = None if phash is None else hashing.to_signed(phash)
    conn.execute(
        "INSERT INTO media (rel_path, root, album, filename, type, size, mtime, present, added_at, "
        "sha256, sha_size, sha_mtime, phash, duration, sig_size, sig_mtime) "
        "VALUES (?, '/r', '', ?, ?, ?, 1, ?, 1, ?, ?, 1, ?, ?, ?, 1)",
        (rel, rel, kind, size, present, sha, size if sha else None, ph, duration, size if phash is not None else None),
    )
    return conn.execute("SELECT id FROM media WHERE rel_path = ?", (rel,)).fetchone()["id"]


def flip(h: int, *bits: int) -> int:
    for b in bits:
        h ^= 1 << b
    return h


H = 0x0F0F_3C3C_A5A5_9669  # a "typical" hash with plenty of both bit values


# -- hashing -------------------------------------------------------------------------------------

def test_sha256_matches_hashlib(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"hello" * 500_000)
    import hashlib

    assert hashing.sha256_file(p, chunk=4096) == hashlib.sha256(p.read_bytes()).hexdigest()


def test_dhash_stable_across_resize_and_recompression(tmp_path):
    orig = tmp_path / "orig.png"
    scene(1).save(orig)
    small = tmp_path / "small.jpg"
    scene(1).resize((200, 150)).save(small, quality=40)
    other = tmp_path / "other.png"
    scene(2).save(other)
    h_orig, h_small, h_other = (hashing.dhash_image(p) for p in (orig, small, other))
    assert None not in (h_orig, h_small, h_other)
    assert hashing.hamming(h_orig, h_small) <= duplicates.DEFAULT_DISTANCE
    assert hashing.hamming(h_orig, h_other) > 14


def test_dhash_ignores_flat_and_unreadable_images(tmp_path):
    flat = tmp_path / "flat.png"
    Image.new("RGB", (100, 100), "white").save(flat)
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")
    assert hashing.dhash_image(flat) is None
    assert hashing.dhash_image(bad) is None
    assert hashing.dhash_image(tmp_path / "missing.jpg") is None


def test_signed_roundtrip():
    for h in (0, 1, (1 << 63) - 1, 1 << 63, (1 << 64) - 1, H):
        s = hashing.to_signed(h)
        assert -(1 << 63) <= s < (1 << 63) and hashing.to_unsigned(s) == h


# -- near_pairs vs brute force -------------------------------------------------------------------

@pytest.mark.parametrize("d", [0, 1, 3, 6, 10])
def test_near_pairs_matches_brute_force(d):
    rng = random.Random(d)
    hashes = [rng.getrandbits(64) for _ in range(250)]
    for h in hashes[:120]:  # add near-neighbours of existing hashes
        hashes.append(flip(h, *rng.sample(range(64), rng.randrange(0, 13))))
    items = list(enumerate(hashes))
    expected = {
        (i, j) for i in range(len(hashes)) for j in range(i + 1, len(hashes)) if hashing.hamming(hashes[i], hashes[j]) <= d
    }
    got = list(duplicates.near_pairs(items, d))
    assert len(got) == len(set(got))  # no duplicate pairs reported
    assert set(got) == expected


# -- grouping --------------------------------------------------------------------------------------

def test_exact_groups_by_sha_largest_first(conn):
    a = add_row(conn, "a.jpg", size=10, sha="s1")
    b = add_row(conn, "b.jpg", size=10, sha="s1")
    add_row(conn, "c.jpg", size=10, sha="s2")
    (g,) = duplicates.find_groups(conn, similar=False)
    assert g.kind == "exact" and sorted(g.ids) == sorted([a, b])


def test_stale_hashes_are_ignored(conn):
    add_row(conn, "a.jpg", size=10, sha="s1")
    add_row(conn, "b.jpg", size=10, sha="s1")
    conn.execute("UPDATE media SET size = 11 WHERE rel_path = 'b.jpg'")  # file changed after hashing
    assert duplicates.find_groups(conn, similar=False) == []


def test_similar_respects_threshold(conn):
    add_row(conn, "a.jpg", phash=H)
    add_row(conn, "b.jpg", phash=flip(H, 1, 20, 33, 60))  # 4 bits away
    assert len(duplicates.find_groups(conn, max_distance=6)) == 1
    assert duplicates.find_groups(conn, max_distance=3) == []
    assert duplicates.find_groups(conn, similar=False) == []  # exact-only mode ignores phash


def test_group_kinds(conn):
    add_row(conn, "a.jpg", size=10, sha="s1", phash=H)
    add_row(conn, "b.jpg", size=10, sha="s1", phash=H)
    (g,) = duplicates.find_groups(conn)
    assert g.kind == "exact"
    add_row(conn, "c.jpg", size=12, phash=flip(H, 5))  # near, but different bytes
    (g,) = duplicates.find_groups(conn)
    assert g.kind == "similar" and len(g.items) == 3


def test_chains_join_into_one_group(conn):
    add_row(conn, "a.jpg", phash=H)
    add_row(conn, "b.jpg", phash=flip(H, 1, 2, 3, 4))
    add_row(conn, "c.jpg", phash=flip(H, 1, 2, 3, 4, 5, 6, 7, 8))  # 8 from a, 4 from b
    assert [len(g.items) for g in duplicates.find_groups(conn, max_distance=4)] == [3]


def test_photos_and_videos_never_mix(conn):
    add_row(conn, "a.jpg", phash=H)
    add_row(conn, "a.mp4", kind="video", phash=H, duration=5)
    assert duplicates.find_groups(conn) == []


def test_video_needs_matching_duration(conn):
    add_row(conn, "a.mp4", kind="video", phash=H, duration=10.0)
    add_row(conn, "b.mp4", kind="video", phash=H, duration=60.0)
    add_row(conn, "c.mp4", kind="video", phash=H, duration=None)
    assert duplicates.find_groups(conn) == []
    add_row(conn, "d.mp4", kind="video", phash=flip(H, 3), duration=10.5)
    (g,) = duplicates.find_groups(conn)
    assert sorted(r["rel_path"] for r in g.items) == ["a.mp4", "d.mp4"]


def test_missing_and_empty_files_excluded(conn):
    add_row(conn, "a.jpg", size=10, sha="s1")
    add_row(conn, "gone.jpg", size=10, sha="s1", present=0)
    add_row(conn, "empty1.jpg", size=0, sha="e")
    add_row(conn, "empty2.jpg", size=0, sha="e")
    assert duplicates.find_groups(conn) == []


def test_ignored_groups(conn):
    a = add_row(conn, "a.jpg", size=10, sha="s1")
    b = add_row(conn, "b.jpg", size=10, sha="s1")
    (g,) = duplicates.find_groups(conn)
    duplicates.ignore_group(conn, g.ids)
    assert duplicates.find_groups(conn) == []
    assert len(duplicates.find_groups(conn, include_ignored=True)) == 1
    add_row(conn, "c.jpg", size=10, sha="s1")  # the group changed, so it comes back
    assert len(duplicates.find_groups(conn)) == 1
    assert {a, b} <= set(duplicates.find_groups(conn)[0].ids)


# -- indexing real files -----------------------------------------------------------------------------

@pytest.fixture
def lib(tmp_path, conn):
    root = tmp_path / "lib"
    (root / "sub").mkdir(parents=True)
    scene(1).save(root / "orig.png")
    shutil.copy(root / "orig.png", root / "sub" / "copy.png")  # exact duplicate
    scene(1).resize((200, 150)).save(root / "small.jpg", quality=40)  # similar, different bytes
    scene(2).save(root / "other.png")
    Image.new("RGB", (80, 80), "white").save(root / "flat.png")
    (root / "corrupt.jpg").write_bytes(b"garbage")
    scanner.scan_root(conn, root)
    return root.resolve()


def col(conn, rel, name):
    return conn.execute(f"SELECT {name} FROM media WHERE rel_path = ?", (rel,)).fetchone()[0]


def test_exact_index_only_hashes_size_collisions(conn, lib):
    assert duplicates.index_exact(conn) == 2
    assert col(conn, "orig.png", "sha256") == col(conn, "sub/copy.png", "sha256") is not None
    assert col(conn, "other.png", "sha256") is None  # unique size: never needs hashing
    assert duplicates.index_exact(conn) == 0  # cached


def test_signature_index_and_cache(conn, lib):
    n = duplicates.index_signatures(conn)
    assert n == 6
    assert col(conn, "orig.png", "phash") is not None
    assert col(conn, "flat.png", "phash") is None and col(conn, "flat.png", "sig_size") is not None
    assert col(conn, "corrupt.jpg", "phash") is None
    assert duplicates.index_signatures(conn) == 0  # nothing retried, nothing recomputed
    scene(7).save(lib / "other.png")  # edited file -> stale -> recomputed after a rescan
    scanner.scan_root(conn, lib)
    assert duplicates.index_signatures(conn) == 1


def test_progress_and_cancel(conn, lib):
    seen = []
    # cancelled() is checked before each file, progress() is reported as each file starts
    done = duplicates.index_signatures(conn, progress=lambda i, n, name: seen.append((i, n)), cancelled=lambda: len(seen) >= 1)
    assert done == 1 and seen[0] == (0, 6)
    assert duplicates.index_signatures(conn) == 5  # resumes where it stopped


def test_end_to_end_groups(conn, lib):
    duplicates.index_exact(conn)
    duplicates.index_signatures(conn)
    (g,) = duplicates.find_groups(conn, similar=True)
    assert g.kind == "similar"
    assert sorted(r["rel_path"] for r in g.items) == ["orig.png", "small.jpg", "sub/copy.png"]
    (g,) = duplicates.find_groups(conn, similar=False)
    assert g.kind == "exact" and sorted(r["rel_path"] for r in g.items) == ["orig.png", "sub/copy.png"]


# -- keep one ------------------------------------------------------------------------------------------

@pytest.fixture
def fake_trash(tmp_path):
    bin_dir = tmp_path / "recycle"
    bin_dir.mkdir()
    calls = []

    def trash(path):
        calls.append(path)
        shutil.move(path, bin_dir / Path(path).name)

    trash.calls, trash.dir = calls, bin_dir
    return trash


def ids_of(conn, *rels):
    return [conn.execute("SELECT id FROM media WHERE rel_path = ?", (r,)).fetchone()["id"] for r in rels]


def test_keep_one_merges_tags_trashes_and_removes_row(conn, lib, fake_trash):
    duplicates.index_exact(conn)
    keep, dup = ids_of(conn, "orig.png", "sub/copy.png")
    tags.add_tags(conn, [keep], ["kept"])
    tags.add_tags(conn, [dup], ["sun", "beach"])
    res = duplicates.keep_one(conn, keep, [dup], "exact", trash=fake_trash)
    assert (res.trashed, res.skipped) == (1, [])
    assert (lib / "orig.png").exists() and not (lib / "sub" / "copy.png").exists()
    assert (fake_trash.dir / "copy.png").exists()  # recoverable, not destroyed
    assert tags.tags_for(conn, keep) == ["beach", "kept", "sun"]
    assert conn.execute("SELECT COUNT(*) FROM media WHERE id = ?", (dup,)).fetchone()[0] == 0


def test_exact_keep_one_rechecks_content(conn, lib, fake_trash):
    duplicates.index_exact(conn)
    keep, dup = ids_of(conn, "orig.png", "sub/copy.png")
    tags.add_tags(conn, [dup], ["sun"])
    p = lib / "sub" / "copy.png"
    p.write_bytes(b"x" * p.stat().st_size)  # same size, different bytes, cache now lies
    res = duplicates.keep_one(conn, keep, [dup], "exact", trash=fake_trash)
    assert res.trashed == 0 and "no longer matches" in res.skipped[0][1]
    assert p.exists() and fake_trash.calls == []
    assert tags.tags_for(conn, keep) == []  # nothing merged from a refused file


def test_similar_keep_one_does_not_require_identical_bytes(conn, lib, fake_trash):
    keep, dup = ids_of(conn, "orig.png", "small.jpg")
    res = duplicates.keep_one(conn, keep, [dup], "similar", trash=fake_trash)
    assert res.trashed == 1 and not (lib / "small.jpg").exists()


def test_trash_failure_keeps_file_and_row(conn, lib):
    duplicates.index_exact(conn)
    keep, dup = ids_of(conn, "orig.png", "sub/copy.png")

    def broken(path):
        raise OSError("no recycle bin")

    res = duplicates.keep_one(conn, keep, [dup], "exact", trash=broken)
    assert res.trashed == 0 and "Recycle Bin" in res.skipped[0][1]
    assert (lib / "sub" / "copy.png").exists()
    assert conn.execute("SELECT COUNT(*) FROM media WHERE id = ?", (dup,)).fetchone()[0] == 1


def test_keep_one_never_removes_the_kept_file(conn, lib, fake_trash):
    keep, = ids_of(conn, "orig.png")
    res = duplicates.keep_one(conn, keep, [keep], "similar", trash=fake_trash)
    assert res.trashed == 0 and (lib / "orig.png").exists() and fake_trash.calls == []


def test_missing_keeper_refused(conn, lib, fake_trash):
    (dup,) = ids_of(conn, "sub/copy.png")
    res = duplicates.keep_one(conn, 99999, [dup], "similar", trash=fake_trash)
    assert res.trashed == 0 and (lib / "sub" / "copy.png").exists()


def test_trash_that_leaves_file_behind_is_reported(conn, lib):
    keep, dup = ids_of(conn, "orig.png", "small.jpg")
    res = duplicates.keep_one(conn, keep, [dup], "similar", trash=lambda p: None)  # silently does nothing
    assert res.trashed == 0 and "still on disk" in res.skipped[0][1]
    assert conn.execute("SELECT COUNT(*) FROM media WHERE id = ?", (dup,)).fetchone()[0] == 1


# -- videos (need ffmpeg) -------------------------------------------------------------------------------------

def make_clip(path: Path, source: str, size="320x240", crf=None, secs=3):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"{source}=duration={secs}:size={size}:rate=15",
           "-pix_fmt", "yuv420p"]
    cmd += ["-crf", str(crf)] if crf else []
    subprocess.run(cmd + [str(path)], check=True)


@needs_ffmpeg
def test_video_signature_survives_reencoding(tmp_path):
    a, b, other = (tmp_path / n for n in ("a.mp4", "b.mp4", "other.mp4"))
    make_clip(a, "testsrc")
    make_clip(b, "testsrc", size="160x120", crf=40)  # smaller and heavily recompressed
    make_clip(other, "testsrc2")
    (da, ha), (db_, hb), (do, ho) = (hashing.video_signature(p) for p in (a, b, other))
    assert None not in (ha, hb, ho, da, db_, do)
    assert abs(da - db_) < 0.5
    assert hashing.hamming(ha, hb) <= duplicates.DEFAULT_DISTANCE
    assert hashing.hamming(ha, ho) > 10


@needs_ffmpeg
def test_video_duplicates_found_through_the_index(tmp_path, conn):
    root = tmp_path / "v"
    root.mkdir()
    make_clip(root / "a.mp4", "testsrc")
    make_clip(root / "b.mp4", "testsrc", size="160x120", crf=40)
    make_clip(root / "c.mp4", "testsrc2")
    scanner.scan_root(conn, root)
    duplicates.index_signatures(conn)
    (g,) = duplicates.find_groups(conn)
    assert sorted(r["rel_path"] for r in g.items) == ["a.mp4", "b.mp4"]
