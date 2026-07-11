(function() {
  'use strict';

  const deviceEl = window.deviceEl || document.getElementById('device');
  const loginSection = document.getElementById('login-section');
  const loginBtn = document.getElementById('login-btn');
  let loginPoll;
  let _firstShow = true;

  function state() { return window.__appState; }
  function toast() { return window.toast && window.toast.apply(window, arguments); }
  function api() { return window.api.apply(window, arguments); }
  function isYoutubeLinkLike(value) {
    return /^(https?:\/\/)?(www\.|m\.|music\.)?(youtube\.com\/|youtu\.be\/)/i.test((value || '').trim());
  }

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
    try { await api('/logout/', {}); } catch (_) { /* best-effort */ }
    showJamEnded('You left the jam', "Open the jam link again to rejoin while it's still live.");
  }

  async function doSignOut(everywhere) {
    if (window.stopSSE) window.stopSSE();
    try {
      localStorage.removeItem(window.CACHE_DEVICES_KEY || 'cachedDevices');
      localStorage.removeItem(window.CACHE_NP_KEY || 'cachedNowPlaying');
    } catch (_) {}
    try {
      await api('/logout/', everywhere ? { everywhere: true } : {});
    } catch (_) { /* best-effort */ }
    try { await caches.delete('mb-pages-v1'); } catch (_) {}
    window.location.replace('/login/');
  }

  function selectedSerial() {
    if (!deviceEl.value) { toast('Pick a device first.', 'error'); return null; }
    return deviceEl.value;
  }

  function selectedDeviceOnline() {
    const opt = deviceEl.selectedOptions && deviceEl.selectedOptions[0];
    return !opt || opt.dataset.online !== '0';
  }

  function syncCustomDropdown() {
    const wrapper = document.getElementById('device-wrapper');
    const trigger = document.getElementById('device-trigger');
    const menu = document.getElementById('device-menu');
    if (!wrapper || !trigger || !menu || !deviceEl) return;
    const triggerLabel = trigger.querySelector('span');
    menu.innerHTML = '';
    for (const opt of deviceEl.options) {
      const item = document.createElement('div');
      item.className = 'custom-select-option' + (opt.selected ? ' selected' : '');
      item.dataset.value = opt.value;
      const isOnline = opt.dataset.online !== '0';
      item.innerHTML = (opt.value ? '<span class="cs-dot ' + (isOnline ? 'online' : 'offline') + '"></span>' : '') +
        window.escHtml(opt.textContent);
      item.addEventListener('click', () => {
        deviceEl.value = opt.value;
        deviceEl.dispatchEvent(new Event('change'));
        closeDropdown();
        updateTriggerLabel();
      });
      menu.appendChild(item);
    }
    if (triggerLabel) updateTriggerLabel();
  }

  function updateTriggerLabel() {
    const trigger = document.getElementById('device-trigger');
    const menu = document.getElementById('device-menu');
    if (!trigger || !menu) return;
    const triggerLabel = trigger.querySelector('span');
    const sel = deviceEl.selectedOptions[0];
    if (triggerLabel) triggerLabel.textContent = sel ? sel.textContent : 'Select device';
    for (const item of menu.children) {
      item.classList.toggle('selected', item.dataset.value === deviceEl.value);
    }
  }

  function closeDropdown() {
    const wrapper = document.getElementById('device-wrapper');
    if (wrapper) wrapper.classList.remove('open');
  }

  async function loadDevices(refresh) {
    const showStatusToast = !!refresh;
    if (showStatusToast) toast('Loading devices...');
    const prevSerial = deviceEl.value || localStorage.getItem('selectedSerial') || '';
    deviceEl.innerHTML = '<option value="">Loading...</option>';
    try {
      const data = await api('/alexa/devices/' + (refresh ? '?refresh=1' : ''));
      const ok = _applyDevices(data.devices || [], prevSerial);
      if (!ok) {
        toast('No compatible devices found.', 'error');
        return;
      }
      if (showStatusToast) toast(data.devices.length + ' device' + (data.devices.length > 1 ? 's' : '') + ' found', 'ok');
      if (window.connectSSE) window.connectSSE();
      if (window.refreshVolume) window.refreshVolume(true);
    } catch (e) {
      deviceEl.innerHTML = '<option value="">Unavailable</option>';
      toast(e.message, 'error');
    }
  }

  async function playDirectLink(query) {
    const serial = selectedSerial();
    if (!serial) return;
    state().lastActionAt = Date.now();
    toast('Resolving link...');
    try {
      const data = await api('/alexa/play/', { serial, query });
      const npInfo = data.now_playing || { title: query, artist: '', thumbnail: '' };
      state()._lastPlayAttemptVideoId = data.video_id || npInfo.video_id;
      if (window.showNowPlaying) window.showNowPlaying(npInfo);
      if (window.progress) window.progress.resetPending(npInfo.video_id);
      state().isPlaying = true;
      state().lastActionIntent = true;
      if (window.syncPlayPause) window.syncPlayPause();
      toast('Playing', 'ok');
      window._lastQueueJson = '';
      if (window.schedulePollNowPlaying) window.schedulePollNowPlaying(3000);
    } catch (e) {
      toast(e.message, 'error');
    }
  }

  function playStartupReveal() {
    document.body.classList.remove('startup-reveal');
    void document.body.offsetWidth;
    document.body.classList.add('startup-reveal');
    clearTimeout(playStartupReveal._timer);
    playStartupReveal._timer = setTimeout(() => {
      document.body.classList.remove('startup-reveal');
    }, 520);
  }

  function showControls(loggedIn) {
    if (loginSection) loginSection.hidden = loggedIn;
    for (const el of document.querySelectorAll('.needs-login')) el.hidden = !loggedIn;
    for (const el of document.querySelectorAll('.auth-only')) el.hidden = (!loggedIn || !window.IS_AUTHENTICATED);
    state()._loggedIn = !!loggedIn;
    if (!loggedIn && window.closeResults) window.closeResults();
    if (window.syncHistoryTriggerVisibility) window.syncHistoryTriggerVisibility();
    if (loggedIn && window.loadHistory) window.loadHistory();
    if (loggedIn && window.loadLibrary) window.loadLibrary();
    else {
      const recs = document.getElementById('recs-section');
      if (recs) recs.hidden = true;
    }
    if (window.syncUiState) window.syncUiState();

    if (_firstShow) {
      _firstShow = false;
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          document.body.classList.remove('preload');
        });
      });
    }
  }

  function _applyDevices(devices, preferSerial) {
    deviceEl.innerHTML = '';
    if (!devices.length) {
      deviceEl.innerHTML = '<option value="">No devices found</option>';
      syncCustomDropdown();
      return false;
    }
    for (const d of devices) {
      const o = document.createElement('option');
      o.value = d.serial;
      o.textContent = d.name + (d.online ? '' : ' (offline)');
      o.dataset.online = d.online ? '1' : '0';
      deviceEl.appendChild(o);
    }
    try { localStorage.setItem(window.CACHE_DEVICES_KEY || 'cachedDevices', JSON.stringify(devices)); } catch (_) {}
    if (preferSerial && [...deviceEl.options].some(o => o.value === preferSerial)) deviceEl.value = preferSerial;
    if (deviceEl.value && !selectedDeviceOnline()) toast('Selected device is offline.', 'error');
    syncCustomDropdown();
    return true;
  }

  async function refreshAuth() {
    try {
      const s = await api('/alexa/status/');
      if (!s.configured) {
        showControls(false);
        if (loginBtn) loginBtn.disabled = true;
        toast('Server missing PUBLIC_BASE_URL config.', 'error');
        return false;
      }
      showControls(!!s.logged_in);
      if (s.logged_in) loadDevices(false);
      else toast('Not connected to Amazon.', 'error');
      return !!s.logged_in;
    } catch (e) {
      toast(e.message, 'error');
      return false;
    }
  }

  async function initPage() {
    const savedSerial = localStorage.getItem('selectedSerial') || '';
    try {
      const data = await api('/alexa/init/' + (savedSerial ? '?serial=' + encodeURIComponent(savedSerial) : ''));
      const s = data.status || {};
      if (!s.configured) {
        showControls(false);
        if (loginBtn) loginBtn.disabled = true;
        toast('Server missing PUBLIC_BASE_URL config.', 'error');
        return;
      }
      showControls(!!s.logged_in);
      if (!s.logged_in) { toast('Not connected to Amazon.', 'error'); return; }

      const ok = _applyDevices(data.devices || [], savedSerial || data.serial);
      if (!ok) { toast('No compatible devices found.', 'error'); return; }

      if (data.now_playing && window.handleNpUpdate) {
        window.handleNpUpdate(data.now_playing);
        if (!state()._paintedFromCache) requestAnimationFrame(playStartupReveal);
      }

      if (window.connectSSE) window.connectSSE();
      if (window.refreshVolume) window.refreshVolume(true);
    } catch (e) {
      toast(e.message, 'error');
      refreshAuth();
    }
  }

  async function startProxyLogin(email, password, force) {
    const data = await api('/alexa/proxy_login/', { email, password, force: !!force });
    document.getElementById('login-password').value = '';
    window.open(data.login_url, '_blank', 'noopener');
    toast('Complete login in the new tab...');
    clearInterval(loginPoll);
    loginPoll = setInterval(async () => {
      try {
        const c = await api('/alexa/proxy_check/');
        if (c.logged_in) {
          clearInterval(loginPoll);
          toast('Connected', 'ok');
          if (loginBtn) loginBtn.disabled = false;
          showControls(true);
          loadDevices(false);
        }
      } catch (_) {}
    }, 2500);
  }

  function clearUiAfterPlaybackReset() {
    const mainEl = document.querySelector('main');
    const resultsSection = document.getElementById('results-section');
    const queueSection = document.getElementById('queue-section');
    const input = document.getElementById('query');
    const wasShowingResults = state()._resultsOpen && resultsSection && !resultsSection.hidden;
    const wasShowingQueue = mainEl && queueSection && mainEl.classList.contains('has-queue') && !queueSection.hidden;
    const shouldStageExit = wasShowingResults || wasShowingQueue;

    if (input) {
      input.value = '';
      input.dispatchEvent(new Event('input'));
    }
    window._searchSeq = (window._searchSeq || 0) + 1;
    window._searchResults = [];
    window._lastQueueJson = '';
    window._lastQueueIndex = -1;

    if (resultsSection) {
      clearTimeout(resultsSection._hideTimer);
      clearTimeout(resultsSection._showTimer);
      resultsSection.classList.remove('is-visible');
    }
    if (queueSection) {
      clearTimeout(queueSection._hideTimer);
      queueSection.classList.remove('is-visible');
    }

    const finish = () => window.animatePlaySectionLayout(() => {
      state()._resultsOpen = false;
      if (resultsSection) resultsSection.hidden = true;
      if (queueSection) queueSection.hidden = true;
      if (mainEl) mainEl.classList.remove('has-queue');
      if (window.showNowPlaying) window.showNowPlaying(null);
      if (window.progress) window.progress.update({});
      if (window.syncUiState) window.syncUiState();
    });

    if (shouldStageExit) setTimeout(finish, 320);
    else finish();
  }

  async function doClearAll() {
    const serial = deviceEl.value || null;
    toast('Clearing...');
    try {
      const data = await api('/alexa/clear/', serial ? { serial } : {});
      state().isPlaying = false;
      state().lastActionIntent = false;
      if (window.syncPlayPause) window.syncPlayPause();
      clearUiAfterPlaybackReset();
      if (window._closeQueueModal) window._closeQueueModal();
      if (window._closeMiniPopup) window._closeMiniPopup();
      if (data.stop_error) toast('Cleared here, but the device may still be playing: ' + data.stop_error, 'error');
      else toast('Cleared', 'ok');
    } catch (e) {
      toast(e.message, 'error');
    }
  }

  function bindUiHandlers() {
    const refreshBtn = document.getElementById('refresh');
    if (refreshBtn) refreshBtn.onclick = () => loadDevices(true);

    const trigger = document.getElementById('device-trigger');
    const wrapper = document.getElementById('device-wrapper');
    if (trigger && wrapper) {
      trigger.addEventListener('click', (e) => {
        e.stopPropagation();
        wrapper.classList.toggle('open');
      });
      document.addEventListener('click', (e) => {
        if (!wrapper.contains(e.target)) closeDropdown();
      });
      if (window.MutationObserver && deviceEl) new MutationObserver(() => syncCustomDropdown()).observe(deviceEl, { childList: true });
      deviceEl.addEventListener('change', updateTriggerLabel);
      syncCustomDropdown();
    }

    const playBtn = document.getElementById('play-query');
    if (playBtn) {
      playBtn.onclick = () => {
        const queryEl = document.getElementById('query');
        const query = queryEl.value.trim();
        if (!query) { toast('Type something', 'error'); return; }
        if (typeof window._recordSearchHistory === 'function') window._recordSearchHistory(query);
        queryEl.blur();
        if (isYoutubeLinkLike(query)) {
          if (!window.JAM_GUEST && query.includes('list=') && confirm('This looks like a playlist. Do you want to save it to your Playlists?')) {
            const name = prompt('Enter a name for this playlist:', 'Imported Playlist');
            if (name) {
              api('/api/playlists/', { name: name, source_url: query }).then(res => {
                toast('Playlist saved. Syncing...', 'ok');
                if (typeof window.syncPlaylist === 'function') window.syncPlaylist(res.id);
              }).catch(() => toast('Failed to save playlist', 'error'));
              return;
            }
          }
          playDirectLink(query);
        } else if (window.runSearch) {
          window.runSearch(query);
        }
      };
    }

    const clearOverlay = document.getElementById('confirm-clear');
    const clearBtn = document.getElementById('clear-all-btn');
    if (clearOverlay && clearBtn) {
      const cancelBtn = document.getElementById('confirm-clear-cancel');
      const yesBtn = document.getElementById('confirm-clear-yes');
      clearBtn.addEventListener('click', () => clearOverlay.classList.add('open'));
      if (cancelBtn) cancelBtn.addEventListener('click', () => clearOverlay.classList.remove('open'));
      clearOverlay.addEventListener('click', (e) => { if (e.target === clearOverlay) clearOverlay.classList.remove('open'); });
      if (yesBtn) yesBtn.addEventListener('click', () => { clearOverlay.classList.remove('open'); doClearAll(); });
    }

    if (loginBtn) {
      loginBtn.onclick = async () => {
        const email = document.getElementById('login-email').value.trim();
        const password = document.getElementById('login-password').value;
        if (!email || !password) { toast('Enter email and password.', 'error'); return; }
        loginBtn.disabled = true;
        toast('Opening Amazon login...');
        try {
          await startProxyLogin(email, password, false);
        } catch (e) {
          if (e.message.includes('force') && confirm(e.message + '\n\nContinue and replace the current session?')) {
            try {
              await startProxyLogin(email, password, true);
            } catch (e2) {
              toast(e2.message, 'error');
              loginBtn.disabled = false;
            }
            return;
          }
          toast(e.message, 'error');
          loginBtn.disabled = false;
        }
      };
    }
  }

  (function bindSignOutConfirm() {
    const overlay = document.getElementById('confirm-signout');
    if (!overlay) return;
    const cancelBtn = document.getElementById('confirm-signout-cancel');
    const yesBtn = document.getElementById('confirm-signout-yes');
    const everywhereEl = document.getElementById('confirm-signout-everywhere');
    function showConfirm() {
      if (window.JAM_GUEST) { leaveJam(); return; }
      everywhereEl.checked = false;
      overlay.classList.add('open');
    }
    function hideConfirm() { overlay.classList.remove('open'); }
    const logoutBtn = document.getElementById('logout');
    if (logoutBtn) logoutBtn.onclick = () => showConfirm();
    if (cancelBtn) cancelBtn.addEventListener('click', hideConfirm);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) hideConfirm(); });
    if (yesBtn) yesBtn.addEventListener('click', () => { const everywhere = everywhereEl.checked; hideConfirm(); doSignOut(everywhere); });
    window._showSignOutConfirm = showConfirm;
  })();

  (function bindJamModal() {
    if (window.JAM_GUEST) return;
    const overlay = document.getElementById('jam-modal-overlay');
    const inactiveEl = document.getElementById('jam-inactive');
    const activeEl = document.getElementById('jam-active');
    const linkEl = document.getElementById('jam-link');
    const startBtn = document.getElementById('jam-start-btn');
    const refreshBtn = document.getElementById('jam-refresh-btn');
    const jamBtn = document.getElementById('jam-btn');
    const sidebarJamBtn = document.getElementById('sidebar-jam-btn');
    const shareBtn = document.getElementById('jam-share-btn');
    const qrBtn = document.getElementById('jam-qr-btn');
    const qrPanel = document.getElementById('jam-qr-panel');
    const qrImg = document.getElementById('jam-qr-img');
    if (!overlay || !inactiveEl || !activeEl || !linkEl) return;
    let jamQrObjectUrl = '';

    function resetJamQr() {
      if (jamQrObjectUrl) URL.revokeObjectURL(jamQrObjectUrl);
      jamQrObjectUrl = '';
      if (qrImg) qrImg.removeAttribute('src');
      if (qrPanel) qrPanel.hidden = true;
      if (qrBtn) qrBtn.textContent = 'Show QR';
    }

    function setJamLiveIndicator(active) {
      [jamBtn, sidebarJamBtn].forEach((btn) => {
        if (!btn) return;
        btn.classList.toggle('jam-live', active);
        btn.title = active ? 'Jam is live' : 'Jam';
      });
    }

    function renderJam(stateValue) {
      const active = !!(stateValue && stateValue.active);
      inactiveEl.hidden = active;
      activeEl.hidden = !active;
      if (stateValue) setJamLiveIndicator(active);
      if (active) {
        if (linkEl.value !== (stateValue.url || '')) resetJamQr();
        linkEl.value = stateValue.url || '';
      } else {
        linkEl.value = '';
        resetJamQr();
      }
    }

    async function openJamModal() {
      overlay.classList.add('open');
      renderJam(null);
      try { renderJam(await api('/alexa/jam/status/')); }
      catch (e) { toast(e.message, 'error'); }
    }
    function closeJamModal() { overlay.classList.remove('open'); }

    if (jamBtn) jamBtn.addEventListener('click', openJamModal);
    if (sidebarJamBtn) sidebarJamBtn.addEventListener('click', () => {
      if (window._closeSidebar) window._closeSidebar();
      openJamModal();
    });
    const closeBtn = document.getElementById('jam-modal-close');
    if (closeBtn) closeBtn.addEventListener('click', closeJamModal);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) closeJamModal(); });

    if (startBtn) startBtn.addEventListener('click', async () => {
      startBtn.disabled = true;
      try { renderJam(await api('/alexa/jam/start/', {})); toast('Jam started - share the link', 'ok'); }
      catch (e) { toast(e.message, 'error'); }
      finally { startBtn.disabled = false; }
    });

    if (refreshBtn) refreshBtn.addEventListener('click', async () => {
      refreshBtn.disabled = true;
      try { renderJam(await api('/alexa/jam/start/', {})); toast('Jam link refreshed - old link revoked', 'ok'); }
      catch (e) { toast(e.message, 'error'); }
      finally { refreshBtn.disabled = false; }
    });

    const copyBtn = document.getElementById('jam-copy-btn');
    if (copyBtn) copyBtn.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(linkEl.value);
        toast('Link copied', 'ok');
      } catch (_) {
        linkEl.focus();
        linkEl.select();
        toast('Press Ctrl+C to copy', 'error');
      }
    });

    if (shareBtn) shareBtn.addEventListener('click', async () => {
      const url = linkEl.value;
      if (!url) return;
      if (navigator.share) {
        try {
          await navigator.share({ title: 'Join my Music Box jam', text: 'Join my jam', url });
          return;
        } catch (e) {
          if (e && e.name === 'AbortError') return;
        }
      }
      try {
        await navigator.clipboard.writeText(url);
        toast('Share link copied', 'ok');
      } catch (_) {
        linkEl.focus();
        linkEl.select();
        toast('Press Ctrl+C to copy', 'error');
      }
    });

    if (qrBtn) qrBtn.addEventListener('click', async () => {
      if (!linkEl.value) return;
      if (!qrPanel.hidden) {
        qrPanel.hidden = true;
        qrBtn.textContent = 'Show QR';
        return;
      }
      try {
        if (!jamQrObjectUrl) {
          const res = await fetch('/alexa/jam/qr/', { credentials: 'same-origin', cache: 'no-store' });
          if (!res.ok) throw new Error('Could not load QR code');
          jamQrObjectUrl = URL.createObjectURL(await res.blob());
          qrImg.src = jamQrObjectUrl;
        }
        qrPanel.hidden = false;
        qrBtn.textContent = 'Hide QR';
      } catch (e) {
        toast(e.message || 'Could not load QR code', 'error');
      }
    });

    const endBtn = document.getElementById('jam-end-btn');
    if (endBtn) endBtn.addEventListener('click', async () => {
      try { renderJam(await api('/alexa/jam/stop/', {})); toast('Jam ended - guest access revoked', 'ok'); }
      catch (e) { toast(e.message, 'error'); }
    });

    api('/alexa/jam/status/').then(renderJam).catch(() => setJamLiveIndicator(false));

    window.openJamModal = openJamModal;
    window.closeJamModal = closeJamModal;
    window.renderJam = renderJam;
    window.setJamLiveIndicator = setJamLiveIndicator;
  })();

  bindUiHandlers();

  window.showJamEnded = showJamEnded;
  window.leaveJam = leaveJam;
  window.doSignOut = doSignOut;
  window.selectedSerial = selectedSerial;
  window.selectedDeviceOnline = selectedDeviceOnline;
  window.loadDevices = loadDevices;
  window.playDirectLink = playDirectLink;
  window.playStartupReveal = playStartupReveal;
  window.showControls = showControls;
  window._applyDevices = _applyDevices;
  window.refreshAuth = refreshAuth;
  window.initPage = initPage;
  window.startProxyLogin = startProxyLogin;
  window.clearUiAfterPlaybackReset = clearUiAfterPlaybackReset;
  window.doClearAll = doClearAll;
  window.syncCustomDropdown = syncCustomDropdown;
  window.updateTriggerLabel = updateTriggerLabel;
  window.closeDropdown = closeDropdown;
})();
