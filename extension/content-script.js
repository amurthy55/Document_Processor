/**
 * eSevaCenter Extension - Content Script
 * 
 * Injected into all web pages.
 * Responsibilities:
 * - Detect if service URL matches any config
 * - Fetch active operation from background
 * - Enumerate form fields
 * - Fill form with structured data
 * - Handle admin mapping mode
 * - Detect form submission (warn user, don't auto-submit)
 */

const API_BASE = "http://127.0.0.1:8765/api";
let tabSessionId = null;
let activeOperation = null;
let adminModeEnabled = false;
let panelInjected = false;

/**
 * Wrapper around chrome.runtime.sendMessage that handles
 * "Extension context invalidated" gracefully (happens when the
 * extension is reloaded while the content script is still alive).
 */
async function safeSend(msg) {
  try {
    return await chrome.runtime.sendMessage(msg);
  } catch (e) {
    if (e && e.message && e.message.includes('Extension context invalidated')) {
      panelShowMessage('Extension was updated — please reload this page (F5).', 'error');
    }
    throw e;
  }
}

/**
 * Initialize: get tab session ID
 */
(async function initialize() {
  const response = await safeSend({
    type: 'GET_TAB_SESSION_ID',
  });

  if (response.ok) {
    tabSessionId = response.tabSessionId;
    console.log('[eSeva] Tab session initialized:', tabSessionId);

    // Inject floating panel immediately
    injectFloatingPanel();

    // No automatic lookup; user will click Load Operation
  } else {
    console.error('[eSeva] Failed to get tab session:', response.error);
  }

  // Check for admin mode
  checkAdminMode();
})();

/**
 * Inject floating control panel into page (for popup windows)
 */
function injectFloatingPanel() {
  if (panelInjected) return;

  // Create panel HTML with draggable and resizable functionality
  const panelHTML = `
    <div id="eseva-floating-panel" style="
      position: fixed;
      bottom: 20px;
      right: 20px;
      width: 320px;
      min-width: 280px;
      max-width: 500px;
      background: white;
      border: 2px solid #0066cc;
      border-radius: 8px;
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
      z-index: 10000;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
      padding: 0;
      resize: both;
      overflow: auto;
    ">
      <!-- Header with drag handle -->
      <div id="eseva-panel-header" style="
        background: linear-gradient(135deg, #0066cc 0%, #004499 100%);
        color: white;
        padding: 12px 16px;
        border-radius: 6px 6px 0 0;
        font-weight: 600;
        font-size: 14px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        cursor: move;
        user-select: none;
      ">
        <span>eSevaCenter</span>
        <div style="display: flex; gap: 4px;">
          <button id="eseva-panel-minimize" style="
            background: rgba(255, 255, 255, 0.3);
            border: none;
            color: white;
            cursor: pointer;
            padding: 4px 8px;
            border-radius: 3px;
            font-size: 16px;
            display: flex;
            align-items: center;
            justify-content: center;
          " title="Minimize panel">−</button>
        </div>
      </div>

      <!-- Status Section -->
      <div id="eseva-status-section" style="
        padding: 12px 16px;
        border-bottom: 1px solid #e0e0e0;
      ">
        <div id="eseva-status-text" style="
          font-size: 13px;
          color: #666;
          margin-bottom: 8px;
        ">No operation loaded</div>
        <div id="eseva-service-name" style="
          font-size: 12px;
          color: #999;
          margin-bottom: 8px;
        "></div>
        <div id="eseva-filled-count" style="
          font-size: 12px;
          color: #999;
        "></div>
      </div>

      <!-- Buttons Section -->
      <div style="
        padding: 12px 16px;
        display: flex;
        flex-direction: column;
        gap: 8px;
      ">
        <button id="eseva-load-btn" style="
          background: #0066cc;
          color: white;
          border: none;
          padding: 10px;
          border-radius: 4px;
          cursor: pointer;
          font-size: 13px;
          font-weight: 600;
          transition: background 0.2s;
        " onmouseover="this.style.background='#0052a3'" onmouseout="this.style.background='#0066cc'">
          Load Operation
        </button>

        <button id="eseva-admin-btn" style="
          background: #6c757d;
          color: white;
          border: none;
          padding: 10px;
          border-radius: 4px;
          cursor: pointer;
          font-size: 13px;
          font-weight: 600;
          transition: background 0.2s;
        " onmouseover="this.style.background='#5a6268'" onmouseout="this.style.background='#6c757d'">
          Toggle Admin Mapping Mode
        </button>
        
        <button id="eseva-refill-btn" style="
          background: #4CAF50;
          color: white;
          border: none;
          padding: 10px;
          border-radius: 4px;
          cursor: pointer;
          font-size: 13px;
          font-weight: 600;
          display: none;
          transition: background 0.2s;
        " onmouseover="this.style.background='#388e3c'" onmouseout="this.style.background='#4CAF50'">
          Refill Form
        </button>

        <button id="eseva-end-btn" style="
          background: #c0392b;
          color: white;
          border: none;
          padding: 10px;
          border-radius: 4px;
          cursor: pointer;
          font-size: 13px;
          font-weight: 700;
          display: none;
          transition: background 0.2s;
          letter-spacing: 0.03em;
        " onmouseover="this.style.background='#96281b'" onmouseout="this.style.background='#c0392b'">
          ■ End Operation
        </button>

      </div>

      <!-- Message Area -->
      <div id="eseva-message-area" style="
        padding: 12px 16px;
        border-top: 1px solid #e0e0e0;
        background: #f5f5f5;
        border-radius: 0 0 6px 6px;
        font-size: 12px;
        color: #666;
        max-height: 100px;
        overflow-y: auto;
        display: none;
      "></div>
    </div>
  `;

  // Helper function to actually inject the panel
  const performInjection = () => {
    try {
      // Check if body exists now
      if (!document.body) {
        console.log('[eSeva] Body still not available, retrying in 50ms');
        setTimeout(performInjection, 50);
        return;
      }

      console.log('[eSeva] Injecting panel into body');
      
      const container = document.createElement('div');
      container.innerHTML = panelHTML;
      const panelElement = container.firstElementChild;
      document.body.appendChild(panelElement);
      console.log('[eSeva] Panel injected successfully');

      // Set up event listeners with null checks
      const loadBtn = document.getElementById('eseva-load-btn');
      if (loadBtn) {
        loadBtn.addEventListener('click', async () => {
          await panelLoadOperation();
        });
        console.log('[eSeva] Load button listener attached');
      }

      const refillBtn = document.getElementById('eseva-refill-btn');
      if (refillBtn) {
        refillBtn.addEventListener('click', async () => {
          await panelRefillForm();
        });
      }

      const endBtn = document.getElementById('eseva-end-btn');
      if (endBtn) {
        endBtn.addEventListener('click', async () => {
          await panelEndOperation();
        });
      }

      const adminBtn = document.getElementById('eseva-admin-btn');
      if (adminBtn) {
        adminBtn.addEventListener('click', () => {
          if (adminModeEnabled) {
            disableAdminMode();
          } else {
            enableAdminMode();
          }
        });
      }

      const minimizeBtn = document.getElementById('eseva-panel-minimize');
      if (minimizeBtn) {
        minimizeBtn.addEventListener('click', () => {
          const statusSection = document.getElementById('eseva-status-section');
          const buttonsSection = statusSection.nextElementSibling;
          const messageArea = document.getElementById('eseva-message-area');
          
          if (statusSection.style.display === 'none') {
            statusSection.style.display = 'block';
            buttonsSection.style.display = 'flex';
            messageArea.style.display = 'none';
          } else {
            statusSection.style.display = 'none';
            buttonsSection.style.display = 'none';
            messageArea.style.display = 'block';
          }
        });
      }

      panelInjected = true;
      console.log('[eSeva] All listeners attached, panel ready');

      // Add drag functionality
      makePanelDraggable();
    } catch (err) {
      console.error('[eSeva] Error during panel injection:', err.message);
    }
  };

  // Make panel draggable
  function makePanelDraggable() {
    const panel = document.getElementById('eseva-floating-panel');
    const header = document.getElementById('eseva-panel-header');
    
    if (!panel || !header) return;

    let isDragging = false;
    let startX, startY, initialX, initialY;

    header.addEventListener('mousedown', (e) => {
      // Only drag if clicking on header, not buttons
      if (e.target.id === 'eseva-panel-minimize') return;
      
      isDragging = true;
      startX = e.clientX;
      startY = e.clientY;
      
      const rect = panel.getBoundingClientRect();
      initialX = rect.left;
      initialY = rect.top;
      
      header.style.cursor = 'grabbing';
      e.preventDefault();
    });

    document.addEventListener('mousemove', (e) => {
      if (!isDragging) return;
      
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;
      
      let newX = initialX + dx;
      let newY = initialY + dy;
      
      // Keep panel within viewport
      const viewportWidth = window.innerWidth;
      const viewportHeight = window.innerHeight;
      const panelWidth = panel.offsetWidth;
      const panelHeight = panel.offsetHeight;
      
      newX = Math.max(0, Math.min(newX, viewportWidth - panelWidth));
      newY = Math.max(0, Math.min(newY, viewportHeight - panelHeight));
      
      panel.style.left = newX + 'px';
      panel.style.top = newY + 'px';
      panel.style.right = 'auto';
      panel.style.bottom = 'auto';
    });

    document.addEventListener('mouseup', () => {
      if (isDragging) {
        isDragging = false;
        header.style.cursor = 'move';
      }
    });
  }

  // Wait for DOM to be ready
  if (document.readyState === 'loading') {
    console.log('[eSeva] DOM still loading, waiting for DOMContentLoaded');
    document.addEventListener('DOMContentLoaded', performInjection, { once: true });
  } else {
    console.log('[eSeva] DOM already loaded, injecting immediately');
    performInjection();
  }
}

