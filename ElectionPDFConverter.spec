# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH)
TESSERACT_DIR = Path(os.environ.get("TESSERACT_DIR", "")).resolve()
if not TESSERACT_DIR.is_dir():
    raise SystemExit("TESSERACT_DIR is missing.")
if not (TESSERACT_DIR / "tesseract.exe").is_file():
    raise SystemExit("tesseract.exe was not found in {}".format(TESSERACT_DIR))
for language in ("hin", "eng"):
    trained = TESSERACT_DIR / "tessdata" / (language + ".traineddata")
    if not trained.is_file():
        raise SystemExit("Missing OCR language file: {}".format(trained))

hiddenimports = []
for package in ("uvicorn", "fastapi", "starlette", "multipart", "pytesseract"):
    hiddenimports += collect_submodules(package)

binaries = []
for package in ("cv2", "fitz", "numpy"):
    binaries += collect_dynamic_libs(package)

# Include the full portable Tesseract installation, including DLLs and
# Hindi/English traineddata, inside the single EXE. PyInstaller extracts these
# files to its private runtime directory when the application starts.
datas = [
    (str(ROOT / "static" / "index.html"), "static"),
]
datas += collect_data_files("pytesseract")
datas += [
    (
        str(path),
        str(Path("tesseract") / path.relative_to(TESSERACT_DIR).parent),
    )
    for path in TESSERACT_DIR.rglob("*")
    if path.is_file()
]

analysis = Analysis(
    [str(ROOT / "launcher_windows.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest"],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="VoterListConverter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "app_icon.ico"),
    version=str(ROOT / "version_info.txt"),
    runtime_tmpdir=None,
)
