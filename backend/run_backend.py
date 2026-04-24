"""
Entry point for running the eSeva FastAPI backend via uvicorn programmatically.

Used in two scenarios:
  1. Development: `python run_backend.py`  (from inside the backend/ directory)
  2. Packaged app: PyInstaller freezes this file as `eseva-backend` executable.
     Electron's main.js spawns it instead of `python -m uvicorn`.

Environment variables (all optional — Electron sets them before spawning):
  ESEVA_APPDATA_ROOT  — writable user-data root (keys/, data/, Operations/)
  ESEVA_CONFIGS_DIR   — path to the service configs folder
  ESEVA_OPS_DIR       — legacy alias; superseded by ESEVA_APPDATA_ROOT
  ESEVA_BIND_HOST     — default 127.0.0.1
  ESEVA_BIND_PORT     — default 8765
  ESEVA_LOG_LEVEL     — default info
  TESSERACT_CMD       — full path to tesseract binary (set by Electron for bundled app)
  TESSDATA_PREFIX     — directory containing tessdata/ (set by Electron for bundled app)
"""
from __future__ import annotations

import sys
import os

# ── Path setup ────────────────────────────────────────────────────────────────
# When frozen by PyInstaller, __file__ is inside the temp extraction dir
# (_MEIPASS). We need the backend/ source on sys.path for `app.*` imports.
if getattr(sys, 'frozen', False):
    # Running as PyInstaller bundle — _MEIPASS is the extraction root
    _BUNDLE_DIR = sys._MEIPASS  # type: ignore[attr-defined]
else:
    _BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))

if _BUNDLE_DIR not in sys.path:
    sys.path.insert(0, _BUNDLE_DIR)

# ── Tesseract path resolution ─────────────────────────────────────────────────
# Priority:
#   1. TESSERACT_CMD env var (set explicitly by Electron main.js)
#   2. Bundled binary next to this executable: <exe_dir>/tesseract/tesseract[.exe]
#   3. System tesseract (dev / Linux with apt-installed tesseract)
def _resolve_tesseract() -> str | None:
    # 1. Explicit env override
    cmd = os.environ.get("TESSERACT_CMD")
    if cmd and os.path.isfile(cmd):
        return cmd

    # 2. Bundled alongside the frozen exe
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        candidates = [
            os.path.join(exe_dir, "tesseract", "tesseract.exe"),  # Windows
            os.path.join(exe_dir, "tesseract", "tesseract"),       # Linux/macOS
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c

    # 3. Let pytesseract find it on PATH
    return None


_tesseract_cmd = _resolve_tesseract()
if _tesseract_cmd:
    try:
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = _tesseract_cmd
        # Also set TESSDATA_PREFIX so tesseract finds language data
        _tessdata = os.environ.get("TESSDATA_PREFIX")
        if not _tessdata and getattr(sys, 'frozen', False):
            # Default: <exe_dir>/tesseract/tessdata
            _tessdata = os.path.join(os.path.dirname(_tesseract_cmd), "tessdata")
        if _tessdata:
            os.environ["TESSDATA_PREFIX"] = _tessdata
    except ImportError:
        pass

import uvicorn
from app.settings import settings


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host=settings.bind_host,
        port=settings.bind_port,
        log_level=settings.log_level,
        access_log=True,
    )


if __name__ == "__main__":
    main()
