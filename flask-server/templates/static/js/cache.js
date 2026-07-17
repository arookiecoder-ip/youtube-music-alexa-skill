(function() {
  'use strict';

  const CACHE_DEVICES_KEY = 'cachedDevices';
  const CACHE_NP_KEY = 'cachedNowPlaying';
  let _npCacheKey = '';

  if (!window.__appState) window.__appState = {};
  if (window.__appState._paintedFromCache === undefined) window.__appState._paintedFromCache = false;

  function _cacheNowPlaying(np) {
    if (window.JAM_GUEST) return;
    if (!np || !np.title) return;
    const key = (np.video_id || '') + ':' + (np.queue ? np.queue.length : 'nq') + ':' + (np.queue_index ?? -1);
    if (key === _npCacheKey) return;
    _npCacheKey = key;
    try {
      const slim = {
        title: np.title,
        artist: np.artist || '',
        thumbnail: np.thumbnail || '',
        video_id: np.video_id || '',
        duration_ms: np.duration_ms || 0,
        position_ms: 0,
        playing: false,
        playback_confirmed: false,
        queue_index: np.queue_index ?? -1,
      };
      if (np.queue) {
        slim.queue = np.queue.slice(0, 50);
      } else {
        const prev = JSON.parse(localStorage.getItem(CACHE_NP_KEY) || 'null');
        if (prev && prev.queue) slim.queue = prev.queue;
      }
      localStorage.setItem(CACHE_NP_KEY, JSON.stringify(slim));
    } catch (_) {}
  }

  function restoreFromCache() {
    if (window.JAM_GUEST) return;
    try {
      const devices = JSON.parse(localStorage.getItem(CACHE_DEVICES_KEY) || 'null');
      if (!devices || !devices.length) return;
      window.__appState._paintedFromCache = true;
      if (window.showControls) window.showControls(true);
      if (window._applyDevices) window._applyDevices(devices, localStorage.getItem('selectedSerial') || '');
      const np = JSON.parse(localStorage.getItem(CACHE_NP_KEY) || 'null');
      if (np && np.title) {
        if (window.handleNpUpdate) window.handleNpUpdate(np);
        if (window.playStartupReveal) requestAnimationFrame(window.playStartupReveal);
      }
    } catch (_) {}
  }

  window.CACHE_DEVICES_KEY = CACHE_DEVICES_KEY;
  window.CACHE_NP_KEY = CACHE_NP_KEY;
  window._cacheNowPlaying = _cacheNowPlaying;
  window.restoreFromCache = restoreFromCache;
})();
