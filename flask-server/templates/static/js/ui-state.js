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
    _homeLoaded: false,
    _homeLoading: false,
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
    const homeSection = document.getElementById('home-section');
    if (homeSection) {
      // The player is a fixed bottom bar now, so the home feed stays visible
      // while a track plays; only search results or the artist page cover it.
      const artistOpen = (window.getRoute() || '').indexOf('#artist/') === 0;
      const albumOpen = (window.getRoute() || '').indexOf('#album/') === 0;
      const npOpen = (window.getRoute() || '') === '#now-playing';
      const shouldShow = state._loggedIn && !state._resultsOpen && !artistOpen && !albumOpen && !npOpen;
      if (shouldShow && !state._homeLoaded && window.loadHomeFeed) window.loadHomeFeed();
      else homeSection.hidden = !shouldShow || !state._homeLoaded;
    }
    if (!player) return;
    clearTimeout(player._hideTimer);
    player.hidden = false;
    player.classList.remove('is-collapsed');
    requestAnimationFrame(() => player.classList.add('is-visible'));

    if (state._hasTrack) {
      player.classList.remove('is-blank');
    } else {
      player.classList.add('is-blank');
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
