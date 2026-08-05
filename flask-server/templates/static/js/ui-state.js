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
    // Play-intent sequencing. Rapid clicks on song A, B, C each fire their own
    // POST /alexa/play_queue/, and those responses can complete out of order
    // (the endpoint ranges from ~400ms to several seconds depending on radio /
    // playlist expansion). Each handler used to render its optimistic
    // now-playing *after* its await, so an earlier click's late response
    // overwrote a later click's UI — showing C, then B, then C again once the
    // server caught up. These fields let every play path discard work that a
    // newer click has superseded, exactly like _volCommandSeq does for volume.
    _playIntentSeq: 0,
    _playIntentVideoId: '',
    _playIntentAt: 0,
    // How long local play intent outranks a contradicting server snapshot.
    // Must comfortably exceed the Echo's cold-start path (~9-10s of yt-dlp
    // before the first byte) or the UI would snap back to the previous track
    // while the new one is still being fetched. Expiring at all is the safety
    // valve: if the play never happens, server state takes over again.
    PLAY_INTENT_GRACE_MS: 12000,
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

  /* ---- play-intent sequencing (see _playIntentSeq in __appState) ---- */

  // Tracks the user clicked away from, with the time they were abandoned.
  // Needed because settling the intent on the first matching snapshot used to
  // disarm the guard completely: with five concurrent clicks the server can
  // publish the newest track *first* and the older ones a moment later, so the
  // UI jumped back through 1, 2, 3, 4 after already showing 5. An abandoned
  // track stays rejected for the whole grace window regardless of settling.
  const _abandoned = new Map();

  function _pruneAbandoned(now) {
    const grace = window.__appState.PLAY_INTENT_GRACE_MS;
    for (const [videoId, at] of _abandoned) {
      if (now - at >= grace) _abandoned.delete(videoId);
    }
  }

  // Call synchronously at click time, before any await, and keep the returned
  // token to check whether this click is still the newest one.
  window.beginPlayIntent = function beginPlayIntent(videoId) {
    const state = window.__appState;
    const now = Date.now();
    _pruneAbandoned(now);
    // The track we are leaving must not be able to re-render itself later.
    if (state._playIntentVideoId && state._playIntentVideoId !== videoId) {
      _abandoned.set(state._playIntentVideoId, now);
    }
    // Re-clicking a previously abandoned track makes it wanted again.
    if (videoId) _abandoned.delete(videoId);
    state._playIntentSeq += 1;
    state._playIntentVideoId = videoId || '';
    state._playIntentAt = now;
    // Existing consumers key their "did the user just act?" grace windows off
    // lastActionAt; keep it in lockstep so behaviour there is unchanged.
    state.lastActionAt = now;
    return state._playIntentSeq;
  };

  window.isCurrentPlayIntent = function isCurrentPlayIntent(seq) {
    return window.__appState._playIntentSeq === seq;
  };

  // True when a server snapshot describes a track the user has already clicked
  // away from, and local intent should therefore win. Returns false as soon as
  // the snapshot catches up to the intended track, so the authoritative state
  // is never blocked — only stale state is.
  window.playIntentSupersedes = function playIntentSupersedes(videoId) {
    const state = window.__appState;
    if (!videoId) return false;
    const now = Date.now();
    _pruneAbandoned(now);
    // Explicitly abandoned: reject for the whole grace window even after the
    // intended track has been settled.
    if (_abandoned.has(videoId)) return true;
    if (!state._playIntentVideoId) return false;
    if (videoId === state._playIntentVideoId) return false;
    return (now - state._playIntentAt) < state.PLAY_INTENT_GRACE_MS;
  };

  // Clears the intent once the server agrees (or a play failed), so later
  // genuine track changes — the queue advancing on its own, a voice command —
  // are not held back by a stale intent.
  window.settlePlayIntent = function settlePlayIntent(videoId) {
    const state = window.__appState;
    if (videoId && state._playIntentVideoId === videoId) {
      state._playIntentVideoId = '';
      state._playIntentAt = 0;
    }
  };

  // Test/diagnostic hook.
  window.__abandonedPlayIntents = _abandoned;
})();
