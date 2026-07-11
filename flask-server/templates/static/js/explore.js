(function () {
  'use strict';
  const state = window.__appState = window.__appState || {};

  async function loadExplore() {
    if (!state._loggedIn || window.JAM_GUEST || !window.IS_AUTHENTICATED) return;
    
    const body = document.getElementById('explore-modal-body');
    if (!body) return;
    
    body.innerHTML = '<div class="loading-spinner"></div>';
    
    try {
      const explore = await window.api('/api/explore/');
      body.innerHTML = '';
      
      // Explore data comes from get_explore in ytmusicapi, which returns new releases, mood playlists, etc.
      if (explore && explore.playlists && explore.playlists.length > 0) {
        explore.playlists.forEach(pl => {
          const row = document.createElement('div');
          row.className = 'history-item';
          row.style.cursor = 'pointer';
          row.innerHTML = `
            <div class="history-item-thumb">
              <img src="${pl.thumbnails?.[0]?.url || '/static/default-art.png'}" loading="lazy" alt="art">
            </div>
            <div class="history-item-info">
              <div class="history-item-title">${escapeHtml(pl.title)}</div>
              <div class="history-item-artist">${escapeHtml(pl.description || 'YouTube Music')}</div>
            </div>
          `;
          row.onclick = () => {
             if (window._closeSidebar) window._closeSidebar();
             window.navigateTo('#playlist/' + encodeURIComponent(pl.playlistId));
          };
          body.appendChild(row);
        });
      } else {
        body.innerHTML = '<div style="padding: 20px; color: var(--muted); text-align: center;">No explore data available.</div>';
      }
    } catch (e) {
      console.warn('Failed to load explore data', e);
      body.innerHTML = '<div style="padding: 20px; color: var(--muted); text-align: center;">Failed to load explore data.</div>';
    }
  }

  window.openExplorePage = function() {
    const overlay = document.getElementById('explore-modal-overlay');
    if (overlay) overlay.classList.add('open');
    loadExplore();
  };

  const closeBtn = document.getElementById('explore-modal-close');
  if (closeBtn) {
    closeBtn.addEventListener('click', () => {
      window.history.back();
    });
  }
})();
