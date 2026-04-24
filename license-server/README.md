# eSeva License Server

Cloudflare Workers-based license validation server for eSeva Center desktop app.

## Architecture

- **Runtime:** Cloudflare Workers (free tier covers ~100k requests/day)
- **Storage:** Cloudflare KV (key-value store, free tier: 100k reads/day)
- **Auth:** HMAC-SHA256 signed JWTs (8-day expiry, weekly refresh)
- **Machine binding:** License tied to hardware fingerprint on first activation

## One-time Setup

### 1. Install Wrangler CLI

```bash
cd license-server
npm install
npx wrangler login        # opens browser to authenticate with your Cloudflare account
```

### 2. Create the KV namespace

```bash
npm run kv:create          # creates production KV namespace
npm run kv:create-preview  # creates preview KV namespace (for local dev)
```

Copy the IDs printed and paste them into `wrangler.toml`:
```toml
[[kv_namespaces]]
binding = "LICENSES"
id = "PASTE_PRODUCTION_ID_HERE"
preview_id = "PASTE_PREVIEW_ID_HERE"
```

### 3. Set secrets (never commit these)

```bash
npx wrangler secret put JWT_SECRET
# enter a long random string, e.g.: openssl rand -hex 32

npx wrangler secret put ADMIN_SECRET
# enter a strong admin password — this protects all /admin/* endpoints
```

### 4. Deploy

```bash
npm run deploy
# → deployed to https://eseva-license.<your-subdomain>.workers.dev
```

Test it:
```bash
curl https://eseva-license.<your-subdomain>.workers.dev/health
# {"status":"ok","service":"eseva-license"}
```

---

## Admin API

All `/admin/*` endpoints require:
```
Authorization: Bearer <ADMIN_SECRET>
```

### Issue a new license key

```bash
curl -X POST https://eseva-license.<subdomain>.workers.dev/admin/issue \
  -H "Authorization: Bearer <ADMIN_SECRET>" \
  -H "Content-Type: application/json" \
  -d '{
    "center_name": "MeSeva Center - T Nagar, Chennai",
    "contact_email": "operator@tnagar-meseva.in",
    "notes": "Issued 2025-03-01"
  }'
```

Response:
```json
{
  "success": true,
  "license_key": "ESEVA-A1B2-C3D4-E5F6",
  "record": { ... }
}
```

Send the `license_key` to the operator — they enter it on first launch.

### List all licenses

```bash
curl https://eseva-license.<subdomain>.workers.dev/admin/list \
  -H "Authorization: Bearer <ADMIN_SECRET>"
```

### Get info on a specific license

```bash
curl "https://eseva-license.<subdomain>.workers.dev/admin/info?key=ESEVA-A1B2-C3D4-E5F6" \
  -H "Authorization: Bearer <ADMIN_SECRET>"
```

### Revoke a license (operator stops paying / misuse)

```bash
curl -X POST https://eseva-license.<subdomain>.workers.dev/admin/revoke \
  -H "Authorization: Bearer <ADMIN_SECRET>" \
  -H "Content-Type: application/json" \
  -d '{ "license_key": "ESEVA-A1B2-C3D4-E5F6" }'
```

The app will reject the license within 8 days (next weekly re-check).

### Reactivate / transfer to new machine

```bash
curl -X POST https://eseva-license.<subdomain>.workers.dev/admin/reactivate \
  -H "Authorization: Bearer <ADMIN_SECRET>" \
  -H "Content-Type: application/json" \
  -d '{
    "license_key": "ESEVA-A1B2-C3D4-E5F6",
    "reset_fingerprint": true
  }'
```

`reset_fingerprint: true` clears the machine binding so the operator can activate
on a new machine (e.g. after replacing a PC).

---

## App API (called by Electron)

### First-time activation

```
POST /activate
{ "license_key": "ESEVA-A1B2-C3D4-E5F6", "machine_fingerprint": "<hash>" }
```

Returns a signed JWT stored locally by the app.

### Weekly re-validation

```
POST /validate
{ "token": "<stored_jwt>", "machine_fingerprint": "<hash>" }
```

Returns a fresh JWT. App caches this and does NOT call the server on every launch —
only when the cached token is older than 7 days.

---

## Local Development

```bash
npm run dev
# Worker runs at http://localhost:8787
```

---

## KV Data Structure

Each license is stored as:
- **Key:** `license:ESEVA-A1B2-C3D4-E5F6`
- **Value:**
```json
{
  "center_name": "MeSeva Center - T Nagar",
  "contact_email": "operator@example.com",
  "machine_fingerprint": "sha256-of-hardware-ids",
  "active": true,
  "created_at": "2025-03-01T10:00:00Z",
  "activated_at": "2025-03-02T08:30:00Z",
  "notes": "Issued batch 1"
}
```
