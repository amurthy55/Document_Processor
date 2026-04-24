const statusEl = document.getElementById('status');
const opEl = document.getElementById('op');
const tokenEl = document.getElementById('token');

async function getActiveTab() {
  const tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  return tabs && tabs[0] ? tabs[0] : null;
}

document.getElementById('bindBtn').addEventListener('click', async () => {
  statusEl.textContent = '';
  const op = opEl.value.trim();
  const token = tokenEl.value.trim();
  if (!op || !token) {
    statusEl.textContent = 'Operation ID and token are required.';
    return;
  }

  const tab = await getActiveTab();
  if (!tab || tab.id == null) {
    statusEl.textContent = 'No active tab found.';
    return;
  }

  console.log('[eSeva bind] Sending to background:', { tabId: tab.id, operationId: op, sessionToken: token });

  chrome.runtime.sendMessage(
    { type: 'BIND_OPERATION_TO_TAB', tabId: tab.id, operationId: op, sessionToken: token },
    (resp) => {
      console.log('[eSeva bind] Background response:', resp);
      if (!resp || !resp.ok) {
        statusEl.textContent = `Bind failed: ${(resp && resp.error) || 'unknown_error'}`;
        return;
      }
      statusEl.textContent = 'Bound. Reload the target page/window to inject.';
    }
  );
});
