# Document Processor / eSeva Center

Document Processor, packaged as the eSeva Center desktop app, is a Windows-focused document processing and form-assist tool for citizen service centers. It combines an Electron desktop shell, a local FastAPI backend, OCR/document extraction pipelines, a Chrome extension for filling supported web forms, and an optional Cloudflare Workers license server.

The desktop app runs everything locally on the operator machine. Electron starts a bundled PyInstaller backend on `127.0.0.1:8765`, serves the UI from local files, copies the browser extension to a visible user folder, and stores operator data under the user's application data directory.

## What It Does

- Uploads PDF/image documents and extracts structured fields from them.
- Uses fast text extraction first, then OCR when documents are scanned.
- Compresses uploaded and generated documents for service workflows.
- Tracks document processing operations and their status.
- Stores operation data locally using SQLite and per-operation folders.
- Provides a Chrome extension that can pull extracted data from the local API and fill supported forms.
- Supports service-specific JSON configuration files for field mappings.
- Supports license activation and periodic validation through a Cloudflare Worker.
- Can be packaged as a Windows folder app or portable/installer build.

## Repository Layout

```text
assets/                 App icons and build assets
backend/                FastAPI backend and document processing code
backend/app/operations/ Operation session models and SQLite-backed store
configs/                Service configuration and field mapping JSON files
electron/               Electron main process, preload, splash, activation UI
extension/              Chrome extension used for browser form filling
license-server/         Cloudflare Workers license validation service
resources/              Large bundled resources, including optional Tesseract
scripts/                Build/helper scripts
ui/                     Desktop UI HTML
eseva-backend.spec      PyInstaller spec for the backend executable
package.json            Electron build scripts and JavaScript dependencies
```

Generated folders such as `dist/`, `build/`, `dist-packaged/`, extracted app folders, ZIPs, installers, logs, SQLite files, and temporary app-data folders are intentionally ignored.

## Architecture

The app has four main pieces:

1. Electron desktop shell
   - Entry point: `electron/main.js`
   - Opens the desktop UI from `ui/index.html`.
   - Starts the backend process.
   - Passes writable paths to the backend through environment variables.
   - Copies bundled configs and extension files into user-accessible locations.
   - Writes Electron logs using `electron-log`.

2. FastAPI backend
   - Entry point: `backend/run_backend.py`
   - ASGI app factory: `backend/app/main.py`
   - Runs on `http://127.0.0.1:8765`.
   - Provides `/api/documents/*`, `/api/operations/*`, `/api/services/*`, and `/api/admin/*` routes.
   - Uses SQLite for operation metadata.
   - Uses local folders for document operation files.

3. Document processing pipeline
   - Main processor: `backend/app/optimized_processor.py`
   - Uses PyMuPDF, Pillow, OpenCV, pytesseract, and related helpers.
   - Handles extraction, OCR fallback, PDF/image processing, and compression.

4. Browser extension
   - Source: `extension/`
   - Talks to the local backend at `http://127.0.0.1:8765/api`.
   - Fetches operation data and service mappings.
   - Fills supported web forms based on config-driven field mappings.

## Runtime Paths

On Windows, the installed app uses Electron's `userData` directory. For this project that is typically:

```powershell
$env:APPDATA\eseva-center
```

Important paths:

```text
%APPDATA%\eseva-center\logs\main.log       Electron/backend startup log
%APPDATA%\eseva-center\license.json        Cached license token
%APPDATA%\eseva-center\configs\            Writable service config copy
%APPDATA%\eseva-center\data\eseva.sqlite3  Backend SQLite database
%APPDATA%\eseva-center\Operations\         Backend operation data
%USERPROFILE%\eSeva-Extension\             Copied Chrome extension folder
%USERPROFILE%\eSeva-Operations\            Operator-visible operation output path
```

To inspect logs:

```powershell
notepad "$env:APPDATA\eseva-center\logs\main.log"
```

To check whether the backend is alive:

```powershell
Invoke-WebRequest http://127.0.0.1:8765/api/documents/operations
```

Expected healthy response:

```json
{"operations":[]}
```

## Prerequisites

For development:

- Windows 10/11 recommended for the packaged desktop workflow
- Python 3.11+ recommended
- Node.js 18+ recommended
- npm
- PyInstaller
- Tesseract OCR, either installed system-wide or bundled under `resources/tesseract`

Python packages are listed in:

```text
backend/requirements.txt
```

Node dependencies are listed in:

```text
package.json
package-lock.json
```

## Local Development Setup

