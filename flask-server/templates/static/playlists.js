(function () {
  'use strict';
  const state = window.__appState = window.__appState || {};
  if (state._loggedIn === undefined) state._loggedIn = false;

  const escapeHtml = window.escHtml || (s => String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'));

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
      return value.url || value.src || imageUrl(value.thumbnails) || imageUrl(value.images) || '';
    }
    return '';
  }

  function preloadPlaylistHero(pl) {
    if (!pl || pl.__heroReady) return Promise.resolve();
    const cover = imageUrl(pl.thumbnails) || imageUrl(pl.thumbnail);
    const image = imageUrl(pl.image) || imageUrl(pl.images);
    const urls = (cover || image) ? [cover || image] : (pl.tracks || []).slice(0, 4).map(track => {
      return imageUrl(track.thumbnails) || imageUrl(track.thumbnail) || imageUrl(track.image);
    }).filter(Boolean);
    return Promise.all(urls.map(url => new Promise(resolve => {
      const img = new Image();
      let settled = false;
      const timer = setTimeout(done, 8000);
      function done() {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        img.onload = null;
        img.onerror = null;
        resolve();
      }
      img.onload = done;
      img.onerror = done;
      img.src = url;
    }))).then(() => { pl.__heroReady = true; });
  }

  function songActions(track) {
    const liked = window._playlistsData && window._playlistsData.liked_songs &&
      window._playlistsData.liked_songs.includes(track.video_id);
    const like = `<svg viewBox="0 0 24 24" fill="${liked ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>`;
    const more = '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>';
    return `<button class="result-like-btn${liked ? ' liked' : ''}" type="button" title="Like" data-vid="${escapeHtml(track.video_id)}">${like}</button>` +
      `<button class="result-more-btn" type="button" title="More options">${more}</button>`;
  }

  function wireSongActions(row, track) {
    row.querySelector('.result-like-btn').addEventListener('click', function (event) {
      event.stopPropagation();
      if (window.toggleLike) window.toggleLike(track, this);
    });
    row.querySelector('.result-more-btn').addEventListener('click', function (event) {
      event.stopPropagation();
      if (window.openSongContextMenu) window.openSongContextMenu(event, track);
    });
  }

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
            const cover = imageUrl(pl.thumbnails) || imageUrl(pl.thumbnail) || imageUrl(pl.image);
            const isLiked = pl.playlistId === 'LM';
            btn.className = 'sidebar-playlist-item';
            btn.innerHTML = `<span class="sidebar-playlist-art${isLiked ? ' is-liked' : ''}">${cover
              ? `<img src="${escapeHtml(cover)}" alt="" loading="lazy">`
              : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"></path><circle cx="6" cy="18" r="3"></circle><circle cx="18" cy="16" r="3"></circle></svg>`
            }</span><span class="sidebar-playlist-copy"><strong>${escapeHtml(pl.title)}</strong><span>${escapeHtml(pl.description || 'Playlist')}</span></span>`;
            btn.onclick = () => {
              if (window._closeSidebar) window._closeSidebar();
              if (window.preloadNavigatePlaylist) window.preloadNavigatePlaylist(pl.playlistId);
              else window.navigateTo('#playlist/' + encodeURIComponent(pl.playlistId));
            };
            container.appendChild(btn);
          });
        }
      }
    } catch (e) {
      console.warn('Failed to load library playlists', e);
    }
  }



  async function openLibraryPlaylist(plId) {
    const overlay = document.getElementById('playlist-detail-modal-overlay');
    // Use the correct element IDs that exist in remote.html
    const titleEl = document.getElementById('playlist-detail-title');
    const body = document.getElementById('playlist-detail-body');

    const route = '#playlist/' + encodeURIComponent(plId);
    const preloaded = window.consumePreload ? window.consumePreload(route) : null;
    const ownsProgress = !preloaded;
    if (ownsProgress && window._barStart) window._barStart();
    try {
      const pl = preloaded || await window.api('/api/library/playlists/' + encodeURIComponent(plId));
      await preloadPlaylistHero(pl);
      if (overlay) overlay.classList.add('open');
      if (titleEl) titleEl.textContent = pl.title || 'Playlist';

      if (body) {
        body.innerHTML = '';
        const tracks = pl.tracks || [];
        const title = pl.title || 'Playlist';
        // Prefer the playlist's own/default thumbnail. Some API responses use
        // `thumbnail` while others use `thumbnails`; `image` is the fallback.
        const playlistCover = imageUrl(pl.thumbnails) || imageUrl(pl.thumbnail);
        const playlistImage = imageUrl(pl.image) || imageUrl(pl.images);
        const trackCoverUrls = tracks.slice(0, 4).map(track => {
          return imageUrl(track.thumbnails) || imageUrl(track.thumbnail) || imageUrl(track.image);
        }).filter(Boolean);
        const fallbackCoverUrls = playlistImage ? [playlistImage] : trackCoverUrls;
        const renderCollage = (urls, primary) => urls.length
          ? `<div class="playlist-collage${urls.length === 1 ? ' playlist-collage-single' : ''}">${urls.map(url => `<img${primary ? ' data-playlist-primary-cover' : ''} src="${escapeHtml(url)}" alt="" loading="lazy">`).join('')}</div>`
          : `<div class="playlist-collage playlist-collage-single"><div class="collage-placeholder"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg></div></div>`;
        const fallbackCollage = renderCollage(fallbackCoverUrls, false);
        const collage = playlistCover ? renderCollage([playlistCover], true) : fallbackCollage;

        const hero = document.createElement('section');
        hero.className = 'playlist-detail-hero';
        hero.innerHTML = `${collage}
          <div class="playlist-detail-hero-info">
            <h2 class="playlist-detail-page-title playlist-detail-hero-name">${escapeHtml(title)}</h2>
            ${pl.description ? `<div class="playlist-detail-hero-desc">${escapeHtml(pl.description)}</div>` : ''}
            <div class="playlist-detail-hero-meta">${tracks.length} ${tracks.length === 1 ? 'song' : 'songs'}</div>
            ${tracks.length ? `<div class="playlist-detail-hero-actions"><span class="playlist-hero-actions-left"></span><button class="playlist-hero-play" type="button" aria-label="Play ${escapeHtml(title)}"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg></button><span class="playlist-hero-actions-right"></span></div>` : ''}
          </div>`;
        body.appendChild(hero);

        // A present-but-expired YouTube thumbnail should fall back exactly as
        // a missing thumbnail does, instead of leaving a broken image tile.
        const primaryCover = hero.querySelector('[data-playlist-primary-cover]');
        if (primaryCover) {
          primaryCover.addEventListener('error', () => {
            const cover = primaryCover.closest('.playlist-collage');
            if (cover) cover.outerHTML = fallbackCollage;
          }, { once: true });
        }

        const list = document.createElement('div');
        list.className = 'history-list';
        if (tracks.length === 0) {
          list.innerHTML = '<div style="padding:24px; color:var(--muted); text-align:center;">No tracks in this playlist</div>';
        } else {
          tracks.forEach(track => {
            const wrapper = document.createElement('div');
            wrapper.className = 'result-swipe-wrapper';
            const row = document.createElement('div');
            row.className = 'history-item';
            const thumbnail = imageUrl(track.thumbnails) || imageUrl(track.thumbnail) || imageUrl(track.image) || '/static/default-art.png';
            const artist = (Array.isArray(track.artists)
              ? track.artists.map(a => typeof a === 'string' ? a : a && a.name).filter(Boolean).join(', ')
              : '') || track.artist || '';
            const videoId = track.videoId || track.video_id || '';
            wrapper.dataset.videoId = videoId;
            wrapper._songContextTrack = {
              video_id: videoId,
              title: track.title || '',
              artist,
              thumbnail
            };
            const contextTrack = wrapper._songContextTrack;
            row.innerHTML = `
              <div class="playlist-track-art"><img src="${escapeHtml(thumbnail)}" class="queue-thumb" loading="lazy" alt="" onload="this.classList.add('loaded')" onerror="this.style.opacity='1'"></div>
              <div class="queue-info">
                <div class="queue-title">${escapeHtml(track.title || '')}</div>
                <div class="queue-artist">${escapeHtml(artist)}</div>
              </div>${songActions(contextTrack)}`;
            row.onclick = () => {
              if (window.playResult) {
                window.playResult({
                  video_id: videoId,
                  title: track.title,
                  artist,
                  thumbnail
                }, false, false, true);
              }
            };
            wireSongActions(row, contextTrack);
            wrapper.appendChild(row);
            list.appendChild(wrapper);
          });
          const heroPlay = hero.querySelector('.playlist-hero-play');
          if (heroPlay) heroPlay.addEventListener('click', () => list.querySelector('.history-item')?.click());
        }
        body.appendChild(list);
      }
    } catch (e) {
      console.warn('Failed to load playlist', e);
      if (titleEl) titleEl.textContent = 'Error loading playlist';
      if (body) body.innerHTML = '<div style="padding:24px; color:var(--muted); text-align:center;">Failed to load playlist</div>';
      if (window._barAbort) window._barAbort();
    }
    if (ownsProgress && window._barComplete) window._barComplete();
  }
  window.openPlaylistDetailModal = openLibraryPlaylist;
  /* ---- New Playlist button (sidebar) ---- */
  (function () {
    const newBtn = document.getElementById('sidebar-new-playlist-btn');
    const overlay = document.getElementById('new-playlist-overlay');
    const closeBtn = document.getElementById('new-playlist-overlay-close');
    const nameInput = document.getElementById('new-playlist-overlay-name');
    const descInput = document.getElementById('new-playlist-overlay-desc');
    const createBtn = document.getElementById('new-playlist-overlay-create');

    if (!newBtn || !overlay) return;

    function openModal() {
      if (nameInput) nameInput.value = '';
      if (descInput) descInput.value = '';
      overlay.classList.add('open');
      if (nameInput) setTimeout(() => nameInput.focus(), 80);
    }

    function closeModal() {
      overlay.classList.remove('open');
    }

    newBtn.addEventListener('click', () => {
      if (window._closeSidebar) window._closeSidebar();
      openModal();
    });

    if (closeBtn) closeBtn.addEventListener('click', closeModal);

    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) closeModal();
    });

    if (nameInput) {
      nameInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') createBtn && createBtn.click();
      });
    }

    if (createBtn) {
      createBtn.addEventListener('click', async () => {
        const name = (nameInput ? nameInput.value : '').trim();
        if (!name) {
          if (nameInput) nameInput.focus();
          return;
        }
        const desc = (descInput ? descInput.value : '').trim();
        createBtn.disabled = true;
        createBtn.textContent = 'Creating…';
        try {
          await window.api('/api/library/playlists/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, description: desc })
          });
          closeModal();
          if (window.showToast) window.showToast('Playlist "' + name + '" created');
          // Refresh the sidebar playlist list
          await loadLibrary();
        } catch (e) {
          console.error('Failed to create playlist', e);
          if (window.showToast) window.showToast('Failed to create playlist');
        } finally {
          createBtn.disabled = false;
          createBtn.textContent = 'Create Playlist';
        }
      });
    }
  })();

  // Load immediately if logged in, else device.js will call it when auth is verified.
  if (state._loggedIn && window.IS_AUTHENTICATED) {
    loadLibrary();
  }

  // Attach to window so device.js can trigger it on login
  window.loadLibrary = loadLibrary;
})();
