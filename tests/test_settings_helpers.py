from gallery.ui.duplicates_dialog import human_size
from gallery.ui.settings_dialog import cache_stats, clear_cache


def test_cache_stats_and_clear_only_touch_thumbnail_files(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"x" * 10)
    (tmp_path / "b.jpg").write_bytes(b"y" * 20)
    (tmp_path / "keep.txt").write_bytes(b"not a thumbnail")
    assert cache_stats(tmp_path) == (3, 45)
    assert clear_cache(tmp_path) == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == ["keep.txt"]


def test_human_size():
    assert human_size(0) == "0 B"
    assert human_size(1023) == "1023 B"
    assert human_size(1536) == "1.5 KB"
    assert human_size(5 * 1024**3) == "5.0 GB"
