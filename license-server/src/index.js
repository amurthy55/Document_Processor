/**
 * eSeva License Server — Cloudflare Workers
 *
 * KV schema (binding: LICENSES):
 *   key:   "license:<LICENSE_KEY>"
 *   value: JSON {
 *     center_name: string,
 *     contact_email: string,
 *     machine_fingerprint: string | null,   // null = not yet activated
 *     active: boolean,
 *     created_at: string,                   // ISO date
 *     activated_at: string | null,
 *     notes: string
 *   }
 *
 * Endpoints:
 *   POST /validate        — app calls on startup / weekly re-check
 *   POST /activate        — app calls on first-time activation
 *   POST /admin/issue     — you call to create a new license key
 *   POST /admin/revoke    — you call to deactivate a license
 *   GET  /admin/list      — you call to list all licenses
 *   GET  /health          — sanity check
 *
 * Auth:
 *   /admin/* requires header:  Authorization: Bearer <ADMIN_SECRET>
 *   /validate and /activate are public (protected by license key + JWT)
 */

// ── Helpers ───────────────────────────────────────────────────────────────────

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function err(message, status = 400) {
  return json({ success: false, error: message }, status);
}

// Simple HMAC-SHA256 JWT (HS256) using Web Crypto API (available in Workers)
async function signJWT(payload, secret) {
  const header  = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' })).replace(/=+$/, '');
  const body    = btoa(JSON.stringify(payload)).replace(/=+$/, '');
  const message = `${header}.${body}`;

  const key = await crypto.subtle.importKey(
    'raw',
    new TextEncoder().encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const sig = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(message));
  const sigB64 = btoa(String.fromCharCode(...new Uint8Array(sig)))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

  return `${message}.${sigB64}`;
}

async function verifyJWT(token, secret) {
  try {
    const [header, body, sig] = token.split('.');
    const message = `${header}.${body}`;

    const key = await crypto.subtle.importKey(
      'raw',
      new TextEncoder().encode(secret),
      { name: 'HMAC', hash: 'SHA-256' },
      false,
      ['verify'],
    );
    const sigBytes = Uint8Array.from(atob(sig.replace(/-/g, '+').replace(/_/g, '/')), c => c.charCodeAt(0));
    const valid = await crypto.subtle.verify('HMAC', key, sigBytes, new TextEncoder().encode(message));
    if (!valid) return null;

    const payload = JSON.parse(atob(body));
    if (payload.exp && Date.now() / 1000 > payload.exp) return null;
    return payload;
  } catch {
    return null;
  }
}

function generateLicenseKey() {
  // Format: ESEVA-XXXX-XXXX-XXXX  (easy to read, type, communicate)
  const seg = () => crypto.randomUUID().replace(/-/g, '').slice(0, 4).toUpperCase();
  return `ESEVA-${seg()}-${seg()}-${seg()}`;
}

function getAdminSecret(env) {
  return env.ADMIN_SECRET || 'change-me-admin-secret';
}

function checkAdmin(request, env) {
  const auth = request.headers.get('Authorization') || '';
  const token = auth.startsWith('Bearer ') ? auth.slice(7) : '';
  return token === getAdminSecret(env);
}

// ── Route handlers ────────────────────────────────────────────────────────────

/**
 * POST /activate
 * Body: { license_key, machine_fingerprint }
 * First-time activation: binds the key to the machine fingerprint.
 * Returns a signed JWT valid for 8 days (app re-validates weekly).
 */
async function handleActivate(request, env) {
  let body;
  try { body = await request.json(); } catch { return err('Invalid JSON'); }

  const { license_key, machine_fingerprint } = body;
  if (!license_key || !machine_fingerprint) {
    return err('license_key and machine_fingerprint are required');
  }

  const record = await env.LICENSES.get(`license:${license_key}`, { type: 'json' });
  if (!record) return err('Invalid license key', 404);
  if (!record.active) return err('License has been deactivated', 403);

  // If already bound to a DIFFERENT machine, reject.
  // Same machine re-activating (e.g. after cache deletion) is allowed.
  if (record.machine_fingerprint && record.machine_fingerprint !== machine_fingerprint) {
    return err('License is already activated on a different machine. Contact support to transfer.', 403);
  }

  // Bind machine on first activation (or re-bind same machine)
  if (!record.machine_fingerprint || record.machine_fingerprint === machine_fingerprint) {
    record.machine_fingerprint = machine_fingerprint;
    if (!record.activated_at) record.activated_at = new Date().toISOString();
    await env.LICENSES.put(`license:${license_key}`, JSON.stringify(record));
  }

  const now = Math.floor(Date.now() / 1000);
  const token = await signJWT(
    {
      sub: license_key,
      center: record.center_name,
      fingerprint: machine_fingerprint,
      iat: now,
      exp: now + 8 * 24 * 3600,   // 8 days
    },
    env.JWT_SECRET,
  );

  return json({
    success: true,
    token,
    center_name: record.center_name,
    message: `Activated for ${record.center_name}`,
  });
}

/**
 * POST /validate
 * Body: { token, machine_fingerprint }
 * Weekly re-validation. Checks token signature + expiry + machine match + still active.
 * Returns a fresh token if valid.
 */
