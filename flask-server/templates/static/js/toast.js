(function() {
  'use strict';

  const toastEl = window.toastEl || document.getElementById('toast');
  const deviceEl = window.deviceEl || document.getElementById('device');

  function toast(msg, kind, detail, playAttemptVideoId) {
    const state = window.__appState;
    if (!toastEl || !state) return;
    if (!msg) {
      toastEl.classList.remove('show');
      state.lastToastMsg = '';
      state.lastToastKind = '';
      return;
    }
    kind = kind || 'info';
    if (toastEl.classList.contains('show') && msg === state.lastToastMsg && kind === state.lastToastKind && !detail) return;
    state.lastToastMsg = msg;
    state.lastToastKind = kind;
    toastEl.className = 'toast show ' + kind;
    toastEl.querySelectorAll('.toast-close, .toast-detail, .toast-retry').forEach(function(el) { el.remove(); });
    toastEl.querySelectorAll('.toast-primary').forEach(function(el) { el.remove(); });

    var primary = document.createElement('span');
    primary.className = 'toast-primary';
    primary.textContent = msg;
    toastEl.appendChild(primary);

    if (detail) {
      var detailEl = document.createElement('span');
      detailEl.className = 'toast-detail';
      detailEl.textContent = detail;
      toastEl.appendChild(detailEl);
    }

    if (kind === 'error') {
      var closeBtn = document.createElement('button');
      closeBtn.className = 'toast-close';
      closeBtn.innerHTML = '&times;';
      closeBtn.onclick = function() { toast('', 'error'); };
      toastEl.appendChild(closeBtn);
    }

    if (kind === 'error' && playAttemptVideoId) {
      var retryBtn = document.createElement('button');
      retryBtn.className = 'toast-retry';
      retryBtn.textContent = 'Retry';
      retryBtn.onclick = function() {
        window.api('/alexa/play_queue/', { serial: deviceEl.value, video_id: playAttemptVideoId });
      };
      toastEl.appendChild(retryBtn);
    }

    clearTimeout(state.toastTimer);
    if (!detail) {
      var delay = kind === 'error' ? 5000 : kind === 'ok' ? 2500 : 3500;
      state.toastTimer = setTimeout(function() {
        toastEl.classList.remove('show');
        state.lastToastMsg = '';
        state.lastToastKind = '';
      }, delay);
    }
  }

  window.toast = toast;
})();
