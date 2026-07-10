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

  // Phase 12: Extract error message from both old (string) and new (structured) envelope.
  function _extractError(json) {
    if (!json) return '';
    var err = json.error;
    if (typeof err === 'string') return err;
    if (err && typeof err.message === 'string') return err.message;
    return '';
  }

  // Generous: a cold /api/home build or a slow search can take >15s server-side.
  var API_TIMEOUT_MS = 30000;

  // v3.1: Shared error response handler — throws appropriate Error for 429, 502/503, and other statuses.
  function _handleErrorResponse(res, json) {
    var errMsg = _extractError(json);
    if (res.status === 429) {
      var retryAfter = res.headers.get('Retry-After');
      var waitMsg = retryAfter ? ' Try again in ' + retryAfter + 's.' : '';
      throw new Error(errMsg || ('Server is busy.' + waitMsg));
    }
    if (res.status === 502 || res.status === 503) {
      throw new Error(errMsg || 'Device is offline or unreachable.');
    }
    throw new Error(errMsg || ('HTTP ' + res.status));
  }

  // Phase 12: Fetch helper with AbortController timeout.
  function _fetchWithTimeout(url, opts) {
    var controller = new AbortController();
    var timer = setTimeout(function() { controller.abort(); }, API_TIMEOUT_MS);
    return fetch(url, Object.assign({}, opts, { signal: controller.signal }))
      .then(function(res) { clearTimeout(timer); return res; })
      .catch(function(err) {
        clearTimeout(timer);
        if (err.name === 'AbortError') {
          var timeoutErr = new Error('Request timed out. Check your connection and try again.');
          timeoutErr._isTimeout = true;
          throw timeoutErr;
        }
        throw err;
      });
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
      res = await _fetchWithTimeout(path, opts);
    } catch (e) {
      throw new Error(e._isTimeout
        ? e.message
        : "Can't reach the server. Check your connection and try again.");
    }
    if (res.status === 401) throw _onUnauthorized();
    const json = await res.json().catch(() => ({}));
    if (!res.ok) _handleErrorResponse(res, json);
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
      res = await _fetchWithTimeout(path, {
        method: 'DELETE',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
    } catch (e) {
      throw new Error(e._isTimeout
        ? e.message
        : "Can't reach the server. Check your connection and try again.");
    }
    if (res.status === 401) throw _onUnauthorized();
    const json = await res.json().catch(() => ({}));
    if (!res.ok) _handleErrorResponse(res, json);
    return json;
  }

  async function apiPatch(path, body) {
    let res;
    try {
      res = await _fetchWithTimeout(path, {
        method: 'PATCH',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
      });
    } catch (e) {
      throw new Error(e._isTimeout
        ? e.message
        : "Can't reach the server. Check your connection and try again.");
    }
    if (res.status === 401) throw _onUnauthorized();
    const json = await res.json().catch(() => ({}));
    if (!res.ok) _handleErrorResponse(res, json);
    return json;
  }

  window.api = api;
  window.apiDelete = apiDelete;
  window.apiPatch = apiPatch;
  window.escHtml = escHtml;
  window._onUnauthorized = _onUnauthorized;
})();
