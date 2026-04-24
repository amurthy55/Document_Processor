/**
 * eSevaCenter Extension - Background Service Worker
 * 
 * Responsibilities:
 * - Listen for messages from content scripts
 * - Manage operation state
 * - Communicate with localhost API
 * - Manage active operation context
 */

const API_BASE = "http://127.0.0.1:8765/api";

// Track sessions and operations
const tabSessionIds = new Map();
const tabBindings = new Map(); // key: tabId OR URL, value: { operationId, sessionToken }
const urlBindings = new Map(); // key: URL, value: { operationId, sessionToken }
let activeOperation = null;

/**
 * Generate unique tab session ID
 */
function newSessionId() {
  const buf = new Uint8Array(16);
  crypto.getRandomValues(buf);
  return Array.from(buf)
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}

/**
 * Main message listener
 */
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || typeof message !== 'object') return;

  const tabId = sender?.tab?.id;

  if (message.type === 'GET_TAB_SESSION_ID') {
    if (tabId == null) {
      sendResponse({ ok: false, error: 'no_tab_context' });
      return;
    }
    let id = tabSessionIds.get(tabId);
    if (!id) {
      id = newSessionId();
      tabSessionIds.set(tabId, id);
    }
    sendResponse({ ok: true, tabSessionId: id });
    return;
  }

  if (message.type === 'FETCH_ACTIVE_OPERATION') {
    const tabSessionId = tabSessionIds.get(tabId);
    fetchActiveOperationFromAPI(tabSessionId)
      .then((op) => {
        activeOperation = op;
        if (tabId) {
          tabBindings.set(tabId, {
            operationId: op.operation_id,
            sessionToken: op.session_token,
            csrfToken: op.csrf_token,
          });
        }
        sendResponse({
          ok: true,
          operation: op,
        });
      })
      .catch((err) => {
        sendResponse({
          ok: false,
          error: err.message,
        });
      });
    return true; // async response
  }

  if (message.type === 'FETCH_ACTIVE_OPERATION_BY_ID') {
    // Legacy: fetch specific operation by ID and token (operations router)
    const { operationId, sessionToken } = message;
    console.log('[eSeva] Fetching operation by ID:', operationId);
    fetchActiveOperationFromAPI(null, operationId, sessionToken)
      .then((op) => {
        sendResponse({ ok: true, operation: op });
      })
      .catch((err) => {
        sendResponse({ ok: false, error: err.message });
      });
    return true;
  }

  if (message.type === 'LOAD_OPERATION_BY_ID') {
    // New simplified load: only needs operation ID, uses documents router (no token)
    const { operationId } = message;
    // Capture the tab URL from the sender so we can match a service config
    const tabUrl = sender?.tab?.url || null;
    console.log('[eSeva] Loading document operation by ID:', operationId, 'tabUrl:', tabUrl);
    loadDocumentOperation(operationId, tabUrl)
      .then((op) => {
        sendResponse({ ok: true, operation: op });
      })
      .catch((err) => {
        sendResponse({ ok: false, error: err.message });
      });
    return true;
  }

  if (message.type === 'END_OPERATION') {
    const { operationId } = message;
    console.log('[eSeva] Ending operation:', operationId);
    endDocumentOperation(operationId)
      .then(() => {
        sendResponse({ ok: true });
      })
      .catch((err) => {
        sendResponse({ ok: false, error: err.message });
      });
    return true;
  }

  if (message.type === 'GET_ACTIVE_OPERATION') {
    if (activeOperation) {
      sendResponse({
        ok: true,
        operation: activeOperation,
      });
    } else {
      sendResponse({
        ok: false,
        error: 'No active operation',
      });
    }
    return;
  }

  if (message.type === 'UPDATE_STRUCTURED_DATA') {
    if (!activeOperation) {
      sendResponse({ ok: false, error: 'No active operation' });
      return;
    }
    updateStructuredDataAPI(
      activeOperation.operation_id,
      message.data,
      activeOperation
    )
      .then(() => {
        sendResponse({ ok: true });
      })
      .catch((err) => {
        sendResponse({ ok: false, error: err.message });
      });
    return true;
  }

  if (message.type === 'BIND_OPERATION_TO_TAB') {
    const { operationId, sessionToken, tabId } = message;
    const targetTabId = tabId || sender.tab?.id;
    if (!targetTabId) {
      sendResponse({ ok: false, error: 'Could not determine target tab' });
      return true;
    }
    const url = sender.tab?.url?.split('#')[0]; // Remove fragment
    console.log('[eSeva] Bind request:', { tabId: targetTabId, url, operationId, sessionToken, senderTabId: sender.tab?.id });

    // Store binding both by tabId (volatile) and URL (persistent)
    tabBindings.set(targetTabId, { operationId, sessionToken });
    if (url) {
      urlBindings.set(url, { operationId, sessionToken });
    }
    console.log('[eSeva] Binding stored for tab:', targetTabId, 'and URL:', url);
    console.log('[eSeva] Current tabBindings:', Array.from(tabBindings.entries()));
    console.log('[eSeva] Current urlBindings:', Array.from(urlBindings.entries()));

    sendResponse({ ok: true });
    return true;
  }

  if (message.type === 'GET_BOUND_OPERATION') {
    const { tabId } = message;
    const url = sender.tab?.url?.split('#')[0]; // Remove fragment
    console.log('[eSeva] Get binding for tab:', tabId, 'URL:', url);

    // First try tabId (volatile)
    const binding = tabBindings.get(tabId);
    if (binding) {
      console.log('[eSeva] Found binding by tabId:', binding);
      const response = { ok: true, operationId: binding.operationId, sessionToken: binding.sessionToken };
      console.log('[eSeva] Sending response (tabId):', response);
      sendResponse(response);
      return true;
    }

    // Fallback to URL (persistent)
    if (url) {
      const urlBinding = urlBindings.get(url);
      if (urlBinding) {
        console.log('[eSeva] Found binding by URL:', urlBinding);
        // Promote URL binding to current tabId for future lookups
        tabBindings.set(tabId, urlBinding);
        const response = { ok: true, operationId: urlBinding.operationId, sessionToken: urlBinding.sessionToken };
        console.log('[eSeva] Sending response (URL):', response);
        sendResponse(response);
        return true;
      }
    }

    console.log('[eSeva] No binding for tab/URL');
    const response = { ok: false, error: 'No binding for tab' };
    console.log('[eSeva] Sending response (none):', response);
    sendResponse(response);
    return true;
  }

  if (message.type === 'GET_MY_TAB_ID') {
    const tabId = sender.tab?.id;
    console.log('[eSeva] GET_MY_TAB_ID from tab:', tabId);
    if (tabId) {
      sendResponse({ ok: true, tabId });
    } else {
      sendResponse({ ok: false, error: 'Could not determine tab ID' });
    }
    return true;
  }

  if (message.type === 'SET_OPERATION_STATUS') {
    if (!activeOperation) {
      sendResponse({ ok: false, error: 'No active operation' });
      return;
    }
    setOperationStatusAPI(message.status, activeOperation)
      .then(() => {
        sendResponse({ ok: true });
      })
      .catch((err) => {
        sendResponse({ ok: false, error: err.message });
      });
    return true;
  }

  if (message.type === 'UNLOCK_OPERATION') {
    if (!activeOperation || !tabId) {
      sendResponse({ ok: false, error: 'No active operation or tab context' });
      return;
    }
    const tabSessionId = tabSessionIds.get(tabId);
    unlockOperationAPI(activeOperation, tabSessionId)
      .then(() => {
        activeOperation = null;
        tabBindings.delete(tabId);
        sendResponse({ ok: true });
      })
      .catch((err) => {
        sendResponse({ ok: false, error: err.message });
      });
    return true;
  }

  if (message.type === 'ENUMERATE_FIELDS_ADMIN') {
    const { fields, serviceId, urlPatterns, approvedMappings, fileMappings } = message;
    // First, get suggestions
    processFieldEnumerationAPI(fields, serviceId, urlPatterns)
      .then((result) => {
        if (approvedMappings) {
          // Merge file_mappings into draft config before finalizing
          if (fileMappings && Object.keys(fileMappings).length > 0) {
            result.draft_config.file_mappings = fileMappings;
          }
          // Finalize config with approved mappings
          return finalizeConfigAPI(serviceId, result.draft_config, approvedMappings)
            .then((finalResult) => ({
              ok: true,
              suggestions: result.suggestions,
              unmapped_schema_fields: result.unmapped_schema_fields,
              draft_config: result.draft_config,
              finalized: finalResult,
            }))
            .catch((err) => {
              if (err.code === 'URL_PATTERN_CONFLICT') {
                return {
                  ok: false,
                  error: 'URL_PATTERN_CONFLICT',
                  message: err.message,
                  details: err.details,
                  suggestions: result.suggestions,
                  draft_config: result.draft_config,
                };
              }
              throw err;
            });
        }
        return {
          ok: true,
          suggestions: result.suggestions,
          unmapped_schema_fields: result.unmapped_schema_fields,
          draft_config: result.draft_config,
        };
      })
      .then((payload) => {
        sendResponse(payload);
      })
      .catch((err) => {
        console.error('[eSeva] ENUMERATE_FIELDS_ADMIN error:', err);
        sendResponse({ ok: false, error: err.message });
      });
    return true; // async response
  }
});

