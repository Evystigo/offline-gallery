"""File fingerprints for duplicate detection (all computed locally).

* `sha256_file`: exact content hash.
* dHash: a 64-bit perceptual hash. It survives resizing and recompression, so near-identical
  photos end up a few bits apart (small Hamming distance) while different photos are far apart.
* Videos: duration plus the dHash of the frame in the middle of the clip (needs ffmpeg/ffprobe).
"""

import hashlib
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageOps

from . import thumbnails  # noqa: F401  (registers HEIC/HEIF support with Pillow)

MASK = (1 << 64) - 1
# A hash that is almost all 0s or all 1s comes from a flat image (blank, solid colour) and would
# "match" every other flat image, so it is not used.
MIN_BITS, MAX_BITS = 6, 58


def sha256_file(path: Path | str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def to_signed(h: int) -> int:
    return h - (1 << 64) if h >= (1 << 63) else h


def to_unsigned(h: int) -> int:
    return h & MASK


def _dhash_from_gray(px: bytes) -> int | None:
    """`px` is a 9x8 grayscale image (72 bytes). Each bit says whether a pixel is brighter than its right neighbour."""
    if len(px) != 72:
        return None
    bits = 0
    for row in range(8):
        for col in range(8):
            i = row * 9 + col
            bits = (bits << 1) | (px[i] > px[i + 1])
    return bits if MIN_BITS <= bits.bit_count() <= MAX_BITS else None


def dhash_image(path: Path | str) -> int | None:
    """Perceptual hash of a photo, or None if unreadable or too uniform to be meaningful."""
    try:
        with Image.open(path) as im:
            im.draft("L", (64, 64))  # lets JPEG decode at reduced size
            im = ImageOps.exif_transpose(im).convert("L").resize((9, 8), Image.Resampling.LANCZOS)
            return _dhash_from_gray(im.tobytes())
    except Exception:
        return None


def find_ffprobe() -> str | None:
    return shutil.which("ffprobe")


def video_signature(path: Path | str) -> tuple[float | None, int | None]:
    """(duration in seconds, dHash of the middle frame); either may be None."""
    ffmpeg, ffprobe = thumbnails.find_ffmpeg(), find_ffprobe()
    if not ffmpeg or not ffprobe:
        return None, None
    path = str(path)
    duration = None
    try:
        out = thumbnails.run_hidden(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout.strip()
        duration = float(out) if out and out != "N/A" else None
    except (subprocess.SubprocessError, OSError, ValueError):
        pass
    seek = (duration / 2) if duration else 1.0
    try:
        raw = thumbnails.run_hidden(
            [ffmpeg, "-v", "error", "-ss", f"{seek:.3f}", "-i", path, "-frames:v", "1",
             "-vf", "scale=9:8:flags=area,format=gray", "-f", "rawvideo", "-"],
            capture_output=True, timeout=120, check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return duration, None
    return duration, _dhash_from_gray(raw)
