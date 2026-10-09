# Gallery

A private, offline photo and video gallery for Windows and Linux. Tag files, organise them into
albums (real folders), search by tag, find duplicates, and keep your tags consistent across machines.
It never connects to the internet.

## Run from source

```
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"      # Linux: .venv/bin/python
.venv\Scripts\python -m gallery.main
.venv\Scripts\python -m pytest                        # run the tests
```

Python 3.12+. Install **ffmpeg** (it includes ffprobe) and put it on your PATH for video thumbnails
and video duplicate detection. Without it everything else still works and videos get a placeholder tile.

## Using it

- **Add folder** (Ctrl+O): your library. Subfolders are albums. Files directly in the library folder that
  have no tags are shown under **Unsorted**.
- **Tags**: select items, type tags (comma separated) in the right panel (Ctrl+T).
- **Albums**: right-click → *Move to album*. This **moves the file** into that folder. Names that already
  exist in the destination are never overwritten. **Drag an album onto another album** (or use *Move…*) to put
  it inside; drop it on *All media* to move it back to the top level.
  Albums with subfolders can be collapsed with the arrow (or ←/→); right-click the list for *Expand all* /
  *Collapse all*. Each machine remembers which albums you left open.
- **Rename** (F2): select any number of files, choose a name, and they become `name1.jpg`, `name2.png`, … in the
  order shown in the grid (each keeps its own extension and stays in its album). The preview shows every new
  name first, and the whole batch is refused if anything would collide. Other machines follow renames at their
  next sync.
- **Search** (Ctrl+F): `#beach` `#sun|sea` (either) `-#nsfw` (exclude) `type:video` `album:Trips/2024`
  `after:2024-01-01` `before:2024-12-31`, and plain words match the file name or path.
- **Duplicates** (Ctrl+D): exact copies and similar photos/videos. You choose which copy to keep; the rest go
  to the Recycle Bin / Trash and their tags are merged onto the kept file.

## Keeping several machines consistent

Put the app's sync folder inside a folder your sync service already shares (e.g. Proton Drive):
*Settings → Sync → "Use .gallery-sync in library"*. Each machine writes its own small JSON file there and
reads the others at startup (or *Sync → Sync now*). It carries relative paths, tags and album moves, never
image data or absolute paths. Files that exist on one machine only simply keep their tags until the file shows up.

Tag changes use "newest change wins", so keep your computers' clocks roughly correct.

## Build a standalone app

This turns the project into a folder you can run (or zip and copy) without installing Python. It uses
[PyInstaller](https://pyinstaller.org), which **cannot cross-compile**: build the Windows version on Windows
and the Linux version on Linux, and on the same CPU type you want to run it on (x64 or ARM64).

Get the code first:

```
git clone https://github.com/Evystigo/offline-gallery.git
cd offline-gallery
```

### Windows

1. **Python 3.12 or newer.** Install it from [python.org](https://www.python.org/downloads/) (tick *Add python.exe to
   PATH*) or run `winget install Python.Python.3.12`. Open a new PowerShell window and check `python --version`.
2. **ffmpeg (optional, recommended)** for video thumbnails and video duplicate detection:
   `winget install Gyan.FFmpeg`. It is *not* bundled, and the finished app looks for it on your PATH.
3. **Build**, from the project folder in PowerShell:

   ```
   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
   ```

   The first run downloads the dependencies (a few hundred MB) and takes a few minutes.
4. **Result**
   - `dist\Gallery\Gallery.exe`: double-click to run. Keep the whole `Gallery` folder together.
   - `dist\Gallery-windows.zip`: the same folder zipped, to copy to another Windows PC of the same CPU type.

Windows SmartScreen may say "unknown publisher" the first time, because the app is not code-signed. Choose
*More info → Run anyway*.

### Linux

Written for Debian/Ubuntu-style systems; on other distributions install the equivalent packages.

1. **Python 3.12 or newer, git, and the Qt runtime libraries:**

   ```
   sudo apt install git python3 python3-venv python3-pip \
       libgl1 libegl1 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
       libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 libxfixes3 libdbus-1-3 libfontconfig1
   ```

   If `python3 --version` is older than 3.12, install a newer Python first (for example with
   [pyenv](https://github.com/pyenv/pyenv) or your distribution's backports).
2. **ffmpeg (optional, recommended):** `sudo apt install ffmpeg`
3. **Build**, from the project folder:

   ```
   bash packaging/build_linux.sh
   ```
4. **Result**
   - `dist/Gallery/Gallery`: run it with `./dist/Gallery/Gallery`.
   - `dist/Gallery-linux.tar.gz`: the same folder as an archive. Extract it anywhere and run `Gallery/Gallery`.
   - Optional menu entry: copy `packaging/gallery.desktop` to `~/.local/share/applications/` and change its
     `Exec=` line to the real path of `Gallery`.

A Linux build runs on the same or a newer distribution version than the one it was built on, not an older one,
so build on the oldest system you want to support.

> The Linux build script has not yet been run on a real Linux machine by the author (the project was developed and
> built on Windows). If it fails for you, please open an issue with the output.

### What the build does, and if it fails

Each script creates a `.venv`, installs the project with `pip install -e ".[build]"`, runs
`pyinstaller packaging/gallery.spec`, then runs the finished app with `--selftest`. The self-test starts the
program headlessly and checks that HEIC images, video playback support and the main window all work inside the
bundle, so a build that prints `selftest OK` is a good one. The scripts stop with an error if it fails.

- **`python` not found (Windows):** reopen PowerShell after installing Python, or run the script with the full
  path to python.
- **Script blocked (Windows):** use the `-ExecutionPolicy Bypass` form shown above.
- **Self-test fails on Linux with a Qt/xcb message:** a Qt runtime library from step 1 is missing. Run
  `QT_DEBUG_PLUGINS=1 dist/Gallery/Gallery --selftest` and install the library it names.
- **Run the self-test by hand:** on Linux, `dist/Gallery/Gallery --selftest`. On Windows the app has no console,
  so it writes a report file (PowerShell):

  ```
  Start-Process dist\Gallery\Gallery.exe -ArgumentList '--selftest', "$env:TEMP\report.txt" -Wait
  Get-Content "$env:TEMP\report.txt"
  ```
- **Clean rebuild:** delete the `build` and `dist` folders and run the script again.

## Where things live

Database, thumbnail cache and `gallery.log` are in `%APPDATA%\gallery` (Windows) or
`~/.local/share/gallery` (Linux). Nothing is written to your photo folders except the album folders you
create, the files you move, and the optional hidden `.gallery-sync` folder.
