"""
Bundle Tesseract OCR binary + tessdata into resources/tesseract/<target>/
so electron-builder can include the right binary for each platform.

Usage
-----
# Bundle for the current host platform (default):
    python scripts/bundle_tesseract.py

# Cross-compile: bundle Windows Tesseract from Linux/macOS (downloads portable zip):
    python scripts/bundle_tesseract.py --target win

# Explicit targets:
    python scripts/bundle_tesseract.py --target linux
    python scripts/bundle_tesseract.py --target mac

Output layout:
    resources/tesseract/
        tesseract[.exe]
        tessdata/
            eng.traineddata
            tam.traineddata
            osd.traineddata
        *.dll               (Windows only — from portable zip)

Windows cross-compile source
-----------------------------
Downloads the UB-Mannheim portable Tesseract from GitHub Releases.
No Wine, no Windows VM required.
Tessdata (.traineddata) is downloaded from tessdata-fast on GitHub.
"""
from __future__ import annotations

import argparse
import io
import os
import shutil
import stat
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR   = REPO_ROOT / "resources" / "tesseract"

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

LANGS = ["eng", "tam", "osd"]

# UB-Mannheim Tesseract Windows installer (Inno Setup — extractable with innoextract)
# See https://github.com/UB-Mannheim/tesseract/releases
WIN_INSTALLER_URL = (
    "https://github.com/UB-Mannheim/tesseract/releases/download/"
    "v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe"
)

# tessdata-fast repo raw URLs for individual .traineddata files
TESSDATA_BASE = "https://github.com/tesseract-ocr/tessdata_fast/raw/main"


# ── Helpers ───────────────────────────────────────────────────────────────────

def copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    if not IS_WIN:
        st = dst.stat()
        dst.chmod(st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def download(url: str, dest: Path, label: str = "") -> None:
    label = label or dest.name
    print(f"  Downloading {label} …", end="", flush=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        urllib.request.urlretrieve(url, dest)
        size_kb = dest.stat().st_size // 1024
        print(f" {size_kb} KB")
    except Exception as exc:
        print(f" FAILED: {exc}")
        raise


# ── Host-platform (Linux / macOS) bundle ──────────────────────────────────────

def find_host_tesseract_bin() -> Path:
    override = os.environ.get("TESSERACT_SRC")
    if override:
        p = Path(override)
        exe = p / ("tesseract.exe" if IS_WIN else "tesseract")
        if exe.exists():
            return exe
        raise FileNotFoundError(f"tesseract not found at {exe}")

    candidates = [
        Path("/usr/bin/tesseract"),
        Path("/usr/local/bin/tesseract"),
        Path("/opt/homebrew/bin/tesseract"),
        Path("/usr/local/opt/tesseract/bin/tesseract"),
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ]
    for c in candidates:
        if c.exists():
            return c
    result = shutil.which("tesseract")
    if result:
        return Path(result)
    raise FileNotFoundError(
        "tesseract binary not found. Install it or set TESSERACT_SRC."
    )


def find_host_tessdata() -> Path:
    override = os.environ.get("TESSDATA_PREFIX")
    if override:
        p = Path(override)
        for td in [p, p / "tessdata"]:
            if (td / "eng.traineddata").exists():
                return td

    candidates = [
        Path("/usr/share/tesseract-ocr/4.00/tessdata"),
        Path("/usr/share/tesseract-ocr/5/tessdata"),
        Path("/usr/share/tessdata"),
        Path("/usr/local/share/tessdata"),
        Path("/opt/homebrew/share/tessdata"),
        Path(r"C:\Program Files\Tesseract-OCR\tessdata"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tessdata"),
    ]
    for c in candidates:
        if c.exists() and (c / "eng.traineddata").exists():
            return c
    raise FileNotFoundError(
        "tessdata directory not found. Set TESSDATA_PREFIX env var."
    )


def bundle_host() -> None:
    tess_bin = find_host_tesseract_bin()
    print(f"  Binary : {tess_bin}")
    copy_file(tess_bin, OUT_DIR / tess_bin.name)

    tessdata_src = find_host_tessdata()
    print(f"  Tessdata: {tessdata_src}")
    for lang in LANGS:
        src = tessdata_src / f"{lang}.traineddata"
        dst = OUT_DIR / "tessdata" / f"{lang}.traineddata"
        if src.exists():
            copy_file(src, dst)
            print(f"    → {dst.relative_to(REPO_ROOT)}")
        else:
            print(f"    ⚠  {lang}.traineddata not found — skipping")


# ── Windows portable bundle (cross-compile from Linux/macOS) ──────────────────

def _check_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(
            f"'{name}' is required for cross-compiling Windows Tesseract.\n"
            f"Install it with:  sudo apt install {name}"
        )
    return path


def bundle_windows_portable() -> None:
    import subprocess
    sevenz = _check_tool("7z") or _check_tool("7za")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        installer = tmp / "tesseract-setup.exe"
        download(WIN_INSTALLER_URL, installer, "UB-Mannheim Tesseract 5.4 setup (Windows x64)")

        # 7z can extract NSIS installers directly
        extract_dir = tmp / "extracted"
        extract_dir.mkdir()
        print("  Extracting with 7z …", flush=True)
        result = subprocess.run(
            [sevenz, "x", str(installer), f"-o{extract_dir}", "-y"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"7z extraction failed:\n{result.stderr}")

        # 7z extracts NSIS contents flat into extract_dir
        # tesseract.exe and DLLs land at the root; tessdata/ as a subdir
        print(f"  Extracted to: {extract_dir}")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        tessdata_dst = OUT_DIR / "tessdata"
        tessdata_dst.mkdir(exist_ok=True)

        # tesseract.exe
        tess_exe = extract_dir / "tesseract.exe"
        if not tess_exe.exists():
            # Some 7z versions nest under a subdirectory
            candidates = list(extract_dir.rglob("tesseract.exe"))
            if not candidates:
                raise RuntimeError(f"tesseract.exe not found in {extract_dir}")
            tess_exe = candidates[0]

        shutil.copy2(tess_exe, OUT_DIR / "tesseract.exe")
        print(f"    → {(OUT_DIR / 'tesseract.exe').relative_to(REPO_ROOT)}")

        # All DLLs at the same level as tesseract.exe
        for dll in tess_exe.parent.glob("*.dll"):
            shutil.copy2(dll, OUT_DIR / dll.name)
            print(f"    → {(OUT_DIR / dll.name).relative_to(REPO_ROOT)}")

        # tessdata from extracted tree
        extracted_tessdata = tess_exe.parent / "tessdata"
        if extracted_tessdata.exists():
            for lang in LANGS:
                src = extracted_tessdata / f"{lang}.traineddata"
                dst = tessdata_dst / f"{lang}.traineddata"
                if src.exists():
                    shutil.copy2(src, dst)
                    print(f"    → {dst.relative_to(REPO_ROOT)}")

    # Any missing .traineddata — download from tessdata-fast
    for lang in LANGS:
        dst = tessdata_dst / f"{lang}.traineddata"
        if not dst.exists():
            url = f"{TESSDATA_BASE}/{lang}.traineddata"
            try:
                download(url, dst, f"{lang}.traineddata")
            except Exception:
                print(f"    ⚠  Could not download {lang}.traineddata — skipping")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Bundle Tesseract for Electron packaging")
    default_target = "win" if IS_WIN else ("mac" if IS_MAC else "linux")
    parser.add_argument(
        "--target",
        choices=["win", "linux", "mac"],
        default=default_target,
        help=f"Target OS for the Tesseract bundle (default: {default_target})",
    )
    args = parser.parse_args()
    target = args.target

    print(f"Bundling Tesseract for target={target}")
    print(f"Output directory: {OUT_DIR}")

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)

    if target == "win":
        if IS_WIN:
            bundle_host()
        else:
            print("  Cross-compiling: downloading Windows portable Tesseract from UB-Mannheim …")
            bundle_windows_portable()
    else:
        bundle_host()

    total_kb = sum(f.stat().st_size for f in OUT_DIR.rglob("*") if f.is_file()) // 1024
    print(f"\n✓  Tesseract bundle written to resources/tesseract/  ({total_kb} KB)")


if __name__ == "__main__":
    main()
