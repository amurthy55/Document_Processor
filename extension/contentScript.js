function getQueryParam(name) {
  try {
    return new URL(window.location.href).searchParams.get(name);
  } catch {
    return null;
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function getTabSessionId() {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ type: 'GET_TAB_SESSION_ID' }, (resp) => {
      if (!resp || !resp.ok) {
        resolve(null);
        return;
      }
      resolve(resp.tabSessionId);
    });
  });
}

async function getBoundOperation() {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ type: 'GET_BOUND_OPERATION' }, (resp) => {
      if (!resp || !resp.ok) {
        resolve(null);
        return;
      }
      resolve(resp.binding || null);
    });
  });
}

function setValue(el, value) {
  if (!el) return false;
  const tag = (el.tagName || '').toLowerCase();

  if (tag === 'input' || tag === 'textarea') {
    el.focus();
    el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  }

  if (tag === 'select') {
    el.focus();
    el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  }

  return false;
}

function findElementBySelector(selector) {
  if (!selector || typeof selector !== 'string') return null;
  try {
    return document.querySelector(selector);
  } catch {
    return null;
  }
}

async function injectOnce(structuredData, serviceConfig) {
  if (!structuredData || typeof structuredData !== 'object') return { filled: 0, attempted: 0 };
  const mappings = (serviceConfig && serviceConfig.field_mappings) ? serviceConfig.field_mappings : {};

  let attempted = 0;
  let filled = 0;

  for (const [fieldKey, mapping] of Object.entries(mappings)) {
    attempted += 1;
    const value = structuredData[fieldKey];
    if (value == null || value === '') continue;

    const selector = mapping && mapping.primary_selector ? mapping.primary_selector : null;
    const el = findElementBySelector(selector);
    if (!el) continue;

    if (setValue(el, String(value))) {
      filled += 1;
    }
  }

  return { filled, attempted };
}

async function fetchOperation(opId, token, tabSessionId) {
  const baseUrl = 'http://127.0.0.1:8765';
  const url = `${baseUrl}/api/operation/${encodeURIComponent(opId)}?token=${encodeURIComponent(token)}`;
  const resp = await fetch(url, {
    method: 'GET',
    headers: tabSessionId ? { 'X-Tab-Session-Id': tabSessionId } : {},
  });

  if (!resp.ok) {
    const text = await resp.text().catch(() => '');
    throw new Error(`operation_fetch_failed:${resp.status}:${text}`);
  }
  return await resp.json();
}

async function main() {
  if (window.location.href.startsWith('chrome-extension://')) return;

  let opId = getQueryParam('op');
  let token = getQueryParam('token');

  if (!opId || !token) {
    const binding = await getBoundOperation();
    if (!binding) return;
    opId = binding.operationId;
    token = binding.sessionToken;
    if (!opId || !token) return;
  }

  const tabSessionId = await getTabSessionId();
  if (!tabSessionId) return;

  let payload;
  try {
    payload = await fetchOperation(opId, token, tabSessionId);
  } catch {
    return;
  }

  const structuredData = payload.structured_data || {};
  const serviceConfig = payload.service_config || {};

  await injectOnce(structuredData, serviceConfig);

  const observer = new MutationObserver(async () => {
    observer.disconnect();
    await sleep(200);
    await injectOnce(structuredData, serviceConfig);
    observer.observe(document.documentElement, { childList: true, subtree: true });
  });

  observer.observe(document.documentElement, { childList: true, subtree: true });
}

main();
