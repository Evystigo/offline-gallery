import re
import subprocess
import sys
from pathlib import Path

import pytest

from gallery import thumbnails


def test_all_subprocess_calls_go_through_run_hidden():
    """A bare subprocess.run in a windowed Windows app flashes a console window per call."""
    offenders = []
    for py in (Path(__file__).parent.parent / "src" / "gallery").rglob("*.py"):
        if py.name == "thumbnails.py":
            continue  # defines run_hidden itself
        if re.search(r"subprocess\.(run|Popen|call|check_output|check_call)\(", py.read_text(encoding="utf-8")):
            offenders.append(py.name)
    assert offenders == []


@pytest.mark.skipif(sys.platform != "win32", reason="CREATE_NO_WINDOW only exists on Windows")
def test_run_hidden_passes_no_window_flag(monkeypatch):
    seen = {}
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: seen.update(kw) or subprocess.CompletedProcess(cmd, 0))
    thumbnails.run_hidden(["x"], check=True)
    assert seen["creationflags"] & subprocess.CREATE_NO_WINDOW
    assert seen["check"] is True
