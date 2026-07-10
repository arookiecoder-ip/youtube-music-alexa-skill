(function() {
  'use strict';

  // Shared 401 handling for api/apiDelete/apiPatch. For a jam guest a 401
  // usually means the host ended the jam, but it can also be a forbidden action.
  function _onUnauthorized() {
    if (window.JAM_GUEST) {
      fetch('/alexa/status/', { credentials: 'same-origin', cache: 'no-store' })
        .then((r) => {
          if (r.status === 401 && window.showJamEnded) window.showJamEnded();
          else if (window.toast) window.toast("That action isn't available in a jam.", 'error');
        })
        .catch(() => { /* network hiccup: leave the UI alone */ });
      return new Error('Not available in this jam');
    }
    if (window.toast) window.toast('Session expired - please log in again.', 'error');
    setTimeout(function() { window.location.href = '/login/'; }, 2000);
    return new Error('Session expired');
  }

  async function api(path, body) {
    const opts = body === undefined
      ? { credentials: 'same-origin', cache: 'no-store' }
      : {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        };
    let res;
    try {
      res = await fetch(path, opts);
    } catch (_) {
      throw new Error("Can't reach the server. Check your connection and try again.");
    }
    if (res.status === 401) throw _onUnauthorized();
    const json = await res.json().catch(() => ({}));
    if (!res.ok) {
      if (res.status === 502 || res.status === 503) {
        throw new Error(json.error || 'Device is offline or unreachable.');
      }
      throw new Error(json.error || ('HTTP ' + res.status));
    }
    return json;
  }

  function escHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  async function apiDelete(path) {
    let res;
    try {
      res = await fetch(path, {
        method: 'DELETE',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
    } catch (_) {
      throw new Error("Can't reach the server. Check your connection and try again.");
    }
    if (res.status === 401) throw _onUnauthorized();
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json.error || ('HTTP ' + res.status));
    return json;
  }

  async function apiPatch(path, body) {
    let res;
    try {
      res = await fetch(path, {
        method: 'PATCH',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
      });
    } catch (_) {
      throw new Error("Can't reach the server. Check your connection and try again.");
    }
    if (res.status === 401) throw _onUnauthorized();
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json.error || ('HTTP ' + res.status));
    return json;
  }

  window.api = api;
  window.apiDelete = apiDelete;
  window.apiPatch = apiPatch;
  window.escHtml = escHtml;
  window._onUnauthorized = _onUnauthorized;
})();
