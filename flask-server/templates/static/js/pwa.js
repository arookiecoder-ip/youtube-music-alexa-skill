(function() {
  'use strict';

  var _deferredPrompt = null;
  var _installBtn = null;
  var _installBanner = null;
  var _updateToast = null;
  var _bc = null;
  // True while applying a state update received from another tab. Suppresses
  // re-broadcasting it, which would otherwise ping-pong between tabs forever.
  var _applyingRemote = false;

  /* ── Multi-tab state sync via BroadcastChannel ── */
  function _initBroadcastChannel() {
    try {
      _bc = new BroadcastChannel('music-box-sync');
    } catch (_) {
      return; // BroadcastChannel not supported
    }

    _bc.onmessage = function(e) {
      if (!e.data) return;
      var type = e.data.type;

      if (type === 'np-update' && e.data.np) {
        if (window.handleNpUpdate) {
          _applyingRemote = true;
          try { window.handleNpUpdate(e.data.np); } finally { _applyingRemote = false; }
        }
      } else if (type === 'queue-update' && e.data.queue) {
        // Keep the shared queue identity/index cache in sync before rendering.
        // Later SSE broadcasts intentionally omit an unchanged queue and rely
        // on this cache to resolve the active row by video_id.
        if (typeof e.data.queueIndex === 'number') {
          var queueJson = JSON.stringify(e.data.queue);
          window._lastQueueJson = queueJson;
          window._lastQueueIndex = e.data.queueIndex;
          if (window.__appState) {
            window.__appState._lastQueueJson = queueJson;
            window.__appState._lastQueueIndex = e.data.queueIndex;
          }
          if (window.showQueue) {
            window.showQueue(e.data.queue, e.data.queueIndex);
          }
        }
      } else if (type === 'liked-update') {
        // Trigger a playlist refresh
        if (!window.JAM_GUEST && window.loadLibrary) {
          setTimeout(window.loadLibrary, 100);
        }
      } else if (type === 'tab-focus') {
        // Another tab was focused — refresh our now-playing state
        if (window.selectedSerial && window.schedulePollNowPlaying) {
          window.schedulePollNowPlaying(200);
        }
      }
    };
  }

  function broadcastNpUpdate(np) {
    if (!_bc || !np || _applyingRemote) return;
    try {
      _bc.postMessage({
        type: 'np-update',
        np: {
          title: np.title,
          artist: np.artist,
          thumbnail: np.thumbnail,
          video_id: np.video_id,
          playing: np.playing,
          duration_ms: np.duration_ms,
          position_ms: np.position_ms,
          queue_index: np.queue_index,
        }
      });
    } catch (_) {}
  }

  function broadcastQueueUpdate(queue, queueIndex) {
    if (!_bc || !queue || _applyingRemote) return;
    try {
      _bc.postMessage({
        type: 'queue-update',
        queue: queue,
        queueIndex: queueIndex,
      });
    } catch (_) {}
  }

  function broadcastLikedUpdate() {
    if (!_bc) return;
    try {
      _bc.postMessage({ type: 'liked-update' });
    } catch (_) {}
  }

  function broadcastTabFocus() {
    if (!_bc) return;
    try {
      _bc.postMessage({ type: 'tab-focus' });
    } catch (_) {}
  }

  /* ── Install prompt ── */
  function _createInstallBanner() {
    if (_installBanner) return;
    _installBanner = document.createElement('div');
    _installBanner.className = 'install-banner';
    _installBanner.innerHTML =
      '<div class="install-banner-content">' +
        '<div class="install-banner-icon">' +
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">' +
            '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>' +
          '</svg>' +
        '</div>' +
        '<div class="install-banner-text">' +
          '<div class="install-banner-title">Install Music Box</div>' +
          '<div class="install-banner-sub">Get the app for quick access</div>' +
        '</div>' +
        '<button class="install-banner-btn" id="install-btn">Install</button>' +
        '<button class="install-banner-dismiss" id="install-dismiss-btn" title="Dismiss">' +
          '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">' +
            '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>' +
          '</svg>' +
        '</button>' +
      '</div>';

    document.body.appendChild(_installBanner);

    _installBtn = document.getElementById('install-btn');
    var dismissBtn = document.getElementById('install-dismiss-btn');

    _installBtn.addEventListener('click', function() {
      if (!_deferredPrompt) return;
      _deferredPrompt.prompt();
      _deferredPrompt.userChoice.then(function(choiceResult) {
        if (choiceResult.outcome === 'accepted') {
          hideInstallBanner();
        }
        _deferredPrompt = null;
      });
    });

    dismissBtn.addEventListener('click', function() {
      hideInstallBanner();
      // Don't show again for 7 days
      try { localStorage.setItem('pwa-install-dismissed', Date.now()); } catch (_) {}
    });
  }

  function showInstallBanner() {
    if (!_installBanner) _createInstallBanner();
    // Check if user dismissed recently (7 days)
    try {
      var dismissed = parseInt(localStorage.getItem('pwa-install-dismissed') || '0', 10);
      if (Date.now() - dismissed < 7 * 24 * 60 * 60 * 1000) return;
    } catch (_) {}
    // Don't show on jam guest
    if (window.JAM_GUEST) return;
    // Don't show if already installed (display-mode: standalone)
    if (window.matchMedia('(display-mode: standalone)').matches) return;
    _installBanner.classList.add('visible');
  }

  function hideInstallBanner() {
    if (_installBanner) _installBanner.classList.remove('visible');
  }

  /* ── Version update notification ── */
  function _createUpdateToast() {
    if (_updateToast) return;
    _updateToast = document.createElement('div');
    _updateToast.className = 'update-toast';
    _updateToast.innerHTML =
      '<div class="update-toast-content">' +
        '<span class="update-toast-text">A new version is available</span>' +
        '<button class="update-toast-btn" id="update-reload-btn">Reload</button>' +
        '<button class="update-toast-close" id="update-dismiss-btn" title="Dismiss">' +
          '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">' +
            '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>' +
          '</svg>' +
        '</button>' +
      '</div>';
    document.body.appendChild(_updateToast);

    document.getElementById('update-reload-btn').addEventListener('click', function() {
      window.location.reload();
    });

    document.getElementById('update-dismiss-btn').addEventListener('click', function() {
      _updateToast.classList.remove('visible');
    });
  }

  function showUpdateNotification() {
    if (!_updateToast) _createUpdateToast();
    _updateToast.classList.add('visible');
    // Auto-dismiss after 30 seconds
    setTimeout(function() {
      if (_updateToast) _updateToast.classList.remove('visible');
    }, 30000);
  }

  /* ── Initialization ── */
  function init() {
    _initBroadcastChannel();

    // Listen for install prompt
    window.addEventListener('beforeinstallprompt', function(e) {
      // Check if user dismissed recently (7 days)
      try {
        var dismissed = parseInt(localStorage.getItem('pwa-install-dismissed') || '0', 10);
        if (Date.now() - dismissed < 7 * 24 * 60 * 60 * 1000) return;
      } catch (_) {}
      // Don't show on jam guest
      if (window.JAM_GUEST) return;
      // Don't show if already installed (display-mode: standalone)
      if (window.matchMedia('(display-mode: standalone)').matches) return;

      e.preventDefault();
      _deferredPrompt = e;
      showInstallBanner();
    });

    // Listen for successful installation
    window.addEventListener('appinstalled', function() {
      hideInstallBanner();
      _deferredPrompt = null;
    });

    // Listen for service worker updates
    if ('serviceWorker' in navigator) {
      // On a first visit the SW claiming the page also fires controllerchange;
      // only a change from an *existing* controller is a real update.
      var hadController = !!navigator.serviceWorker.controller;
      navigator.serviceWorker.addEventListener('controllerchange', function() {
        if (hadController) showUpdateNotification();
        hadController = true;
      });
    }

    // Broadcast tab focus to other tabs
    document.addEventListener('visibilitychange', function() {
      if (!document.hidden) {
        broadcastTabFocus();
      }
    });
  }

  // Export public API
  window._initPwa = init;
  window.broadcastNpUpdate = broadcastNpUpdate;
  window.broadcastQueueUpdate = broadcastQueueUpdate;
  window.broadcastLikedUpdate = broadcastLikedUpdate;
  window.showUpdateNotification = showUpdateNotification;
})();
