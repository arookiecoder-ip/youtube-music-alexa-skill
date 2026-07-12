(function () {
  'use strict';
  const state = window.__appState = window.__appState || {};


/* ---- Mobile sidebar ---- */
(function () {
  const deviceEl = document.getElementById('device');
  const hamburger = document.getElementById('hamburger-btn');
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
    overlay.style.display = 'block';
    requestAnimationFrame(() => {
      sidebar.classList.add('open');
      overlay.classList.add('open');
    });
    syncSidebarDropdown();
  }

  function closeSidebar() {
    sidebar.classList.remove('open');
    overlay.classList.remove('open');
    sbWrapper.classList.remove('open');
    setTimeout(() => { overlay.style.display = 'none'; }, 300);
  }

  hamburger.addEventListener('click', openSidebar);
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
    if (state._resultsOpen && window.closeResults) window.closeResults();
    if (window.getRoute() !== '#home') window.navigateTo('#home');
    else window.dispatchEvent(new Event('hashchange'));
    if (window._closeSidebar) window._closeSidebar();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  if (navHome) navHome.addEventListener('click', goHome);

  if (navHistory) navHistory.addEventListener('click', () => {
    if (state._resultsOpen && window.closeResults) window.closeResults();
    if (window._closeSidebar) window._closeSidebar();
    window.navigateTo('#history');
  });

  if (navExplore) navExplore.addEventListener('click', () => {
    if (state._resultsOpen && window.closeResults) window.closeResults();
    if (window._closeSidebar) window._closeSidebar();
    window.navigateTo('#explore');
  });

  if (navLibrary) navLibrary.addEventListener('click', () => {
    if (state._resultsOpen && window.closeResults) window.closeResults();
    if (window._closeSidebar) window._closeSidebar();
    window.navigateTo('#library');
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
    
    if (open && window.api) {
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
          ythEl.textContent = status.youtube_header_auth_working ? 'Working' : 'Not Working';
          ythEl.style.color = status.youtube_header_auth_working ? '#4ade80' : '#ff6b6b';
          ythEl.style.backgroundColor = status.youtube_header_auth_working ? 'rgba(74, 222, 128, 0.1)' : 'rgba(255, 107, 107, 0.1)';
          if (status.debug && status.debug.headers) ythEl.title = status.debug.headers;
        }
        
        const amzSignout = document.getElementById('amazon-signout');
        const amzSignin = document.getElementById('amazon-signin');
        if (amzSignout) amzSignout.style.display = status.amazon_connected ? 'block' : 'none';
        if (amzSignin) amzSignin.style.display = status.amazon_connected ? 'none' : 'block';
        const ytSignin = document.getElementById('youtube-signin');
        if (ytSignin) ytSignin.style.display = status.youtube_header_auth_working ? 'none' : 'block';
        
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
  const brand = document.getElementById('sidebar-brand-home');
  if (!brand) return;
  function goHome(e) {
    if (e.target.closest('.sidebar-rail-toggle')) return;
    if (window.navigateTo) window.navigateTo('#home');
  }
  brand.addEventListener('click', goHome);
  brand.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    e.preventDefault();
    goHome(e);
  });
  
})();
})();

/* ---- YouTube OAuth Modal ---- */
(function () {
  const ytSigninBtn = document.getElementById('youtube-signin');
  const ytOauthModalWrap = document.getElementById('youtube-oauth-modal-wrap');
  const ytOauthClose = document.getElementById('youtube-oauth-close');
  const ytOauthUserCode = document.getElementById('yt-oauth-user-code');
  const ytOauthLink = document.getElementById('yt-oauth-link');
  const ytOauthStatusText = document.getElementById('yt-oauth-status-text');
  let oauthPollInterval = null;
  let currentDeviceCode = null;

  if (ytSigninBtn && ytOauthModalWrap) {
    ytSigninBtn.addEventListener('click', async () => {
      const wrap = document.getElementById('profile-menu-wrap');
      if (wrap) wrap.classList.remove('open');
      
      try {
        if (window.toast) window.toast('Starting YouTube OAuth...', 2000);
        const res = await window.api('/api/youtube/oauth/start', {});
        if (res.error) throw new Error(res.error);
        
        currentDeviceCode = res.device_code;
        if (ytOauthUserCode) ytOauthUserCode.textContent = res.user_code;
        if (ytOauthLink) ytOauthLink.href = res.verification_url + '?user_code=' + res.user_code;
        if (ytOauthStatusText) ytOauthStatusText.textContent = 'Waiting for authorization...';
        
        ytOauthModalWrap.hidden = false;
        
        if (oauthPollInterval) clearInterval(oauthPollInterval);
        oauthPollInterval = setInterval(async () => {
          if (!currentDeviceCode) return;
          try {
            const pollRes = await window.api('/api/youtube/oauth/finish', { device_code: currentDeviceCode });
            if (pollRes.success) {
              clearInterval(oauthPollInterval);
              if (ytOauthStatusText) ytOauthStatusText.textContent = 'Success! Authenticated.';
              if (window.toast) window.toast('Successfully logged in to YouTube Music!');
              setTimeout(() => {
                window.location.href = '/?refresh=1';
              }, 1500);
            }
          } catch (e) {
            // Ignore polling errors
          }
        }, 5000);
        
      } catch (err) {
        if (window.toast) window.toast('Failed to start OAuth: ' + err.message, 4000);
      }
    });
    
    if (ytOauthClose) {
      ytOauthClose.addEventListener('click', () => {
        ytOauthModalWrap.hidden = true;
        if (oauthPollInterval) clearInterval(oauthPollInterval);
        currentDeviceCode = null;
      });
    }
  }
})();
