(() => {
  const menuButton = document.getElementById('menuButton');
  const sidebar = document.getElementById('sidebar');
  if (menuButton && sidebar) {
    menuButton.addEventListener('click', () => sidebar.classList.toggle('open'));
  }

  window.appCsrfToken = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
  window.showToast = (message, type = '') => {
    const region = document.getElementById('toastRegion');
    if (!region) return;
    const toast = document.createElement('div');
    toast.className = `toast ${type}`.trim();
    toast.textContent = message;
    region.appendChild(toast);
    window.setTimeout(() => toast.remove(), 4200);
  };

  window.fetchJson = async (url, options = {}) => {
    const headers = new Headers(options.headers || {});
    headers.set('X-CSRF-Token', window.appCsrfToken());
    if (options.body && !(options.body instanceof FormData)) {
      headers.set('Content-Type', 'application/json');
    }
    const response = await fetch(url, { ...options, headers });
    let payload;
    try {
      payload = await response.json();
    } catch (_) {
      payload = { ok: false, error: `Request failed with status ${response.status}.` };
    }
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || `Request failed with status ${response.status}.`);
    }
    return payload;
  };
})();
