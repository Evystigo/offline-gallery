# PyInstaller spec shared by Windows and Linux builds (run from the repo root):
#   pyinstaller packaging/gallery.spec --noconfirm
from PyInstaller.utils.hooks import collect_all

# pillow-heif ships a native library that PyInstaller does not discover on its own.
heif_datas, heif_binaries, heif_hidden = collect_all("pillow_heif")

a = Analysis(
    ["run_gallery.py"],
    pathex=["../src"],
    binaries=heif_binaries,
    datas=heif_datas,
    hiddenimports=heif_hidden,
    excludes=["tkinter", "unittest", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Gallery",
    console=False,  # no terminal window; errors go to gallery.log in the app data folder
)
coll = COLLECT(exe, a.binaries, a.datas, name="Gallery")
