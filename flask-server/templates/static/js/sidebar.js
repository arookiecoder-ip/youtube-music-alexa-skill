(function () {
  'use strict';
  const state = window.__appState = window.__appState || {};


/* ---- Mobile sidebar ---- */
(function () {
  const deviceEl = document.getElementById('device');
  const hamburger = document.getElementById('hamburger-btn');
  const mobileSearchToggle = document.getElementById('mobile-search-toggle');
  const mobilePlayerClose = document.getElementById('mobile-player-close');
  const searchSection = document.querySelector('.header-search-wrap');
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
      item.classList.toggle('logged-out', opt.dataset.loggedOut === '1');
      const isOnline = opt.dataset.online !== '0';
      item.innerHTML = (opt.value
        ? '<span class="cs-dot ' + (isOnline ? 'online' : 'offline') + '"></span>'
        : '') + escHtml(opt.textContent);
      item.addEventListener('click', () => {
        if (opt.dataset.loggedOut === '1') { window.toast?.('Sign in to Amazon to control Alexa.', 'error'); return; }
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
    hamburger.setAttribute('aria-expanded', 'true');
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
    hamburger.setAttribute('aria-expanded', 'false');
    setTimeout(() => { overlay.style.display = 'none'; }, 300);
  }

  hamburger.addEventListener('click', () => {
    if (sidebar.classList.contains('open')) closeSidebar();
    else openSidebar();
  });

  function closeMobileSearch() {
    document.body.classList.remove('mobile-search-open');
    if (mobileSearchToggle) mobileSearchToggle.setAttribute('aria-expanded', 'false');
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
      mobileSearchToggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
      if (isOpen && searchInput) {
        requestAnimationFrame(() => searchInput.focus());
      }
    });
    document.addEventListener('click', (event) => {
      if (!document.body.classList.contains('mobile-search-open')) return;
      if (event.target.closest('.header-search-wrap, .mobile-search-toggle')) return;
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

/* ---- Nav rail (Home / Recently Listened) ---- */
(function () {
  const navHome = document.getElementById('nav-home-btn');
  const navHistory = document.getElementById('nav-history-btn');
  const navExplore = document.getElementById('nav-explore-btn');
  const navLibrary = document.getElementById('nav-library-btn');

  function goHome() {
    const alreadyHome = window.getRoute && window.getRoute() === '#home';
    const overlayOpen = document.body.classList.contains('now-playing-route');
    // An active Home item is intentionally inert: do not reapply the route,
    // dispatch a synthetic hashchange, or reset the user's scroll position.
    // Exception: if the full player overlay is open, Home must still
    // minimize it back down to the (already active) home page.
    if (alreadyHome && !state._resultsOpen && !overlayOpen) {
      if (window._closeSidebar) window._closeSidebar();
      return;
    }
    if (state._resultsOpen && window.closeResults) window.closeResults();
    // navigateTo() re-runs applyRoute() even when the route hasn't changed,
    // which is what closes ("minimizes") the now-playing overlay.
    if (!alreadyHome || overlayOpen) window.navigateTo('#home');
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

/* ---- Top-bar profile menu ----
   The menu reports what is not linked and points at /account. Signing in to
   Amazon or YouTube, and signing out, both live on the account page so there is
   one place to do either. */
(function () {
  const wrap = document.getElementById('profile-menu-wrap');
  const trigger = document.getElementById('profile-menu-trigger');
  const menu = document.getElementById('profile-menu');
  if (!wrap || !trigger || !menu) return;

  function paint(id, text, ok) {
    const node = document.getElementById(id);
    if (!node) return;
    node.textContent = text;
    node.classList.toggle('is-ok', !!ok);
    node.classList.toggle('is-bad', !ok);
  }

  function renderAlerts(status) {
    const alert = document.getElementById('profile-alert');
    const list = document.getElementById('profile-alert-list');
    if (!alert || !list) return;
    const missing = [];
    if (status.alexa_access_enabled === true && !status.amazon_connected) missing.push('Amazon / Alexa is not connected.');
    // The server's own session is not this account being linked; it plays music
    // but is not reported here as a connection.
    if (!status.youtube_own_session) missing.push('YouTube Music is not connected.');
    if (status.youtube_cookies_source === 'none') {
      missing.push('No download cookies are saved.');
    }
    list.replaceChildren(...missing.map((text) => {
      const item = document.createElement('li');
      item.textContent = text;
      return item;
    }));
    alert.hidden = missing.length === 0;
  }

  trigger.addEventListener('click', async (event) => {
    event.stopPropagation();
    const open = wrap.classList.toggle('open');
    trigger.setAttribute('aria-expanded', String(open));
    if (!open || !window.api || window.JAM_GUEST) return;
    try {
      const status = await window.api('/api/profile_status/?audio_check=0');
      window.updateAmazonAccess?.(status.alexa_access_enabled === true);
      window.setAmazonConnection?.(status.amazon_connected);
      paint('status-amazon', status.amazon_connected ? 'Connected' : 'Not connected',
        status.amazon_connected);
      const youtubeOwn = !!status.youtube_own_session;
      paint('status-yt-headers',
        status.youtube_browser_refreshing ? 'Refreshing'
          : status.youtube_browser_reconnect_required ? 'Needs attention'
            : youtubeOwn ? 'Connected' : 'Not connected',
        youtubeOwn);
      renderAlerts(status);
    } catch (_) {
      paint('status-amazon', 'Check failed', false);
      paint('status-yt-headers', 'Check failed', false);
    }
  });

  document.addEventListener('click', (event) => {
    if (wrap.contains(event.target)) return;
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
    const overlayOpen = document.body.classList.contains('now-playing-route');
    // Search results live on the Home route, so navigating to #home alone is
    // a no-op. Close the results explicitly before treating the brand as an
    // already-active Home shortcut. Exception: if the full player overlay
    // is open, the brand must still minimize it back down to home.
    if (alreadyHome && !overlayOpen) {
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


// Connection health is independent of opening the account menu. Never run an
// audio download probe for this indicator, and do not turn a network error red.
(() => {
 window.updateAmazonAccess=enabled=>{document.body.dataset.alexaEnabled=String(enabled);window.dispatchEvent(new CustomEvent('amazon-access', {detail:enabled}));const status=document.getElementById('status-amazon');if(status?.parentElement)status.parentElement.hidden=!enabled;if(!enabled){const trigger=document.getElementById('profile-menu-trigger');trigger?.classList.remove('amazon-disconnected');trigger?.setAttribute('aria-label','Account');}};
 const trigger=document.getElementById('profile-menu-trigger');
 if(!trigger || window.JAM_GUEST)return;
 window.setAmazonConnection=connected=>{if(typeof connected!=='boolean')return;trigger.classList.toggle('amazon-disconnected',!connected);trigger.setAttribute('aria-label',connected?'Account':'Account — Amazon disconnected');};
 let timer;
 async function check(){if(!document.hidden && window.api){try{const d=await window.api('/api/profile_status/?audio_check=0');window.updateAmazonAccess(d.alexa_access_enabled===true);if(d.alexa_access_enabled===true)window.setAmazonConnection(d.amazon_connected);}catch(_){}}timer=setTimeout(check,60000);}
 check();window.addEventListener('pagehide',()=>clearTimeout(timer));
})();
