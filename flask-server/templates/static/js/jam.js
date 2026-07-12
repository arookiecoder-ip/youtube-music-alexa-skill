(function () {
  'use strict';

  let _deviceSerial = '';

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
    try {
      renderJamHome(await window.api('/api/jam/home/'));
    } catch (_) {
      if (rows) rows.innerHTML = '<div class="jam-home-empty">Indian recommendations are unavailable right now. Search for a song to keep the jam going.</div>';
    }
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
      const data = await window.api('/api/jam/session/');
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
    } catch (_) {
      showJamEnded('Connection failed',
        'Could not reach the host. Check that the server is running.');
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
      const query = queryEl.value.trim();
      if (!query) return;
      queryEl.blur();
      if (window.runSearch) window.runSearch(query);
    };
  }

  function syncHistoryTriggerVisibility() {}
  function getRoute() { return ''; }
  function navigateTo() {}

  window.showJamEnded = showJamEnded;
  window.leaveJam = leaveJam;
  window.selectedSerial = selectedSerial;
  window.selectedDeviceOnline = selectedDeviceOnline;
  window.initJamPage = initJamPage;
  window.loadJamHome = loadJamHome;
  window.syncHistoryTriggerVisibility = syncHistoryTriggerVisibility;
  window.getRoute = getRoute;
  window.navigateTo = navigateTo;
})();
