(function () {
  'use strict';
  const state = window.__appState = window.__appState || {};

  // ── helpers ────────────────────────────────────────────────────────────────
  function escHtml(s) {
    if (window.escHtml) return window.escHtml(s);
    return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  const FALLBACK_IMG = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23333'%3E%3Crect width='24' height='24' rx='4' fill='%231a1a1a'/%3E%3Cpath d='M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z' fill='%23444'/%3E%3C/svg%3E";

  function imageUrl(value) {
    if (!value) return '';
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) {
      for (let i = value.length - 1; i >= 0; i -= 1) {
        const url = imageUrl(value[i]);
        if (url) return url;
      }
      return '';
    }
    if (typeof value === 'object') {
      return value.url || value.src || imageUrl(value.thumbnails) ||
        imageUrl(value.thumbnail) || imageUrl(value.images) || imageUrl(value.image) || '';
    }
    return '';
  }

  function imgWithFallback(url, alt) {
    const safe = escHtml(url || '');
    const safeAlt = escHtml(alt || '');
    if (!url) return `<div class="explore-thumb-placeholder"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z"/></svg></div>`;
    return `<img src="${safe}" alt="${safeAlt}" loading="lazy" onload="this.style.opacity='1'" onerror="this.onerror=null;this.style.opacity='1';this.src='${FALLBACK_IMG}'">`;
  }

  // ── render helpers ─────────────────────────────────────────────────────────
  function openExploreMenu(event, item, onClick) {
    event.preventDefault();
    event.stopPropagation();
    const id = item.videoId || item.video_id || item.browseId || item.playlistId || item.albumId;
    const menu = document.createElement('div');
    menu.className = 'explore-context-menu';
    menu.innerHTML = `<button data-action="open">Open</button><button data-action="play">Play now</button><button data-action="queue">Add to queue</button>`;
    document.body.appendChild(menu);
    const x = Math.min(event.clientX, window.innerWidth - 190), y = Math.min(event.clientY, window.innerHeight - 150);
    menu.style.left = Math.max(8, x) + 'px'; menu.style.top = Math.max(8, y) + 'px';
    const close = () => { menu.remove(); document.removeEventListener('click', close); };
    menu.addEventListener('click', function (e) {
      const action = e.target.dataset.action;
      if (action === 'open' && onClick) onClick();
      if (action === 'play' && item.video_id && window.playResult) window.playResult(item, false, true);
      if (action === 'queue' && item.video_id && window.addToQueue) window.addToQueue(item);
      close();
    });
    setTimeout(() => document.addEventListener('click', close), 0);
  }

  function renderCard(item, onClick) {
    const thumb = imageUrl(item.thumbnails) || imageUrl(item.thumbnail) ||
      imageUrl(item.images) || imageUrl(item.image);
    const title = item.title || item.name || 'Unknown';
    const sub = item.description || item.subtitle || item.artists?.[0]?.name || item.year || '';

    const card = document.createElement('div');
    card.className = 'explore-card';
    card.innerHTML = `
      <div class="explore-card-art">${imgWithFallback(thumb, title)}<span class="explore-card-play" aria-hidden="true">▶</span><button class="explore-card-more" type="button" aria-label="More options">•••</button></div>
      <div class="explore-card-info">
        <div class="explore-card-title">${escHtml(title)}</div>
        ${sub ? `<div class="explore-card-sub">${escHtml(sub)}</div>` : ''}
      </div>
    `;
    if (onClick) card.addEventListener('click', onClick);
    card.addEventListener('contextmenu', e => openExploreMenu(e, item, onClick));
    card.querySelector('.explore-card-more').addEventListener('click', e => openExploreMenu(e, item, onClick));
    if (onClick) card.style.cursor = 'pointer';
    return card;
  }

  function renderSection(title, items, getOnClick) {
    if (!items || !items.length) return null;

    const section = document.createElement('div');
    section.className = 'explore-section';
    section.dataset.exploreKey = title.toLowerCase().replace(/[^a-z]+/g, '_').replace(/^_|_$/g, '');

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
    let html = '<div class="explore-loading-status" role="status" aria-live="polite"><span class="explore-loading-dot"></span>Personalizing your Explore feed</div>';
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
    // Render synchronously, before the authenticated account request starts.
    // window.api owns the global top-progress bar for this request.
    body.innerHTML = renderSkeleton();

    try {
      const explore = await window.api('/api/explore/');
      body.innerHTML = '';
      _loaded = true;

      if (!explore || typeof explore !== 'object') {
        throw new Error('Empty response');
      }

      let hasContent = false;
      // The server wraps authenticated, account-specific Home shelves with
      // discovery data. Keep the legacy shape as a fallback for old servers.
      const discovery = explore.discovery || explore;
      const openRecommendedItem = (item) => () => {
        const videoId = item.videoId || item.video_id || '';
        if (videoId && window.playResult) {
          window.playResult(Object.assign({}, item, { video_id: videoId }), false, true);
          return;
        }
        const id = item.playlistId || item.browseId || item.albumId || '';
        if (!id) return;
        if (item.type === 'Album' || String(id).startsWith('MPREb')) window.navigateTo('#album/' + encodeURIComponent(id));
        else if (window.preloadNavigatePlaylist) window.preloadNavigatePlaylist(id);
        else window.navigateTo('#playlist/' + encodeURIComponent(id));
      };
      const quick = document.createElement('div');
      quick.className = 'explore-quick-nav';
      [['For you','for_you'],['New releases','new_releases'],['Charts','charts'],['Moods & genres','moods'],['Podcasts','podcasts']].forEach(([label, key]) => {
        const button = document.createElement('button');
        button.className = 'explore-quick-card';
        button.innerHTML = `<span class="explore-quick-icon">${key === 'charts' ? '↗' : key === 'moods' ? '◉' : key === 'podcasts' ? '◌' : '✦'}</span><span>${label}</span>`;
        button.addEventListener('click', () => {
          const target = body.querySelector(`[data-explore-key="${key}"]`) || body.querySelector(`[data-explore-key*="${key}"]`);
          if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
          else if (window.toast) window.toast(`${label} is not available for this account yet`, 'info');
        });
        quick.appendChild(button);
      });
      body.appendChild(quick);

      // These shelves are generated by the active YouTube Music account and
      // change with its listening history, likes and subscriptions.
      (explore.personal_shelves || []).forEach((shelf, index) => {
        const sec = renderSection(shelf.title || 'Recommended for you', shelf.items || [], openRecommendedItem);
        if (sec) {
          sec.dataset.exploreKey = index === 0 ? 'for_you' : 'personal_' + index;
          body.appendChild(sec);
          hasContent = true;
        }
      });

      // ── New Releases ───────────────────────────────────────────────────────
      const newReleases = discovery.new_releases || discovery.newReleases || [];
      if (newReleases.length) {
        const sec = renderSection('New Releases', newReleases, (item) => () => {
          if (window._closeSidebar) window._closeSidebar();
          const id = item.browseId || item.playlistId || item.albumId || '';
          if (!id) return;
          if (item.type === 'Album' || item.browseId?.startsWith('MPREb')) {
            window.navigateTo('#album/' + encodeURIComponent(id));
          } else {
            if (window.preloadNavigatePlaylist) window.preloadNavigatePlaylist(id);
            else window.navigateTo('#playlist/' + encodeURIComponent(id));
          }
        });
        if (sec) { body.appendChild(sec); hasContent = true; }
      }

      // ── Playlists / Featured ───────────────────────────────────────────────
      const playlists = discovery.playlists || discovery.featured || [];
      if (playlists.length) {
        const label = discovery.featured ? 'Featured Playlists' : 'Playlists';
        const sec = renderSection(label, playlists, (item) => () => {
          if (window._closeSidebar) window._closeSidebar();
          const id = item.playlistId || item.browseId || '';
          if (!id) return;
          if (window.preloadNavigatePlaylist) window.preloadNavigatePlaylist(id);
          else window.navigateTo('#playlist/' + encodeURIComponent(id));
        });
        if (sec) { body.appendChild(sec); hasContent = true; }
      }

      // ── Moods & Genres ─────────────────────────────────────────────────────
      const moods = discovery.moods || discovery.genres || discovery.moods_and_genres || [];
      if (moods.length) {
        const sec = renderSection('Moods & Genres', moods, (item) => () => {
          if (window._closeSidebar) window._closeSidebar();
          const id = item.params || item.playlistId || item.browseId || '';
          if (!id) return;
          // Mood params use browse not playlist
          if (window.preloadNavigatePlaylist) window.preloadNavigatePlaylist(id);
          else window.navigateTo('#playlist/' + encodeURIComponent(id));
        });
        if (sec) { body.appendChild(sec); hasContent = true; }
      }

      // ── Charts ─────────────────────────────────────────────────────────────
      const charts = discovery.charts || discovery.trending || [];
      if (charts.length) {
        const sec = renderSection('Charts', charts, (item) => () => {
          if (window._closeSidebar) window._closeSidebar();
          const id = item.playlistId || item.browseId || '';
          if (!id) return;
          if (window.preloadNavigatePlaylist) window.preloadNavigatePlaylist(id);
          else window.navigateTo('#playlist/' + encodeURIComponent(id));
        });
        if (sec) { body.appendChild(sec); hasContent = true; }
      }

      // ── Fallback: catch-all unknown keys ───────────────────────────────────
      {
        // Try to render whatever arrays the API returned
        const knownKeys = new Set(['new_releases', 'newReleases', 'playlists', 'featured', 'moods', 'genres', 'moods_and_genres', 'charts', 'trending']);
        const keys = Object.keys(discovery).filter(k => !knownKeys.has(k) && Array.isArray(discovery[k]) && discovery[k].length > 0);
        for (const key of keys) {
          const label = key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
          const sec = renderSection(label, discovery[key], (item) => () => {
            if (window._closeSidebar) window._closeSidebar();
            const id = item.playlistId || item.browseId || item.albumId || '';
            if (id) {
              if (window.preloadNavigatePlaylist) window.preloadNavigatePlaylist(id);
              else window.navigateTo('#playlist/' + encodeURIComponent(id));
            }
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
