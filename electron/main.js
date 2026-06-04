'use strict';

const { app, BrowserWindow, ipcMain, shell, dialog, Menu } = require('electron');
const path = require('path');
const { spawn, execFile } = require('child_process');
const fs = require('fs');
const log = require('electron-log');
const { checkLicense, activate: activateLicense } = require('./license');

log.transports.file.level = 'info';
log.transports.console.level = 'debug';

// ── Constants ────────────────────────────────────────────────────────────────
const IS_DEV = process.argv.includes('--dev');
const IS_WIN = process.platform === 'win32';
const IS_MAC = process.platform === 'darwin';
const BACKEND_PORT = 8765;
const API_BASE = "http://127.0.0.1:" + BACKEND_PORT;

// Resource paths — differ between dev and packaged app
const APP_ROOT = app.isPackaged
  ? path.join(process.resourcesPath)
  : path.join(__dirname, '..');

// Prevent multiple instances racing for the same backend port.
const gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotSingleInstanceLock) {
  app.quit();
}

// User data (DB, keys, writable configs) — never bundled
const USER_DATA = app.getPath('userData');
// Operations folder is visible in home dir so operators can easily find output files
const OPS_DIR = path.join(app.getPath('home'), 'eSeva-Operations');

const BACKEND_DIR   = path.join(APP_ROOT, 'backend');
const CONFIGS_SRC   = path.join(APP_ROOT, 'configs');   // read-only inside AppImage
const CONFIGS_DIR   = app.isPackaged
  ? path.join(USER_DATA, 'configs')                      // writable copy in userData
  : path.join(APP_ROOT, 'configs');                      // dev: use repo directly
const UI_DIR        = path.join(APP_ROOT, 'ui');

// Extension is bundled inside the AppImage (read-only mount) — copy it to
// ~/eSeva-Extension so Chrome's file picker can navigate to it (Chrome hides
// ~/.config and similar dotfile paths in its folder picker).
const EXTENSION_SRC  = path.join(APP_ROOT, 'extension');        // inside AppImage
const EXTENSION_DIR  = app.isPackaged
  ? path.join(app.getPath('home'), 'eSeva-Extension')  // visible, Chrome-accessible
  : path.join(APP_ROOT, 'extension');                  // dev: use repo directly

function ensureExtensionCopied() {
  if (!app.isPackaged) return;   // dev mode — no copy needed
  try {
    // Always sync extension files in packaged mode.
    // Manifest version may stay the same while scripts change during hotfix builds.
    fs.cpSync(EXTENSION_SRC, EXTENSION_DIR, { recursive: true, force: true });
    log.info(`[extension] Synced to: ${EXTENSION_DIR}`);
  } catch (err) {
    log.error(`[extension] Failed to sync: ${err.message}`);
  }
}

function ensureConfigsCopied() {
  if (!app.isPackaged) return;   // dev mode — use repo configs directly
  // Create writable configs dir if missing
  if (!fs.existsSync(CONFIGS_DIR)) {
    fs.mkdirSync(CONFIGS_DIR, { recursive: true });
    // Seed from bundled read-only configs (only on first install)
    if (fs.existsSync(CONFIGS_SRC)) {
      try {
        fs.cpSync(CONFIGS_SRC, CONFIGS_DIR, { recursive: true });
        log.info(`[configs] Seeded writable configs from bundle: ${CONFIGS_DIR}`);
      } catch (err) {
        log.warn(`[configs] Could not seed configs: ${err.message}`);
      }
    }
  }
}

// ── Backend executable resolution ────────────────────────────────────────────
// Packaged: use the PyInstaller-frozen binary in resources/eseva-backend/
// Dev:      use the project .venv python with uvicorn
function getBackendCommand() {
  if (app.isPackaged) {
    // Try multiple possible executable names
    const possibleNames = IS_WIN ? 
      ['eseva-backend.exe', 'eseva-backend'] : 
      ['eseva-backend'];
    
    for (const exeName of possibleNames) {
      const frozenExe = path.join(APP_ROOT, 'eseva-backend', exeName);
      if (fs.existsSync(frozenExe)) {
        return { exe: frozenExe, args: [], cwd: path.dirname(frozenExe), frozen: true };
      }
    }

    // Try resources/eseva-backend path (electron-packager structure)
    for (const exeName of possibleNames) {
      const frozenExe = path.join(APP_ROOT, 'resources', 'eseva-backend', exeName);
      if (fs.existsSync(frozenExe)) {
        return { exe: frozenExe, args: [], cwd: path.dirname(frozenExe), frozen: true };
      }
    }

    log.warn('[backend] PyInstaller binary not found, falling back to python');
  }

  // Dev: venv python
  const venvPy = IS_WIN
    ? path.join(APP_ROOT, '.venv', 'Scripts', 'python.exe')
    : path.join(APP_ROOT, '.venv', 'bin', 'python3');
  const python = fs.existsSync(venvPy) ? venvPy : (IS_WIN ? 'python' : 'python3');
  return {
    exe: python,
    args: ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(BACKEND_PORT)],
    cwd: BACKEND_DIR,
    frozen: false,
  };
}

