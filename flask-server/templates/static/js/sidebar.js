(function () {
  'use strict';
  const state = window.__appState = window.__appState || {};


/* ---- Mobile sidebar ---- */
(function () {
  const deviceEl = document.getElementById('device');
  const hamburger = document.getElementById('hamburger-btn');
  const mobileSearchToggle = document.getElementById('mobile-search-toggle');
  const mobilePlayerClose = document.getElementById('mobile-player-close');
  const searchSection = document.querySelector('.play-section');
  const searchInput = document.getElementById('query');
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('sidebar-overlay');
  const closeBtn = document.getElementById('sidebar-close');
  const deviceSidebar = document.getElementById('device-sidebar');
  const refreshSidebar = document.getElementById('refresh-sidebar');

  // Sidebar custom dropdown elements
  const sbWrapper = document.getElementById('device-sidebar-wrapper');
  const sbTrigger = document.getElementById('device-sidebar-trigger');
  const sbMenu = document.getElementById('device-sidebar-menu');
  const sbLabel = sbTrigger.querySelector('span');

  function syncSidebarDropdown() {
    // Copy options from the main hidden select into sidebar hidden select
    deviceSidebar.innerHTML = deviceEl.innerHTML;
    deviceSidebar.value = deviceEl.value;
    // Rebuild the custom menu
    sbMenu.innerHTML = '';
    for (const opt of deviceSidebar.options) {
      const item = document.createElement('div');
      item.className = 'custom-select-option' + (opt.selected ? ' selected' : '');
      item.dataset.value = opt.value;
      const isOnline = opt.dataset.online !== '0';
      item.innerHTML = (opt.value
        ? '<span class="cs-dot ' + (isOnline ? 'online' : 'offline') + '"></span>'
        : '') + escHtml(opt.textContent);
      item.addEventListener('click', () => {
        deviceSidebar.value = opt.value;
        deviceEl.value = opt.value;
        deviceEl.dispatchEvent(new Event('change'));
        sbWrapper.classList.remove('open');
        updateSbLabel();
      });
      sbMenu.appendChild(item);
    }
    updateSbLabel();
  }

  function updateSbLabel() {
    const sel = deviceSidebar.selectedOptions[0];
    sbLabel.textContent = sel ? sel.textContent : 'Select device';
    for (const item of sbMenu.children) {
      item.classList.toggle('selected', item.dataset.value === deviceSidebar.value);
    }
  }

  sbTrigger.addEventListener('click', (e) => {
    e.stopPropagation();
    sbWrapper.classList.toggle('open');
  });
  document.addEventListener('click', (e) => {
    if (!sbWrapper.contains(e.target)) sbWrapper.classList.remove('open');
  });

  function openSidebar() {
    document.documentElement.classList.add('sidebar-open');
    document.body.classList.add('sidebar-open');
    overlay.style.display = 'block';
    requestAnimationFrame(() => {
      sidebar.classList.add('open');
      overlay.classList.add('open');
    });
    syncSidebarDropdown();
  }

  function closeSidebar() {
    document.documentElement.classList.remove('sidebar-open');
    document.body.classList.remove('sidebar-open');
    sidebar.classList.remove('open');
    overlay.classList.remove('open');
    sbWrapper.classList.remove('open');
    setTimeout(() => { overlay.style.display = 'none'; }, 300);
  }

  hamburger.addEventListener('click', () => {
    if (sidebar.classList.contains('open')) closeSidebar();
    else openSidebar();
  });

  function closeMobileSearch() {
    document.body.classList.remove('mobile-search-open');
  }

  function closeMobileProfile() {
    if (!window.matchMedia('(max-width: 899px)').matches) return;
    const profile = document.getElementById('profile-menu-wrap');
    const profileTrigger = document.getElementById('profile-menu-trigger');
    if (profile) profile.classList.remove('open');
    if (profileTrigger) profileTrigger.setAttribute('aria-expanded', 'false');
  }

  if (mobileSearchToggle && searchSection) {
    mobileSearchToggle.addEventListener('click', (event) => {
      event.stopPropagation();
      closeMobileProfile();
      const isOpen = document.body.classList.toggle('mobile-search-open');
      if (isOpen && searchInput) {
        requestAnimationFrame(() => searchInput.focus());
      }
    });
    document.addEventListener('click', (event) => {
      if (!document.body.classList.contains('mobile-search-open')) return;
      if (event.target.closest('.play-section, .mobile-search-toggle')) return;
      closeMobileSearch();
    });
    if (searchInput) {
      searchInput.addEventListener('focus', closeMobileProfile);
      searchInput.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') closeMobileSearch();
      });
    }
  }

  if (mobilePlayerClose) {
    mobilePlayerClose.addEventListener('click', (event) => {
      event.stopPropagation();
      if (window.closeNowPlayingOverlay) window.closeNowPlayingOverlay();
      else if (window.navigateTo) window.navigateTo('#home');
    });
  }

  // A quick downward flick anywhere on the non-interactive player surface
  // dismisses the expanded mobile player through its normal slide-out path.
  const nowPlayingSection = document.getElementById('now-playing-section');
  if (nowPlayingSection) {
    let swipeStartX = 0;
    let swipeStartY = 0;
    let swipeStartedAt = 0;
    let swipeTracking = false;

    nowPlayingSection.addEventListener('touchstart', (event) => {
      if (!document.body.classList.contains('now-playing-route') ||
          window.matchMedia('(min-width: 900px)').matches ||
          event.touches.length !== 1 ||
          event.target.closest('button, a, input, select, textarea, [role="slider"], .progress-track')) {
        swipeTracking = false;
        return;
      }
      const touch = event.touches[0];
      swipeStartX = touch.clientX;
      swipeStartY = touch.clientY;
      swipeStartedAt = performance.now();
      swipeTracking = true;
    }, { passive: true });

    nowPlayingSection.addEventListener('touchend', (event) => {
      if (!swipeTracking || !event.changedTouches.length) return;
      swipeTracking = false;
      const touch = event.changedTouches[0];
      const deltaX = touch.clientX - swipeStartX;
      const deltaY = touch.clientY - swipeStartY;
      const elapsed = Math.max(1, performance.now() - swipeStartedAt);
      const velocity = deltaY / elapsed;
      if (deltaY >= 72 && velocity >= .55 && deltaY > Math.abs(deltaX) * 1.25) {
        if (window.closeNowPlayingOverlay) window.closeNowPlayingOverlay();
      }
    }, { passive: true });

    nowPlayingSection.addEventListener('touchcancel', () => {
      swipeTracking = false;
    }, { passive: true });
  }
  closeBtn.addEventListener('click', closeSidebar);
  overlay.addEventListener('click', closeSidebar);

  // When main device list loads, sync sidebar
  new MutationObserver(() => syncSidebarDropdown()).observe(deviceEl, { childList: true });

  refreshSidebar.addEventListener('click', () => {
    loadDevices(true);
  });

  // Exposed so playing/removing a history row from inside the sidebar (mobile)
  // can close it, matching the sign-out button's behavior.
  window._closeSidebar = closeSidebar;
})();

