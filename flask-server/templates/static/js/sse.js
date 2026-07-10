(function() {
  'use strict';

  const deviceEl = window.deviceEl || document.getElementById('device');
  let _evtSource = null;
  let _evtSourceSerial = '';
  let _lastHistoryVideoId = null;
  let _rafQueuedData = null;
  let _rafQueuedIndex = -1;
  let _rafPending = false;
  let _pollNowPlayingInFlight = false;
  let _pollNowPlayingRetry = false;
  let _pollNowPlayingTimer = null;
  let _pollNowPlayingDueAt = 0;

  function state() { return window.__appState; }

  function handleNpUpdate(np) {
    const npVideoId = (np && np.video_id) || null;
    if (window._cacheNowPlaying) window._cacheNowPlaying(np);
    // Broadcast to other tabs via PWA BroadcastChannel
    if (window.broadcastNpUpdate && np && np.title) window.broadcastNpUpdate(np);
    if (window.checkLikedVersion) window.checkLikedVersion(np);
    if (!window.JAM_GUEST && npVideoId && npVideoId !== _lastHistoryVideoId) {
      _lastHistoryVideoId = npVideoId;
      if (window.loadHistory) setTimeout(window.loadHistory, 1500);
    }
    if (np.playing && window.selectedDeviceOnline && !window.selectedDeviceOnline()) {
      np = Object.assign({}, np, { playing: false });
    }
    if (np.volume !== undefined && np.volume !== null && window.syncVolume) window.syncVolume(np.volume);
    if (np.playback_error) {
      var errType = (typeof np.playback_error === 'object') ? (np.playback_error.type || 'unknown') : 'unknown';
      var errMsg = (typeof np.playback_error === 'object') ? (np.playback_error.message || '') : np.playback_error;
      state()._lastPlaybackError = { type: errType, message: errMsg };
      if (window.toast) window.toast(errMsg, 'error', 'Error code: ' + errType + ' - ' + errMsg, state()._lastPlayAttemptVideoId);
      state().isPlaying = false;
      state().lastActionIntent = false;
      if (window.syncPlayPause) window.syncPlayPause();
    }
    if (np.title) {
      if (window.showNowPlaying) window.showNowPlaying(np);
      if (np.playing !== undefined) {
        const inGrace = (Date.now() - state().lastActionAt) < state().GRACE_MS;
        const contradictsIntent = inGrace && state().lastActionIntent !== null && np.playing !== state().lastActionIntent;
        if (!contradictsIntent && (np.playing || !inGrace)) {
          state().isPlaying = np.playing;
          if (window.syncPlayPause) window.syncPlayPause();
        }
      }
    } else {
      if (window.showNowPlaying) window.showNowPlaying(null);
      if (np.playing !== undefined) {
        const inGrace = (Date.now() - state().lastActionAt) < state().GRACE_MS;
        const contradictsIntent = inGrace && state().lastActionIntent !== null && np.playing !== state().lastActionIntent;
        if (!contradictsIntent && !inGrace) {
          state().isPlaying = np.playing;
          if (window.syncPlayPause) window.syncPlayPause();
        }
      }
    }
    if (window.progress) window.progress.update(np);
    window._lastQueueIndex = np.queue_index ?? -1;
    if (np.queue !== undefined && np.queue !== null) {
      _rafQueuedData = np.queue;
      _rafQueuedIndex = window._lastQueueIndex;
      if (!_rafPending) {
        _rafPending = true;
        requestAnimationFrame(() => {
          _rafPending = false;
          const qJson = JSON.stringify(_rafQueuedData);
          if (qJson !== window._lastQueueJson) {
            window._lastQueueJson = qJson;
            if (window.showQueue) window.showQueue(_rafQueuedData, _rafQueuedIndex);
            refreshQueueModalIfOpen();
            // Broadcast queue update to other tabs via PWA BroadcastChannel
            if (window.broadcastQueueUpdate) window.broadcastQueueUpdate(_rafQueuedData, _rafQueuedIndex);
          } else if (window.updateQueueActive) {
            window.updateQueueActive(_rafQueuedIndex);
          }
        });
      }
    } else {
      for (const id of ['queue-list', 'queue-modal-body']) {
        const container = document.getElementById(id);
        if (container && container._lazyQueue && window._renderedQueueRows &&
            window._lastQueueIndex >= window._renderedQueueRows(container).length &&
            window._appendLazyQueueRows) {
          window._appendLazyQueueRows(container, window._lastQueueIndex + 11);
        }
      }
      if (window.updateQueueActive) window.updateQueueActive(window._lastQueueIndex);
      if (window.updateQueueModalActive) window.updateQueueModalActive(window._lastQueueIndex);
    }
  }

  function refreshQueueModalIfOpen() {
    const overlay = document.getElementById('queue-modal-overlay');
    if (overlay && overlay.classList.contains('open') && window._renderQueueModal) {
      window._renderQueueModal();
    }
  }

  function connectSSE() {
    const serial = deviceEl.value;
    if (!serial) return;
    if (_evtSource && _evtSourceSerial === serial) return;
    stopSSE();
    _evtSourceSerial = serial;
    _evtSource = new EventSource('/alexa/now_playing/stream?serial=' + encodeURIComponent(serial));
    _evtSource.onmessage = (e) => {
      try { handleNpUpdate(JSON.parse(e.data)); } catch (_) {}
    };
    _evtSource.onerror = () => {
      // SSE auto-reconnects.
    };
  }

  function stopSSE() {
    if (_evtSource) { _evtSource.close(); _evtSource = null; }
    _evtSourceSerial = '';
  }

  async function pollNowPlaying() {
    const serial = deviceEl.value;
    if (!serial) return;
    if (_pollNowPlayingInFlight) {
      _pollNowPlayingRetry = true;
      return;
    }
    _pollNowPlayingInFlight = true;
    try {
      const np = await window.api('/alexa/now_playing/?serial=' + encodeURIComponent(serial));
      handleNpUpdate(np);
      if (window.refreshVolume) window.refreshVolume(false);
    } catch (_) {
      // Best-effort; SSE remains primary.
    } finally {
      _pollNowPlayingInFlight = false;
      if (_pollNowPlayingRetry) {
        _pollNowPlayingRetry = false;
        schedulePollNowPlaying(300);
      }
    }
  }

  function schedulePollNowPlaying(delayMs) {
    const dueAt = Date.now() + delayMs;
    if (_pollNowPlayingTimer && _pollNowPlayingDueAt <= dueAt) return;
    clearTimeout(_pollNowPlayingTimer);
    _pollNowPlayingDueAt = dueAt;
    _pollNowPlayingTimer = setTimeout(() => {
      _pollNowPlayingTimer = null;
      _pollNowPlayingDueAt = 0;
      pollNowPlaying();
    }, delayMs);
  }

  document.addEventListener('visibilitychange', () => {
    if (window.progress) window.progress.syncLoop();
    if (document.hidden) stopSSE();
    else if (deviceEl.value) {
      connectSSE();
      if (window.refreshVolume) window.refreshVolume(true);
    }
  });
  window.addEventListener('pagehide', stopSSE);
  window.addEventListener('pageshow', () => {
    if (deviceEl.value && !document.hidden) connectSSE();
  });
  window.addEventListener('focus', () => {
    if (window.refreshVolume) window.refreshVolume(false);
  });

  if (deviceEl) {
    deviceEl.addEventListener('change', () => {
      if (deviceEl.value) {
        localStorage.setItem('selectedSerial', deviceEl.value);
        if (window.selectedDeviceOnline && !window.selectedDeviceOnline() && window.toast) {
          window.toast('That device is offline. Commands may not reach it.', 'error');
        }
        connectSSE();
        if (window.refreshVolume) window.refreshVolume(true);
      }
    });
  }

  window.handleNpUpdate = handleNpUpdate;
  window.refreshQueueModalIfOpen = refreshQueueModalIfOpen;
  window.connectSSE = connectSSE;
  window.stopSSE = stopSSE;
  window.pollNowPlaying = pollNowPlaying;
  window.schedulePollNowPlaying = schedulePollNowPlaying;
})();