/**
 * Update floating panel status
 */
function updatePanelStatus() {
  const statusText = document.getElementById('eseva-status-text');
  const serviceName = document.getElementById('eseva-service-name');
  const filledCount = document.getElementById('eseva-filled-count');
  const loadBtn = document.getElementById('eseva-load-btn');
  const refillBtn = document.getElementById('eseva-refill-btn');
  const endBtn = document.getElementById('eseva-end-btn');

  if (activeOperation) {
    const opStatus = activeOperation.status || 'loaded';
    statusText.textContent = `Op: ${activeOperation.operation_id.slice(0, 8)}…  [${opStatus.toUpperCase()}]`;
    const nameLabel = activeOperation.name ? `  ${activeOperation.name}` : '';
    serviceName.textContent = `Service: ${activeOperation.service_config?.service_name || 'Document Operation'}${nameLabel}`;
    const fieldCount = Object.keys(activeOperation.merged_fields || activeOperation.structured_data || {}).length;
    filledCount.textContent = `Extracted fields: ${fieldCount}`;

    loadBtn.style.display = 'none';
    refillBtn.style.display = 'block';
    if (endBtn) endBtn.style.display = 'block';
  } else {
    statusText.textContent = 'No operation loaded';
    serviceName.textContent = '';
    filledCount.textContent = '';

    loadBtn.style.display = 'block';
    refillBtn.style.display = 'none';
    if (endBtn) endBtn.style.display = 'none';
  }
}

/**
 * Panel: Load operation — asks only for Operation ID, no token required.
 */
async function panelLoadOperation() {
  const opId = prompt('Enter Operation ID:');
  if (!opId || !opId.trim()) {
    panelShowMessage('Operation ID is required.', 'error');
    return;
  }

  panelShowMessage('Loading operation...', 'info');

  try {
    const response = await safeSend({
      type: 'LOAD_OPERATION_BY_ID',
      operationId: opId.trim(),
    });

    if (response.ok && response.operation) {
      activeOperation = response.operation;
      console.log('[eSeva] Document operation loaded:', activeOperation);

      // Fill form if service config was matched to this tab URL
      if (activeOperation.service_config && activeOperation.structured_data) {
        const fieldCount = Object.keys(activeOperation.structured_data).length;
        await fillFormWithData(activeOperation);
        injectSubmitInterceptor();
        panelShowMessage(`✅ Loaded & filled ${fieldCount} field(s) using config: ${activeOperation.service_config.service_name}`, 'success');
      } else {
        panelShowMessage('✅ Operation loaded. No service config matched this URL — form not auto-filled.', 'info');
      }

      updatePanelStatus();
    } else {
      panelShowMessage('❌ Failed to load: ' + (response.error || 'unknown'), 'error');
    }
  } catch (err) {
    panelShowMessage('❌ Error: ' + err.message, 'error');
  }
}

/**
 * Panel: End Operation — marks the operation as completed on the backend.
 * Always shown after load; works even if already completed.
 */
async function panelEndOperation() {
  if (!activeOperation) {
    panelShowMessage('No operation loaded.', 'error');
    return;
  }

  panelShowMessage('Ending operation...', 'info');

  try {
    const response = await safeSend({
      type: 'END_OPERATION',
      operationId: activeOperation.operation_id,
    });

    if (response.ok) {
      activeOperation.status = 'completed';
      updatePanelStatus();
      panelShowMessage('✅ Operation marked as COMPLETE. Desktop UI will update on next refresh.', 'success');
    } else {
      panelShowMessage('❌ Failed to end: ' + (response.error || 'unknown'), 'error');
    }
  } catch (err) {
    panelShowMessage('❌ Error: ' + err.message, 'error');
  }
}

/**
 * Panel: Refill form
 */
async function panelRefillForm() {
  if (!activeOperation) {
    panelShowMessage('No operation loaded', 'error');
    return;
  }

  panelShowMessage('Refilling form...', 'info');
  await fillFormWithData(activeOperation);
  panelShowMessage('Form refilled successfully', 'success');
}

/**
 * Panel: Show message
 */