/* ---- Persistent YouTube browser authentication ---- */
(function () {
  const openBtn = document.getElementById('youtube-browser-auth');
  const modal = document.getElementById('youtube-browser-auth-modal-wrap');
  const closeBtn = document.getElementById('youtube-browser-auth-close');
  const saveBtn = document.getElementById('youtube-browser-auth-save');
  const input = document.getElementById('youtube-browser-headers');
  const status = document.getElementById('youtube-browser-auth-status');
  const startBtn = document.getElementById('youtube-browser-session-start');
  const retryBtn = document.getElementById('youtube-browser-session-retry');
  const stopBtn = document.getElementById('youtube-browser-session-stop');
  const browserLink = document.getElementById('youtube-browser-session-link');
  if (!openBtn || !modal || !saveBtn || !input) return;
  let pollTimer = null;
  let pollCount = 0;
  const labels = {
    capture_requested: 'Capture requested...',
    settling_session: 'Saving the signed-in browser profile...',
    validating_login: 'Validating the selected YouTube Music account...',
    idle: 'Ready to connect.', connecting: 'Starting the secure browser…',
    waiting_for_login: 'Waiting until YouTube Music is fully signed in…',
    captured: 'Signed-in request captured…', validating: 'Validating personalized access…',
    refreshing: 'Refreshing the saved browser session…', connected: 'Connected. Personalized data is ready.',
    reconnect_required: 'Google needs you to sign in or complete a challenge.',
    unavailable: 'The browser service is unavailable. You can still use manual import below.'
  };

  function stopPolling() {
    if (pollTimer) window.clearTimeout(pollTimer);
    pollTimer = null;
  }
  function render(data) {
    const stateName = data.state || 'unavailable';
    if (status) status.textContent = data.message || labels[stateName] || stateName;
    modal.dataset.browserState = stateName;
    if (retryBtn) retryBtn.hidden = !['reconnect_required', 'unavailable'].includes(stateName);
    if (stopBtn) stopBtn.hidden = !['connecting', 'waiting_for_login', 'capture_requested',
      'settling_session', 'validating_login', 'captured', 'validating'].includes(stateName);
    if (stateName === 'connected') {
      stopPolling();
      window.setTimeout(() => { window.location.href = '/?refresh=1'; }, 700);
    } else if (['reconnect_required', 'unavailable', 'idle'].includes(stateName)) stopPolling();
  }
  async function poll() {
    if (modal.hidden || pollCount++ >= 120) { stopPolling(); return; }
    try {
      const data = await window.api('/api/youtube/browser-session/status');
      render(data);
      if (!['connected', 'reconnect_required', 'unavailable', 'idle'].includes(data.state)) {
        pollTimer = window.setTimeout(poll, 1500);
      }
    } catch (error) { render({ state: 'unavailable', message: error.message }); }
  }
  async function begin(endpoint) {
    render({ state: 'connecting' });
    try {
      const data = await window.api(endpoint, {});
      render(data);
      if (browserLink && data.url) {
        browserLink.href = data.url;
        browserLink.hidden = false;
        const popup = window.open(data.url, '_blank');
        if (popup) popup.opener = null;
        if (!popup) browserLink.textContent = 'Popup blocked — open browser here';
      }
      pollCount = 0;
      stopPolling();
      pollTimer = window.setTimeout(poll, 1000);
    } catch (error) { render({ state: 'unavailable', message: error.message }); }
  }

  function close() {
    stopPolling();
    modal.hidden = true;
    input.value = '';
    if (status) status.textContent = '';
  }

  openBtn.addEventListener('click', () => {
    const profile = document.getElementById('profile-menu-wrap');
    if (profile) profile.classList.remove('open');
    modal.hidden = false;
    render({ state: 'idle' });
  });
  if (startBtn) startBtn.addEventListener('click', () => begin('/api/youtube/browser-session/start'));
  if (retryBtn) retryBtn.addEventListener('click', () => begin('/api/youtube/browser-session/retry'));
  if (stopBtn) stopBtn.addEventListener('click', async () => {
    stopPolling();
    try { await window.api('/api/youtube/browser-session/stop', {}); } catch (_) {}
    render({ state: 'idle' });
    if (browserLink) browserLink.hidden = true;
  });
  if (closeBtn) closeBtn.addEventListener('click', close);
  modal.addEventListener('click', (event) => { if (event.target === modal) close(); });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !modal.hidden) close();
  });

  saveBtn.addEventListener('click', async () => {
    const headers = input.value.trim();
    if (!headers) {
      if (status) status.textContent = 'Paste the request headers first.';
      return;
    }
    saveBtn.disabled = true;
    saveBtn.textContent = 'Validating…';
    if (status) status.textContent = 'Checking headers with YouTube Music…';
    try {
      await window.api('/api/youtube/browser-auth', { headers });
      input.value = '';
      if (status) status.textContent = 'Connected. Reloading your personalized data…';
      window.location.href = '/?refresh=1';
    } catch (error) {
      if (status) status.textContent = error.message || 'Could not validate these headers.';
    } finally {
      saveBtn.disabled = false;
      saveBtn.textContent = 'Validate manual headers';
    }
  });
})();


