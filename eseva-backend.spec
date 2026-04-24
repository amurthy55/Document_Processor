# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for the eSeva FastAPI backend.
#
# Build from the repo root:
#   .venv/bin/pyinstaller eseva-backend.spec
#
# Output: dist/eseva-backend/   (--onedir, faster startup than --onefile)
#
# The Electron main.js spawns dist/eseva-backend/eseva-backend (Linux/macOS)
# or dist/eseva-backend/eseva-backend.exe (Windows) in packaged mode.

import sys, os
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = os.path.dirname(os.path.abspath(SPEC))   # repo root
BACKEND = os.path.join(ROOT, 'backend')

# ── Collect everything from the packages that use dynamic imports ─────────────
datas   = []
hiddenimports = []
binaries = []

for pkg in ['uvicorn', 'fastapi', 'starlette', 'anyio', 'h11',
            'pydantic', 'pydantic_settings', 'pydantic_core',
            'email_validator', 'httptools', 'uvloop',
            'multipart', 'python_multipart',
            'cryptography', 'platformdirs',
            'PIL', 'pytesseract']:
    d, b, h = collect_all(pkg)
    datas   += d
    binaries += b
    hiddenimports += h

# PyMuPDF ships its own libmupdf — collect_all handles it
for pkg in ['fitz']:
    d, b, h = collect_all(pkg)
    datas   += d
    binaries += b
    hiddenimports += h

# OpenCV — collect the .so and its data
for pkg in ['cv2']:
    d, b, h = collect_all(pkg)
    datas   += d
    binaries += b
    hiddenimports += h

# numpy (required by cv2)
for pkg in ['numpy']:
    d, b, h = collect_all(pkg)
    datas   += d
    binaries += b
    hiddenimports += h

# ── App source — include the entire backend/app package as data ───────────────
# This ensures all .py files land in the bundle (PyInstaller finds most via
# static analysis but routers are imported dynamically).
datas += [(os.path.join(BACKEND, 'app'), 'app')]

# ── Explicit hidden imports for dynamic router loading in app/main.py ─────────
hiddenimports += [
    'app.main',
    'app.db',
    'app.security',
    'app.settings',
    'app.improved_extractor',
    'app.optimized_processor',
    'app.mapping_engine',
    'app.operations.store',
    'app.services.config_store',
    'app.routers.documents',
    'app.routers.operations',
    'app.routers.services',
    'app.routers.admin',
    'app.routers.sync_documents',
    # uvicorn internals loaded at runtime
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.loops.asyncio',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.http.h11_impl',
    'uvicorn.protocols.http.httptools_impl',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    # starlette internals
    'starlette.middleware',
    'starlette.middleware.cors',
    # cryptography backends
    'cryptography.hazmat.primitives.ciphers.aead',
    'cryptography.hazmat.backends.openssl',
    # pydantic v2
    'pydantic.v1',
    # multipart
    'multipart',
    # stdlib used at runtime
    'sqlite3',
    'logging.handlers',
    'email',
    'email.mime',
    'email.mime.text',
]

# ── De-duplicate ──────────────────────────────────────────────────────────────
datas          = list(dict.fromkeys(datas))
hiddenimports  = list(dict.fromkeys(hiddenimports))

a = Analysis(
    [os.path.join(BACKEND, 'run_backend.py')],
    pathex=[BACKEND, ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # not needed at runtime — saves ~20 MB
        'tkinter', '_tkinter', 'matplotlib', 'scipy',
        'IPython', 'notebook', 'pytest', 'setuptools', 'pip',
        'torch', 'easyocr', 'ocrmypdf',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # --onedir: binaries go into COLLECT
    name='eseva-backend.exe' if sys.platform == 'win32' else 'eseva-backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,            # keep console visible so Electron can read stdout/stderr
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='eseva-backend',
)

# Post-build step: ensure .exe extension exists on Windows
if sys.platform == 'win32':
    import shutil
    exe_path = os.path.join(coll.name, 'eseva-backend.exe')
    if not os.path.exists(exe_path):
        # PyInstaller might have created without .exe, rename it
        no_exe_path = os.path.join(coll.name, 'eseva-backend')
        if os.path.exists(no_exe_path):
            shutil.move(no_exe_path, exe_path)
