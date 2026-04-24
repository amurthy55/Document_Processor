/**
 * eSevaCenter Extension - Popup Script
 * 
 * Manages the popup UI for operation control
 */

let currentOperation = null;

/**
 * Initialize popup
 */
document.addEventListener('DOMContentLoaded', async () => {
  setupEventListeners();
  updateStatus();
});

/**
 * Setup button event listeners
 */
function setupEventListeners() {
  document.getElementById('btn-fetch-operation').addEventListener('click', fetchOperation);
  document.getElementById('btn-refill-form').addEventListener('click', refillForm);
}

/**
 * Update status display
 */
async function updateStatus() {
  try {
    const response = await chrome.runtime.sendMessage({
      type: 'GET_ACTIVE_OPERATION',
    });

    const statusSection = document.getElementById('status-section');
    const message = document.getElementById('message-container');

    if (response.ok && response.operation) {
      currentOperation = response.operation;
      
      statusSection.innerHTML = `
        <div class="status">
          <div class="status-label">Active Operation</div>
          <div class="status-value active">✓ Ready</div>
        </div>
      `;

      document.getElementById('op-id').textContent = currentOperation.operation_id.substring(0, 8) + '...';
      document.getElementById('op-service').textContent = currentOperation.service_config?.service_name || 'Unknown';
      document.getElementById('op-status').textContent = currentOperation.status;

      document.getElementById('operation-section').style.display = 'block';

      // Show/hide buttons
      document.getElementById('btn-fetch-operation').style.display = 'none';
      document.getElementById('btn-refill-form').style.display = 'block';

      showMessage('Operation loaded successfully', 'success');
    } else {
      statusSection.innerHTML = `
        <div class="status">
          <div class="status-label">Status</div>
          <div class="status-value inactive">No Active Operation</div>
        </div>
      `;

      document.getElementById('operation-section').style.display = 'none';
      document.getElementById('btn-fetch-operation').style.display = 'block';
      document.getElementById('btn-refill-form').style.display = 'none';
    }
  } catch (err) {
    console.error('Status update failed:', err);
    showMessage('Error: ' + err.message, 'error');
  }
}

/**
 * Fetch active operation
 */
async function fetchOperation() {
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tabs || tabs.length === 0) {
      showMessage('No active tab found', 'error');
      return;
    }

    showMessage('Fetching operation...', 'info');

    // Send message to content script to fetch operation
    chrome.tabs.sendMessage(tabs[0].id, {
      type: 'FETCH_ACTIVE_OPERATION',
    }, (response) => {
      if (chrome.runtime.lastError) {
        showMessage('Error: ' + chrome.runtime.lastError.message, 'error');
        return;
      }

      if (response && response.ok) {
        showMessage('Operation loaded!', 'success');
        updateStatus();
      } else {
        showMessage('Error: ' + (response?.error || 'Unknown error'), 'error');
      }
    });
  } catch (err) {
    showMessage('Error: ' + err.message, 'error');
  }
}

/**
 * Refill form with data
 */
async function refillForm() {
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tabs || tabs.length === 0) {
      showMessage('No active tab found', 'error');
      return;
    }

    showMessage('Refilling form...', 'info');

    chrome.tabs.sendMessage(tabs[0].id, {
      type: 'FILL_FORM_AGAIN',
    }, (response) => {
      if (chrome.runtime.lastError) {
        showMessage('Error: ' + chrome.runtime.lastError.message, 'error');
        return;
      }

      if (response?.ok) {
        showMessage('Form refilled!', 'success');
      } else {
        showMessage('Refill failed', 'error');
      }
    });
  } catch (err) {
    showMessage('Error: ' + err.message, 'error');
  }
}

/**
 * Show message to user
 */
function showMessage(text, type = 'info') {
  const container = document.getElementById('message-container');
  
  const msgDiv = document.createElement('div');
  msgDiv.className = `message ${type}`;
  msgDiv.textContent = text;

  container.insertBefore(msgDiv, container.firstChild);

  setTimeout(() => {
    msgDiv.remove();
  }, 4000);
}
