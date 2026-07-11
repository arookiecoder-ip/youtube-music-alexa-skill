(function () {
  'use strict';
  const state = window.__appState = window.__appState || {};
  if (state._loggedIn === undefined) state._loggedIn = false;

  async function loadLibrary() {
    if (!state._loggedIn || window.JAM_GUEST || !window.IS_AUTHENTICATED) return;
    try {
      const data = await api('/api/library/');
      const container = document.getElementById('sidebar-playlist-list');
      if (container) {
        container.innerHTML = '';
        if (data.playlists && data.playlists.length > 0) {
          data.playlists.forEach(pl => {
            const btn = document.createElement('button');
            btn.className = 'sidebar-nav-btn playlist-nav-btn';
            btn.innerHTML = `<div class="playlist-nav-name">${escapeHtml(pl.title)}</div>`;
            btn.onclick = () => {
              if (window._closeSidebar) window._closeSidebar();
              window.navigateTo('#playlist/' + encodeURIComponent(pl.playlistId));
            };
            container.appendChild(btn);
          });
        }
      }
    } catch (e) {
      console.warn('Failed to load library playlists', e);
    }
  }

  window.addEventListener('hashchange', () => {
    const hash = window.getRoute();
    if (hash.startsWith('#playlist/')) {
      const plId = decodeURIComponent(hash.substring(10));
      openLibraryPlaylist(plId);
    }
  });

  async function openLibraryPlaylist(plId) {
    const overlay = document.getElementById('playlist-detail-modal-overlay');
    if (overlay) overlay.classList.add('open');
    const headerTitle = document.getElementById('playlist-detail-header-title');
    const headerMeta = document.getElementById('playlist-detail-header-meta');
    const list = document.getElementById('playlist-detail-tracks');
    
    if (headerTitle) headerTitle.textContent = 'Loading...';
    if (headerMeta) headerMeta.textContent = '';
    if (list) list.innerHTML = '<div class="loading-spinner"></div>';
    
    try {
      const pl = await window.api('/api/library/playlists/' + encodeURIComponent(plId));
      if (headerTitle) headerTitle.textContent = pl.title || 'Playlist';
      if (headerMeta && pl.trackCount) headerMeta.textContent = `${pl.trackCount} tracks`;
      
      if (list) {
        list.innerHTML = '';
        if (pl.tracks && pl.tracks.length > 0) {
          pl.tracks.forEach(track => {
            const row = document.createElement('div');
            row.className = 'playlist-track-row';
            row.innerHTML = `
              <div class="playlist-track-thumb-container">
                <img src="${track.thumbnails?.[0]?.url || '/static/default-art.png'}" class="playlist-track-thumb" loading="lazy" alt="art">
              </div>
              <div class="playlist-track-info">
                <div class="playlist-track-title">${escapeHtml(track.title)}</div>
                <div class="playlist-track-artist">${escapeHtml(track.artists?.map(a => a.name).join(', ') || '')}</div>
              </div>
            `;
            row.onclick = () => {
              if (window.playVideo) window.playVideo(track.videoId);
            };
            list.appendChild(row);
          });
        }
      }
    } catch (e) {
      console.warn('Failed to load playlist', e);
      if (headerTitle) headerTitle.textContent = 'Error loading playlist';
      if (list) list.innerHTML = '';
    }
  }

  // Load immediately if logged in, else device.js will call it when auth is verified.
  if (state._loggedIn && window.IS_AUTHENTICATED) {
    loadLibrary();
  }
  
  // Attach to window so device.js can trigger it on login
  window.loadLibrary = loadLibrary;
})();
