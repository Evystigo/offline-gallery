"""Thumbnail generation and on-disk cache (local only)."""

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageOps

from .db import app_data_dir

try:  # HEIC/HEIF support is optional at runtime
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover
    pass

THUMB_SIZE = 256


def run_hidden(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run that never flashes a console window (a windowed Windows app otherwise opens
    one for every ffmpeg/ffprobe call)."""
    if sys.platform == "win32":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
    return subprocess.run(cmd, **kwargs)


def cache_dir() -> Path:
    d = app_data_dir() / "thumbs"
    d.mkdir(exist_ok=True)
    return d


def cache_path(source: Path, size: int, mtime: float, directory: Path | None = None) -> Path:
    key = f"{source}|{size}|{mtime}".encode()
    return (directory or cache_dir()) / (hashlib.sha1(key).hexdigest() + ".jpg")


def find_ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _image_thumb(source: Path, dest: Path) -> bool:
    with Image.open(source) as im:
        im = ImageOps.exif_transpose(im)
        im.thumbnail((THUMB_SIZE, THUMB_SIZE))
        im.convert("RGB").save(dest, "JPEG", quality=85)
    return True


def _video_thumb(source: Path, dest: Path) -> bool:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return False
    # Grab a frame at 1s; fall back to the first frame for very short clips.
    for seek in ("1", "0"):
        cmd = [
            ffmpeg, "-v", "error", "-y", "-ss", seek, "-i", str(source),
            "-frames:v", "1", "-vf", f"scale={THUMB_SIZE}:{THUMB_SIZE}:force_original_aspect_ratio=decrease",
            str(dest),
        ]
        try:
            run_hidden(cmd, check=True, timeout=30, capture_output=True)
        except (subprocess.SubprocessError, OSError):
            continue
        if dest.exists() and dest.stat().st_size > 0:
            return True
    return False


def get_thumbnail(
    source: Path, media_type: str, mtime: float, directory: Path | None = None
) -> Path | None:
    """Return the cached thumbnail path, generating it if needed. None on failure."""
    dest = cache_path(source, THUMB_SIZE, mtime, directory)
    if dest.exists():
        return dest
    tmp = dest.with_suffix(".tmp.jpg")
    try:
        ok = _image_thumb(source, tmp) if media_type == "photo" else _video_thumb(source, tmp)
        if ok:
            tmp.replace(dest)
            return dest
    except Exception:
        pass
    tmp.unlink(missing_ok=True)
    return None
