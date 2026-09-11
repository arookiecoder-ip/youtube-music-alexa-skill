(function () {
  'use strict';

  let _deviceSerial = '';

  // Homepage-style header: transparent over the warm tint at the top, opaque
  // black once scrolled — same contract as the owner remote (router.js).
  // Without this the shared transparent-header rule matches the jam shell
  // (body.home-route) forever and scrolled results show through the bar.
  function syncJamHeaderScrollState() {
    document.body.classList.toggle('header-scrolled', window.scrollY > 12);
  }
  window.addEventListener('scroll', syncJamHeaderScrollState, { passive: true });
  syncJamHeaderScrollState();

  function state() { return window.__appState; }

  function showJamEnded(title, msg) {
    if (window.stopSSE) window.stopSSE();
    const overlay = document.getElementById('jam-ended-overlay');
    if (!overlay) return;
    if (title) document.getElementById('jam-ended-title').textContent = title;
    if (msg) document.getElementById('jam-ended-msg').textContent = msg;
    overlay.style.display = 'flex';
  }

  async function leaveJam() {
    if (window.stopSSE) window.stopSSE();
    try { await window.api('/api/jam/leave/', {}); } catch (_) {}
    showJamEnded('You left the jam',
      "Open the jam link again to rejoin while it's still live.");
  }

  function selectedSerial() {
    return _deviceSerial || null;
  }

  function selectedDeviceOnline() {
    return true;
  }

  function renderJamHome(feed) {
    const rows = document.getElementById('jam-home-rows');
    if (!rows) return;
    const shelves = feed && Array.isArray(feed.shelves) ? feed.shelves : [];
    if (!shelves.length) {
      rows.innerHTML = '<div class="jam-home-empty">Indian recommendations are unavailable right now. Search for a song to keep the jam going.</div>';
      return;
    }
    rows.innerHTML = shelves.map(window.HomeRenderers.renderShelf).join('');
  }

  async function loadJamHome() {
    const rows = document.getElementById('jam-home-rows');
    if (rows) rows.innerHTML = '<div class="jam-home-loading">Loading public Indian charts…</div>';
    // The join redirect sets the guest cookie immediately before this page
    // loads; behind a reverse proxy the first request can race it and 401.
    // The session handshake below retries for the same reason, so mirror
    // that backoff here instead of showing a permanent empty state.
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        renderJamHome(await window.api('/api/jam/home/'));
        return;
      } catch (_) {
        if (attempt < 2) await new Promise(resolve => setTimeout(resolve, 800 * (attempt + 1)));
      }
    }
    if (rows) rows.innerHTML = '<div class="jam-home-empty">Indian recommendations are unavailable right now. Search for a song to keep the jam going.</div>';
  }

  const jamRows = document.getElementById('jam-home-rows');
  if (jamRows) {
    jamRows.addEventListener('click', (event) => {
      const scrollButton = event.target.closest('.home-scroll-left, .home-scroll-right');
      if (scrollButton) {
        const content = scrollButton.closest('.home-shelf')?.querySelector('.home-shelf-content');
        if (content) content.scrollBy({
          left: (scrollButton.classList.contains('home-scroll-left') ? -1 : 1) * content.clientWidth * .8,
          behavior: 'smooth'
        });
        return;
      }
      const card = event.target.closest('.home-item');
      if (!card || !card.dataset.videoId || !window.playFromQueue) return;
      window.playFromQueue({
        video_id: card.dataset.videoId,
        title: card.querySelector('.home-item-title')?.textContent || '',
        artist: card.querySelector('.home-item-subtitle')?.textContent || '',
        thumbnail: card.querySelector('img')?.src || ''
      });
    });
  }

  async function initJamPage() {
    const deviceEl = document.getElementById('device');
    loadJamHome();
    try {
      // The join redirect sets the guest cookie immediately before loading
      // this page. A reverse proxy/browser can race that first API request,
      // so retry the session handshake before declaring the host unreachable.
      let data;
      let lastError;
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          data = await window.api('/api/jam/session/');
          break;
        } catch (error) {
          lastError = error;
          if (attempt < 2) await new Promise(resolve => setTimeout(resolve, 800 * (attempt + 1)));
        }
      }
      if (!data) throw lastError || new Error('Jam session handshake failed');
      const status = data.status || {};
      if (!status.configured || !status.logged_in) {
        showJamEnded('Jam unavailable', 'The host\'s server is not fully configured.');
        return;
      }
      if (!data.device_available || !data.serial) {
        showJamEnded('Jam unavailable', 'The host does not have a playback device available.');
        return;
      }

      // `jam` is an opaque client-side handle. The real Echo serial and name
      // remain server-side and are never exposed to the guest browser.
      _deviceSerial = data.serial;
      if (deviceEl) {
        deviceEl.innerHTML = '<option value="jam">Jam device</option>';
        deviceEl.value = _deviceSerial;
      }
      state()._loggedIn = true;
      if (data.now_playing && window.handleNpUpdate) {
        window.handleNpUpdate(data.now_playing);
      }
      if (window.connectSSE) window.connectSSE();
      if (window.refreshVolume) window.refreshVolume(true);
      if (window.syncUiState) window.syncUiState();
      // The handshake has now confirmed the guest session cookie. If the feed
      // request raced it and is still showing the empty state, retry once.
      const jamRows = document.getElementById('jam-home-rows');
      if (jamRows && jamRows.querySelector('.jam-home-empty')) loadJamHome();
    } catch (error) {
      showJamEnded('Connection failed',
        error && error.message
          ? error.message
          : 'Could not reach the host. Check that the server is running.');
    }
  }

  const leaveBtn = document.getElementById('jam-leave-btn');
  if (leaveBtn) {
    leaveBtn.addEventListener('click', () => {
      if (confirm('Leave this jam?')) leaveJam();
    });
  }

  const playBtn = document.getElementById('play-query');
  if (playBtn) {
    playBtn.onclick = () => {
      const queryEl = document.getElementById('query');
      queryEl.blur();
      submitPlayQuery(queryEl.value);
    };
  }

  // Pasted YouTube / YT Music links play directly on the shared room queue,
  // matching the owner remote. Jam has no device.js, so this mirrors its
  // submitPlayQuery/playDirectLink path (/alexa/play/ is jam-allowed and
  // resolves the shared device server-side).
  function isYoutubeLinkLike(value) {
    if (typeof window.isYoutubeLinkLike === 'function') return window.isYoutubeLinkLike(value);
    return /^(https?:\/\/)?(www\.|m\.|music\.)?(youtube\.com\/|youtu\.be\/)/i.test((value || '').trim());
  }

  async function playDirectLink(query) {
    const serial = selectedSerial();
    if (!serial) return;
    if (window.progress && window.progress.resetPending) window.progress.resetPending();
    if (window.toast) window.toast('Resolving link...');
    try {
      const data = await window.api('/alexa/play/', { serial, query });
      const npInfo = data.now_playing || { title: query, artist: '', thumbnail: '' };
      if (window.preloadNowPlayingArtwork) window.preloadNowPlayingArtwork(npInfo);
      if (window.showNowPlaying) window.showNowPlaying(npInfo);
      if (window.progress) window.progress.resetPending(npInfo.video_id);
      state().isPlaying = true;
      state().lastActionIntent = true;
      if (window.syncPlayPause) window.syncPlayPause();
      if (window.toast) window.toast('Playing', 'ok');
      window._lastQueueJson = '';
      if (window.schedulePollNowPlaying) window.schedulePollNowPlaying(3000);
    } catch (e) {
      if (window.progress && window.progress.cancelPending) window.progress.cancelPending();
      if (window.toast) window.toast(e.message, 'error');
    }
  }

  function closeSearchForDirectPlay() {
    document.body.classList.remove('mobile-search-open');
    if (window.closeSearchSuggestions) window.closeSearchSuggestions();
    const input = document.getElementById('query');
    if (input) input.blur();
    // Closing the results panel re-shows the jam home and reveals the
    // compact player bar once the resolved track renders.
    if (window.closeResults) window.closeResults();
  }

  function submitPlayQuery(query) {
    query = (query || '').trim();
    if (!query) { if (window.toast) window.toast('Type something', 'error'); return; }
    if (isYoutubeLinkLike(query)) {
      closeSearchForDirectPlay();
      playDirectLink(query);
    } else if (window.runSearch) {
      window.runSearch(query);
    }
  }

  // ---- Expanded now-playing overlay (same as the normal remote) ----
  // The compact playbar opens this full now-playing page via
  // navigateTo('#now-playing') (player.js). Body classes drive the same
  // slide/fade transitions as the normal remote (player.css), so the mobile
  // experience is identical: art hero, action squares, progress, transport,
  // volume, swipe-to-skip, and the queue modal.
  function _isMobileViewport() {
    return typeof window !== 'undefined' &&
      window.matchMedia && window.matchMedia('(max-width: 899px)').matches;
  }
  function lockPlayerScroll() {
    if (!_isMobileViewport()) return;
    if (window.__npScrollLocked) return;
    window.__npScrollLocked = true;
    document.body.dataset.npScrollY = String(window.scrollY);
    document.body.style.overscrollBehavior = 'none';
    document.documentElement.style.overscrollBehavior = 'none';
  }
  function unlockPlayerScroll() {
    if (!window.__npScrollLocked) return;
    window.__npScrollLocked = false;
    var y = parseInt(document.body.dataset.npScrollY || '0', 10);
    document.body.style.removeProperty('overflow');
    document.body.style.removeProperty('overscroll-behavior');
    document.documentElement.style.removeProperty('overflow');
    document.documentElement.style.removeProperty('overscroll-behavior');
    delete document.body.dataset.npScrollY;
    requestAnimationFrame(function() { window.scrollTo(0, y); });
  }

  function openNowPlayingOverlay() {
    if (document.body.classList.contains('now-playing-route')) return;
    window.__npReturnRoute = getRoute() || '';
    var npSection = document.getElementById('now-playing-section');
    if (npSection && npSection._closeTimer) {
      clearTimeout(npSection._closeTimer);
      npSection._closeTimer = null;
    }
    if (npSection && npSection._closeCleanup) {
      npSection.removeEventListener('transitionend', npSection._closeCleanup);
      npSection._closeCleanup = null;
    }
    if (npSection) {
      npSection.hidden = false;
      void npSection.offsetHeight;
    }
    document.body.classList.remove('now-playing-closing');
    document.body.classList.add('now-playing-route');
    lockPlayerScroll();
    // Re-apply the current track artwork + in-page queue, mirroring the
    // normal remote's route renderer.
    var npPage = npSection && npSection.querySelector('.np-page');
    var currentThumb = state() && state()._currentThumbnail;
    if (npPage) {
      if (currentThumb) npPage.style.setProperty('--np-cover', 'url(' + JSON.stringify(currentThumb) + ')');
      else npPage.style.removeProperty('--np-cover');
    }
    var queueJson = window._lastQueueJson || (state() && state()._lastQueueJson);
    if (queueJson && window.renderNpQueue) {
      try {
        var queue = JSON.parse(queueJson);
        var queueIndex = typeof window._lastQueueIndex === 'number'
          ? window._lastQueueIndex
          : ((state() && state()._lastQueueIndex) || 0);
        window.renderNpQueue(queue, queueIndex);
      } catch (_) {}
    }
    if (window.scrollQueueToCurrent) window.scrollQueueToCurrent();
    if (window.syncUiState) window.syncUiState();
  }

  function closeNowPlayingOverlay() {
    if (!document.body.classList.contains('now-playing-route')) return;
    var npSection = document.getElementById('now-playing-section');
    document.body.classList.add('now-playing-closing');
    var queueSection = document.getElementById('queue-section');
    if (queueSection) queueSection.hidden = true;
    if (npSection) {
      if (npSection._closeTimer) clearTimeout(npSection._closeTimer);
      if (npSection._closeCleanup) npSection.removeEventListener('transitionend', npSection._closeCleanup);
      var finishClose = function(event) {
        if (event && (event.target !== npSection || event.propertyName !== 'transform')) return;
        if (npSection._closeTimer) clearTimeout(npSection._closeTimer);
        npSection.removeEventListener('transitionend', finishClose);
        npSection.hidden = true;
        npSection._closeTimer = null;
        npSection._closeCleanup = null;
        document.body.classList.remove('now-playing-route', 'now-playing-closing');
        document.documentElement.style.removeProperty('overflow');
        document.body.style.removeProperty('overflow');
        unlockPlayerScroll();
      };
      npSection._closeCleanup = finishClose;
      npSection.addEventListener('transitionend', finishClose);
      npSection._closeTimer = setTimeout(finishClose, 450);
    } else {
      document.body.classList.remove('now-playing-route', 'now-playing-closing');
      unlockPlayerScroll();
    }
  }

  function syncHistoryTriggerVisibility() {}
  function getRoute() {
    return document.body.classList.contains('now-playing-route') ? '#now-playing' : '';
  }
  function navigateTo(route) {
    if (route === '#now-playing') {
      openNowPlayingOverlay();
      return;
    }
    if ((route === '#home' || route === '' || route == null) &&
        document.body.classList.contains('now-playing-route')) {
      closeNowPlayingOverlay();
    }
  }

  var mobilePlayerClose = document.getElementById('mobile-player-close');
  if (mobilePlayerClose) {
    mobilePlayerClose.addEventListener('click', function(event) {
      event.stopPropagation();
      closeNowPlayingOverlay();
    });
  }

  // Dismiss the expanded mobile player with Escape or a quick downward flick
  // on the non-interactive surface — same as the normal remote.
  document.addEventListener('keydown', function(event) {
    if (event.key === 'Escape' && document.body.classList.contains('now-playing-route')) {
      closeNowPlayingOverlay();
    }
  });
  (function wireJamNowPlayingDismissSwipe() {
    var npSection = document.getElementById('now-playing-section');
    if (!npSection) return;
    var startX = 0, startY = 0, startedAt = 0, tracking = false;
    npSection.addEventListener('touchstart', function(event) {
      if (!document.body.classList.contains('now-playing-route') ||
          (window.matchMedia && window.matchMedia('(min-width: 900px)').matches) ||
          event.touches.length !== 1 ||
          (event.target.closest && event.target.closest('button, a, input, select, textarea, [role="slider"], .progress-track'))) {
        tracking = false;
        return;
      }
      var touch = event.touches[0];
      startX = touch.clientX;
      startY = touch.clientY;
      startedAt = performance.now();
      tracking = true;
    }, { passive: true });
    npSection.addEventListener('touchend', function(event) {
      if (!tracking || !event.changedTouches.length) return;
      tracking = false;
      var touch = event.changedTouches[0];
      var deltaX = touch.clientX - startX;
      var deltaY = touch.clientY - startY;
      var elapsed = Math.max(1, performance.now() - startedAt);
      if (deltaY >= 72 && (deltaY / elapsed) >= 0.55 && deltaY > Math.abs(deltaX) * 1.25) {
        closeNowPlayingOverlay();
      }
    }, { passive: true });
    npSection.addEventListener('touchcancel', function() { tracking = false; }, { passive: true });
  })();

  window.openNowPlayingOverlay = openNowPlayingOverlay;
  window.closeNowPlayingOverlay = closeNowPlayingOverlay;

  window.showJamEnded = showJamEnded;
  window.leaveJam = leaveJam;
  window.selectedSerial = selectedSerial;
  window.selectedDeviceOnline = selectedDeviceOnline;
  window.initJamPage = initJamPage;
  window.loadJamHome = loadJamHome;
  window.syncHistoryTriggerVisibility = syncHistoryTriggerVisibility;
  window.getRoute = getRoute;
  window.navigateTo = navigateTo;
  window.submitPlayQuery = submitPlayQuery;
  window.playDirectLink = playDirectLink;
})();