/**
 * Load a document-processing operation by ID only (no token).
 * Fetches merged fields + status from the documents router.
 * Also fetches service config matched to tabUrl so form filling works.
 */
async function loadDocumentOperation(operationId, tabUrl) {
  // 1. Fetch merged fields (extracted data)
  const rawRes = await fetch(`${API_BASE}/documents/results/${operationId}/raw`);
  if (!rawRes.ok) {
    throw new Error(`Operation not found or not yet processed (HTTP ${rawRes.status})`);
  }
  const fields = await rawRes.json();

  // 2. Fetch status + name from operations list
  let status = 'completed';
  let name = fields.name || fields.child_name || null;
  try {
    const listRes = await fetch(`${API_BASE}/documents/operations`);
    if (listRes.ok) {
      const listData = await listRes.json();
      const match = (listData.operations || []).find(o => o.operation_id === operationId);
      if (match) {
        status = match.status;
        name = name || match.name;
      }
    }
  } catch (_) { /* status stays 'completed' */ }

  // 3. Fetch service config matched to the current tab URL
  let service_config = null;
  let structured_data = null;
  if (tabUrl) {
    try {
      const matchRes = await fetch(`${API_BASE}/services/match?url=${encodeURIComponent(tabUrl)}`);
      if (matchRes.ok) {
        const matchData = await matchRes.json();
        if (matchData.config) {
          service_config = matchData.config;
          // Build structured_data by mapping merged_fields through field_mappings keys
          // The field_mappings keys ARE the master field names (e.g. 'applicant_name', 'mobile')
          // merged_fields uses its own keys (e.g. 'name', 'mobile')
          // We resolve by: try exact key, then common aliases
          const ALIASES = {
            applicant_name: ['name', 'child_name', 'applicant_name'],
            father_name:    ['father_name'],
            mother_name:    ['mother_name'],
            dob:            ['dob', 'date_of_birth'],
            gender:         ['gender'],
            mobile:         ['mobile'],
            email:          ['email'],
            address:        ['address'],
            pincode:        ['pincode'],
            aadhaar:        ['aadhaar'],
            registration_no:['registration_no'],
            place_of_birth: ['place_of_birth'],
          };
          structured_data = {};
          for (const masterField of Object.keys(service_config.field_mappings || {})) {
            const candidates = ALIASES[masterField] || [masterField];
            for (const alias of candidates) {
              if (fields[alias] != null) {
                // Clean up raw extraction artifacts: trim whitespace and stray newlines
                const raw = String(fields[alias]);
                structured_data[masterField] = raw.replace(/\s*\n\s*/g, ' ').trim();
                break;
              }
            }
          }
          console.log('[eSeva] Service config matched:', service_config.service_name);
          console.log('[eSeva] Structured data built:', structured_data);
        }
      }
    } catch (err) {
      console.warn('[eSeva] Service config fetch failed:', err.message);
    }
  }

  return {
    operation_id: operationId,
    status,
    name,
    merged_fields: fields,
    service_config,
    structured_data,
  };
}

