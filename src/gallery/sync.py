"""Cross-machine sync through plain JSON files in a folder the user already syncs (Proton Drive).

Each machine writes only its own file (`gallery-sync.<machine-id>.json`), so the sync service never
sees a write conflict, and imports every other machine's file. Files carry relative paths, tags and
the album moves this app performed. No image data and no absolute paths ever leave the machine.

Merge rules:
  * tags: per (file, tag) last-writer-wins on `updated_at`; removals are tombstones.
  * moves and renames: applied to this machine's copy only if it is provably the same file (same
    path, same size), the destination is free, and it has not been applied before. Never overwrites.
    A move changes the folder, a rename changes the name; doing both at once is not applied.
  * records for files this machine does not have are held in `pending_sync` and attach if the
    file shows up later. Files without any record stay untouched (Unsorted).
"""

import json
import os
import posixpath
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import albums, models, renaming

FORMAT = "gallery-sync"
VERSION = 1
FILE_PREFIX = "gallery-sync."
MOVE_RETENTION_DAYS = 90
MAX_FILE_BYTES = 50 * 1024 * 1024


class SyncError(Exception):
    pass


@dataclass
class ImportResult:
    files_read: int = 0
    tags_changed: int = 0
    moves_applied: int = 0
    moves_skipped: list[str] = field(default_factory=list)
    held_for_missing_files: int = 0  # records for files not on this machine
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.tags_changed} tag change(s)", f"{self.moves_applied} file move(s)"]
        if self.held_for_missing_files:
            parts.append(f"{self.held_for_missing_files} record(s) for files not on this machine")
        if self.moves_skipped:
            parts.append(f"{len(self.moves_skipped)} move(s) skipped")
        if self.errors:
            parts.append(f"{len(self.errors)} file problem(s)")
        return ", ".join(parts)


# -- identity & paths -----------------------------------------------------------

def machine_id(conn: sqlite3.Connection) -> str:
    mid = models.get_setting(conn, "machine_id")
    if not mid:
        mid = uuid.uuid4().hex[:12]
        models.set_setting(conn, "machine_id", mid)
    return mid


def safe_rel_path(path: object) -> str | None:
    """A relative posix path inside a library root, or None if it could escape one."""
    if not isinstance(path, str) or not path or len(path) > 1024:
        return None
    if "\\" in path or path.startswith("/") or ":" in path or "\x00" in path:
        return None
    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    return path


# -- export -----------------------------------------------------------------------

