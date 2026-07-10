(function() {
  'use strict';

  const toastEl = document.getElementById('toast');
  const deviceEl = document.getElementById('device');

  window.__appState = Object.assign({
    isPlaying: false,
    toastTimer: null,
    lastToastMsg: '',
    lastToastKind: '',
    lastActionAt: 0,
    lastActionIntent: null,
    _lastPlaybackError: null,
    _lastPlayAttemptVideoId: '',
    GRACE_MS: 8000,
    _currentVideoId: '',
    _currentThumbnail: '',
    volumeUserActive: false,
    volumeGraceUntil: 0,
    VOLUME_GRACE_MS: 4000,
    _volCommandSeq: 0,
    lastVolumeRefreshAt: 0,
    _hasTrack: false,
    _resultsOpen: false,
    _loggedIn: false,
  }, window.__appState || {});

  function syncUiState() {
    const state = window.__appState;
    const mainEl = document.querySelector('main');
    const player = document.querySelector('.player-section');
    const mini = document.getElementById('mini-player');
    const clearBtn = document.getElementById('clear-all-btn');
    if (clearBtn) clearBtn.hidden = !(state._hasTrack || state._resultsOpen);
    document.body.classList.toggle('results-open', state._resultsOpen);
    if (mini) mini.classList.toggle('visible', state._resultsOpen && state._hasTrack);
    if (mainEl) mainEl.classList.toggle('idle', state._loggedIn && !state._hasTrack && !state._resultsOpen);
    const recsSection = document.getElementById('recs-section');
    if (recsSection) {
      const shouldShow = state._loggedIn && !state._resultsOpen && !state._hasTrack;
      if (shouldShow && !window._recsLoaded && window.loadRecommendations) window.loadRecommendations();
      else recsSection.hidden = !shouldShow || !window._recsLoaded;
    }
    if (!player) return;
    if (state._hasTrack) {
      clearTimeout(player._hideTimer);
      player.classList.remove('is-collapsed');
      requestAnimationFrame(() => player.classList.add('is-visible'));
    } else {
      player.classList.remove('is-visible');
      clearTimeout(player._hideTimer);
      player._hideTimer = setTimeout(() => player.classList.add('is-collapsed'), 300);
    }
  }

  function animatePlaySectionLayout(applyState) {
    applyState();
  }

  window.toastEl = toastEl;
  window.deviceEl = deviceEl;
  window.syncUiState = syncUiState;
  window.animatePlaySectionLayout = animatePlaySectionLayout;
})();