/**
 * Mark a document operation as completed via the documents router.
 */
async function endDocumentOperation(operationId) {
  const res = await fetch(`${API_BASE}/documents/operations/${operationId}/complete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!res.ok) {
    throw new Error(`Failed to complete operation (HTTP ${res.status})`);
  }
  return await res.json();
}

/**
 * Fetch active operation from API
 * @param {string} tabSessionId - Optional tab session ID for storage lookup
 * @param {string} operationId - Optional operation ID for direct fetch
 * @param {string} sessionToken - Optional session token for direct fetch
 */
async function fetchActiveOperationFromAPI(tabSessionId, operationId, sessionToken) {
  // If operationId and sessionToken provided, fetch directly
  if (operationId && sessionToken) {
    console.log('[eSeva] Fetching operation directly by ID:', operationId);
    try {
      const response = await fetch(`${API_BASE}/operation/${operationId}?token=${encodeURIComponent(sessionToken)}`, {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
        },
      });
      if (!response.ok) {
        throw new Error(`Failed to fetch operation: ${response.statusText}`);
      }
      const op = await response.json();
      console.log('[eSeva] Fetched operation by ID:', op);
      return op;
    } catch (err) {
      console.error('[eSeva] Direct fetch error:', err);
      throw err;
    }
  }

  // Otherwise, try to get operation from storage
  const stored = await chrome.storage.local.get('eseva_operation');

  if (stored.eseva_operation) {
    const op = stored.eseva_operation;
    const response = await fetch(
      `${API_BASE}/operation/${op.operation_id}?token=${encodeURIComponent(op.session_token)}`,
      {
        method: 'GET',
        headers: {
          'X-Tab-Session-Id': tabSessionId,
        },
      }
    );

    if (!response.ok) {
      throw new Error(`API error: ${response.status}`);
    }

    const data = await response.json();
    return {
      operation_id: data.operation.operation_id,
      session_token: op.session_token,
      csrf_token: op.csrf_token,
      structured_data: data.structured_data,
      service_config: data.service_config,
      status: data.operation.status,
    };
  }

  throw new Error('No stored operation found');
}

/**
 * Update structured data
 */
async function updateStructuredDataAPI(operationId, data, activeOp) {
  const response = await fetch(
    `${API_BASE}/operation/${operationId}/structured?token=${encodeURIComponent(activeOp.session_token)}`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': activeOp.csrf_token,
      },
      body: JSON.stringify({ structured_data: data }),
    }
  );

  if (!response.ok) {
    throw new Error(`API error: ${response.status}`);
  }

  return await response.json();
}

/**
 * Set operation status
 */
async function setOperationStatusAPI(status, activeOp) {
  const response = await fetch(
    `${API_BASE}/operation/${activeOp.operation_id}/status?token=${encodeURIComponent(activeOp.session_token)}`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': activeOp.csrf_token,
      },
      body: JSON.stringify({ status }),
    }
  );

  if (!response.ok) {
    throw new Error(`API error: ${response.status}`);
  }

  return await response.json();
}

/**
 * Unlock operation
 */
async function unlockOperationAPI(activeOp, tabSessionId) {
  const response = await fetch(
    `${API_BASE}/operation/${activeOp.operation_id}/unlock?token=${encodeURIComponent(activeOp.session_token)}`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': activeOp.csrf_token,
      },
      body: JSON.stringify({ tab_session_id: tabSessionId }),
    }
  );

  if (!response.ok) {
    throw new Error(`API error: ${response.status}`);
  }

  return await response.json();
}

/**
 * Process field enumeration for admin mode
 */
async function processFieldEnumerationAPI(fields, serviceId, urlPatterns) {
  const response = await fetch(`${API_BASE}/admin/process-fields`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      service_name: serviceId,
      url_patterns: urlPatterns,
      fields: fields,
    }),
  });

  const text = await response.text();
  let result;
  try {
    result = JSON.parse(text);
  } catch {
    throw new Error(`API error: ${response.status} - ${text}`);
  }

  if (!response.ok) {
    throw new Error(`API error: ${response.status} - ${result.detail || 'Unknown error'}`);
  }

  return result;
}

/**
 * Finalize config via admin API
 */
async function finalizeConfigAPI(serviceId, draftConfig, approvedMappings) {
  const response = await fetch(`${API_BASE}/admin/finalize-config`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      service_name: serviceId,
      config: draftConfig,       // already has file_mappings merged in
      approved_mappings: approvedMappings,
    }),
  });

  const text = await response.text();
  let result;
  try {
    result = JSON.parse(text);
  } catch {
    throw new Error(`API error: ${response.status} - ${text}`);
  }

  if (!response.ok) {
    // Handle specific error types
    if (result.error === 'url_pattern_conflict') {
      const conflict = new Error(result.message);
      conflict.code = 'URL_PATTERN_CONFLICT';
      conflict.details = result;
      throw conflict;
    }
    throw new Error(`API error: ${response.status} - ${result.detail || 'Unknown error'}`);
  }

  return result;
}

/**
 * Clean up on tab close
 */
chrome.tabs.onRemoved.addListener((tabId) => {
  tabSessionIds.delete(tabId);
  tabBindings.delete(tabId);
});

/**
 * Extension installed
 */
chrome.runtime.onInstalled.addListener(() => {
  console.log('eSevaCenter Extension installed');
});

/**
 * Keyboard shortcut handler
 */
if (chrome.commands && chrome.commands.onCommand) {
  chrome.commands.onCommand.addListener((command) => {
    if (command === 'bind-or-admin') {
      // Open bind page in a new tab
      chrome.tabs.create({ url: chrome.runtime.getURL('bind.html'), active: true });
    }
  });
} else {
  console.warn('[eSeva] chrome.commands API not available; keyboard shortcut disabled');
}