function panelShowMessage(message, type = 'info') {
  const messageArea = document.getElementById('eseva-message-area');
  const timestamp = new Date().toLocaleTimeString();
  
  let color = '#666';
  if (type === 'success') color = '#4CAF50';
  if (type === 'error') color = '#f44336';
  if (type === 'info') color = '#0066cc';

  const msg = document.createElement('div');
  msg.style.color = color;
  msg.style.marginBottom = '4px';
  msg.textContent = `[${timestamp}] ${message}`;

  messageArea.appendChild(msg);
  messageArea.style.display = 'block';

  // Scroll to bottom
  setTimeout(() => {
    messageArea.scrollTop = messageArea.scrollHeight;
  }, 0);

  // Auto-hide success messages after 5 seconds
  if (type === 'success') {
    setTimeout(() => {
      if (messageArea.children.length === 1) {
        messageArea.style.display = 'none';
      }
    }, 5000);
  }
}

/**
 * Check if there's an active operation
 */
async function checkForActiveOperation() {
  try {
    // First, try to get bound operation for this tab
    const tabSessionId = await safeSend({ type: 'GET_TAB_SESSION_ID' }).then(r => r?.tabSessionId);
    console.log('[eSeva] My tabSessionId:', tabSessionId);

    // Ask background for our tab ID
    console.log('[eSeva] Sending GET_MY_TAB_ID message');
    const { tabId } = await safeSend({ type: 'GET_MY_TAB_ID' });
    console.log('[eSeva] Current tab ID from background:', tabId);

    console.log('[eSeva] Sending GET_BOUND_OPERATION for tabId:', tabId);
    const bound = await safeSend({ type: 'GET_BOUND_OPERATION', tabId });
    console.log('[eSeva] Bound operation response for tabId', tabId, ':', bound);

    if (bound.ok && bound.operationId) {
      // Fetch operation by ID
      const response = await safeSend({
        type: 'FETCH_ACTIVE_OPERATION',
        operationId: bound.operationId,
        sessionToken: bound.sessionToken,
      });
      console.log('[eSeva] Fetched operation by bound ID:', response);

      if (response.ok && response.operation) {
        activeOperation = response.operation;
        console.log('[eSeva] Active operation fetched:', activeOperation);

        // Fill form with structured data
        await fillFormWithData(activeOperation);

        // Inject submit interceptor
        injectSubmitInterceptor();

        // Update floating panel
        updatePanelStatus();
        return;
      } else {
        console.log('[eSeva] Failed to fetch operation by ID:', response.error);
      }
    } else {
      console.log('[eSeva] No bound operation found for tabId', tabId, 'error:', bound.error);
    }

    // Fallback: try stored operation (for non-popup flows)
    const response = await safeSend({
      type: 'FETCH_ACTIVE_OPERATION',
    });

    console.log('[eSeva] Fallback stored operation response:', response);

    if (response.ok && response.operation) {
      activeOperation = response.operation;
      console.log('[eSeva] Active operation fetched:', activeOperation);

      // Fill form with structured data
      await fillFormWithData(activeOperation);

      // Inject submit interceptor
      injectSubmitInterceptor();

      // Update floating panel
      updatePanelStatus();
    } else {
      console.log('[eSeva] No active operation or error:', response.error);
    }
  } catch (err) {
    console.log('[eSeva] Background message failed:', err.message);
  }
}

/**
 * Fill form fields with structured data
 */
function getMappedFieldIds(mapping) {
  if (!mapping) return [];
  if (typeof mapping === 'string') return [mapping];
  if (Array.isArray(mapping)) return mapping.filter(Boolean);

  const ids =
    mapping.form_field_ids ||
    mapping.field_ids ||
    mapping.targets ||
    mapping.form_field_id ||
    mapping.field_id;

  if (!ids) return [];
  return (Array.isArray(ids) ? ids : [ids]).filter(Boolean);
}