def build_export(conn: sqlite3.Connection, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    media: dict[int, dict] = {}
    rows = conn.execute(
        "SELECT m.id, m.rel_path, m.size, t.name, mt.updated_at, mt.deleted "
        "FROM media m JOIN media_tags mt ON mt.media_id = m.id JOIN tags t ON t.id = mt.tag_id "
        "WHERE m.present = 1 ORDER BY m.rel_path, t.name"
    )
    for r in rows:
        rec = media.setdefault(r["id"], {"path": r["rel_path"], "size": r["size"], "tags": []})
        rec["tags"].append({"name": r["name"], "updated_at": r["updated_at"], "deleted": bool(r["deleted"])})
    cutoff = now - MOVE_RETENTION_DAYS * 86400
    with conn:
        conn.execute("DELETE FROM move_log WHERE moved_at < ?", (cutoff,))
    moves = [
        {"from": r["from_path"], "to": r["to_path"], "size": r["size"], "at": r["moved_at"]}
        for r in conn.execute("SELECT * FROM move_log ORDER BY moved_at, id")
    ]
    return {
        "format": FORMAT,
        "version": VERSION,
        "machine_id": machine_id(conn),
        "exported_at": now,
        "media": list(media.values()),
        "moves": moves,
    }


def write_export(conn: sqlite3.Connection, folder: Path | str, now: float | None = None) -> Path:
    """Write this machine's file atomically (temp file + rename)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    data = build_export(conn, now)
    dest = folder / f"{FILE_PREFIX}{data['machine_id']}.json"
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, dest)
    return dest


# -- reading files ----------------------------------------------------------------

def parse_file(path: Path) -> dict:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise SyncError("file is too large")
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SyncError(f"cannot read: {e}") from e
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise SyncError("not a gallery sync file")
    if not isinstance(data.get("version"), int) or data["version"] > VERSION:
        raise SyncError(f"written by a newer version of the app (v{data.get('version')})")
    if not isinstance(data.get("machine_id"), str):
        raise SyncError("missing machine id")
    return data


def read_folder(conn: sqlite3.Connection, folder: Path | str) -> tuple[list[dict], list[str]]:
    """Other machines' files in the sync folder. Returns (parsed files, error messages)."""
    folder = Path(folder)
    if not folder.is_dir():
        return [], [f"Sync folder not found: {folder}"]
    return load_files(conn, sorted(folder.glob(f"{FILE_PREFIX}*.json")))


def load_files(conn: sqlite3.Connection, paths: list[Path]) -> tuple[list[dict], list[str]]:
    """Parse sync files, skipping this machine's own. Returns (parsed files, error messages)."""
    own = machine_id(conn)
    files, errors = [], []
    for p in paths:
        try:
            data = parse_file(Path(p))
        except SyncError as e:
            errors.append(f"{Path(p).name}: {e}")
            continue
        if data["machine_id"] != own:
            files.append(data)
    return files, errors


# -- import -----------------------------------------------------------------------

def import_files(
    conn: sqlite3.Connection, files: list[dict], apply_moves: bool = True, dry_run: bool = False
) -> ImportResult:
    """Merge other machines' data. `dry_run` reports what would happen and changes nothing."""
    result = ImportResult(files_read=len(files))
    moves = []
    for data in files:
        for m in data.get("moves") or []:
            if isinstance(m, dict):
                moves.append(m)
    if apply_moves:
        for m in sorted(moves, key=lambda m: m.get("at") if isinstance(m.get("at"), (int, float)) else 0):
            _apply_move(conn, m, result, dry_run)
    for data in files:
        for rec in data.get("media") or []:
            if isinstance(rec, dict):
                _merge_record(conn, rec, result, dry_run)
    if not dry_run:
        relink_moved(conn)
        attach_pending(conn)
    return result


def _move_key_known(conn, m: dict) -> bool:
    return conn.execute(
        "SELECT 1 FROM move_log WHERE from_path = ? AND to_path = ? AND size = ? AND moved_at = ?",
        (m["from"], m["to"], m["size"], m["at"]),
    ).fetchone() is not None


def _apply_move(conn, m: dict, result: ImportResult, dry_run: bool) -> None:
    src, dst, size, at = safe_rel_path(m.get("from")), safe_rel_path(m.get("to")), m.get("size"), m.get("at")
    label = f"{m.get('from')} → {m.get('to')}"
    if not (src and dst and isinstance(size, int) and isinstance(at, (int, float))):
        result.moves_skipped.append(f"{label}: malformed record")
        return
    same_dir = posixpath.dirname(src).lower() == posixpath.dirname(dst).lower()
    same_name = posixpath.basename(src) == posixpath.basename(dst)
    if same_dir and same_name:
        return  # nothing changed
    if not same_dir and not same_name:
        result.moves_skipped.append(f"{label}: moving and renaming in one step is not applied")
        return
    if _move_key_known(conn, {"from": src, "to": dst, "size": size, "at": at}):
        return  # already applied (or performed here)
    row = conn.execute("SELECT * FROM media WHERE rel_path = ? AND present = 1", (src,)).fetchone()
    if row is None:
        # Not on this machine, or the sync service already moved/renamed the file here: if the
        # destination is that same file (same size), carry the old record's tags over to it.
        if not dry_run:
            _relink_by_record(conn, src, dst, size)
        return
    if row["size"] != size:
        result.moves_skipped.append(f"{label}: a different file with the same name is here")
        return
    root = row["root"]
    if os.path.lexists(Path(root) / dst):
        result.moves_skipped.append(f"{label}: destination already occupied")
        return
    if dry_run:
        result.moves_applied += 1
        return
    try:
        if same_dir:  # a rename: the file keeps its album
            renaming.rename_media(conn, row["id"], posixpath.basename(dst), now=at)
        else:
            dest_album = posixpath.dirname(dst)
            if dest_album:
                albums.create_album(conn, root, dest_album)
            res = albums.move_to_album(conn, [row["id"]], root, dest_album, now=at)
            if not res.moved:
                if res.skipped:
                    result.moves_skipped.append(f"{label}: {res.skipped[0][1]}")
                return
    except (albums.AlbumError, renaming.RenameError) as e:
        result.moves_skipped.append(f"{label}: {e}")
        return
    result.moves_applied += 1


def _relink_by_record(conn, src: str, dst: str, size: int) -> None:
    old = conn.execute("SELECT id FROM media WHERE rel_path = ? AND present = 0", (src,)).fetchone()
    new = conn.execute("SELECT id FROM media WHERE rel_path = ? AND present = 1 AND size = ?", (dst, size)).fetchone()
    if old and new:
        merge_media_rows(conn, old["id"], new["id"])


def merge_media_rows(conn, old_id: int, new_id: int) -> None:
    """Fold the vanished row's tags into the row that replaces it (newest change per tag wins), then drop it."""
    with conn:
        conn.execute(
            "INSERT INTO media_tags (media_id, tag_id, updated_at, deleted) "
            "SELECT ?, tag_id, updated_at, deleted FROM media_tags WHERE media_id = ? "
            "ON CONFLICT(media_id, tag_id) DO UPDATE SET updated_at = excluded.updated_at, "
            "deleted = excluded.deleted WHERE excluded.updated_at > media_tags.updated_at",
            (new_id, old_id),
        )
        conn.execute("DELETE FROM media WHERE id = ?", (old_id,))  # cascades its tag links


def _find_media(conn, path: str, size) -> sqlite3.Row | None:
    row = conn.execute("SELECT * FROM media WHERE rel_path = ?", (path,)).fetchone()
    if row is not None:
        return row
    # Not at that path here: accept a single file with the same name and size (e.g. in another folder).
    name = posixpath.basename(path)
    cands = conn.execute(
        "SELECT * FROM media WHERE filename = ? AND present = 1 AND (? IS NULL OR size = ?)", (name, size, size)
    ).fetchall()
    return cands[0] if len(cands) == 1 else None


def _merge_record(conn, rec: dict, result: ImportResult, dry_run: bool) -> None:
    path = safe_rel_path(rec.get("path"))
    tags_in = [t for t in rec.get("tags") or [] if _valid_tag(t)]
    if not path or not tags_in:
        return
    size = rec.get("size") if isinstance(rec.get("size"), int) else None
    row = _find_media(conn, path, size)
    if row is None or not row["present"]:
        for t in tags_in:
            if dry_run:
                result.held_for_missing_files += 1
            elif _hold(conn, path, t):
                result.held_for_missing_files += 1
        return
    for t in tags_in:
        if merge_tag(conn, row["id"], t["name"], t["updated_at"], bool(t["deleted"]), apply=not dry_run):
            result.tags_changed += 1


def _valid_tag(t) -> bool:
    return (
        isinstance(t, dict)
        and isinstance(t.get("name"), str)
        and bool(" ".join(t["name"].split()))
        and isinstance(t.get("updated_at"), (int, float))
    )


def merge_tag(conn, media_id: int, name: str, updated_at: float, deleted: bool, apply: bool = True) -> bool:
    """Last-writer-wins merge of one (file, tag) record. Returns True if it changes (or would change) anything."""
    name = " ".join(name.split())
    row = conn.execute(
        "SELECT mt.updated_at, mt.deleted FROM media_tags mt JOIN tags t ON t.id = mt.tag_id "
        "WHERE mt.media_id = ? AND t.name = ?",
        (media_id, name),
    ).fetchone()
    if row is not None and updated_at <= row["updated_at"]:
        return False
    if row is None and deleted:
        changed = False  # a removal of something we never had: keep the tombstone only
    else:
        changed = row is None or bool(row["deleted"]) != deleted
    if apply:
        with conn:
            conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
            tag_id = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()["id"]
            conn.execute(
                "INSERT INTO media_tags (media_id, tag_id, updated_at, deleted) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(media_id, tag_id) DO UPDATE SET updated_at = excluded.updated_at, deleted = excluded.deleted",
                (media_id, tag_id, updated_at, int(deleted)),
            )
    return changed


def _hold(conn, path: str, t: dict) -> bool:
    name = " ".join(t["name"].split())
    old = conn.execute("SELECT updated_at FROM pending_sync WHERE rel_path = ? AND name = ?", (path, name)).fetchone()
    if old is not None and t["updated_at"] <= old["updated_at"]:
        return False
    with conn:
        conn.execute(
            "INSERT INTO pending_sync (rel_path, name, updated_at, deleted) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(rel_path, name) DO UPDATE SET updated_at = excluded.updated_at, deleted = excluded.deleted",
            (path, name, t["updated_at"], int(bool(t["deleted"]))),
        )
    return True


# -- housekeeping after scans / imports --------------------------------------------

def attach_pending(conn) -> int:
    """Apply held records whose file now exists here. Returns how many tag changes were made."""
    changed = 0
    for p in conn.execute("SELECT * FROM pending_sync").fetchall():
        row = conn.execute("SELECT id FROM media WHERE rel_path = ? AND present = 1", (p["rel_path"],)).fetchone()
        if row is None:
            continue
        if merge_tag(conn, row["id"], p["name"], p["updated_at"], bool(p["deleted"])):
            changed += 1
        with conn:
            conn.execute("DELETE FROM pending_sync WHERE id = ?", (p["id"],))
    return changed


def relink_moved(conn) -> int:
    """Carry tags over when a file was moved outside this app (Explorer, Proton Drive, another
    machine's move). A vanished file with tags is matched to a new file only when exactly one
    vanished and exactly one present file share the same name and size."""
    key = lambda r: (r["root"], r["filename"].lower(), r["size"])
    missing: dict[tuple, list] = {}
    for r in conn.execute(
        "SELECT id, root, filename, size FROM media WHERE present = 0 "
        "AND EXISTS (SELECT 1 FROM media_tags mt WHERE mt.media_id = media.id)"
    ):
        missing.setdefault(key(r), []).append(r["id"])
    if not missing:
        return 0
    present: dict[tuple, list] = {}
    for r in conn.execute("SELECT id, root, filename, size FROM media WHERE present = 1"):
        if key(r) in missing:
            present.setdefault(key(r), []).append(r["id"])
    linked = 0
    for k, old_ids in missing.items():
        new_ids = present.get(k, [])
        if len(old_ids) == 1 and len(new_ids) == 1:
            merge_media_rows(conn, old_ids[0], new_ids[0])
            linked += 1
    return linked


def after_scan(conn) -> None:
    """Run by the scan thread once the media table is current."""
    relink_moved(conn)
    attach_pending(conn)


# -- one-call helpers for the UI -----------------------------------------------------

def sync_now(conn, folder: Path | str, apply_moves: bool = True) -> ImportResult:
    """Import everyone else's data, then publish ours."""
    files, errors = read_folder(conn, folder)
    result = import_files(conn, files, apply_moves=apply_moves)
    result.errors += errors
    write_export(conn, folder)
    return result