async function handleValidate(request, env) {
  let body;
  try { body = await request.json(); } catch { return err('Invalid JSON'); }

  const { token, machine_fingerprint } = body;
  if (!token || !machine_fingerprint) {
    return err('token and machine_fingerprint are required');
  }

  const payload = await verifyJWT(token, env.JWT_SECRET);
  if (!payload) return err('Invalid or expired token. Please re-activate.', 401);

  if (payload.fingerprint !== machine_fingerprint) {
    return err('Machine fingerprint mismatch.', 403);
  }

  // Double-check the license is still active in KV (catches revocations)
  const record = await env.LICENSES.get(`license:${payload.sub}`, { type: 'json' });
  if (!record || !record.active) {
    return err('License has been revoked. Contact support.', 403);
  }

  // Issue a fresh 8-day token
  const now = Math.floor(Date.now() / 1000);
  const newToken = await signJWT(
    {
      sub: payload.sub,
      center: record.center_name,
      fingerprint: machine_fingerprint,
      iat: now,
      exp: now + 8 * 24 * 3600,
    },
    env.JWT_SECRET,
  );

  return json({
    success: true,
    token: newToken,
    center_name: record.center_name,
  });
}

/**
 * POST /admin/issue
 * Body: { center_name, contact_email, notes? }
 * Creates a new license key (unactivated).
 */
async function handleAdminIssue(request, env) {
  let body;
  try { body = await request.json(); } catch { return err('Invalid JSON'); }

  const { center_name, contact_email, notes = '' } = body;
  if (!center_name || !contact_email) {
    return err('center_name and contact_email are required');
  }

  const license_key = generateLicenseKey();
  const record = {
    center_name,
    contact_email,
    machine_fingerprint: null,
    active: true,
    created_at: new Date().toISOString(),
    activated_at: null,
    notes,
  };

  await env.LICENSES.put(`license:${license_key}`, JSON.stringify(record));

  return json({ success: true, license_key, record }, 201);
}

/**
 * POST /admin/revoke
 * Body: { license_key }
 * Deactivates a license. App will fail validation within 8 days (next re-check).
 */
async function handleAdminRevoke(request, env) {
  let body;
  try { body = await request.json(); } catch { return err('Invalid JSON'); }

  const { license_key } = body;
  if (!license_key) return err('license_key is required');

  const record = await env.LICENSES.get(`license:${license_key}`, { type: 'json' });
  if (!record) return err('License not found', 404);

  record.active = false;
  await env.LICENSES.put(`license:${license_key}`, JSON.stringify(record));

  return json({ success: true, message: `License ${license_key} revoked` });
}

/**
 * POST /admin/reactivate
 * Body: { license_key, reset_fingerprint? }
 * Re-activates a revoked license. Optionally clears the machine binding
 * (needed when transferring to a new machine).
 */
async function handleAdminReactivate(request, env) {
  let body;
  try { body = await request.json(); } catch { return err('Invalid JSON'); }

  const { license_key, reset_fingerprint = false } = body;
  if (!license_key) return err('license_key is required');

  const record = await env.LICENSES.get(`license:${license_key}`, { type: 'json' });
  if (!record) return err('License not found', 404);

  record.active = true;
  if (reset_fingerprint) {
    record.machine_fingerprint = null;
    record.activated_at = null;
  }
  await env.LICENSES.put(`license:${license_key}`, JSON.stringify(record));

  return json({ success: true, message: `License ${license_key} reactivated`, record });
}

/**
 * GET /admin/list
 * Returns all licenses with their status.
 */
async function handleAdminList(env) {
  const { keys } = await env.LICENSES.list({ prefix: 'license:' });
  const licenses = await Promise.all(
    keys.map(async ({ name }) => {
      const record = await env.LICENSES.get(name, { type: 'json' });
      return { license_key: name.replace('license:', ''), ...record };
    }),
  );
  return json({ success: true, count: licenses.length, licenses });
}

/**
 * GET /admin/info?key=ESEVA-XXXX-XXXX-XXXX
 */
async function handleAdminInfo(request, env) {
  const url = new URL(request.url);
  const license_key = url.searchParams.get('key');
  if (!license_key) return err('key query parameter required');

  const record = await env.LICENSES.get(`license:${license_key}`, { type: 'json' });
  if (!record) return err('License not found', 404);

  return json({ success: true, license_key, ...record });
}

// ── Main fetch handler ────────────────────────────────────────────────────────

export default {
  async fetch(request, env) {
    const url    = new URL(request.url);
    const path   = url.pathname;
    const method = request.method;

    // CORS preflight
    if (method === 'OPTIONS') {
      return new Response(null, {
        headers: {
          'Access-Control-Allow-Origin': '*',
          'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
          'Access-Control-Allow-Headers': 'Content-Type, Authorization',
        },
      });
    }

    // Health check
    if (path === '/health' && method === 'GET') {
      return json({ status: 'ok', service: 'eseva-license' });
    }

    // Public endpoints
    if (path === '/activate' && method === 'POST') return handleActivate(request, env);
    if (path === '/validate' && method === 'POST') return handleValidate(request, env);

    // Admin endpoints — require Bearer token
    if (path.startsWith('/admin/')) {
      if (!checkAdmin(request, env)) {
        return err('Unauthorized', 401);
      }
      if (path === '/admin/issue'      && method === 'POST') return handleAdminIssue(request, env);
      if (path === '/admin/revoke'     && method === 'POST') return handleAdminRevoke(request, env);
      if (path === '/admin/reactivate' && method === 'POST') return handleAdminReactivate(request, env);
      if (path === '/admin/list'       && method === 'GET')  return handleAdminList(env);
      if (path === '/admin/info'       && method === 'GET')  return handleAdminInfo(request, env);
      return err('Not found', 404);
    }

    return err('Not found', 404);
  },
};
