(function() {
  'use strict';

  // Images are rendered throughout the app after navigation. Cancel their
  // native drag behavior once at the document level so desktop browsers do
  // not show a draggable ghost image. CSS supplies the matching mobile fix.
  document.addEventListener('dragstart', function(event) {
    if (event.target instanceof HTMLImageElement) event.preventDefault();
  });

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

  // Player lifecycle trace. Run window.dumpPlayerDebugLogs() in the console
  // after reproducing the flicker to export the last 300 state changes.
  window.__playerDebugLog = window.__playerDebugLog || function(event, details) {
    const main = document.querySelector('main');
    const np = document.getElementById('now-playing-section');
    const bar = document.querySelector('.player-section');
    const record = {
      t: Math.round(performance.now()), event,
      route: window.__route || location.hash || '#home',
      body: document.body.className,
      mainQueue: !!(main && main.classList.contains('has-queue')),
      player: bar && { hidden: bar.hidden, visible: bar.classList.contains('is-visible'), collapsed: bar.classList.contains('is-collapsed') },
      nowPlaying: np && { hidden: np.hidden, visibility: getComputedStyle(np).visibility, transform: getComputedStyle(np).transform },
      hasTrack: !!window.__appState._hasTrack,
      ...(details || {})
    };
    window.__playerDebugRecords = (window.__playerDebugRecords || []).concat(record).slice(-300);
    return record;
  };
  window.dumpPlayerDebugLogs = function() {
    const logs = window.__playerDebugRecords || [];
    return logs;
  };
  window.__playerDebugLog('ui-state-ready');

  function installPlayerMutationTrace() {
    const targets = [document.querySelector('.player-section'), document.getElementById('now-playing-section')].filter(Boolean);
    targets.forEach(function(el) {
      const label = el.id || el.className;
      let last = '';
      const report = function(reason) {
        const rect = el.getBoundingClientRect();
        const value = [el.hidden, el.className, Math.round(rect.top), Math.round(rect.bottom), Math.round(rect.height), getComputedStyle(el).transform].join('|');
        if (value === last) return;
        last = value;
        window.__playerDebugLog('mutation:' + label, { reason: reason, rect: { top: rect.top, bottom: rect.bottom, height: rect.height }, className: el.className, hidden: el.hidden });
      };
      new MutationObserver(function() { report('mutation'); }).observe(el, { attributes: true, attributeFilter: ['class', 'hidden', 'style'] });
      if (window.ResizeObserver) new ResizeObserver(function() { report('resize'); }).observe(el);
      report('initial');
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', installPlayerMutationTrace, { once: true });
  else installPlayerMutationTrace();

  function syncUiState() {
    const state = window.__appState;
    window.__playerDebugLog('sync:start');
    const route = window.getRoute ? (window.getRoute() || '') : '';
    // Search results are retained while visiting an artist/album so Back can
    // restore them, but their shell class must only style the visible #home
    // route. Leaving results-open on an artist route forces the header black
    // and blocks the hero artwork from bleeding underneath it.
    const searchRoute = route.indexOf('#search?') === 0;
    const resultsVisible = !!state._resultsOpen && searchRoute;
    const mainEl = document.querySelector('main');
    const player = document.querySelector('.player-section');
    const clearBtn = document.getElementById('clear-all-btn');
    if (clearBtn) clearBtn.hidden = !(state._hasTrack || state._resultsOpen);
    document.body.classList.toggle('results-open', resultsVisible);
    if (mainEl) mainEl.classList.toggle('idle', route === '#home' && state._loggedIn && !state._hasTrack && !resultsVisible);
    const homeSection = document.getElementById('home-section');
    if (homeSection) {
      // The player is a fixed bottom bar now, so the home feed stays visible
      // while a track plays; only search results or the artist page cover it.
      const artistOpen = route.indexOf('#artist/') === 0;
      const albumOpen = route.indexOf('#album/') === 0;
      const historyOpen = route === '#history';
      const exploreOpen = route === '#explore';
      const moodOpen = route.indexOf('#mood/') === 0;
      const libraryOpen = route === '#library';
      const npOpen = route === '#now-playing';
      const shouldShow = state._loggedIn && !searchRoute && !state._resultsOpen && !artistOpen && !albumOpen && !historyOpen && !exploreOpen && !moodOpen && !libraryOpen && !npOpen;
      if (shouldShow && !state._homeLoaded && window.loadHomeFeed) window.loadHomeFeed();
      else homeSection.hidden = !shouldShow || !state._homeLoaded;
    }
    const jamHomeSection = document.getElementById('jam-home-section');
    if (jamHomeSection) jamHomeSection.hidden = !!state._resultsOpen;
    if (!player) return;
    const routeNowPlaying = (window.getRoute && window.getRoute() === '#now-playing') ||
      document.body.classList.contains('now-playing-route') ||
      document.body.classList.contains('now-playing-closing');
    // The bottom player must remain a stable layer while the full player is
    // opening/closing. Re-queuing its visibility animation here causes a
    // one-frame flicker during route synchronization.
    if (routeNowPlaying) {
      clearTimeout(player._hideTimer);
      player.hidden = false;
      player.classList.remove('is-collapsed');
      player.classList.add('is-visible');
      player.classList.toggle('is-blank', !state._hasTrack);
      window.__playerDebugLog('sync:player-locked-during-now-playing');
      return;
    }
    if (window.JAM_GUEST && !state._hasTrack) {
      player.classList.remove('is-visible');
      player.classList.add('is-collapsed');
      player.hidden = true;
      return;
    }
    clearTimeout(player._hideTimer);
    player.hidden = false;
    player.classList.remove('is-collapsed');
    requestAnimationFrame(() => {
      player.classList.add('is-visible');
      window.__playerDebugLog('sync:player-visible-rAF');
    });
    window.__playerDebugLog('sync:player-queued-visible');

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
