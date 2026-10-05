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

- Windows: `powershell -File packaging\build_windows.ps1` → `dist\Gallery-windows.zip`
- Linux: `bash packaging/build_linux.sh` → `dist/Gallery-linux.tar.gz`

Both scripts run `Gallery --selftest` on the result before packaging. Build on the OS (and CPU
architecture) you are targeting; PyInstaller does not cross-compile.

## Where things live

Database, thumbnail cache and `gallery.log` are in `%APPDATA%\gallery` (Windows) or
`~/.local/share/gallery` (Linux). Nothing is written to your photo folders except the album folders you
create, the files you move, and the optional hidden `.gallery-sync` folder.
