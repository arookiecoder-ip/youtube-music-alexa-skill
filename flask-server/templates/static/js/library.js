(function () {
  'use strict';
  const state = window.__appState = window.__appState || {};

  // ── helpers ────────────────────────────────────────────────────────────────
  function esc(s) {
    if (window.escHtml) return window.escHtml(s);
    return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  const FALLBACK_IMG = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Crect width='24' height='24' rx='4' fill='%231a1a1a'/%3E%3Cpath d='M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z' fill='%23444'/%3E%3C/svg%3E";

  function renderSkeleton() {
    let cards = '';
    for (let i = 0; i < 12; i++) {
      cards += `<div class="library-card library-skeleton-card">
        <div class="library-card-art"><div class="skeleton-block"></div></div>
        <div class="library-card-info">
          <div class="skeleton-line" style="width:75%;height:13px;margin-bottom:5px;"></div>
          <div class="skeleton-line" style="width:50%;height:11px;"></div>
        </div>
      </div>`;
    }
    return `<div class="library-section">
      <div class="library-section-header">
        <div class="skeleton-line" style="width:180px;height:20px;margin:0 0 18px;"></div>
      </div>
      <div class="library-grid">${cards}</div>
    </div>`;
  }

  function renderPlaylistCard(pl) {
    const thumbs = pl.thumbnails || [];
    const thumb = (thumbs[thumbs.length - 1] || {}).url || '';
    const title = pl.title || pl.name || 'Untitled Playlist';
    const count = pl.count != null ? `${pl.count} songs` : (pl.trackCount != null ? `${pl.trackCount} songs` : '');
    const sub = count || pl.description || '';
    const isLiked = pl.playlistId === 'LM' || pl.id === 'LM';

    const card = document.createElement('div');
    card.className = 'library-card' + (isLiked ? ' library-card-liked' : '');
    card.setAttribute('role', 'button');
    card.setAttribute('tabindex', '0');
    card.setAttribute('aria-label', esc(title));

    const thumbHtml = thumb
      ? `<img src="${esc(thumb)}" alt="${esc(title)}" loading="lazy" onerror="this.onerror=null;this.src='${FALLBACK_IMG}'">`
      : `<div class="library-card-art-placeholder">${isLiked
          ? `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>`
          : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>`
        }</div>`;

    card.innerHTML = `
      <div class="library-card-art">${thumbHtml}</div>
      <div class="library-card-info">
        <div class="library-card-title">${esc(title)}</div>
        ${sub ? `<div class="library-card-sub">${esc(sub)}</div>` : ''}
      </div>
    `;

    const playlistId = pl.playlistId || pl.id || '';
    function open() {
      if (!playlistId) return;
      if (window._closeSidebar) window._closeSidebar();
      window.navigateTo('#playlist/' + encodeURIComponent(playlistId));
    }
    card.addEventListener('click', open);
    card.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
    return card;
  }

  // ── main load function ─────────────────────────────────────────────────────
  let _loaded = false;
  let _loading = false;

  async function loadLibrary(force) {
    if (!state._loggedIn || window.JAM_GUEST || !window.IS_AUTHENTICATED) return;

    const body = document.getElementById('library-modal-body');
    if (!body) return;

    if (_loading) return;
    if (_loaded && !force) return;

    _loading = true;
    body.innerHTML = renderSkeleton();

    try {
      const data = await window.api('/api/library/');
      _loaded = true;

      body.innerHTML = '';

      const playlists = (data && data.playlists) || [];

      if (!playlists.length) {
        body.innerHTML = `<div class="library-empty">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="52" height="52">
            <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/>
            <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>
          </svg>
          <div class="library-empty-title">Your library is empty</div>
          <div class="library-empty-sub">Create playlists in YouTube Music to see them here.</div>
        </div>`;
        return;
      }

      // ── Header with count ──────────────────────────────────────────────────
      const header = document.createElement('div');
      header.className = 'library-section';
      header.innerHTML = `<div class="library-section-header">
        <h2 class="library-section-title">Playlists <span class="library-count">${playlists.length}</span></h2>
      </div>`;
      const grid = document.createElement('div');
      grid.className = 'library-grid';
      header.appendChild(grid);
      body.appendChild(header);

      playlists.forEach(pl => {
        grid.appendChild(renderPlaylistCard(pl));
      });

    } catch (e) {
      console.warn('[library] Failed to load', e);
      _loaded = false;
      body.innerHTML = `<div class="library-empty">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="52" height="52">
          <circle cx="12" cy="12" r="10"/>
          <line x1="12" y1="8" x2="12" y2="12"/>
          <line x1="12" y1="16" x2="12.01" y2="16"/>
        </svg>
        <div class="library-empty-title">Couldn't load your library</div>
        <div class="library-empty-sub">${esc(e && e.message ? e.message : 'Check your YT Music connection and try again.')}</div>
        <button type="button" class="btn-accent" id="library-retry-btn">Try Again</button>
      </div>`;
      const btn = document.getElementById('library-retry-btn');
      if (btn) btn.addEventListener('click', () => { _loaded = false; loadLibrary(true); });
    } finally {
      _loading = false;
    }
  }

  // ── public API ─────────────────────────────────────────────────────────────
  window.openLibraryPage = function (force) {
    const overlay = document.getElementById('library-modal-overlay');
    if (overlay) overlay.classList.add('open');
    loadLibrary(force);
  };

  window.closeLibraryPage = function () {
    const overlay = document.getElementById('library-modal-overlay');
    if (overlay) overlay.classList.remove('open');
  };

  // ── close button ───────────────────────────────────────────────────────────
  const closeBtn = document.getElementById('library-modal-close');
  if (closeBtn) {
    closeBtn.addEventListener('click', function () {
      window.navigateTo('#home');
    });
  }
})();
