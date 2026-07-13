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

  let _activeRequests = 0;
  let _progressTimeout = null;

  function _showProgress(url) {
    if (url.includes('/alexa/status') || url.includes('/alexa/now_playing') || url.includes('/alexa/command') || url.includes('/alexa/jam/status') || url.includes('/alexa/volume') || url.includes('/alexa/seek')) return;
    _activeRequests++;
    if (_activeRequests === 1) {
      const bar = document.getElementById('top-progress-bar');
      if (bar) {
        clearTimeout(_progressTimeout);
        bar.classList.remove('done');
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            bar.classList.add('loading');
          });
        });
      }
    }
  }

  function _hideProgress(url) {
    if (url.includes('/alexa/status') || url.includes('/alexa/now_playing') || url.includes('/alexa/command') || url.includes('/alexa/jam/status') || url.includes('/alexa/volume') || url.includes('/alexa/seek')) return;
    _activeRequests = Math.max(0, _activeRequests - 1);
    if (_activeRequests === 0) {
      const bar = document.getElementById('top-progress-bar');
      if (bar) {
        bar.classList.remove('loading');
        bar.classList.add('done');
        _progressTimeout = setTimeout(() => {
          bar.classList.remove('done');
          bar.style.width = '0';
        }, 400);
      }
    }
  }

  // Phase 12: Fetch helper with AbortController timeout.
  function _fetchWithTimeout(url, opts) {
    _showProgress(url);
    var controller = new AbortController();
    var timer = setTimeout(function() { controller.abort(); }, API_TIMEOUT_MS);
    return fetch(url, Object.assign({}, opts, { signal: controller.signal }))
      .then(function(res) { clearTimeout(timer); _hideProgress(url); return res; })
      .catch(function(err) {
        clearTimeout(timer);
        _hideProgress(url);
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
    if (res.status === 401 || (res.redirected && res.url.includes('/login'))) throw _onUnauthorized();
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

  // Catalog endpoints expose length in different shapes. Keep every song row
  // consistent: prefer the ready-to-display string, otherwise format seconds.
  window.formatTrackDuration = function(track) {
    if (!track) return '';
    var value = track.duration || track.length || '';
    if (typeof value === 'string' && /^\d{1,2}:\d{2}(?::\d{2})?$/.test(value.trim())) return value.trim();
    var seconds = Number(track.duration_seconds || track.lengthSeconds || 0);
    if (!seconds && track.duration_ms) seconds = Number(track.duration_ms) / 1000;
    seconds = Math.floor(seconds || 0);
    if (!seconds) return '';
    var hours = Math.floor(seconds / 3600);
    var minutes = Math.floor((seconds % 3600) / 60);
    var secs = seconds % 60;
    return hours
      ? hours + ':' + String(minutes).padStart(2, '0') + ':' + String(secs).padStart(2, '0')
      : minutes + ':' + String(secs).padStart(2, '0');
  };

  async function apiDelete(path, body) {
    let res;
    try {
      res = await _fetchWithTimeout(path, {
        method: 'DELETE',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
      });
    } catch (e) {
      throw new Error(e._isTimeout
        ? e.message
        : "Can't reach the server. Check your connection and try again.");
    }
    if (res.status === 401 || (res.redirected && res.url.includes('/login'))) throw _onUnauthorized();
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
    if (res.status === 401 || (res.redirected && res.url.includes('/login'))) throw _onUnauthorized();
    const json = await res.json().catch(() => ({}));
    if (!res.ok) _handleErrorResponse(res, json);
    return json;
  }

  window.api = api;
  window.apiDelete = apiDelete;
  window.apiPatch = apiPatch;
  window.escHtml = escHtml;
  window._onUnauthorized = _onUnauthorized;

  // Globally hide broken images
  window.addEventListener('error', function(e) {
    if (e.target && e.target.tagName === 'IMG') {
      e.target.style.opacity = '0';
    }
  }, true);

  // Fix protocol-relative URLs in API responses
  function fixUrls(obj) {
    if (!obj) return obj;
    if (Array.isArray(obj)) {
      for (let i = 0; i < obj.length; i++) obj[i] = fixUrls(obj[i]);
    } else if (typeof obj === 'object') {
      for (const k in obj) {
        if (typeof obj[k] === 'string') {
          if (obj[k].startsWith('//')) {
            obj[k] = 'https:' + obj[k];
          } else if (!obj[k].startsWith('http') && !obj[k].startsWith('/') && (obj[k].includes('googleusercontent.com') || obj[k].includes('ggpht.com'))) {
            obj[k] = 'https://' + obj[k];
          }
        } else if (typeof obj[k] === 'object') {
          obj[k] = fixUrls(obj[k]);
        }
      }
    }
    return obj;
  }

  // Wrap the original api to fix URLs
  const originalApi = api;
  window.api = async function(path, body) {
    const json = await originalApi(path, body);
    return fixUrls(json);
  };
})();