// ── Tesseract env vars (for both packaged and dev when TESSERACT_CMD not set) ─
function buildTesseractEnv() {
  const env = {};
  // In packaged mode, point at the bundled tesseract binary
  if (app.isPackaged) {
    const tessExe = IS_WIN
      ? path.join(APP_ROOT, 'tesseract', 'tesseract.exe')
      : path.join(APP_ROOT, 'tesseract', 'tesseract');
    if (fs.existsSync(tessExe)) {
      env.TESSERACT_CMD = tessExe;
      env.TESSDATA_PREFIX = path.join(APP_ROOT, 'tesseract', 'tessdata');
      log.info(`[tesseract] Using bundled: ${tessExe}`);
    }
  }
  return env;
}

// ── Backend process management ───────────────────────────────────────────────
let backendProcess = null;
let _backendStopping = false;   // set true on intentional quit to suppress auto-restart
const RESTART_DELAY_MS = 2000;
const MAX_RESTART_ATTEMPTS = 5;
let restartAttempts = 0;

function startBackend() {
  if (restartAttempts >= MAX_RESTART_ATTEMPTS) {
    log.error(`[backend] Max restart attempts (${MAX_RESTART_ATTEMPTS}) reached. Stopping.`);
    _backendStopping = true;
    return;
  }

  _backendStopping = false;
  restartAttempts++;
  const { exe, args, cwd, frozen } = getBackendCommand();
  log.info(`[backend] Starting ${frozen ? 'frozen' : 'dev'} backend (attempt ${restartAttempts}/${MAX_RESTART_ATTEMPTS}): ${exe}`);

  const env = {
    ...process.env,
    PYTHONUNBUFFERED: '1',
    ESEVA_APPDATA_ROOT: USER_DATA,
    ESEVA_CONFIGS_DIR: CONFIGS_DIR,
    ESEVA_OPS_DIR: OPS_DIR,
    ...buildTesseractEnv(),
  };

  // Clear stale packaged backend processes from older AppImage mounts.
  if (app.isPackaged && process.platform === 'linux') {
    execFile('pkill', ['-f', '/resources/eseva-backend/eseva-backend'], (err) => {
      if (err) log.debug('[backend] pre-start pkill:', err.message);
      backendProcess = spawn(exe, args, { cwd, env, stdio: ['ignore', 'pipe', 'pipe'] });
      attachBackendHandlers();
    });
    return;
  }

  backendProcess = spawn(exe, args, { cwd, env, stdio: ['ignore', 'pipe', 'pipe'] });
  attachBackendHandlers();
}

function attachBackendHandlers() {
  if (!backendProcess) return;
  
  let startupCompleted = false;
  function markStartupComplete(output) {
    if (
      !startupCompleted &&
      (output.includes('Application startup complete') ||
        output.includes('Uvicorn running on'))
    ) {
      startupCompleted = true;
      clearTimeout(startupTimeout);
      restartAttempts = 0; // Reset counter on successful start
      log.info('[backend] Startup completed successfully');
    }
  }

  const startupTimeout = setTimeout(() => {
    if (!startupCompleted) {
      log.error('[backend] Startup timeout - backend may be hanging');
      backendProcess.kill('SIGKILL');
    }
  }, 60000); // 60 second timeout

  backendProcess.stdout.on('data', d => {
    const output = d.toString().trim();
    log.info(`[uvicorn] ${output}`);
    markStartupComplete(output);
  });
  
  backendProcess.stderr.on('data', d => {
    const error = d.toString().trim();
    log.warn(`[uvicorn] ${error}`);
    markStartupComplete(error);
    
    // Check for critical errors that require immediate stop
    if (error.includes('Permission denied') ||
        error.includes('Address already in use') ||
        error.includes('No such file or directory')) {
      log.error(`[backend] Critical error detected: ${error}`);
      clearTimeout(startupTimeout);
      backendProcess.kill('SIGKILL');
    }
  });

  backendProcess.on('exit', (code, signal) => {
    clearTimeout(startupTimeout);
    log.warn(`[backend] exited — code=${code} signal=${signal}`);
    backendProcess = null;
    
    // Auto-restart unless we are shutting down intentionally or max attempts reached
    if (!_backendStopping && restartAttempts < MAX_RESTART_ATTEMPTS) {
      log.info(`[backend] Restarting in ${RESTART_DELAY_MS}ms…`);
      setTimeout(startBackend, RESTART_DELAY_MS);
    } else if (restartAttempts >= MAX_RESTART_ATTEMPTS) {
      log.error('[backend] Max restart attempts reached - giving up');
    }
  });
}