Create and activate a Python virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend\requirements.txt
python -m pip install pyinstaller
```

Install JavaScript dependencies:

```powershell
npm install
```

Run the backend directly:

```powershell
cd backend
$env:ESEVA_APPDATA_ROOT = "D:\DocMagic\.dev-appdata"
$env:ESEVA_CONFIGS_DIR = "D:\DocMagic\configs"
python run_backend.py
```

In another terminal, verify:

```powershell
Invoke-WebRequest http://127.0.0.1:8765/api/documents/operations
```

Run Electron in development mode:

```powershell
npm run dev
```

## Backend Environment Variables

Electron sets these automatically when launching the packaged backend:

```text
ESEVA_APPDATA_ROOT   Writable app data root for keys, SQLite, and operations
ESEVA_CONFIGS_DIR    Folder containing service config JSON files
ESEVA_OPS_DIR        Legacy operations path alias
ESEVA_BIND_HOST      Backend bind host, default 127.0.0.1
ESEVA_BIND_PORT      Backend bind port, default 8765
ESEVA_LOG_LEVEL      Uvicorn log level, default info
TESSERACT_CMD        Full path to bundled or installed tesseract executable
TESSDATA_PREFIX      Tesseract language data folder
```

## Service Configurations

Service definitions live in `configs/*.json`. These files describe URL patterns, service names, and field mappings used by the browser extension and admin tooling.

In packaged mode, configs are copied from bundled resources into the writable user data folder on first launch:

```text
%APPDATA%\eseva-center\configs
```

This allows operators or admin tooling to update service mappings without modifying the installed application directory.

## Operation API

The operation API manages secure browser-extension sessions.

Common routes:

```text
POST /api/operations
GET  /api/operation/{operation_id}?token=...
POST /api/operation/{operation_id}/structured?token=...
POST /api/operation/{operation_id}/status?token=...
POST /api/operation/{operation_id}/unlock?token=...
```

Operation sessions use:

- `session_token` for read/write authorization
- `csrf_token` for mutating requests
- `locked_by_tab` to prevent two browser tabs from taking over the same operation

The SQLite table is initialized in `backend/app/db.py`, while runtime behavior lives in `backend/app/operations/store.py`.

## Document API

The document routes are mounted under:

```text
/api/documents
```

Common routes include operation listing, uploads, status checks, result download, raw extracted data reads, completion, and deletion. The desktop UI mostly works through these routes.

The readiness check used by Electron is:

```text
GET /api/documents/operations
```

## Building The Backend Executable

Build the FastAPI backend with PyInstaller from the repo root:

```powershell
python -m PyInstaller eseva-backend.spec --noconfirm
```

Expected output:

```text
dist\eseva-backend\eseva-backend.exe
dist\eseva-backend\_internal\...
```

Test the frozen backend:

```powershell
$env:ESEVA_APPDATA_ROOT = "D:\DocMagic\.test-appdata"
$env:ESEVA_CONFIGS_DIR = "D:\DocMagic\configs"
.\dist\eseva-backend\eseva-backend.exe
```

Then verify:

```powershell
Invoke-WebRequest http://127.0.0.1:8765/api/documents/operations
```

## Building The Windows Desktop App

This repository currently supports a folder-based Windows package using Electron Packager:

```powershell
npm run build:win-packager
```

That produces:

```text
dist-packaged\eSeva-Center-win32-x64\eSeva-Center.exe
```

Important: that `eSeva-Center.exe` is not standalone. It must be distributed with the entire folder:

```text
dist-packaged\eSeva-Center-win32-x64\
```

Copy or zip the whole folder when moving the app to another Windows machine.

If you use Electron Builder instead, ensure `dist/eseva-backend` is included as an extra resource. Electron expects the backend executable at one of these packaged locations:

```text
resources\eseva-backend\eseva-backend.exe
resources\resources\eseva-backend\eseva-backend.exe
```

The second path is supported for Electron Packager layouts that nest resources differently.

## Installing On Another Windows Machine

For a folder-based package:

1. Build or obtain the full `eSeva-Center-win32-x64` folder.
2. Copy the entire folder to the target machine.
3. Run `eSeva-Center.exe`.
4. Activate the license when prompted.
5. Install/load the Chrome extension from the copied folder:

```text
%USERPROFILE%\eSeva-Extension
```

Do not copy only `eSeva-Center.exe`; the app requires DLLs, `resources/`, `locales/`, the bundled backend, configs, UI, and extension files.

## License Server

The license server is a Cloudflare Workers app in `license-server/`. It issues and validates license keys using Cloudflare KV and HMAC-signed JWTs.

The Electron app currently points to:

```text
https://eseva-license.amurthy.workers.dev
```

See `license-server/README.md` for:

- Wrangler setup
- KV namespace creation
- secret configuration
- admin license issuing
- revoke/reactivate flows
- local Worker development

## Troubleshooting

### App is stuck on "Starting backend services" or "Loading OCR engine"

This usually means the backend process crashed before it bound to port `8765`.

Check the log:

```powershell
notepad "$env:APPDATA\eseva-center\logs\main.log"
```

Run the installed backend manually:

```powershell
cd "$env:LOCALAPPDATA\Programs\eSeva Center\resources\eseva-backend"
.\eseva-backend.exe
```

If the backend is healthy:

```powershell
Invoke-WebRequest http://127.0.0.1:8765/api/documents/operations
```

returns HTTP 200.

### `ModuleNotFoundError: No module named 'app.operations'`

The backend bundle is stale or incomplete. Rebuild it:

```powershell
python -m PyInstaller eseva-backend.spec --noconfirm
```

Then ensure the rebuilt `dist\eseva-backend` folder is copied into the packaged app's `resources\eseva-backend` folder.

### Only copying `eSeva-Center.exe` does not work

The Electron Packager output is folder-based. Copy the full `eSeva-Center-win32-x64` folder, not just the executable.

### Port 8765 does not respond

Check whether another process is using the port:

```powershell
netstat -ano | findstr :8765
```

Then inspect logs and manually run the backend executable.

## Git And Release Hygiene

Commit source, configs, scripts, and docs. Do not commit generated packages or local runtime data:

- `dist/`
- `build/`
- `dist-packaged/`
- extracted app folders
- installers and ZIPs
- logs
- SQLite databases
- local license caches
- temporary app-data folders

For release distribution, upload packaged ZIPs or installers as GitHub Releases rather than committing them to the repository.

## Quick Validation Checklist

After backend or packaging changes:

```powershell
python -m PyInstaller eseva-backend.spec --noconfirm

$env:ESEVA_APPDATA_ROOT = "D:\DocMagic\.test-appdata"
$env:ESEVA_CONFIGS_DIR = "D:\DocMagic\configs"
.\dist\eseva-backend\eseva-backend.exe
```

In another terminal:

```powershell
Invoke-WebRequest http://127.0.0.1:8765/api/documents/operations
```

Expected result:

```json
{"operations":[]}
```

Then run or package Electron and confirm the splash screen advances into the main UI.
