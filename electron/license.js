'use strict';

/**
 * License management for eSeva Center
 *
 * - Machine fingerprint: SHA-256 of hostname + platform + CPU model
 * - JWT cache: stored at userData/license.json
 * - Weekly re-validation: if token is older than 7 days, re-validate with server
 * - Grace period: if server unreachable, allow up to 8 days (JWT expiry)
 */

const crypto  = require('crypto');
const fs      = require('fs');
const os      = require('os');
const path    = require('path');

const LICENSE_SERVER = 'https://eseva-license.amurthy.workers.dev';
const REVALIDATE_INTERVAL_MS = 7 * 24 * 60 * 60 * 1000;   // 7 days

// ── Machine fingerprint ───────────────────────────────────────────────────────

function getMachineFingerprint() {
  const parts = [
    os.hostname(),
    os.platform(),
    os.arch(),
    // CPU model — reasonably unique per physical machine
    (os.cpus()[0] || {}).model || 'unknown-cpu',
  ];
  return crypto.createHash('sha256').update(parts.join('|')).digest('hex');
}

// ── Token cache (userData/license.json) ──────────────────────────────────────

function getLicensePath(userData) {
  return path.join(userData, 'license.json');
}

function loadCache(userData) {
  try {
    const raw = fs.readFileSync(getLicensePath(userData), 'utf8');
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function saveCache(userData, data) {
  fs.mkdirSync(userData, { recursive: true });
  fs.writeFileSync(getLicensePath(userData), JSON.stringify(data, null, 2), 'utf8');
}

// ── JWT expiry (decode without verifying — server verifies) ──────────────────

function jwtExpiry(token) {
  try {
    const payload = JSON.parse(
      Buffer.from(token.split('.')[1], 'base64').toString('utf8')
    );
    return payload.exp ? payload.exp * 1000 : null;
  } catch {
    return null;
  }
}

function jwtCenterName(token) {
  try {
    const payload = JSON.parse(
      Buffer.from(token.split('.')[1], 'base64').toString('utf8')
    );
    return payload.center || null;
  } catch {
    return null;
  }
}

// ── Network calls ─────────────────────────────────────────────────────────────

async function safeJson(res) {
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    // Server returned non-JSON (e.g. Cloudflare HTML error page or proxy error)
    throw new Error(`License server returned an unexpected response (HTTP ${res.status}). Please try again.`);
  }
}

async function activateOnServer(licenseKey, fingerprint) {
  const res = await fetch(`${LICENSE_SERVER}/activate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ license_key: licenseKey, machine_fingerprint: fingerprint }),
  });
  const data = await safeJson(res);
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;   // { success, token, center_name }
}

async function revalidateOnServer(token, fingerprint) {
  const res = await fetch(`${LICENSE_SERVER}/validate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token, machine_fingerprint: fingerprint }),
  });
  const data = await safeJson(res);
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;   // { success, token, center_name }
}

// ── Public API ────────────────────────────────────────────────────────────────

/**
 * checkLicense(userData)
 *
 * Returns:
 *   { status: 'valid',    centerName, token }   — licensed, proceed normally
 *   { status: 'activate' }                      — no valid cache, show activation screen
 *   { status: 'revoked',  error }               — server says revoked
 */
async function checkLicense(userData) {
  const fingerprint = getMachineFingerprint();
  const cache = loadCache(userData);

  if (cache && cache.token) {
    const expiry = jwtExpiry(cache.token);
    const now    = Date.now();

    // JWT not yet expired
    if (expiry && now < expiry) {
      const lastCheck = cache.lastValidated || 0;
      const needsRecheck = (now - lastCheck) > REVALIDATE_INTERVAL_MS;

      if (!needsRecheck) {
        // Still within 7-day window — no network call needed
        return { status: 'valid', centerName: cache.centerName, token: cache.token };
      }

      // Try weekly re-validation
      try {
        const result = await revalidateOnServer(cache.token, fingerprint);
        const updated = {
          token: result.token,
          centerName: result.center_name,
          lastValidated: now,
          activatedAt: cache.activatedAt,
        };
        saveCache(userData, updated);
        return { status: 'valid', centerName: result.center_name, token: result.token };
      } catch (err) {
        // Server said explicitly revoked/invalid — lock the app
        if (err.message.includes('revoked') || err.message.includes('Invalid')) {
          return { status: 'revoked', error: err.message };
        }
        // Network error — use cached token within grace period (still not expired)
        return { status: 'valid', centerName: cache.centerName, token: cache.token };
      }
    }

    // JWT expired — must re-validate (network required)
    try {
      const result = await revalidateOnServer(cache.token, fingerprint);
      const updated = {
        token: result.token,
        centerName: result.center_name,
        lastValidated: now,
        activatedAt: cache.activatedAt,
      };
      saveCache(userData, updated);
      return { status: 'valid', centerName: result.center_name, token: result.token };
    } catch (err) {
      if (err.message.includes('revoked') || err.message.includes('Invalid') || err.message.includes('expired')) {
        return { status: 'activate' };
      }
      // Network down + token expired → force activation
      return { status: 'activate' };
    }
  }

  // No cache at all → show activation screen
  return { status: 'activate' };
}

/**
 * activate(userData, licenseKey)
 * Called when operator submits the key from the activation screen.
 * Returns { success, centerName } or throws with a user-facing error message.
 */
async function activate(userData, licenseKey) {
  const fingerprint = getMachineFingerprint();
  const key = licenseKey.trim().toUpperCase();

  const result = await activateOnServer(key, fingerprint);

  const cache = {
    token: result.token,
    centerName: result.center_name,
    lastValidated: Date.now(),
    activatedAt: new Date().toISOString(),
  };
  saveCache(userData, cache);

  return { success: true, centerName: result.center_name };
}

module.exports = { checkLicense, activate, getMachineFingerprint };