function stopBackend() {
  if (_backendStopping) return;
  _backendStopping = true;
  if (backendProcess) {
    log.info('[backend] Stopping...');
    // Try graceful shutdown first
    backendProcess.kill('SIGTERM');
    // Force kill after 3 seconds if still running
    setTimeout(() => {
      if (backendProcess && !backendProcess.killed) {
        log.warn('[backend] Force killing backend...');
        backendProcess.kill('SIGKILL');
      }
    }, 3000);
    backendProcess = null;
  }
  // Also kill any lingering eseva-backend processes (cleanup)
  if (process.platform === 'linux') {
    execFile('pkill', ['-f', 'eseva-backend'], (err) => {
      if (err) log.debug('[backend] pkill cleanup:', err.message);
    });
  }
}

// ── Wait for backend to be ready ─────────────────────────────────────────────
// Frozen PyInstaller binary can take 30-60s on first cold start (unpacking libs).
async function waitForBackend(timeoutMs = 90000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const res = await fetch(`${API_BASE}/api/documents/operations`);
      if (res.ok || res.status === 404) return true;
    } catch (_) {}
    // Update splash status text every ~5s so the user knows it's still loading
    const elapsed = Math.round((Date.now() - start) / 1000);
    if (mainWindow && elapsed % 5 === 0 && elapsed > 0) {
      mainWindow.webContents.executeJavaScript(
        `document.querySelector && (el => el && (el.textContent = 'Starting backend… (${elapsed}s)'))(document.getElementById('status'))`,
      ).catch(() => {});
    }
    await new Promise(r => setTimeout(r, 500));
  }
  return false;
}

// ── License activation window ────────────────────────────────────────────────
let activationWindow = null;
let _activationResolve = null;

// Registered at module level — always available before the window loads,
// and survives multiple attempts (wrong key → retry without losing the handler).
ipcMain.handle('license-activate', async (_, key) => {
  try {
    const result = await activateLicense(USER_DATA, key);
    return result;   // { success, centerName }
  } catch (err) {
    return { success: false, error: err.message };
  }
});

ipcMain.on('license-activation-complete', (_, centerName) => {
  log.info(`[license] Activated for: ${centerName}`);
  if (activationWindow) { activationWindow.close(); activationWindow = null; }
  if (_activationResolve) { _activationResolve({ activated: true, centerName }); _activationResolve = null; }
});

function showActivationWindow() {
  return new Promise((resolve) => {
    _activationResolve = resolve;

    activationWindow = new BrowserWindow({
      width: 560,
      height: 620,
      resizable: false,
      center: true,
      title: 'eSeva Center — Activate',
      webPreferences: {
        preload: path.join(__dirname, 'preload.js'),
        contextIsolation: true,
        nodeIntegration: false,
      },
    });

    activationWindow.setMenuBarVisibility(false);
    activationWindow.loadFile(path.join(__dirname, 'activate.html'));

    activationWindow.on('closed', () => {
      activationWindow = null;
      if (_activationResolve) { _activationResolve({ activated: false }); _activationResolve = null; }
    });
  });
}