async function fillFormWithData(operation) {
  if (!operation.service_config) {
    console.warn('[eSeva] No service config available');
    return;
  }

  const fieldMappings = operation.service_config.field_mappings || {};
  const structuredData = operation.structured_data || {};

  console.log('[eSeva] Filling form with mappings:', fieldMappings);
  console.log('[eSeva] Data:', structuredData);

  for (const [masterField, mapping] of Object.entries(fieldMappings)) {
    const value = structuredData[masterField];
    if (!value) continue;

    const inputType = typeof mapping === 'object' && !Array.isArray(mapping)
      ? mapping.input_type || 'text'
      : 'text';
    const fieldIds = getMappedFieldIds(mapping);

    for (const fieldId of fieldIds) {
      // Try to find field by ID or name
      let field =
        document.getElementById(fieldId) ||
        document.querySelector(`input[name="${fieldId}"]`) ||
        document.querySelector(`textarea[name="${fieldId}"]`) ||
        document.querySelector(`select[name="${fieldId}"]`);

      if (!field) {
        console.warn(`[eSeva] Field not found: "${fieldId}" (masterField="${masterField}", value="${value}")`);
        continue;
      }
      console.log(`[eSeva] Filling "${fieldId}" with "${value}"`);

      // For Google Forms: hidden inputs hold the name, but visible inputs are separate.
      // Find the visible input in the same question container and fill that instead.
      if (field.type === 'hidden') {
        const visibleField = findVisibleInputForHidden(field, fieldId);
        if (visibleField) {
          console.log(`[eSeva] Found visible input for hidden field "${fieldId}"`);
          setFieldValue(visibleField, value, inputType);
          // Trigger events that Google Forms listens to
          visibleField.focus();
          visibleField.dispatchEvent(new Event('input', { bubbles: true }));
          visibleField.dispatchEvent(new Event('change', { bubbles: true }));
          visibleField.dispatchEvent(new Event('blur', { bubbles: true }));
          continue;
        }
        // If no visible input found, set hidden field value as fallback
        console.log(`[eSeva] No visible input found for "${fieldId}", setting hidden field`);
      }

      setFieldValue(field, value, inputType);

      // Trigger change event
      field.dispatchEvent(new Event('input', { bubbles: true }));
      field.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }

  console.log('[eSeva] Form filled successfully');

  // Auto-inject compressed files (PDFs + passport photos) into file inputs on the page
  if (operation.operation_id) {
    await injectOperationFiles(operation.operation_id, operation.service_config || null);
  }
}

/**
 * Fetch all files stored in the operation folder and inject them into
 * <input type="file"> elements on the current page.
 *
 * Routing priority:
 * 1. If service_config.file_mappings exists: use explicit field_id → attachment_label map
 *    to match each stored file (by name) to the correct input.
 * 2. Heuristic fallback: images → image-only inputs first; PDFs → pdf-accepting inputs.
 */
async function injectOperationFiles(operationId, serviceConfig) {
  try {
    const res = await fetch(`${API_BASE}/documents/files/${operationId}`);
    if (!res.ok) return;
    const data = await res.json();
    const allFiles = (data.files || []).filter(f => {
      // skip extracted_data sub-items (is_image false, in subdir) — only top-level files
      return !f.name.includes('/');
    });
    if (!allFiles.length) return;

    const fileMappings = serviceConfig?.file_mappings || {};
    const hasMappings = Object.keys(fileMappings).length > 0;

    if (hasMappings) {
      // ── Explicit mapping mode ──────────────────────────────────────────────
      // file_mappings: { "field_id": "Attachment Label", ... }
      // Files are matched to labels by checking if the stored filename contains
      // the label (case-insensitive) or by upload order.
      const imageFiles = allFiles.filter(f => f.is_image);
      const pdfFiles   = allFiles.filter(f => !f.is_image);

      for (const [fieldId, attachmentLabel] of Object.entries(fileMappings)) {
        const inputEl = document.getElementById(fieldId)
          || document.querySelector(`input[type="file"][name="${fieldId}"]`);
        if (!inputEl) {
          console.warn(`[eSeva] file_mappings: no input found for "${fieldId}"`);
          continue;
        }

        // Pick the matching file: prefer one whose name loosely matches the label,
        // otherwise pick by attachment type (image inputs get image files, doc inputs get PDFs).
        // wantsImage = accept attribute exists and does NOT include 'pdf'
        // (covers accept=".jpg,.jpeg,.png" which has no 'image' keyword but is image-only)
        const labelLower = attachmentLabel.toLowerCase();
        const inputAccept = (inputEl.accept || '').toLowerCase();
        const wantsImage = inputAccept !== '' && !inputAccept.includes('pdf');
        const pool = wantsImage ? imageFiles : pdfFiles;

        // Find best match by filename substring or just take first in pool
        let chosen = pool.find(f => f.name.toLowerCase().includes(labelLower.replace(/\s+/g, '')))
          || pool.find(f => labelLower.split(/\s+/).some(w => f.name.toLowerCase().includes(w)))
          || pool[0];

        if (!chosen) {
          console.warn(`[eSeva] file_mappings: no file available for "${fieldId}" (${attachmentLabel})`);
          continue;
        }

        // Remove from pool so it isn't reused
        pool.splice(pool.indexOf(chosen), 1);
        await _injectFileIntoInput(operationId, chosen, inputEl);
      }
    } else {
      // ── Heuristic fallback ─────────────────────────────────────────────────
      // Images → image-only inputs first, then any file input.
      // PDFs   → inputs that accept pdf, sorted to prefer pdf-only inputs.
      const imageFiles = allFiles.filter(f => f.is_image);
      const pdfFiles   = allFiles.filter(f => !f.is_image);

      const allInputs = Array.from(document.querySelectorAll('input[type="file"]'));

      // Image inputs: no .pdf in accept, or empty accept
      const imageInputs = allInputs
        .filter(inp => {
          const a = (inp.accept || '').toLowerCase();
          return !a.includes('pdf');
        })
        .sort((a, b) => {
          // prefer inputs with explicit image accept over empty-accept inputs
          const aHasImg = (a.accept || '').includes('image') || (a.accept || '').includes('jpg');
          const bHasImg = (b.accept || '').includes('image') || (b.accept || '').includes('jpg');
          return (aHasImg ? 0 : 1) - (bHasImg ? 0 : 1);
        });

      // PDF inputs: accept includes .pdf
      const pdfInputs = allInputs.filter(inp => (inp.accept || '').toLowerCase().includes('pdf'));

      for (let i = 0; i < Math.min(imageFiles.length, imageInputs.length); i++) {
        await _injectFileIntoInput(operationId, imageFiles[i], imageInputs[i]);
      }
      for (let i = 0; i < Math.min(pdfFiles.length, pdfInputs.length); i++) {
        await _injectFileIntoInput(operationId, pdfFiles[i], pdfInputs[i]);
      }
    }
  } catch (err) {
    console.warn('[eSeva] injectOperationFiles error:', err.message);
  }
}

/**
 * Download a file from the operation folder and inject it into a file input.
 */
async function _injectFileIntoInput(operationId, fileMeta, inputEl) {
  const MIME = {
    pdf: 'application/pdf',
    jpg: 'image/jpeg', jpeg: 'image/jpeg',
    png: 'image/png',
  };
  try {
    const fileRes = await fetch(`${API_BASE}/documents/download/${operationId}/${encodeURIComponent(fileMeta.name)}`);
    if (!fileRes.ok) return;
    const blob = await fileRes.blob();
    const ext = fileMeta.name.split('.').pop().toLowerCase();
    const mimeType = MIME[ext] || blob.type || 'application/octet-stream';
    const file = new File([blob], fileMeta.name, { type: mimeType });

    const dt = new DataTransfer();
    dt.items.add(file);
    inputEl.files = dt.files;

    inputEl.dispatchEvent(new Event('change', { bubbles: true }));
    inputEl.dispatchEvent(new Event('input', { bubbles: true }));

    console.log(`[eSeva] Injected "${fileMeta.name}" (${mimeType}) into #${inputEl.id || inputEl.name}`);
  } catch (err) {
    console.warn(`[eSeva] Failed to inject "${fileMeta.name}":`, err.message);
  }
}

/**
 * Find the visible input element associated with a hidden input.
 * Many form frameworks use hidden inputs for submission while visible inputs
 * are separate elements in the same question container.
 * Uses generic DOM traversal — no site-specific selectors.
 */
function findVisibleInputForHidden(hiddenField, fieldName) {
  const VISIBLE_INPUT_SELECTOR =
    'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="image"])';
  const INTERACTIVE_SELECTOR =
    VISIBLE_INPUT_SELECTOR + ', textarea, select, [role="radio"], [role="checkbox"], [role="listbox"]';

  // Strategy 1 (best): Find a container anywhere in the document whose attribute
  // references the numeric ID from the field name. Many form builders embed
  // entry IDs in data-attributes on question containers.
  const idParts = fieldName.match(/(\d{5,})/);
  if (idParts) {
    const numericId = idParts[1];
    // Search all elements with any attribute containing this numeric ID.
    // Use a broad selector for elements that commonly carry data attributes.
    const allEls = document.querySelectorAll('[data-params], [data-item-id], [data-field-id], [data-entry]');
    for (const container of allEls) {
      let matches = false;
      for (const attr of container.attributes) {
        if (attr.value.includes(numericId)) { matches = true; break; }
      }
      if (!matches) continue;
      const visibleInput = container.querySelector(INTERACTIVE_SELECTOR);
      if (visibleInput && visibleInput !== hiddenField) return visibleInput;
    }

    // Also walk up ancestors of the hidden field checking for attribute match
    let el = hiddenField.parentElement;
    let depth = 0;
    while (el && depth < 12 && el.tagName !== 'BODY') {
      for (const attr of el.attributes) {
        if (attr.value.includes(numericId)) {
          const visibleInput = el.querySelector(INTERACTIVE_SELECTOR);
          if (visibleInput && visibleInput !== hiddenField) return visibleInput;
        }
      }
      el = el.parentElement;
      depth++;
    }
  }

  // Strategy 2: Walk up progressively from the hidden input, looking for a
  // nearby visible input in the same tight container.
  // Only attempt this if the hidden field's name/id suggests it's a data field
  // (has a numeric ID or a structured name like "field_name"). Avoid pairing
  // standalone framework fields (e.g. "tag", "token") with random visible inputs.
  const looksLikeDataField = /\d{3,}/.test(fieldName) || fieldName.includes('.') || fieldName.includes('[');
  if (looksLikeDataField) {
    let el = hiddenField.parentElement;
    let depth = 0;
    while (el && depth < 8) {
      if (el.tagName === 'FORM' || el.tagName === 'BODY') break;

      // Only match if this container doesn't have multiple question groups
      const headings = el.querySelectorAll('[role="heading"], h1, h2, h3, h4, h5, h6');
      if (headings.length > 1) break; // Too broad — multiple questions here

      const visibleInput = el.querySelector(INTERACTIVE_SELECTOR);
      if (visibleInput && visibleInput !== hiddenField) return visibleInput;

      el = el.parentElement;
      depth++;
    }
  }

  return null;
}

/**
 * Set field value based on type.
 * Uses native setter to bypass framework (React/Closure) value tracking.
 */
function setFieldValue(field, value, inputType) {
  if (inputType === 'select') {
    // For select, find option and select it
    const options = field.querySelectorAll('option');
    for (const opt of options) {
      if (opt.value === value || opt.textContent.trim() === value) {
        field.value = opt.value;
        break;
      }
    }
  } else if (inputType === 'checkbox') {
    field.checked = Boolean(value);
  } else if (inputType === 'radio') {
    const radio = document.querySelector(
      `input[type="radio"][name="${field.name}"][value="${value}"]`
    );
    if (radio) radio.checked = true;
  } else {
    // Use native setter to bypass framework value tracking (React, Google Closure, etc.)
    // Must pick the correct prototype based on element type to avoid "Illegal invocation"
    let nativeSetter;
    if (field.tagName === 'TEXTAREA') {
      nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set;
    } else if (field.tagName === 'SELECT') {
      nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value')?.set;
    } else {
      nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    }
    if (nativeSetter) {
      nativeSetter.call(field, value);
    } else {
      field.value = value;
    }
  }
}

/**
 * Inject submit interceptor
 */
function injectSubmitInterceptor() {
  // Prevent form submission
  document.addEventListener(
    'submit',
    (e) => {
      if (e.target.tagName === 'FORM') {
        e.preventDefault();
        console.log('[eSeva] Form submission intercepted');

        // Show warning
        showMessage(
          'eSeva: Form not submitted. Please review and submit manually.',
          'warning'
        );

        // Save structured data if possible
        saveFormData();
      }
    },
    true
  );

  // Warn about submit buttons
  const submitButtons = document.querySelectorAll(
    'button[type="submit"], input[type="submit"]'
  );
  for (const btn of submitButtons) {
    btn.addEventListener('click', (e) => {
      console.log('[eSeva] Submit button clicked - preventing default');
      e.preventDefault();
      e.stopPropagation();

      showMessage(
        'eSeva: Please review the form and click Submit again to proceed.',
        'warning'
      );
    });
  }
}

/**
 * Save form data back to API
 */
async function saveFormData() {
  if (!activeOperation) return;

  const formData = captureFormData();
  try {
    await safeSend({
      type: 'UPDATE_STRUCTURED_DATA',
      data: formData,
    });
    console.log('[eSeva] Form data saved');
  } catch (err) {
    console.error('[eSeva] Failed to save form data:', err);
  }
}

/**
 * Capture current form data
 */
function captureFormData() {
  const data = {};
  const allInputs = document.querySelectorAll('input, textarea, select');

  for (const input of allInputs) {
    if (!input.name) continue;

    if (input.type === 'checkbox') {
      data[input.name] = input.checked;
    } else if (input.type === 'radio') {
      if (input.checked) {
        data[input.name] = input.value;
      }
    } else {
      data[input.name] = input.value;
    }
  }

  return data;
}

/**
 * Check and enable admin mode if requested
 */
function checkAdminMode() {
  const urlParams = new URLSearchParams(window.location.search);
  const adminMode = urlParams.get('eseva_admin_mode');

  if (adminMode === 'true') {
    enableAdminMode();
  }
}

/**
 * Enable admin mapping mode
 */
function enableAdminMode() {
  adminModeEnabled = true;
  console.log('[eSeva] Admin mode enabled');

  // Add admin UI
  injectAdminUI();

  // Enumerate fields on page load
  setTimeout(async () => {
    const fields = enumerateFormFields();
    console.log('[eSeva] Enumerated fields:', fields);

    // Get suggestions from backend
    try {
      const _dhn = window.location.hostname;
      const _dfn = window.location.pathname.split('/').pop().replace(/\.[^.]+$/, '').replace(/[^a-zA-Z0-9]/g, '_');
      const derivedId = (_dhn ? _dhn.replace(/\./g, '_') : _dfn) || 'unknown_service';
      const response = await safeSend({
        type: 'ENUMERATE_FIELDS_ADMIN',
        fields: fields,
        serviceId: derivedId,
        urlPatterns: [window.location.hostname],
      });

      console.log('[eSeva] Backend response for suggestions:', response);

      if (response.ok && response.suggestions) {
        console.log('[eSeva] Using suggestions:', response.suggestions);
        displayFieldsInAdminPanel(fields, response.suggestions, response.draft_config || null);
      } else {
        console.warn('[eSeva] No suggestions or error, rendering empty dropdowns');
        displayFieldsInAdminPanel(fields, [], response?.draft_config || null);
      }
    } catch (err) {
      console.error('[eSeva] Failed to fetch suggestions:', err);
      displayFieldsInAdminPanel(fields, [], null);
    }
  }, 1000);
}

/**
 * Enumerate all form fields on page.
 * Input-centric approach: processes each input individually,
 * finds its nearest label via progressive DOM search.
 * Works generically across any website — no site-specific selectors.
 */
function enumerateFormFields() {
  const fields = [];
  const seen = new Set();

  // Common framework-internal hidden fields to exclude
  const INTERNAL_FIELD_PATTERNS = [
    /^__/, /^_csrf/i, /^csrf/i, /^token$/i, /^_token$/i,
    /^authenticity_token$/i, /^_method$/i, /^utf8$/,
    /^fvv$/, /^fbzx$/, /^pageHistory$/, /^submissionTimestamp$/,
    /^dlut$/, /^pli$/, /^usp$/, /^hl$/,
    /^partialResponse$/i, /^tag$/i, /^action$/i, /^formAction$/i,
    /^redirect$/i, /^next$/i, /^return$/i, /^returnUrl$/i,
    /^__VIEWSTATE/i, /^__EVENTVALIDATION/i, /^__EVENTTARGET/i, /^__EVENTARGUMENT/i,
    /^javax\.faces/i, /^_ga$/, /^_gid$/,
  ];

  function isInternalField(name) {
    return INTERNAL_FIELD_PATTERNS.some(p => p.test(name));
  }

  const allInputs = document.querySelectorAll('input, textarea, select');

  // First pass: collect all hidden inputs that have a paired visible input nearby.
  // This avoids listing both the hidden and visible version of the same field.
  const hiddenToVisible = new Map();
  const visibleClaimed = new Set();

  for (const input of allInputs) {
    if (input.type !== 'hidden' || !input.name) continue;
    const visible = findVisibleInputForHidden(input, input.name);
    if (visible) {
      hiddenToVisible.set(input, visible);
      visibleClaimed.add(visible);
    }
  }

  for (const input of allInputs) {
    const name = input.name || '';
    const id = input.id || '';
    const type = input.type || 'text';

    // Skip extension-owned admin UI fields
    if (id && id.startsWith('eseva-')) continue;
    if (name && name.startsWith('eseva-')) continue;

    // Skip internal/framework fields
    if (name && isInternalField(name)) continue;
    if (type === 'hidden' && !name) continue;
    // Skip submit/button/image inputs
    if (type === 'submit' || type === 'button' || type === 'image') continue;

    // Skip visible inputs that are already paired with a hidden input
    if (visibleClaimed.has(input)) continue;

    const fieldName = name || id;
    if (!fieldName) continue;
    if (seen.has(fieldName)) continue;

    // Skip sentinel fields (framework bookkeeping for radio/checkbox groups)
    if (fieldName.endsWith('_sentinel')) continue;

    // For hidden fields with no paired visible: filter further
    if (type === 'hidden' && !hiddenToVisible.has(input)) {
      // Skip if value looks like a token/hash
      if (input.value && /^[A-Za-z0-9_-]{32,}$/.test(input.value) && !input.value.includes(' ')) continue;
    }

    seen.add(fieldName);

    // Determine the representative visible input (if hidden has a pair)
    const pairedVisible = hiddenToVisible.get(input);
    const displayInput = pairedVisible || input;

    // Find label for this input
    const label = findLabelForInput(displayInput, input);

    fields.push({
      id: displayInput.id || id,
      name: fieldName,
      type: pairedVisible ? (pairedVisible.type || 'text') : type,
      placeholder: displayInput.placeholder || '',
      label: label,
      value: input.value || displayInput.value || '',
      dom_path: getDomPath(displayInput),
    });
  }

  return fields;
}

/**
 * Find a human-readable label for an input element.
 * Searches progressively outward from the input using generic heuristics.
 * Works on any website — no site-specific class names.
 */
function findLabelForInput(input, hiddenInput) {
  // 1. aria-label directly on the input
  const ariaLabel = input.getAttribute('aria-label');
  if (ariaLabel && ariaLabel.length > 1 && ariaLabel.length < 200) return ariaLabel.trim();

  // 2. aria-labelledby — resolve referenced elements
  const labelledBy = input.getAttribute('aria-labelledby');
  if (labelledBy) {
    const parts = labelledBy.split(/\s+/)
      .map(rid => document.getElementById(rid)?.textContent?.trim())
      .filter(t => t && t.length > 0);
    const text = parts.join(' ').replace(/\s*\*\s*$/, '').trim();
    if (text && text.length > 1 && text.length < 200) return text;
  }

  // Also check the hidden input's aria attributes if different
  if (hiddenInput && hiddenInput !== input) {
    const hAriaLabel = hiddenInput.getAttribute('aria-label');
    if (hAriaLabel && hAriaLabel.length > 1 && hAriaLabel.length < 200) return hAriaLabel.trim();
    const hLabelledBy = hiddenInput.getAttribute('aria-labelledby');
    if (hLabelledBy) {
      const parts = hLabelledBy.split(/\s+/)
        .map(rid => document.getElementById(rid)?.textContent?.trim())
        .filter(t => t && t.length > 0);
      const text = parts.join(' ').replace(/\s*\*\s*$/, '').trim();
      if (text && text.length > 1 && text.length < 200) return text;
    }
  }

  // 3. <label for="inputId"> anywhere in the document
  for (const el of [input, hiddenInput]) {
    if (el?.id) {
      const label = document.querySelector(`label[for="${el.id}"]`);
      if (label) {
        const text = label.textContent.trim().replace(/\s*\*\s*$/, '');
        if (text && text.length > 1 && text.length < 200) return text;
      }
    }
  }

  // 4. Parent <label> wrapping the input
  const parentLabel = input.closest('label');
  if (parentLabel) {
    const text = parentLabel.textContent.trim().replace(/\s*\*\s*$/, '');
    if (text && text.length > 1 && text.length < 200) return text;
  }

  // 5. Walk up the DOM progressively, looking for the nearest label-like text.
  //    Start from the visible input (which is near the label in the DOM),
  //    not the hidden input (which may be grouped elsewhere, e.g. at form bottom).
  const targetInput = input;
  let el = targetInput.parentElement;
  let depth = 0;
  while (el && depth < 10) {
    if (el.tagName === 'BODY') break;
    // If this is a FORM, stop — we've gone too far
    if (el.tagName === 'FORM') break;

    // If this container has multiple headings, it's too broad (contains multiple questions)
    const headingsInside = el.querySelectorAll('[role="heading"], h1, h2, h3, h4, h5, h6');
    if (headingsInside.length > 1) break;

    // Look for heading/label elements inside this container
    const labelText = findDirectLabelInContainer(el, targetInput);
    if (labelText) return labelText;

    // If this container has role="listitem" or is a fieldset, it's a natural boundary.
    if (el.getAttribute('role') === 'listitem' || el.tagName === 'FIELDSET') {
      if (el.tagName === 'FIELDSET') {
        const legend = el.querySelector('legend');
        if (legend) {
          const text = legend.textContent.trim().replace(/\s*\*\s*$/, '');
          if (text && text.length > 1 && text.length < 200) return text;
        }
      }
      break;
    }

    el = el.parentElement;
    depth++;
  }

  // 6. Placeholder text as fallback
  if (input.placeholder) return input.placeholder;

  // 7. Derive readable name from field name/id
  return deriveReadableName(input.name || input.id || hiddenInput?.name || '');
}

/**
 * Search for a label-like element that is a direct child (or close descendant)
 * of the container, but NOT inside a deeper nested input group.
 * Returns the text, or empty string if not found.
 */
function findDirectLabelInContainer(container, inputEl) {
  // Prioritized list of label selectors (generic, no site-specific classes)
  const selectors = [
    'legend',                    // fieldset legend
    '[role="heading"]',          // ARIA headings
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',  // HTML headings
    'label',                     // standard labels
  ];

  for (const sel of selectors) {
    const candidates = container.querySelectorAll(sel);
    for (const candidate of candidates) {
      // Skip if the candidate contains the input (it's a wrapper, not a label)
      if (candidate.contains(inputEl)) continue;

      const text = candidate.textContent.trim().replace(/\s*\*\s*$/, '');
      if (!text || text.length < 2 || text.length > 200) continue;

      // Verify this label belongs to our input, not a sibling question.
      // The common parent of the label and input should be this container
      // (or a descendant of it), meaning they're in the same subtree.
      const commonParent = findCommonParent(candidate, inputEl);
      if (commonParent === container || container.contains(commonParent)) {
        return text;
      }
    }
  }

  return '';
}

/**
 * Check if ancestor is an ancestor of (or equal to) descendant.
 */
function isAncestorOf(ancestor, descendant) {
  let el = descendant;
  while (el) {
    if (el === ancestor) return true;
    el = el.parentElement;
  }
  return false;
}

/**
 * Find the nearest common parent of two elements.
 */
function findCommonParent(el1, el2) {
  const parents = new Set();
  let p = el1;
  while (p) { parents.add(p); p = p.parentElement; }
  p = el2;
  while (p) {
    if (parents.has(p)) return p;
    p = p.parentElement;
  }
  return document.body;
}

/**
 * Derive a human-readable name from a field name or ID.
 * Converts "applicant_name" -> "Applicant Name", "firstName" -> "First Name", etc.
 */
function deriveReadableName(fieldName) {
  if (!fieldName) return '';
  return fieldName
    .replace(/^entry\./, '')
    .replace(/[._-]/g, ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/\b\w/g, c => c.toUpperCase())
    .trim();
}

/**
 * Get DOM path for element
 */
function getDomPath(element) {
  const paths = [];
  while (element.parentElement) {
    let index = 0;
    let sibling = element;
    while ((sibling = sibling.previousElementSibling)) {
      index++;
    }
    const tagName = element.tagName.toLowerCase();
    const pathIndex = index ? `[${index}]` : '';
    paths.unshift(tagName + pathIndex);
    element = element.parentElement;
  }
  return paths.length ? '/' + paths.join('/') : '';
}

/**
 * Inject admin UI panel
 */
function injectAdminUI() {
  const panel = document.createElement('div');
  panel.id = 'eseva-admin-panel';
  panel.style.cssText = `
    position: fixed;
    top: 10px;
    right: 10px;
    width: 420px;
    max-height: 80vh;
    background: #fff;
    border: 2px solid #0066cc;
    border-radius: 8px;
    padding: 15px;
    font-family: Arial, sans-serif;
    font-size: 12px;
    z-index: 999999;
    overflow-y: auto;
    box-shadow: 0 2px 10px rgba(0, 0, 0, 0.2);
  `;

  const _hostname = window.location.hostname;
  const _fileId = window.location.pathname.split('/').pop().replace(/\.[^.]+$/, '').replace(/[^a-zA-Z0-9]/g, '_');
  const defaultServiceId = (_hostname ? _hostname.replace(/\./g, '_') : _fileId) || 'unknown_service';
  panel.innerHTML = `
    <div style="margin-bottom: 10px; font-weight: bold;">
      eSeva Admin Mapping Mode
      <button id="eseva-close-admin" style="float: right; cursor: pointer; background: none; border: none; font-size: 16px;">×</button>
    </div>
    <div style="margin-bottom: 8px;">
      <label style="font-size: 11px; color: #555; display: block; margin-bottom: 3px;">Service ID (config filename, no spaces):</label>
      <input id="eseva-service-id" type="text" value="${defaultServiceId}"
        style="width: 100%; box-sizing: border-box; padding: 5px 7px; border: 1px solid #aaa; border-radius: 4px; font-size: 12px; font-family: monospace;"
        placeholder="e.g. tn_community_certificate" />
    </div>
    <div id="eseva-fields-list"></div>
    <button id="eseva-save-config" style="width: 100%; padding: 8px; background: #0066cc; color: white; border: none; border-radius: 4px; cursor: pointer; margin-top: 10px;">
      Save Config
    </button>
    <div id="eseva-admin-message" style="margin-top: 10px; font-size: 12px; color: #666;"></div>
  `;

  document.body.appendChild(panel);

  document.getElementById('eseva-close-admin').addEventListener('click', () => {
    panel.remove();
    adminModeEnabled = false;
  });

  document.getElementById('eseva-save-config').addEventListener('click', () => {
    saveAdminMappings();
  });
}

/**
 * Display enumerated fields in admin panel with mapping dropdowns
 */
function displayFieldsInAdminPanel(fields, suggestions = [], draftConfig = null) {
  const list = document.getElementById('eseva-fields-list');
  if (!list) return;

  // Master schema options
  const masterOptions = [
    "name",
    "applicant_name",
    "father_name",
    "mother_name",
    "dob",
    "gender",
    "aadhaar",
    "mobile",
    "email",
    "address",
    "address_line1",
    "address_line2",
    "district",
    "taluk",
    "pincode",
    "income",
    "community",
    "religion",
    "marital_status",
    "occupation",
  ];

  // Build suggestion map: form_field_id -> master_field
  const suggestionMap = {};
  for (const s of suggestions) {
    suggestionMap[s.form_field] = s.master_field;
  }

  // Build persisted map: form_field_id -> master_field from draft config.
  // Supports both legacy string mappings and object mappings.
  const persistedMap = {};
  const configMappings = draftConfig?.field_mappings || {};
  for (const [masterField, mapping] of Object.entries(configMappings)) {
    for (const formFieldId of getMappedFieldIds(mapping)) {
      persistedMap[formFieldId] = masterField;
    }
  }
  const fileMappingHints = draftConfig?.file_mappings || {};

  // Separate text/select fields from file inputs
  const textFields = fields.filter(f => f.type !== 'file');
  const fileFields = fields.filter(f => f.type === 'file');

  let html = `<div style="border: 1px solid #ddd; padding: 10px; border-radius: 4px;">`;
  html += `<strong>Map ${textFields.length} form fields:</strong><br>`;

  for (const field of textFields) {
    const formKey = field.name || field.id || `(unnamed)`;
    const displayName = field.label || formKey;
    const suggested = persistedMap[formKey] || suggestionMap[formKey] || '';

    html += `
      <div style="margin-top: 8px; padding: 8px; background: #f5f5f5; border-radius: 4px;">
        <div style="font-weight: bold; color: #333;">${displayName}</div>
        <div style="font-size: 11px; color: #888; margin-top: 2px;">Field: ${formKey} | Type: ${field.type}</div>
        <select id="eseva-map-${formKey}" style="width: 100%; margin-top: 4px; font-size: 12px;">
          <option value="">-- unmapped --</option>
          ${masterOptions.map(opt => `<option value="${opt}" ${opt === suggested ? 'selected' : ''}>${opt}</option>`).join('')}
        </select>
      </div>
    `;
  }

  html += '</div>';

  // File upload mappings section
  if (fileFields.length) {
    html += `
      <div style="border: 1px solid #f59e0b; padding: 10px; border-radius: 4px; margin-top: 10px; background: #fffbeb;">
        <strong style="color: #92400e;">📎 File Upload Mappings (${fileFields.length} input${fileFields.length > 1 ? 's' : ''})</strong>
        <div style="font-size: 11px; color: #78716c; margin: 4px 0 8px;">Label each upload field so operators know what document to upload. This label is used to route compressed files to the right input.</div>
    `;
    for (const field of fileFields) {
      const formKey = field.name || field.id || `(unnamed)`;
      const displayName = field.label || formKey;
      html += `
        <div style="margin-top: 8px; padding: 8px; background: #fef3c7; border-radius: 4px;">
          <div style="font-weight: bold; color: #333;">${displayName}</div>
          <div style="font-size: 11px; color: #888; margin-top: 2px;">Field ID: ${formKey}</div>
          <input id="eseva-file-label-${formKey}" type="text" value="${fileMappingHints[formKey] || ''}" placeholder="e.g. Aadhaar Card, Passport Photo, Income Certificate…"
            style="width: 100%; margin-top: 4px; font-size: 12px; padding: 4px 6px; border: 1px solid #d97706; border-radius: 3px;">
        </div>
      `;
    }
    html += '</div>';
  }

  list.innerHTML = html;
  // Store fileFields on the list element so saveAdminMappings can read them
  list._fileFields = fileFields;
}

/**
 * Save admin mappings and send to backend
 */
async function saveAdminMappings(customServiceName = null) {
  const fields = enumerateFormFields();
  const urlParams = new URLSearchParams(window.location.search);
  // Prefer: explicit override → operator-edited input → URL param → hostname → filename
  const inputEl = document.getElementById('eseva-service-id');
  const inputValue = inputEl?.value?.trim().replace(/\s+/g, '_') || '';
  const _hn = window.location.hostname;
  const _fn = window.location.pathname.split('/').pop().replace(/\.[^.]+$/, '').replace(/[^a-zA-Z0-9]/g, '_');
  const defaultServiceId = (_hn ? _hn.replace(/\./g, '_') : _fn) || 'unknown_service';
  const serviceId = customServiceName || inputValue || urlParams.get('eseva_service_id') || defaultServiceId;
  const urlPatterns = [window.location.hostname || window.location.href];

  // Collect text field dropdown selections
  const approvedMappings = {};
  for (const field of fields) {
    const formKey = field.name || field.id || `(unnamed)`;
    const selectEl = document.getElementById(`eseva-map-${formKey}`);
    if (selectEl) {
      const master = selectEl.value;
      if (master) {
        if (!approvedMappings[master]) approvedMappings[master] = [];
        approvedMappings[master].push(formKey);
      }
    }
  }

  // Collect file upload label inputs → file_mappings: { field_id: "Attachment Label" }
  const fileMappings = {};
  const list = document.getElementById('eseva-fields-list');
  const fileFields = list?._fileFields || fields.filter(f => f.type === 'file');
  for (const field of fileFields) {
    const formKey = field.name || field.id || `(unnamed)`;
    const labelEl = document.getElementById(`eseva-file-label-${formKey}`);
    const label = labelEl?.value?.trim();
    if (label) fileMappings[formKey] = label;
  }

  const msgEl = document.getElementById('eseva-admin-message');
  if (msgEl) msgEl.textContent = 'Saving config...';

  try {
    console.log('[eSeva] Sending to backend:', { fields, serviceId, urlPatterns, approvedMappings, fileMappings });
    const response = await safeSend({
      type: 'ENUMERATE_FIELDS_ADMIN',
      fields: fields,
      serviceId: serviceId,
      urlPatterns: urlPatterns,
      approvedMappings: approvedMappings,
      fileMappings: fileMappings,
    });

    console.log('[eSeva] Backend response:', response);

    if (response.ok) {
      if (msgEl) msgEl.textContent = '✅ Config saved. Reload extension to use.';
      console.log('[eSeva] Config saved:', response);
      displayFieldsInAdminPanel(fields, response.suggestions || [], response.draft_config || null);
    } else {
      if (response.error === 'URL_PATTERN_CONFLICT') {
        // Show conflict details and options
        const details = response.details;
        const conflictMsg = `
          ⚠️ URL Pattern Conflict
          
          These URL patterns are already used by "${details.conflicts[0].service}":
          ${details.conflicts[0].overlapping_patterns.join(', ')}
          
          Options:
          1. Create a new service with a different name
          2. Use the existing service "${details.conflicts[0].service}"
          
          ${details.suggestion}
        `;
        if (msgEl) {
          msgEl.innerHTML = conflictMsg.replace(/\n/g, '<br>');
          msgEl.style.color = '#ff9800';
        }
        
        // Add buttons for user choice
        const panel = document.getElementById('eseva-admin-panel');
        if (panel) {
          const choiceDiv = document.createElement('div');
          choiceDiv.style.marginTop = '10px';
          choiceDiv.innerHTML = `
            <button id="eseva-create-new" style="background: #4CAF50; color: white; border: none; padding: 8px; margin-right: 8px; cursor: pointer;">Create New Service</button>
            <button id="eseva-use-existing" style="background: #2196F3; color: white; border: none; padding: 8px; cursor: pointer;">Use Existing Service</button>
          `;
          panel.appendChild(choiceDiv);
          
          const btnNew = document.getElementById('eseva-create-new');
          const btnExisting = document.getElementById('eseva-use-existing');
          const newHandler = () => {
            const newServiceName = prompt('Enter new service name:', details.conflicts[0].service + '_new');
            if (newServiceName) saveAdminMappings(newServiceName);
          };
          const existingHandler = () => {
            if (msgEl) msgEl.textContent = `ℹ️ Using existing service "${details.conflicts[0].service}". Please save mappings for that service instead.`;
          };
          btnNew.replaceWith(btnNew.cloneNode(true));
          btnExisting.replaceWith(btnExisting.cloneNode(true));
          document.getElementById('eseva-create-new').addEventListener('click', newHandler);
          document.getElementById('eseva-use-existing').addEventListener('click', existingHandler);
        }
      } else {
        if (msgEl) msgEl.textContent = '❌ Error: ' + response.error;
      }
    }
  } catch (err) {
    console.error('[eSeva] Network error:', err);
    if (msgEl) msgEl.textContent = '❌ Network error: ' + err.message;
  }
}

/**
 * Show temporary message to user
 */
function showMessage(msg, type = 'info') {
  const msgDiv = document.createElement('div');
  msgDiv.style.cssText = `
    position: fixed;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    padding: 20px;
    background: ${type === 'error' ? '#ff6b6b' : type === 'success' ? '#51cf66' : '#4dabf7'};
    color: white;
    border-radius: 8px;
    z-index: 999999;
    font-family: Arial, sans-serif;
    max-width: 400px;
  `;
  msgDiv.textContent = msg;
  document.body.appendChild(msgDiv);

  setTimeout(() => msgDiv.remove(), 4000);
}

/**
 * Listen for messages from popup or background
 */
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.type === 'FILL_FORM_AGAIN') {
    fillFormWithData(activeOperation).then(() => {
      sendResponse({ ok: true });
    });
    return true;
  }

  if (request.type === 'TOGGLE_ADMIN_MODE') {
    if (adminModeEnabled) {
      disableAdminMode();
    } else {
      enableAdminMode();
    }
    sendResponse({ ok: true, adminModeEnabled });
    return true;
  }
});

/**
 * Disable admin mode
 */
function disableAdminMode() {
  adminModeEnabled = false;
  const panel = document.getElementById('eseva-admin-panel');
  if (panel) panel.remove();
  console.log('[eSeva] Admin mode disabled');
}

console.log('[eSeva] Content script loaded');
