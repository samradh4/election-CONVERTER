# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH)
TESSERACT_DIR = Path(os.environ.get("TESSERACT_DIR", "")).resolve()
if not TESSERACT_DIR.is_dir():
    raise SystemExit(
        "TESSERACT_DIR is missing. Run build_exe_windows.bat instead of calling PyInstaller directly."
    )
if not (TESSERACT_DIR / "tesseract.exe").is_file():
    raise SystemExit("tesseract.exe was not found in {}".format(TESSERACT_DIR))
if not (TESSERACT_DIR / "tessdata" / "hin.traineddata").is_file():
    raise SystemExit("Hindi OCR file hin.traineddata is missing from Tesseract tessdata.")
if not (TESSERACT_DIR / "tessdata" / "eng.traineddata").is_file():
    raise SystemExit("English OCR file eng.traineddata is missing from Tesseract tessdata.")

hiddenimports = []
for package in ("uvicorn", "fastapi", "starlette", "multipart", "pytesseract"):
    hiddenimports += collect_submodules(package)

binaries = []
for package in ("cv2", "fitz", "numpy"):
    binaries += collect_dynamic_libs(package)

datas = [
    (str(ROOT / "static" / "index.html"), "static"),
    (str(ROOT / "tessdata" / ".gitkeep"), "tessdata"),
]
datas += collect_data_files("pytesseract")
datas += [(str(path), str(Path("tesseract") / path.relative_to(TESSERACT_DIR).parent))
          for path in TESSERACT_DIR.rglob("*") if path.is_file()]

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
    [],
    exclude_binaries=True,
    name="ElectionPDFConverter",
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
)

collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ElectionPDFConverter",
)