// ── Window ───────────────────────────────────────────────────────────────────
let mainWindow = null;

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 880,
    minWidth: 900,
    minHeight: 600,
    title: ' DocMagic 🪄',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: true,
    },
    show: false,
  });

  // ── License check (skip in dev mode for convenience) ──────────────────────
  if (app.isPackaged) {
    const licenseStatus = await checkLicense(USER_DATA);
    log.info(`[license] status=${licenseStatus.status}`);

    if (licenseStatus.status === 'activate') {
      const result = await showActivationWindow();
      if (!result.activated) {
        app.quit();
        return;
      }
    } else if (licenseStatus.status === 'revoked') {
      dialog.showErrorBox(
        'License Revoked',
        `Your license has been deactivated.\n\n${licenseStatus.error}\n\nContact your administrator.`,
      );
      app.quit();
      return;
    }
    // status === 'valid' → fall through
  }

  // Load a splash while backend starts
  mainWindow.loadFile(path.join(__dirname, 'splash.html'));
  mainWindow.show();

  // Build native menu
  buildMenu();

  // Start backend then load the real UI
  startBackend();
  const ready = await waitForBackend();
  if (!ready) {
    // Backend failed to start - check if it's due to max restart attempts
    if (restartAttempts >= MAX_RESTART_ATTEMPTS) {
      log.error('[backend] Failed to start after maximum attempts');
      if (mainWindow) {
        const errorMessage = "document.body.innerHTML = '<div style=\"display:flex;align-items:center;justify-content:center;height:100vh;background:linear-gradient(135deg,#dc2626,#991b1b);\"><div style=\"background:white;padding:40px;border-radius:14px;text-align:center;box-shadow:0 20px 40px rgba(0,0,0,0.12);max-width:500px;\"><div style=\"font-size:2.5em;margin-bottom:16px;\">❌</div><h2 style=\"color:#dc2626;margin-bottom:12px;\">Backend Failed to Start</h2><p style=\"color:#6b7280;margin-bottom:20px;font-size:0.9em;\">The backend service failed to start after " + MAX_RESTART_ATTEMPTS + " attempts.</p><div style=\"background:#f3f4f6;padding:15px;border-radius:7px;margin:15px 0;\"><h3 style=\"margin:0 0 10px 0;color:#374151;\">Possible Solutions:</h3><ul style=\"text-align:left;margin:0;padding-left:20px;color:#6b7280;\"><li>Restart as Administrator</li><li>Check if another eSeva Center is already running</li><li>Ensure port 8765 is not blocked by antivirus/firewall</li><li>Reinstall application</li></ul></div><button onclick=\"location.reload()\" style=\"background:#dc2626;color:white;border:none;padding:10px 24px;border-radius:7px;cursor:pointer;font-size:0.9em;font-weight:600;\">🔄 Retry</button></div></div>';";
        mainWindow.webContents.executeJavaScript(errorMessage).catch(() => {});
      }
    } else {
      // Backend is taking longer than expected
      log.warn('[backend] Did not respond within timeout — continuing anyway');
      if (mainWindow) {
        mainWindow.webContents.executeJavaScript(
          "document.querySelector && (el => el && (el.textContent = 'Backend is taking longer than expected'))(document.getElementById('status'))",
        ).catch(() => {});
      }
    }
  }

  mainWindow.loadFile(path.join(UI_DIR, 'index.html'));

  mainWindow.on('closed', () => { mainWindow = null; });

  if (IS_DEV) mainWindow.webContents.openDevTools();
}

// ── IPC handlers ─────────────────────────────────────────────────────────────
ipcMain.handle('get-api-base', () => API_BASE);
ipcMain.handle('get-extension-dir', () => EXTENSION_DIR);
ipcMain.handle('get-ops-dir', () => OPS_DIR);

ipcMain.handle('open-file-dialog', async (_, options) => {
  const result = await dialog.showOpenDialog(mainWindow, options || {
    properties: ['openFile', 'multiSelections'],
    filters: [
      { name: 'Documents', extensions: ['pdf', 'jpg', 'jpeg', 'png'] },
    ],
  });
  return result;
});

ipcMain.handle('show-item-in-folder', (_, filePath) => {
  shell.showItemInFolder(filePath);
});

ipcMain.handle('open-extension-dir', () => {
  shell.openPath(EXTENSION_DIR);
});

ipcMain.handle('open-operations-dir', () => {
  fs.mkdirSync(OPS_DIR, { recursive: true });
  shell.openPath(OPS_DIR);
});

// ── App lifecycle ─────────────────────────────────────────────────────────────
app.whenReady().then(() => {
  if (!gotSingleInstanceLock) return;
  ensureExtensionCopied();
  ensureConfigsCopied();
  createWindow();
});

app.on('second-instance', () => {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  }
});

app.on('window-all-closed', () => {
  stopBackend();
  if (!IS_MAC) app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

app.on('before-quit', stopBackend);

// ── Application menu ──────────────────────────────────────────────────────────
function buildMenu() {
  const template = [
    {
      label: 'File',
      submenu: [
        {
          label: 'Open Extension Folder',
          click: () => shell.openPath(EXTENSION_DIR),
        },
        { type: 'separator' },
        { role: 'quit' },
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
    {
      label: 'Help',
      submenu: [
        {
          label: 'View Logs',
          click: () => shell.openPath(log.transports.file.getFile().path),
        },
        {
          label: 'Backend API Docs',
          click: () => shell.openExternal(`${API_BASE}/docs`),
        },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}
