(function () {
  'use strict';
  const state = window.__appState = window.__appState || {};

  // ── helpers ────────────────────────────────────────────────────────────────
  function escHtml(s) {
    if (window.escHtml) return window.escHtml(s);
    return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  const FALLBACK_IMG = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23333'%3E%3Crect width='24' height='24' rx='4' fill='%231a1a1a'/%3E%3Cpath d='M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z' fill='%23444'/%3E%3C/svg%3E";

  function imgWithFallback(url, alt) {
    const safe = escHtml(url || '');
    const safeAlt = escHtml(alt || '');
    if (!url) return `<div class="explore-thumb-placeholder"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z"/></svg></div>`;
    return `<img src="${safe}" alt="${safeAlt}" loading="lazy" onerror="this.onerror=null;this.src='${FALLBACK_IMG}'">`;
  }

  // ── render helpers ─────────────────────────────────────────────────────────
  function renderCard(item, onClick) {
    const thumbs = item.thumbnails || item.thumbnail || [];
    const thumbArr = Array.isArray(thumbs) ? thumbs : [thumbs];
    const thumb = (thumbArr[thumbArr.length - 1] || {}).url || (typeof thumbs === 'string' ? thumbs : '');
    const title = item.title || item.name || 'Unknown';
    const sub = item.description || item.subtitle || item.artists?.[0]?.name || item.year || '';

    const card = document.createElement('div');
    card.className = 'explore-card';
    card.innerHTML = `
      <div class="explore-card-art">${imgWithFallback(thumb, title)}</div>
      <div class="explore-card-info">
        <div class="explore-card-title">${escHtml(title)}</div>
        ${sub ? `<div class="explore-card-sub">${escHtml(sub)}</div>` : ''}
      </div>
    `;
    if (onClick) card.addEventListener('click', onClick);
    if (onClick) card.style.cursor = 'pointer';
    return card;
  }

  function renderSection(title, items, getOnClick) {
    if (!items || !items.length) return null;

    const section = document.createElement('div');
    section.className = 'explore-section';

    const header = document.createElement('div');
    header.className = 'explore-section-header';
    header.innerHTML = `<h2 class="explore-section-title">${escHtml(title)}</h2>`;
    section.appendChild(header);

    const grid = document.createElement('div');
    grid.className = 'explore-grid';
    items.forEach((item, idx) => {
      const card = renderCard(item, getOnClick ? getOnClick(item, idx) : null);
      grid.appendChild(card);
    });
    section.appendChild(grid);
    return section;
  }

  function renderSkeleton() {
    let html = '';
    for (let s = 0; s < 3; s++) {
      html += `<div class="explore-section">
        <div class="explore-section-header">
          <div class="skeleton-line" style="width:160px;height:18px;margin:0 0 16px;"></div>
        </div>
        <div class="explore-grid">
          ${Array(6).fill('<div class="explore-card explore-skeleton-card"><div class="explore-card-art"><div class="skeleton-block"></div></div><div class="explore-card-info"><div class="skeleton-line" style="width:80%;height:12px;margin:6px 0 4px;"></div><div class="skeleton-line" style="width:55%;height:10px;"></div></div></div>').join('')}
        </div>
      </div>`;
    }
    return html;
  }

  // ── main load function ─────────────────────────────────────────────────────
  let _loaded = false;
  let _loading = false;

  async function loadExplore(force) {
    if (!state._loggedIn || window.JAM_GUEST || !window.IS_AUTHENTICATED) return;

    const body = document.getElementById('explore-modal-body');
    if (!body) return;

    if (_loading) return;
    if (_loaded && !force) return;

    _loading = true;

    try {
      const explore = await window.api('/api/explore/');
      body.innerHTML = '';
      _loaded = true;

      if (!explore || typeof explore !== 'object') {
        throw new Error('Empty response');
      }

      let hasContent = false;

      // ── New Releases ───────────────────────────────────────────────────────
      const newReleases = explore.new_releases || explore.newReleases || [];
      if (newReleases.length) {
        const sec = renderSection('New Releases', newReleases, (item) => () => {
          if (window._closeSidebar) window._closeSidebar();
          const id = item.browseId || item.playlistId || item.albumId || '';
          if (!id) return;
          if (item.type === 'Album' || item.browseId?.startsWith('MPREb')) {
            window.navigateTo('#album/' + encodeURIComponent(id));
          } else {
            window.navigateTo('#playlist/' + encodeURIComponent(id));
          }
        });
        if (sec) { body.appendChild(sec); hasContent = true; }
      }

      // ── Playlists / Featured ───────────────────────────────────────────────
      const playlists = explore.playlists || explore.featured || [];
      if (playlists.length) {
        const label = explore.featured ? 'Featured Playlists' : 'Playlists';
        const sec = renderSection(label, playlists, (item) => () => {
          if (window._closeSidebar) window._closeSidebar();
          const id = item.playlistId || item.browseId || '';
          if (!id) return;
          window.navigateTo('#playlist/' + encodeURIComponent(id));
        });
        if (sec) { body.appendChild(sec); hasContent = true; }
      }

      // ── Moods & Genres ─────────────────────────────────────────────────────
      const moods = explore.moods || explore.genres || [];
      if (moods.length) {
        const sec = renderSection('Moods & Genres', moods, (item) => () => {
          if (window._closeSidebar) window._closeSidebar();
          const id = item.params || item.playlistId || item.browseId || '';
          if (!id) return;
          // Mood params use browse not playlist
          window.navigateTo('#playlist/' + encodeURIComponent(id));
        });
        if (sec) { body.appendChild(sec); hasContent = true; }
      }

      // ── Charts ─────────────────────────────────────────────────────────────
      const charts = explore.charts || explore.trending || [];
      if (charts.length) {
        const sec = renderSection('Charts', charts, (item) => () => {
          if (window._closeSidebar) window._closeSidebar();
          const id = item.playlistId || item.browseId || '';
          if (!id) return;
          window.navigateTo('#playlist/' + encodeURIComponent(id));
        });
        if (sec) { body.appendChild(sec); hasContent = true; }
      }

      // ── Fallback: catch-all unknown keys ───────────────────────────────────
      if (!hasContent) {
        // Try to render whatever arrays the API returned
        const keys = Object.keys(explore).filter(k => Array.isArray(explore[k]) && explore[k].length > 0);
        for (const key of keys) {
          const label = key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
          const sec = renderSection(label, explore[key], (item) => () => {
            if (window._closeSidebar) window._closeSidebar();
            const id = item.playlistId || item.browseId || item.albumId || '';
            if (id) window.navigateTo('#playlist/' + encodeURIComponent(id));
          });
          if (sec) { body.appendChild(sec); hasContent = true; }
        }
      }

      if (!hasContent) {
        body.innerHTML = `<div class="explore-empty">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="48" height="48"><circle cx="12" cy="12" r="10"/><polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76"/></svg>
          <div class="explore-empty-title">Nothing to explore right now</div>
          <div class="explore-empty-sub">Try again later or check your YT Music account connection.</div>
          <button type="button" class="btn-accent" id="explore-retry-btn">Retry</button>
        </div>`;
        const retryBtn = document.getElementById('explore-retry-btn');
        if (retryBtn) retryBtn.addEventListener('click', () => { _loaded = false; loadExplore(true); });
      }

    } catch (e) {
      console.warn('[explore] Failed to load explore data', e);
      _loaded = false;
      body.innerHTML = `<div class="explore-empty">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="48" height="48"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
        <div class="explore-empty-title">Couldn't load Explore</div>
        <div class="explore-empty-sub">${escHtml(e && e.message ? e.message : 'Network or authentication error.')}</div>
        <button type="button" class="btn-accent" id="explore-retry-btn">Try Again</button>
      </div>`;
      const retryBtn = document.getElementById('explore-retry-btn');
      if (retryBtn) retryBtn.addEventListener('click', () => { _loaded = false; loadExplore(true); });
    } finally {
      _loading = false;
    }
  }

  // ── public API ─────────────────────────────────────────────────────────────
  window.openExplorePage = function (force) {
    const overlay = document.getElementById('explore-modal-overlay');
    if (overlay) overlay.classList.add('open');
    loadExplore(force);
  };

  window.closeExplorePage = function () {
    const overlay = document.getElementById('explore-modal-overlay');
    if (overlay) overlay.classList.remove('open');
  };

  // ── close button ───────────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', function () {
    const closeBtn = document.getElementById('explore-modal-close');
    if (closeBtn) {
      closeBtn.addEventListener('click', function () {
        window.navigateTo('#home');
      });
    }
  });
  // Belt-and-suspenders: also wire up immediately in case DOM is already ready
  const closeBtn = document.getElementById('explore-modal-close');
  if (closeBtn) {
    closeBtn.addEventListener('click', function () {
      window.navigateTo('#home');
    });
  }
})();