/* ---- Nav rail (Home / Recently Listened) ---- */
(function () {
  const navHome = document.getElementById('nav-home-btn');
  const navHistory = document.getElementById('nav-history-btn');
  const navExplore = document.getElementById('nav-explore-btn');
  const navLibrary = document.getElementById('nav-library-btn');

  function goHome() {
    const alreadyHome = window.getRoute && window.getRoute() === '#home';
    // An active Home item is intentionally inert: do not reapply the route,
    // dispatch a synthetic hashchange, or reset the user's scroll position.
    if (alreadyHome && !state._resultsOpen) {
      if (window._closeSidebar) window._closeSidebar();
      return;
    }
    if (state._resultsOpen && window.closeResults) window.closeResults();
    if (!alreadyHome) window.navigateTo('#home');
    if (window._closeSidebar) window._closeSidebar();
    if (!alreadyHome) window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  if (navHome) navHome.addEventListener('click', goHome);

  if (navHistory) navHistory.addEventListener('click', () => {
    if (window.getRoute && window.getRoute() === '#history') {
      if (window._closeSidebar) window._closeSidebar();
      return;
    }
    if (state._resultsOpen && window.closeResults) window.closeResults();
    if (window._closeSidebar) window._closeSidebar();
    window.navigateTo('#history');
  });

  if (navExplore) navExplore.addEventListener('click', () => {
    if (window.getRoute && window.getRoute() === '#explore') {
      if (window._closeSidebar) window._closeSidebar();
      return;
    }
    if (state._resultsOpen && window.closeResults) window.closeResults();
    if (window._closeSidebar) window._closeSidebar();
    if (window.preloadNavigateExplore) window.preloadNavigateExplore();
    else window.navigateTo('#explore');
  });

  if (navLibrary) navLibrary.addEventListener('click', () => {
    if (window.getRoute && window.getRoute() === '#library') {
      if (window._closeSidebar) window._closeSidebar();
      return;
    }
    if (state._resultsOpen && window.closeResults) window.closeResults();
    if (window._closeSidebar) window._closeSidebar();
    if (window.preloadNavigateLibrary) window.preloadNavigateLibrary();
    else window.navigateTo('#library');
  });
})();


/* ---- Rail toggle (desktop): collapse/expand the left sidebar. On mobile the
   same button opens the drawer, replacing the old right-side hamburger. ---- */
(function () {
  const btn = document.getElementById('rail-toggle-btn');
  const sidebarBtn = document.getElementById('sidebar-rail-toggle');
  if (!btn && !sidebarBtn) return;
  const KEY = 'railCollapsed';
  try {
    if (localStorage.getItem(KEY) === '1') document.body.classList.add('rail-collapsed');
  } catch (_) {}
  function toggleRail() {
    if (window.matchMedia('(max-width: 899px)').matches) {
      // Mobile: the rail is a drawer — open it.
      const hamburger = document.getElementById('hamburger-btn');
      if (hamburger) hamburger.click();
      return;
    }
    const collapsed = document.body.classList.toggle('rail-collapsed');
    try { localStorage.setItem(KEY, collapsed ? '1' : '0'); } catch (_) {}
  }
  if (btn) btn.addEventListener('click', toggleRail);
  if (sidebarBtn) sidebarBtn.addEventListener('click', toggleRail);
})();

/* ---- Top-bar profile menu ---- */
(function () {
  const wrap = document.getElementById('profile-menu-wrap');
  const trigger = document.getElementById('profile-menu-trigger');
  const menu = document.getElementById('profile-menu');
  if (!wrap || !trigger || !menu) return;
  trigger.addEventListener('click', async (e) => {
    e.stopPropagation();
    const open = wrap.classList.toggle('open');
    trigger.setAttribute('aria-expanded', String(open));
    
    if (open && window.api && !window.JAM_GUEST) {
      try {
        const status = await window.api('/api/profile_status/');
        
        const amzEl = document.getElementById('status-amazon');
        if (amzEl) {
          amzEl.textContent = status.amazon_connected ? 'Connected' : 'Disconnected';
          amzEl.style.color = status.amazon_connected ? '#4ade80' : '#ff6b6b';
          amzEl.style.backgroundColor = status.amazon_connected ? 'rgba(74, 222, 128, 0.1)' : 'rgba(255, 107, 107, 0.1)';
          if (status.debug && status.debug.amazon) amzEl.title = status.debug.amazon;
        }
        

        
        const ythEl = document.getElementById('status-yt-headers');
        if (ythEl) {
          ythEl.textContent = status.youtube_browser_refreshing ? 'Refreshing' :
            (status.youtube_browser_reconnect_required ? 'Reconnect' :
              (status.youtube_auth_working ? 'Saved session active' : 'Not connected'));
          ythEl.style.color = status.youtube_auth_working ? '#4ade80' : '#ff6b6b';
          ythEl.style.backgroundColor = status.youtube_auth_working ? 'rgba(74, 222, 128, 0.1)' : 'rgba(255, 107, 107, 0.1)';
          if (status.youtube_browser_last_successful_renewal) {
            ythEl.title = `Last browser renewal: ${new Date(status.youtube_browser_last_successful_renewal * 1000).toLocaleString()}`;
          } else if (status.debug && status.debug.headers) ythEl.title = status.debug.headers;
        }

        const ytcEl = document.getElementById('status-yt-cookies');
        if (ytcEl) {
          const cookiesOk = status.youtube_cookies_working;
          ytcEl.textContent = cookiesOk ? 'Valid' : (status.youtube_cookies_present ? 'Invalid' : 'Not Found');
          ytcEl.style.color = cookiesOk ? '#4ade80' : '#ff6b6b';
          ytcEl.style.backgroundColor = cookiesOk ? 'rgba(74, 222, 128, 0.1)' : 'rgba(255, 107, 107, 0.1)';
          if (status.debug && status.debug.cookies) ytcEl.title = status.debug.cookies;
        }
        
        const amzSignout = document.getElementById('amazon-signout');
        const amzSignin = document.getElementById('amazon-signin');
        if (amzSignout) amzSignout.style.display = status.amazon_connected ? 'block' : 'none';
        if (amzSignin) amzSignin.style.display = status.amazon_connected ? 'none' : 'block';
        
      } catch (err) {
        console.error("Failed to load profile status", err);
      }
    }
  });
  
  const amzSignoutBtn = document.getElementById('amazon-signout');
  if (amzSignoutBtn) {
    amzSignoutBtn.addEventListener('click', async () => {
      try {
        if (window.api) {
          await window.api('/alexa/amazon_signout/', {});
          if (window.toast) window.toast('Signed out of Amazon');
          window.location.reload();
        }
      } catch (e) {
        console.error(e);
      }
    });
  }

  const amzSigninBtn = document.getElementById('amazon-signin');
  if (amzSigninBtn) {
    amzSigninBtn.addEventListener('click', () => {
      wrap.classList.remove('open');
      const emailInput = document.getElementById('login-email');
      if (emailInput) {
        document.getElementById('login-section').scrollIntoView({ behavior: 'smooth' });
        setTimeout(() => emailInput.focus(), 300);
      }
    });
  }
  
  document.addEventListener('click', (e) => {
    if (wrap.contains(e.target)) return;
    wrap.classList.remove('open');
    trigger.setAttribute('aria-expanded', 'false');
  });
})();

/* Logo/wordmark is a Home shortcut; the nested rail toggle keeps its own
   independent action. */
(function () {
  const brands = [
    { element: document.getElementById('header-brand-home'), toggle: '.rail-toggle-btn' },
    { element: document.getElementById('sidebar-brand-home'), toggle: '.sidebar-rail-toggle' }
  ];
  function goHome(e, toggleSelector) {
    if (e.target.closest(toggleSelector)) return;
    if (window.matchMedia('(max-width: 899px)').matches && window._closeSidebar) {
      window._closeSidebar();
    }
    const alreadyHome = (window.getRoute && window.getRoute()) === '#home';
    // Search results live on the Home route, so navigating to #home alone is
    // a no-op. Close the results explicitly before treating the brand as an
    // already-active Home shortcut.
    if (alreadyHome) {
      if (state._resultsOpen && window.closeResults) {
        window.closeResults();
        window.scrollTo({ top: 0, behavior: 'smooth' });
      }
      return;
    }
    if (state._resultsOpen && window.closeResults) window.closeResults();
    if (window.navigateTo) window.navigateTo('#home');
  }
  brands.forEach(function (entry) {
    const brand = entry.element;
    if (!brand) return;
    brand.addEventListener('click', function (e) { goHome(e, entry.toggle); });
    brand.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      e.preventDefault();
      goHome(e, entry.toggle);
    });
  });
})();
})();

