// Closes every open per-track menu, returning each one from <body> (where it
// was portaled while open, see below) back to its row.
function _closeAllPlaylistMoreMenus() {
  document.querySelectorAll('.playlist-more-menu.open').forEach(m => {
    m.classList.remove('open');
    // Defer the reparent until the fade-out transition finishes so a
    // body-portaled menu doesn't visibly jump mid-fade.
    setTimeout(() => {
      if (m.classList.contains('open')) return;
      if (m._home && m.parentElement !== m._home) m._home.appendChild(m);
    }, 150);
  });
};

window.togglePlaylistMoreMenu = function(btn) {
  // Once opened, the menu is portaled to <body> (see below) and is no longer
  // btn.nextElementSibling -- if this same button is clicked again before the
  // deferred reparent-back finishes, nextElementSibling would return whatever
  // now sits next to the button (or null), not the menu. Remember it on the
  // button itself instead of relying on DOM adjacency.
  const menu = btn._menu || btn.nextElementSibling;
  btn._menu = menu;
  const wasOpen = menu.classList.contains('open');
  _closeAllPlaylistMoreMenus();
  if (wasOpen) return;
  const rect = btn.getBoundingClientRect();
  menu.style.position = 'fixed';
  menu.style.top = (rect.bottom + 4) + 'px';
  menu.style.right = (window.innerWidth - rect.right) + 'px';
  menu.style.bottom = 'auto';
  menu.style.left = 'auto';
  // Portal to <body> while open: inside the row it sits under an
  // overflow-hidden wrapper within a scrollable list, and fixed-position
  // elements there can get clipped or fail to receive clicks (see the same
  // fix applied to the queue/search-result more-menus).
  menu._home = btn.parentElement;
  document.body.appendChild(menu);
  menu.classList.add('open');
};

document.addEventListener('click', _closeAllPlaylistMoreMenus);
// The menu is fixed-positioned at its row's on-screen coordinates when it
// opens; scrolling the track list afterward moves the row but not the menu
// (it doesn't scroll with the list once portaled to <body>), so it visually
// drifts onto whatever row ends up underneath it. Closing on scroll avoids
// that instead of trying to keep a fixed-position element live-repositioned.
document.getElementById('playlist-detail-body').addEventListener('scroll', _closeAllPlaylistMoreMenus, { passive: true });

// ponytail: IndexedDB image cache — stores thumbnail blobs keyed by URL
const _imgCache = (() => {
  let db;
  const _open = () => new Promise(resolve => {
    if (db && db.name === 'thumbnails') return resolve(db);
    const r = indexedDB.open('thumbnails', 1);
    r.onupgradeneeded = () => r.result.createObjectStore('thumbs');
    r.onsuccess = () => { db = r.result; resolve(db); };
    r.onerror = () => resolve(null);
  });
  return {
    async get(url) {
      const d = await _open();
      if (!d) return null;
      return new Promise(r => {
        const tx = d.transaction('thumbs');
        tx.objectStore('thumbs').get(url).onsuccess = e => r(e.target.result || null);
      });
    },
    async set(url, blob) {
      const d = await _open();
      if (!d) return;
      try { d.transaction('thumbs', 'readwrite').objectStore('thumbs').put(blob, url); } catch (_) {}
    }
  };
})();

// ponytail: IndexedDB playlist cache — stores full playlist objects keyed by id
const _plCache = (() => {
  let db;
  const _open = () => new Promise(resolve => {
    if (db && db.name === 'playlists-db') return resolve(db);
    const r = indexedDB.open('playlists-db', 1);
    r.onupgradeneeded = () => r.result.createObjectStore('playlists');
    r.onsuccess = () => { db = r.result; resolve(db); };
    r.onerror = () => resolve(null);
  });
  const TTL = 5 * 60 * 1000;
  return {
    async get(key) {
      const d = await _open();
      if (!d) return null;
      return new Promise(r => {
        const tx = d.transaction('playlists');
        tx.objectStore('playlists').get(key).onsuccess = e => {
          const entry = e.target.result;
          if (!entry) return r(null);
          if (Date.now() - entry.cached_at > TTL) {
            d.transaction('playlists', 'readwrite').objectStore('playlists').delete(key);
            return r(null);
          }
          r(entry.data || null);
        };
      });
    },
    async set(key, data) {
      const d = await _open();
      if (!d) return;
      try {
        d.transaction('playlists', 'readwrite').objectStore('playlists').put(
          { data, cached_at: Date.now() }, key
        );
      } catch (_) {}
    }
  };
})();

let _playlistsData = { playlists: {}, liked_songs: [] };

async function loadPlaylists() {
  if (window.JAM_GUEST) return;
  try {
    const data = await api('/api/playlists/?_=' + Date.now());
    if (data && data.playlists) {
      _playlistsData = data;
      // Cache each playlist in IDB for instant re-opening after page reload
      for (const [id, pl] of Object.entries(data.playlists)) {
        _plCache.set(id, pl);
      }
      renderSidebarPlaylists();
    }
  } catch (e) {
    console.error("Failed to load playlists", e);
  }
}

function getPlaylistsList() {
  let lists = Object.values(_playlistsData.playlists);
  if (!lists.find(p => p.id === 'liked')) {
    lists.push({ id: 'liked', name: 'Liked Songs', updated_at: 0, tracks: [] });
  }
  return lists.sort((a, b) => {
    if (a.id === 'liked') return -1;
    if (b.id === 'liked') return 1;
    return b.updated_at - a.updated_at;
  });
}

/* ── Build a 2×2 artwork collage HTML from up to 4 tracks ── */
function _buildCollageHtml(tracks, isLiked) {
  if (isLiked) {
    return `
      <div class="playlist-collage">
        <div class="collage-placeholder"><svg viewBox="0 0 24 24" fill="currentColor" width="18" height="18" style="color:var(--primary);"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg></div>
        <div class="collage-placeholder"></div>
        <div class="collage-placeholder"></div>
        <div class="collage-placeholder"></div>
      </div>`;
  }
  const valid = (tracks || []).filter(t => t && t.thumbnail);
  let cells = '';
  for (let i = 0; i < 4; i++) {
    if (i < valid.length) {
      cells += `<img src="${escHtml(valid[i].thumbnail)}" alt="" loading="lazy">`;
    } else {
      cells += `<div class="collage-placeholder"></div>`;
    }
  }
  return `<div class="playlist-collage">${cells}</div>`;
}

function _formatUpdatedAt(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  const now = new Date();
  const diffDays = Math.max(0, Math.floor((now - d) / (1000 * 60 * 60 * 24)));
  if (diffDays === 0) return 'Today';
  if (diffDays === 1) return 'Yesterday';
  if (diffDays < 7) return diffDays + ' days ago';
  if (diffDays < 30) return Math.floor(diffDays / 7) + ' weeks ago';
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function renderPlaylists() {
  const body = document.getElementById('playlists-modal-body');
  if (!body) return;
  
  const lists = getPlaylistsList();
  if (lists.length === 0) {
    body.innerHTML = '<div class="history-modal-empty">No playlists yet. Save some songs!</div>';
    return;
  }
  
  let html = '<div class="history-list">';
  lists.forEach((pl) => {
    const trackCount = (pl.tracks || []).length;
    const collageHtml = _buildCollageHtml(pl.tracks, pl.id === 'liked');
    const desc = (pl.description || '').trim();
    const updatedLabel = pl.updated_at ? 'Updated ' + _formatUpdatedAt(pl.updated_at) : '';

    const playSvg = `<svg viewBox="0 0 24 24" fill="currentColor" width="14" height="14"><path d="M8 5v14l11-7z"/></svg>`;

    html += `
      <div class="playlist-card" onclick="openPlaylistDetailModal('${escHtml(pl.id)}')">
        ${collageHtml}
        <div class="playlist-card-info">
          <div class="playlist-card-name">${escHtml(pl.name)}</div>
          <div class="playlist-card-meta">${trackCount} ${trackCount === 1 ? 'song' : 'songs'}${updatedLabel ? ' · ' + updatedLabel : ''}</div>
          ${desc ? `<div class="playlist-card-desc">${escHtml(desc)}</div>` : ''}
        </div>
        ${trackCount > 0 ? `<button class="playlist-card-play" onclick="event.stopPropagation(); playPlaylist('${escHtml(pl.id)}', this)">${playSvg}</button>` : ''}
      </div>
    `;
  });
  html += '</div>';
  body.innerHTML = html;
}

async function openPlaylistsModal(fromRoute) {
  if (!fromRoute && window.matchMedia('(min-width: 900px)').matches) {
    location.hash = '#playlists';
    return;
  }
  const overlay = document.getElementById('playlists-modal-overlay');
  overlay.classList.add('open');
  const body = document.getElementById('playlists-modal-body');
  if (body && (!getPlaylistsList().length)) {
    body.innerHTML = '<div class="history-modal-empty">Loading...</div>';
  }
  await loadPlaylists();
  renderPlaylists();
}

function closePlaylistsModal() {
  document.getElementById('playlists-modal-overlay').classList.remove('open');
  if (location.hash === '#playlists') location.hash = '#home';
}

let _currentPlaylistDetailId = null;

async function openPlaylistDetailModal(pl_id, fromRoute) {
  if (!fromRoute && window.matchMedia('(min-width: 900px)').matches) {
    location.hash = '#playlist/' + encodeURIComponent(pl_id);
    return;
  }
  // Phase 12: Performance marker for profiling playlist detail render time
  if (window.performance && performance.mark) {
    performance.mark('playlist-detail-start');
  }
  // Try IDB cache first — survives page reload for instant paint
  let pl = _playlistsData.playlists[pl_id];
  if (!pl) {
    const cached = await _plCache.get(pl_id);
    if (cached) {
      pl = cached;
      _playlistsData.playlists[pl_id] = cached;
    }
  }
  if (!pl) return;
  _currentPlaylistDetailId = pl_id;

  // Show modal with title + loader immediately
  document.getElementById('playlist-detail-title').textContent = pl.name;
  document.getElementById('playlists-modal-overlay').classList.add('open');
  document.getElementById('playlist-detail-modal-overlay').classList.add('open');
  const body = document.getElementById('playlist-detail-body');
  body.innerHTML = '<div class="history-modal-empty" style="padding:40px; text-align:center;"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" class="spin" style="width:24px;height:24px;margin:0 auto 12px;"><path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/></svg><div>Loading tracks...</div></div>';

  // Yield so the loader paints before heavy DOM work
  await new Promise(r => setTimeout(r, 0));

  const syncBtn = document.getElementById('playlist-sync-btn');
  if (pl.source_url) {
    syncBtn.hidden = false;
    syncBtn.onclick = () => syncPlaylist(pl_id);
  } else {
    syncBtn.hidden = true;
  }

  const moreBtn = document.getElementById('playlist-detail-more-btn');
  const renameOpt = document.getElementById('playlist-detail-rename-opt');
  const deleteOpt = document.getElementById('playlist-detail-delete-opt');
  moreBtn.hidden = pl_id === 'liked';
  renameOpt.hidden = pl_id === 'liked';
  deleteOpt.hidden = pl_id === 'liked';

  const playAllBtn = document.getElementById('playlist-detail-play-all-btn');
  playAllBtn.hidden = !pl.tracks || pl.tracks.length === 0;
  const shuffleBtn = document.getElementById('playlist-detail-shuffle-btn');
  shuffleBtn.hidden = !pl.tracks || pl.tracks.length === 0;
  // Start hidden for fade-in after chunked render completes
  if (!playAllBtn.hidden) { playAllBtn.style.opacity = '0'; playAllBtn.style.transition = 'opacity .3s'; }
  if (!shuffleBtn.hidden) { shuffleBtn.style.opacity = '0'; shuffleBtn.style.transition = 'opacity .3s'; }

  document.querySelectorAll('body > .playlist-more-menu').forEach(m => m.remove());
  if (!pl.tracks || pl.tracks.length === 0) {
    body.innerHTML = '<div class="history-modal-empty">Playlist is empty.</div>';
    return;
  }
  const trashIcon = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>`;
  const moreSvg = `<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>`;
  const queueAddSvg = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>`;
  const playNextSvg = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>`;
  const heartFilledSvg = `<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>`;

  const list = document.createElement('div');
  list.className = 'history-list';
  const imgElements = [];

  // Build one track row (extracted from the old forEach body)
  const _buildTrackRow = (track, pl_id) => {
    const item = { video_id: track.video_id, title: track.title, artist: track.artist,
      thumbnail: track.thumbnail || '', duration_ms: Number(track.duration_ms) || 0 };

    const wrapper = document.createElement('div');
    wrapper.className = 'result-swipe-wrapper';
    wrapper.dataset.trackUuid = track.uuid || track.video_id || '';
    const trackKey = track.uuid || track.video_id;
    if (_recentlyAddedTrackKeys.has(trackKey) || _recentlyAddedTrackKeys.has(track.video_id)) {
      wrapper.classList.add('track-added-anim');
      _recentlyAddedTrackKeys.delete(trackKey);
      _recentlyAddedTrackKeys.delete(track.video_id);
      wrapper.addEventListener('animationend', () => wrapper.classList.remove('track-added-anim'), { once: true });
    }
    wrapper.innerHTML = `
      <div class="result-swipe-underlay underlay-play-next">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
        Play next
      </div>
      <div class="result-swipe-underlay underlay-add-queue">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
        Add to queue
      </div>
    `;

    const row = document.createElement('div');
    row.className = 'history-item';
    row.style.position = 'relative';

    if (track.thumbnail) {
      const img = document.createElement('img');
      img.className = 'queue-thumb';
      img.alt = '';
      img.dataset.src = track.thumbnail;
      img.style.background = 'rgba(255,255,255,0.05)';
      imgElements.push(img);
      row.appendChild(img);
    } else {
      const ph = document.createElement('div');
      ph.className = 'queue-thumb';
      ph.style.cssText = 'display:flex; align-items:center; justify-content:center; background: rgba(255,255,255,0.05);';
      ph.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="width: 20px; height: 20px; color: var(--text-muted, #888);"><path d="M9 18V5l12-2v13"></path><circle cx="6" cy="18" r="3"></circle><circle cx="18" cy="16" r="3"></circle></svg>`;
      row.appendChild(ph);
    }

    // Liked Songs keeps its fixed oldest-first order — no drag handle there.
    if (pl_id !== 'liked') {
      const dragSvg = `<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="9" cy="5" r="1.5"/><circle cx="15" cy="5" r="1.5"/><circle cx="9" cy="10" r="1.5"/><circle cx="15" cy="10" r="1.5"/><circle cx="9" cy="15" r="1.5"/><circle cx="15" cy="15" r="1.5"/><circle cx="9" cy="20" r="1.5"/><circle cx="15" cy="20" r="1.5"/></svg>`;
      const dragHandle = document.createElement('div');
      dragHandle.className = 'playlist-drag-handle';
      dragHandle.title = 'Drag to reorder';
      dragHandle.innerHTML = dragSvg;
      row.appendChild(dragHandle);
    }

    const info = document.createElement('div');
    info.className = 'history-info';
    info.style.cssText = 'flex:1; min-width:0; cursor:pointer;';
    const titleEl = document.createElement('div');
    titleEl.className = 'history-title';
    titleEl.style.cssText = 'white-space:nowrap; overflow:hidden; text-overflow:ellipsis;';
    titleEl.textContent = track.title;
    const artistEl = document.createElement('div');
    artistEl.className = 'history-artist';
    artistEl.style.cssText = 'white-space:nowrap; overflow:hidden; text-overflow:ellipsis;';
    artistEl.textContent = track.artist;
    info.appendChild(titleEl);
    info.appendChild(artistEl);
    row.appendChild(info);

      if (pl_id === 'liked') {
        const heartBtn = document.createElement('button');
        heartBtn.className = 'track-like-btn liked';
        heartBtn.type = 'button';
        heartBtn.title = 'Dislike';
        heartBtn.innerHTML = heartFilledSvg;
        heartBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          toggleLike(item, heartBtn);
        });
        row.appendChild(heartBtn);
      }

      const moreContainer = document.createElement('div');
      moreContainer.className = 'playlist-more-container';
      moreContainer.style.cssText = 'position:relative; display:flex; align-items:center;';

      const moreBtn = document.createElement('button');
      moreBtn.className = 'track-more-btn';
      moreBtn.type = 'button';
      moreBtn.title = 'More options';
      moreBtn.innerHTML = moreSvg;
      moreBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        window.togglePlaylistMoreMenu(moreBtn);
      });

      const menu = document.createElement('div');
      menu.className = 'playlist-more-menu';
      menu.style.cssText = 'position:absolute; right:8px; top:100%; background: var(--surface); border: 1px solid var(--border); z-index: 9999; min-width: 170px; box-shadow: 0 8px 24px rgba(0,0,0,.5); overflow: hidden;';

      function addMenuOption(iconHtml, label, danger, onClick) {
        const opt = document.createElement('div');
        opt.className = 'queue-menu-option' + (danger ? ' danger' : '');
        opt.innerHTML = `${iconHtml}${label}`;
        opt.addEventListener('click', (e) => {
          e.stopPropagation();
          _closeAllPlaylistMoreMenus();
          onClick();
        });
        menu.appendChild(opt);
      }

      addMenuOption(playNextSvg, 'Play next', false, () => addToQueue(item, 'next'));
      addMenuOption(queueAddSvg, 'Add to queue', false, () => addToQueue(item, 'last'));
      // Jam guests get play/queue options only — removing tracks writes the
      // owner's playlist (and the server would 401 it anyway).
      if (pl_id !== 'liked' && !window.JAM_GUEST) {
        addMenuOption(trashIcon, 'Remove from playlist', true,
          () => removeFromPlaylist(pl_id, track.uuid || track.video_id));
      }

      moreContainer.appendChild(moreBtn);
      moreContainer.appendChild(menu);
      row.appendChild(moreContainer);

      attachQueueItemTap(row, () => playResult(item));

      wrapper.appendChild(row);
      _attachSwipeGesture(wrapper, row, item);
      if (pl_id !== 'liked') _attachPlaylistDragReorder(wrapper, list);
      return wrapper;
  };

  // Build playlist hero: collage + description + metadata
  const heroDiv = document.createElement('div');
  heroDiv.className = 'playlist-detail-hero';
  const updatedLabel = pl.updated_at ? 'Updated ' + _formatUpdatedAt(pl.updated_at) : '';
  const trackLabel = (pl.tracks || []).length + ' ' + (((pl.tracks || []).length === 1) ? 'song' : 'songs');
  const desc = (pl.description || '').trim();
  heroDiv.innerHTML = `
    ${_buildCollageHtml(pl.tracks, pl.id === 'liked')}
    <div class="playlist-detail-hero-info">
      <h1 class="playlist-detail-page-title">${escHtml(pl.name)}</h1>
      <div class="playlist-detail-hero-meta">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="width:14px;height:14px;"><path d="M9 18V5l12-2v13"></path><circle cx="6" cy="18" r="3"></circle><circle cx="18" cy="16" r="3"></circle></svg>
        ${trackLabel}
        ${updatedLabel ? `<span>·</span><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="width:14px;height:14px;"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>${updatedLabel}` : ''}
      </div>
      ${desc ? `<div class="playlist-detail-hero-desc">${escHtml(desc)}</div>` : ''}
      <div class="playlist-detail-hero-actions">
        <button class="playlist-hero-play" type="button" title="Play all"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg></button>
        <button class="playlist-hero-shuffle" type="button" title="Shuffle">${shuffleBtn.innerHTML}</button>
      </div>
    </div>
  `;

  heroDiv.querySelector('.playlist-hero-play').addEventListener('click', () => playAllBtn.click());
  heroDiv.querySelector('.playlist-hero-shuffle').addEventListener('click', () => shuffleBtn.click());

  body.innerHTML = '';
  body.appendChild(heroDiv);
  body.appendChild(list);

  // Chunked lazy rendering: render ~100 tracks at a time, then yield. An
  // IntersectionObserver sentinel at the bottom of the rendered list triggers
  // the next chunk when scrolled near. Same pattern as _appendLazyQueueRows in
  // remote.js, but simpler (no drag-reorder, no SSE updates).
  const CHUNK_SIZE = 100;
  const total = pl.tracks.length;
  let rendered = 0;
  let sentinel = null;
  let observer = null;

  const _appendTrackChunk = () => {
    const start = rendered;
    const end = Math.min(start + CHUNK_SIZE, total);
    const imgStart = imgElements.length;
    const frag = document.createDocumentFragment();
    for (let i = start; i < end; i++) {
      frag.appendChild(_buildTrackRow(pl.tracks[i], pl_id));
    }

    if (sentinel && sentinel.parentNode) {
      list.insertBefore(frag, sentinel);
    } else {
      list.appendChild(frag);
    }

    rendered = end;

    // First chunk on screen means the list is usable — show action buttons now
    if (start === 0) {
      if (!playAllBtn.hidden) playAllBtn.style.opacity = '1';
      if (!shuffleBtn.hidden) shuffleBtn.style.opacity = '1';
    }

    // Load thumbnails for the rows just added, not after the whole playlist
    _loadPlaylistImages(imgElements.slice(imgStart));

    if (rendered >= total) {
      // All tracks rendered — clean up sentinel and observer
      if (observer) observer.disconnect();
      if (sentinel && sentinel.parentNode) sentinel.parentNode.removeChild(sentinel);
    } else {
      // Position sentinel after new rows and re-observe
      if (!sentinel) {
        sentinel = document.createElement('div');
        sentinel.style.height = '1px';
      }
      list.appendChild(sentinel);
      if (!observer) {
        observer = new IntersectionObserver((entries) => {
          if (entries.some(e => e.isIntersecting)) _appendTrackChunk();
        }, { root: body, rootMargin: '400px' });
      }
      observer.disconnect();
      observer.observe(sentinel);
    }
  };

  // Render first chunk immediately (no yield needed — first 100 rows is fast)
  _appendTrackChunk();

  // Phase 12: Performance marker — measure render-to-screen time
  if (window.performance && performance.mark && performance.measure) {
    performance.mark('playlist-detail-end');
    try { performance.measure('playlist-detail-render', 'playlist-detail-start', 'playlist-detail-end'); } catch (_) {}
  }
}

// Loads playlist track thumbnails progressively: IDB cache first, then
// network fetch+cache. Processes chunks between yields so names appear first.
async function _loadPlaylistImages(imgs) {
  const CHUNK = 8;
  for (let i = 0; i < imgs.length; i += CHUNK) {
    const chunk = imgs.slice(i, i + CHUNK);
    await Promise.all(chunk.map(async img => {
      const url = img.dataset.src;
      if (!url) return;
      try {
        const cached = await _imgCache.get(url);
        if (cached) {
          img.src = URL.createObjectURL(cached);
          img.classList.add('loaded');
          return;
        }
      } catch (_) {}
      // Network fallback with native lazy loading
      img.loading = 'lazy';
      img.src = url;
      img.addEventListener('load', async () => {
        img.classList.add('loaded');
        // Cache the fetched blob for next time
        try {
          const res = await fetch(url);
          const blob = await res.blob();
          _imgCache.set(url, blob);
        } catch (_) {}
      }, { once: true });
    }));
    // Yield every chunk so the UI thread stays responsive
    await new Promise(r => setTimeout(r, 0));
  }
}

function _closePlaylistDetailMoreMenu() {
  const menu = document.getElementById('playlist-detail-more-menu');
  menu.classList.remove('open');
  // Defer the reparent until the fade-out transition finishes, same as
  // _closeAllPlaylistMoreMenus -- mirrors the per-track menu handling below.
  setTimeout(() => {
    if (menu.classList.contains('open')) return;
    if (menu._home && menu.parentElement !== menu._home) menu._home.appendChild(menu);
  }, 150);
}

function closePlaylistDetailModal() {
  document.getElementById('playlist-detail-modal-overlay').classList.remove('open');
  _closePlaylistDetailMoreMenu();
  if (location.hash.indexOf('#playlist/') === 0) location.hash = '#playlists';
}

function renderSidebarPlaylists() {
  const container = document.getElementById('sidebar-playlist-list');
  if (!container) return;
  const lists = getPlaylistsList();
  container.innerHTML = lists.map(pl => `
    <button class="sidebar-playlist-item" type="button" data-playlist-id="${escHtml(pl.id)}" title="${escHtml(pl.name)}">
      <strong>${escHtml(pl.name)}</strong>
      <span>${(pl.tracks || []).length} ${(pl.tracks || []).length === 1 ? 'song' : 'songs'}</span>
    </button>`).join('');
  container.querySelectorAll('.sidebar-playlist-item').forEach(btn => {
    btn.addEventListener('click', () => {
      if (window._closeSidebar) window._closeSidebar();
      openPlaylistDetailModal(btn.dataset.playlistId);
    });
  });
}

document.getElementById('playlist-detail-more-btn').addEventListener('click', (e) => {
  e.stopPropagation();
  const menu = document.getElementById('playlist-detail-more-menu');
  const wasOpen = menu.classList.contains('open');
  _closePlaylistDetailMoreMenu();
  if (!wasOpen) {
    const rect = e.currentTarget.getBoundingClientRect();
    menu.style.top = (rect.bottom + 4) + 'px';
    menu.style.right = (window.innerWidth - rect.right) + 'px';
    menu.style.left = 'auto';
    // Portal to <body> while open: it lives inside .history-modal, which is
    // animated with `transform` on open/close. position:fixed on a
    // descendant of a transformed ancestor resolves relative to that
    // ancestor instead of the viewport, so closing the modal while this menu
    // was still fading out dragged it sideways along with the modal's
    // transform instead of just fading in place.
    menu._home = menu.parentElement;
    document.body.appendChild(menu);
    menu.classList.add('open');
  }
});
document.addEventListener('click', () => {
  _closePlaylistDetailMoreMenu();
});

document.getElementById('playlist-detail-rename-opt').addEventListener('click', () => {
  _closePlaylistDetailMoreMenu();
  if (!_currentPlaylistDetailId) return;
  const pl = _playlistsData.playlists[_currentPlaylistDetailId];
  if (!pl) return;
  const input = document.getElementById('rename-playlist-input');
  input.value = pl.name;
  document.getElementById('rename-playlist-overlay').classList.add('open');
  setTimeout(() => input.focus(), 0);
});

document.getElementById('rename-playlist-close').addEventListener('click', () => {
  document.getElementById('rename-playlist-overlay').classList.remove('open');
});

document.getElementById('rename-playlist-btn').addEventListener('click', async () => {
  const input = document.getElementById('rename-playlist-input');
  const name = input.value.trim();
  if (!name || !_currentPlaylistDetailId) return;
  const btn = document.getElementById('rename-playlist-btn');
  btn.disabled = true;
  try {
    await apiPatch('/api/playlists/' + encodeURIComponent(_currentPlaylistDetailId), { name });
    _playlistsData.playlists[_currentPlaylistDetailId].name = name;
    document.getElementById('playlist-detail-title').textContent = name;
    document.getElementById('rename-playlist-overlay').classList.remove('open');
    toast('Playlist renamed', 'ok');
  } catch (e) {
    toast(e.message || 'Error renaming playlist', 'error');
  }
  btn.disabled = false;
});

// Generic on-screen confirm/alert dialog (replaces the native confirm()/
// alert() popups). Resolves true/false depending on which button the user
// clicks; for an alert-only notice (no Cancel button), always resolves true.
// opts: { okLabel, danger (bool), alertOnly (bool) }
function confirmDialog(message, opts) {
  opts = opts || {};
  const overlay = document.getElementById('confirm-dialog-overlay');
  const okBtn = document.getElementById('confirm-dialog-ok');
  const cancelBtn = document.getElementById('confirm-dialog-cancel');
  document.getElementById('confirm-dialog-message').textContent = message;
  okBtn.textContent = opts.okLabel || 'Delete';
  okBtn.style.background = opts.danger === false ? '' : '#e03131';
  okBtn.style.borderColor = opts.danger === false ? '' : '#e03131';
  cancelBtn.hidden = !!opts.alertOnly;
  overlay.classList.add('open');
  return new Promise((resolve) => {
    const cleanup = (result) => {
      overlay.classList.remove('open');
      okBtn.removeEventListener('click', onOk);
      cancelBtn.removeEventListener('click', onCancel);
      overlay.removeEventListener('click', onOverlay);
      resolve(result);
    };
    const onOk = () => cleanup(true);
    const onCancel = () => cleanup(false);
    const onOverlay = (e) => { if (e.target === overlay) cleanup(!!opts.alertOnly); };
    okBtn.addEventListener('click', onOk);
    cancelBtn.addEventListener('click', onCancel);
    overlay.addEventListener('click', onOverlay);
  });
}

document.getElementById('playlist-detail-delete-opt').addEventListener('click', async () => {
  _closePlaylistDetailMoreMenu();
  const pl_id = _currentPlaylistDetailId;
  if (!pl_id) return;
  const pl = _playlistsData.playlists[pl_id];
  if (!pl) return;
  const ok = await confirmDialog('Delete playlist "' + pl.name + '"? This cannot be undone.');
  if (!ok) return;
  try {
    await apiDelete('/api/playlists/' + encodeURIComponent(pl_id));
    delete _playlistsData.playlists[pl_id];
    closePlaylistDetailModal();
    renderPlaylists();
    toast('Playlist deleted', 'ok');
  } catch (e) {
    toast(e.message || 'Error deleting playlist', 'error');
  }
});

function _shuffled(arr) {
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

async function playPlaylist(pl_id, btn, shuffle) {
  const pl = _playlistsData.playlists[pl_id];
  if (!pl || !pl.tracks || pl.tracks.length === 0) return;
  if (btn) btn.disabled = true;
  try {
    const tracks = shuffle ? _shuffled(pl.tracks) : pl.tracks;
    const [first, ...rest] = tracks;
    // Suppress the server's auto radio-queue build for this first track --
    // the rest of the playlist is about to be appended below, and without
    // this the two race: the server can see a still-single-item queue and
    // overwrite it with generated recommendations before our own queue_add
    // calls land (see /alexa/play_queue/'s suppress_radio handling).
    await playResult({ video_id: first.video_id, title: first.title, artist: first.artist, thumbnail: first.thumbnail, duration_ms: first.duration_ms }, rest.length > 0);
    // Return to the main view the instant the first track is playing, instead
    // of waiting out one HTTP round-trip per remaining song -- queuing the
    // rest continues in the background below, so a long playlist doesn't
    // hold the user on this modal for several seconds.
    closePlaylistDetailModal();
    document.getElementById('playlists-modal-overlay').classList.remove('open');
    if (rest.length) {
      toast('Adding rest of “' + pl.name + '” to queue…');
      (async () => {
        for (const track of rest) {
          // silent: this loop queues the rest of the playlist in bulk, so
          // per-track "Adding to queue…" toasts would spam the screen — one
          // toast up front for the whole playlist is enough.
          try {
            await addToQueue({ video_id: track.video_id, title: track.title, artist: track.artist, thumbnail: track.thumbnail, duration_ms: track.duration_ms }, 'last', true);
          } catch (e) {
            toast(e.message || 'Error queuing “' + track.title + '”', 'error');
          }
        }
      })();
    }
  } catch (e) {
    toast(e.message || 'Error playing playlist', 'error');
  }
  if (btn) btn.disabled = false;
}

document.getElementById('playlist-detail-play-all-btn').addEventListener('click', (e) => {
  if (_currentPlaylistDetailId) playPlaylist(_currentPlaylistDetailId, e.currentTarget);
});

document.getElementById('playlist-detail-shuffle-btn').addEventListener('click', (e) => {
  if (_currentPlaylistDetailId) playPlaylist(_currentPlaylistDetailId, e.currentTarget, true);
});

async function toggleLike(item, btnElement) {
  const vid = item.video_id;
  const isLiked = _playlistsData.liked_songs.includes(vid);
  
  try {
    if (isLiked) {
      const res = await apiDelete('/api/playlists/liked/tracks/' + encodeURIComponent(vid));
      _playlistsData.liked_songs = res.liked_songs || [];
      if (_playlistsData.playlists.liked) {
        _playlistsData.playlists.liked.tracks = _playlistsData.playlists.liked.tracks.filter(t => t.video_id !== vid);
      }
      if (btnElement) {
        btnElement.classList.remove('liked');
        btnElement.title = "Like";
        btnElement.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>`;
      }
      toast('Removed from Liked Songs', 'ok');
      if (window.broadcastLikedUpdate) window.broadcastLikedUpdate();
    } else {
      const res = await api('/api/playlists/liked/tracks/', item);
      _playlistsData.liked_songs = res.liked_songs || [];
      if (!_playlistsData.playlists.liked) {
         _playlistsData.playlists.liked = {id: 'liked', name: 'Liked Songs', updated_at: Date.now(), tracks: []};
      }
      // Sort by added_at rather than just appending: the server may have
      // restored the track's original timestamp (see _recent_unlike_added_at
      // server-side) if this is an undo-relike shortly after un-liking, in
      // which case it belongs back in its old spot, not at the end.
      _playlistsData.playlists.liked.tracks.push(res.track);
      _playlistsData.playlists.liked.tracks.sort((a, b) => (a.added_at || 0) - (b.added_at || 0));
      if (btnElement) {
        btnElement.classList.add('liked');
        btnElement.title = "Dislike";
        btnElement.innerHTML = `<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>`;
      }
      toast('Added to Liked Songs', 'ok');
      if (window.broadcastLikedUpdate) window.broadcastLikedUpdate();
    }
  } catch (e) {
    console.error(e);
    toast('Error updating liked status', 'error');
  }
}

let _currentItemToSave = null;
function openAddToPlaylistModal(item) {
  _currentItemToSave = item;
  const modalTitle = document.getElementById('add-to-playlist-title');
  if (modalTitle) modalTitle.textContent = 'Save to Playlist';
  const listEl = document.getElementById('add-to-playlist-list');
  const lists = getPlaylistsList().filter(p => p.id !== 'liked');
  
  let html = '';
  lists.forEach(pl => {
    const trackCount = (pl.tracks || []).length;
    let thumbHtml = `
      <div class="queue-thumb" style="display: flex; align-items: center; justify-content: center; background: rgba(255, 255, 255, 0.05);">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="width: 20px; height: 20px; color: var(--text-muted, #888);"><path d="M9 18V5l12-2v13"></path><circle cx="6" cy="18" r="3"></circle><circle cx="18" cy="16" r="3"></circle></svg>
      </div>`;
    if (trackCount > 0 && pl.tracks[0].thumbnail) {
      thumbHtml = `<img class="queue-thumb loaded" src="${escHtml(pl.tracks[0].thumbnail)}" alt="">`;
    }
    const existingTrack = (pl.tracks || []).find(t => t.video_id === item.video_id);
    const iconHtml = existingTrack
      ? `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`
      : `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>`;
    // Already saved: tapping removes it (and flips back to "+" instantly).
    // Not saved yet: tapping adds it. Either way there's nothing to do if the
    // membership state doesn't match what the user is trying to toggle.
    const onclick = existingTrack
      ? `removeFromPlaylistToggle('${escHtml(pl.id)}', '${escHtml(existingTrack.uuid || existingTrack.video_id)}')`
      : `addToPlaylist('${escHtml(pl.id)}')`;
    html += `
      <div style="padding: 12px; border-bottom: 1px solid var(--border); cursor: pointer; display:flex; align-items:center; gap:12px;" onclick="${onclick}">
         ${thumbHtml}
         <div style="flex:1; font-weight: 500;">${escHtml(pl.name)}</div>
         ${iconHtml}
      </div>
    `;
  });
  listEl.innerHTML = html || '<div style="color:var(--muted); padding: 12px;">No custom playlists found. Create one!</div>';
  
  document.getElementById('new-playlist-name').value = '';
  document.getElementById('add-to-playlist-overlay').classList.add('open');
}

function closeAddToPlaylistModal() {
  document.getElementById('add-to-playlist-overlay').classList.remove('open');
  _currentItemToSave = null;
}

function openCreatePlaylistModal() {
  _currentItemToSave = null;
  const title = document.getElementById('add-to-playlist-title');
  const list = document.getElementById('add-to-playlist-list');
  const input = document.getElementById('new-playlist-name');
  if (title) title.textContent = 'New Playlist';
  if (list) list.innerHTML = '';
  input.value = '';
  input.disabled = false;
  document.getElementById('add-to-playlist-overlay').classList.add('open');
  requestAnimationFrame(() => input.focus());
}

async function createNewPlaylist() {
  const nameInput = document.getElementById('new-playlist-name');
  const name = nameInput.value.trim();
  if (!name) return;
  
  nameInput.disabled = true;
  try {
    const res = await api('/api/playlists/', { name: name });
    _playlistsData.playlists[res.id] = res;
    renderPlaylists();
    renderSidebarPlaylists();
    if (_currentItemToSave) {
      await addToPlaylist(res.id);
    } else {
      toast('Playlist created', 'ok');
      closeAddToPlaylistModal();
    }
  } catch(e) {
    toast('Error creating playlist', 'error');
  }
  nameInput.disabled = false;
}

// Track keys (uuid or video_id) of songs just added this session, so the
// playlist detail view can play their "slid in at the top" animation the next
// time it renders them (one-shot; consumed by openPlaylistDetailModal).
const _recentlyAddedTrackKeys = new Set();

async function addToPlaylist(pl_id) {
  if (!_currentItemToSave) return;
  const item = _currentItemToSave;
  const plName = (_playlistsData.playlists[pl_id] || {}).name || 'playlist';
  // Optimistic placeholder so the row flips to a checkmark immediately; swapped
  // for the server's real track (uuid, backfilled thumbnail, etc.) on success,
  // or rolled back on failure. New songs go to the TOP, matching YT Music
  // (and the newest-first order the server now returns).
  const placeholder = { uuid: 'pending-' + Date.now(), video_id: item.video_id, title: item.title,
    artist: item.artist, thumbnail: item.thumbnail, duration_ms: item.duration_ms };
  _playlistsData.playlists[pl_id].tracks.unshift(placeholder);
  _recentlyAddedTrackKeys.add(placeholder.video_id);
  if (document.getElementById('add-to-playlist-overlay').classList.contains('open')) {
    openAddToPlaylistModal(item);
  }
  _refreshOpenPlaylistDetail(pl_id);
  toast('Added to “' + plName + '”', 'ok');
  try {
    const res = await api('/api/playlists/' + encodeURIComponent(pl_id) + '/tracks/', item);
    const idx = _playlistsData.playlists[pl_id].tracks.indexOf(placeholder);
    if (idx !== -1) _playlistsData.playlists[pl_id].tracks[idx] = res.track;
    if (res.track && res.track.uuid) _recentlyAddedTrackKeys.add(res.track.uuid);
  } catch (e) {
    console.error(e);
    _playlistsData.playlists[pl_id].tracks = _playlistsData.playlists[pl_id].tracks.filter(t => t !== placeholder);
    _recentlyAddedTrackKeys.delete(placeholder.video_id);
    if (document.getElementById('add-to-playlist-overlay').classList.contains('open')) {
      openAddToPlaylistModal(item);
    }
    _refreshOpenPlaylistDetail(pl_id);
    toast('Error saving to playlist: ' + (e.message || 'unknown'), 'error');
  }
}

// Re-render the playlist detail modal if it's currently showing pl_id, so an
// add lands visibly (animated, at the top) without the user reopening it.
function _refreshOpenPlaylistDetail(pl_id) {
  if (_currentPlaylistDetailId === pl_id
      && document.getElementById('playlist-detail-modal-overlay').classList.contains('open')) {
    openPlaylistDetailModal(pl_id);
  }
}

async function removeFromPlaylistToggle(pl_id, track_uuid) {
  const pl = _playlistsData.playlists[pl_id];
  if (!pl) return;
  const removedIdx = pl.tracks.findIndex(t => (t.uuid || t.video_id) === track_uuid);
  const removedTrack = removedIdx !== -1 ? pl.tracks[removedIdx] : null;
  pl.tracks = pl.tracks.filter(t => (t.uuid || t.video_id) !== track_uuid);
  if (document.getElementById('add-to-playlist-overlay').classList.contains('open') && _currentItemToSave) {
    openAddToPlaylistModal(_currentItemToSave);
  }
  toast('Removed from playlist', 'ok');
  try {
    await apiDelete('/api/playlists/' + encodeURIComponent(pl_id) + '/tracks/' + encodeURIComponent(track_uuid));
  } catch (e) {
    if (removedTrack) pl.tracks.splice(removedIdx, 0, removedTrack);
    if (document.getElementById('add-to-playlist-overlay').classList.contains('open') && _currentItemToSave) {
      openAddToPlaylistModal(_currentItemToSave);
    }
    toast('Error removing song', 'error');
  }
}

async function removeFromPlaylist(pl_id, track_uuid) {
  const pl = _playlistsData.playlists[pl_id];
  if (!pl) return;
  // Optimistic: remove from the UI immediately and only fall back to
  // restoring + an error toast if the server actually rejects it, instead of
  // making the user wait out the round-trip before seeing anything happen.
  const isLikedMatch = (t) => t.video_id === track_uuid;
  const isPlaylistMatch = (t) => (t.uuid || t.video_id) === track_uuid;
  const matchFn = pl_id === 'liked' ? isLikedMatch : isPlaylistMatch;
  const removedIdx = pl.tracks.findIndex(matchFn);
  const removedTrack = removedIdx !== -1 ? pl.tracks[removedIdx] : null;
  pl.tracks = pl.tracks.filter(t => !matchFn(t));
  let removedLikedIdx = -1;
  if (pl_id === 'liked') {
    removedLikedIdx = _playlistsData.liked_songs.indexOf(track_uuid);
    if (removedLikedIdx !== -1) _playlistsData.liked_songs.splice(removedLikedIdx, 1);
  }
  if (document.getElementById('playlist-detail-modal-overlay').classList.contains('open')) {
    openPlaylistDetailModal(pl_id);
  }
  toast('Removed from playlist', 'ok');

  try {
    await apiDelete('/api/playlists/' + encodeURIComponent(pl_id) + '/tracks/' + encodeURIComponent(track_uuid));
  } catch (e) {
    // Roll back: put the track back where it was and tell the user it failed.
    if (removedTrack) pl.tracks.splice(removedIdx, 0, removedTrack);
    if (pl_id === 'liked' && removedLikedIdx !== -1) _playlistsData.liked_songs.splice(removedLikedIdx, 0, track_uuid);
    if (document.getElementById('playlist-detail-modal-overlay').classList.contains('open')) {
      openPlaylistDetailModal(pl_id);
    }
    toast('Error removing song', 'error');
  }
}

/* ── Reorder playlist track (drag complete) ── */
async function reorderPlaylistTrack(pl_id, fromIndex, toIndex) {
  if (fromIndex === toIndex || !pl_id) return;
  // Optimistically reorder local data
  const pl = _playlistsData.playlists[pl_id];
  if (pl && pl.tracks) {
    const tracks = pl.tracks;
    if (fromIndex >= 0 && fromIndex < tracks.length && toIndex >= 0 && toIndex < tracks.length) {
      const [moved] = tracks.splice(fromIndex, 1);
      tracks.splice(toIndex, 0, moved);
    }
  }
  try {
    await api('/api/playlists/' + encodeURIComponent(pl_id) + '/tracks/reorder/', { from_index: fromIndex, to_index: toIndex });
    // Refresh the detail view with the new order
    if (_currentPlaylistDetailId === pl_id
        && document.getElementById('playlist-detail-modal-overlay').classList.contains('open')) {
      // Reload playlists from server to get fresh timestamps
      await loadPlaylists();
      openPlaylistDetailModal(pl_id);
    }
  } catch (e) {
    // Refresh on error to restore true order
    await loadPlaylists();
    if (_currentPlaylistDetailId === pl_id
        && document.getElementById('playlist-detail-modal-overlay').classList.contains('open')) {
      openPlaylistDetailModal(pl_id);
    }
    toast(e.message || 'Error reordering tracks', 'error');
  }
}

/* ── Playlist track drag-to-reorder (mirrors queue's _attachQueueDragReorder) ── */
var _playlistDragging = false;

function _attachPlaylistDragReorder(wrapper, listEl) {
  const handle = wrapper.querySelector('.playlist-drag-handle');
  if (!handle) return;

  let startY = 0, initialTop = 0, cloneEl = null, placeholder = null;
  let currentOver = -1, fromIdx = -1;
  let _scrollRafId = null, _scrollSpeed = 0, _scrollContainer = null;
  const EDGE_ZONE = 50, MAX_SPEED = 12;

  function getItemElements() {
    return Array.from(listEl.querySelectorAll('.result-swipe-wrapper'));
  }

  function findScrollContainer() {
    let node = listEl;
    while (node && node !== document.body) {
      const style = getComputedStyle(node);
      if ((style.overflowY === 'auto' || style.overflowY === 'scroll') &&
          node.scrollHeight > node.clientHeight) return node;
      node = node.parentElement;
    }
    return null;
  }

  function startAutoScroll() {
    if (_scrollRafId) return;
    function tick() {
      if (!_playlistDragging || !_scrollContainer || _scrollSpeed === 0) {
        _scrollRafId = null; return;
      }
      _scrollContainer.scrollTop += _scrollSpeed;
      _scrollRafId = requestAnimationFrame(tick);
    }
    _scrollRafId = requestAnimationFrame(tick);
  }

  function stopAutoScroll() {
    if (_scrollRafId) { cancelAnimationFrame(_scrollRafId); _scrollRafId = null; }
    _scrollSpeed = 0;
  }

  function updateAutoScroll(clientY) {
    if (!_scrollContainer) return;
    const rect = _scrollContainer.getBoundingClientRect();
    const dTop = clientY - rect.top;
    const dBot = rect.bottom - clientY;
    if (dTop < EDGE_ZONE && _scrollContainer.scrollTop > 0) {
      _scrollSpeed = -(MAX_SPEED * Math.max(0, Math.min(1, 1 - dTop / EDGE_ZONE)));
      startAutoScroll();
    } else if (dBot < EDGE_ZONE && _scrollContainer.scrollTop < _scrollContainer.scrollHeight - _scrollContainer.clientHeight) {
      _scrollSpeed = MAX_SPEED * Math.max(0, Math.min(1, 1 - dBot / EDGE_ZONE));
      startAutoScroll();
    } else {
      _scrollSpeed = 0;
    }
  }

  function beginDrag(clientY) {
    _playlistDragging = true;
    document.body.classList.add('drag-lock');
    startY = clientY;
    // Derive the index from the DOM: chunked rendering and earlier reorders
    // mean a render-time index would go stale.
    fromIdx = getItemElements().indexOf(wrapper);
    if (fromIdx < 0) { _playlistDragging = false; document.body.classList.remove('drag-lock'); return; }
    const rect = handle.getBoundingClientRect();
    // Use the wrapper's rect since it's the full row
    const wrapRect = wrapper.getBoundingClientRect();
    initialTop = wrapRect.top;

    cloneEl = wrapper.cloneNode(true);
    cloneEl.style.position = 'fixed';
    cloneEl.style.left = wrapRect.left + 'px';
    cloneEl.style.top = wrapRect.top + 'px';
    cloneEl.style.width = wrapRect.width + 'px';
    cloneEl.style.zIndex = '1000';
    cloneEl.style.pointerEvents = 'none';
    cloneEl.style.opacity = '.85';
    cloneEl.style.boxShadow = '0 8px 32px rgba(0,0,0,.5)';
    cloneEl.style.background = 'var(--surface)';
    cloneEl.style.borderRadius = '8px';
    document.body.appendChild(cloneEl);

    wrapper.classList.add('dragging');
    currentOver = fromIdx;
    _scrollContainer = findScrollContainer();
  }

  function moveDrag(clientY) {
    if (!_playlistDragging || !cloneEl) return;
    const dy = clientY - startY;
    cloneEl.style.top = (initialTop + dy) + 'px';
    updateAutoScroll(clientY);

    const items = getItemElements();
    let targetIdx = fromIdx;
    for (let i = 0; i < items.length; i++) {
      const r = items[i].getBoundingClientRect();
      if (clientY < r.top + r.height / 2) { targetIdx = i; break; }
      targetIdx = i + 1;
    }
    targetIdx = Math.min(targetIdx, items.length - 1);
    if (targetIdx !== currentOver) {
      const old = listEl.querySelector('.queue-drop-placeholder');
      if (old) old.remove();
      placeholder = document.createElement('div');
      placeholder.className = 'queue-drop-placeholder';
      if (targetIdx < items.length) {
        listEl.insertBefore(placeholder, items[targetIdx]);
      } else {
        listEl.appendChild(placeholder);
      }
      currentOver = targetIdx;
    }
  }

  function endDrag() {
    if (!_playlistDragging) return;
    _playlistDragging = false;
    document.body.classList.remove('drag-lock');
    stopAutoScroll();
    _scrollContainer = null;
    wrapper.classList.remove('dragging');
    if (cloneEl) { cloneEl.remove(); cloneEl = null; }
    if (placeholder) { placeholder.remove(); placeholder = null; }

    let toIdx = currentOver;
    if (toIdx > fromIdx) toIdx -= 1;
    toIdx = Math.max(0, Math.min(toIdx, getItemElements().length - 1));
    if (toIdx !== fromIdx) {
      reorderPlaylistTrack(_currentPlaylistDetailId, fromIdx, toIdx);
    }
    currentOver = -1;
  }

  handle.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    e.stopPropagation();
    handle.setPointerCapture(e.pointerId);
    beginDrag(e.clientY);
  });
  handle.addEventListener('pointermove', (e) => {
    if (_playlistDragging) { e.preventDefault(); e.stopPropagation(); moveDrag(e.clientY); }
  });
  handle.addEventListener('pointerup', (e) => {
    if (_playlistDragging) {
      e.preventDefault(); e.stopPropagation();
      try { handle.releasePointerCapture(e.pointerId); } catch (_) {}
      endDrag();
    }
  });
  handle.addEventListener('pointercancel', () => { if (_playlistDragging) endDrag(); });
  handle.addEventListener('lostpointercapture', () => { if (_playlistDragging) endDrag(); });
}

async function syncPlaylist(pl_id, btnEl = null) {
  const btn = btnEl || document.getElementById('playlist-sync-btn');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" class="spin" style="width:16px;height:16px;"><path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/></svg>` + (btnEl ? '' : ' Syncing...');
  }
  
  try {
    await api('/api/playlists/' + encodeURIComponent(pl_id) + '/sync/', {});
    toast('Sync started...', 'ok');
    
    setTimeout(async () => {
      await loadPlaylists();
      if (document.getElementById('playlist-detail-modal-overlay').classList.contains('open')) {
        openPlaylistDetailModal(pl_id);
      } else {
        renderPlaylists();
      }
      if (btn && !btnEl) {
        btn.disabled = false;
        btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg> Sync`;
      }
    }, 4000);
  } catch(e) {
    if (btn && !btnEl) {
      btn.disabled = false;
      btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg> Sync`;
    }
    toast('Sync failed', 'error');
  }
}

document.addEventListener('DOMContentLoaded', () => {
  if (!window.JAM_GUEST) loadPlaylists();
  
  const plBtn = document.getElementById('playlists-modal-btn');
  if (plBtn) plBtn.addEventListener('click', openPlaylistsModal);
  
  const sidebarPlBtn = document.getElementById('sidebar-playlists-btn');
  if (sidebarPlBtn) {
    sidebarPlBtn.addEventListener('click', () => {
      // Close sidebar first
      const overlay = document.querySelector('.sidebar-overlay');
      const sidebar = document.querySelector('.sidebar');
      if (overlay) overlay.classList.remove('open');
      if (sidebar) sidebar.classList.remove('open');
      
      openPlaylistsModal();
    });
  }
  
  const plClose = document.getElementById('playlists-modal-close');
  if (plClose) plClose.addEventListener('click', closePlaylistsModal);
  
  const importPlBtn = document.getElementById('import-playlist-btn');
  const importOverlay = document.getElementById('import-playlist-overlay');
  if (importPlBtn && importOverlay) {
    importPlBtn.addEventListener('click', () => {
      document.getElementById('import-playlist-url').value = '';
      const submitBtn = document.getElementById('import-playlist-submit');
      submitBtn.innerHTML = 'Import';
      submitBtn.disabled = false;
      importOverlay.classList.add('open');
    });

    document.getElementById('import-playlist-close').addEventListener('click', () => {
      importOverlay.classList.remove('open');
    });

    // Extracts the YouTube "list=" id from a playlist URL so two differently
    // formatted links to the same playlist are recognized as duplicates.
    function extractPlaylistListId(url) {
      try {
        const u = new URL(url);
        return u.searchParams.get('list') || url;
      } catch (_) {
        const m = url.match(/[?&]list=([\w-]+)/);
        return m ? m[1] : url;
      }
    }

    document.getElementById('import-playlist-submit').addEventListener('click', async () => {
      const url = document.getElementById('import-playlist-url').value.trim();
      if (!url) return;

      const newListId = extractPlaylistListId(url);
      const existing = Object.values(_playlistsData.playlists).find(
        (p) => p.source_url && extractPlaylistListId(p.source_url) === newListId
      );
      if (existing) {
        await confirmDialog('You already imported "' + existing.name + '" from this playlist. Open it from the Playlists list, or use its Sync button to pull in new tracks.', { okLabel: 'OK', danger: false, alertOnly: true });
        return;
      }

      const submitBtn = document.getElementById('import-playlist-submit');
      submitBtn.disabled = true;
      submitBtn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" class="spin" style="width:18px;height:18px;"><path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/></svg> Importing...`;

      try {
        // name omitted — the server resolves the real YouTube playlist title.
        const res = await api('/api/playlists/', { source_url: url });
        _playlistsData.playlists[res.id] = res;
        await api('/api/playlists/' + res.id + '/sync/', {}, 'POST');
        renderPlaylists();
        setTimeout(async () => {
          await loadPlaylists();
          renderPlaylists();
          importOverlay.classList.remove('open');
          toast('Playlist imported successfully', 'ok');
        }, 2500);
      } catch (e) {
        submitBtn.disabled = false;
        submitBtn.innerHTML = 'Import';
        toast(e.message || 'Error importing playlist', 'error');
        console.error(e);
      }
    });
  }

  const plDetailClose = document.getElementById('playlist-detail-close');
  if (plDetailClose) plDetailClose.addEventListener('click', closePlaylistDetailModal);
  
  const plDetailBack = document.getElementById('playlist-detail-back');
  if (plDetailBack) plDetailBack.addEventListener('click', closePlaylistDetailModal);
  
  const addClose = document.getElementById('add-to-playlist-close');
  if (addClose) addClose.addEventListener('click', closeAddToPlaylistModal);
  
  const newPlBtn = document.getElementById('new-playlist-btn');
  if (newPlBtn) newPlBtn.addEventListener('click', createNewPlaylist);
  const sidebarNewPlBtn = document.getElementById('sidebar-new-playlist-btn');
  if (sidebarNewPlBtn) sidebarNewPlBtn.addEventListener('click', openCreatePlaylistModal);

  // Close modals when clicking outside on the overlay
  const plOverlay = document.getElementById('playlists-modal-overlay');
  if (plOverlay) plOverlay.addEventListener('click', (e) => { if (e.target === plOverlay) closePlaylistsModal(); });

  const detailOverlay = document.getElementById('playlist-detail-modal-overlay');
  if (detailOverlay) detailOverlay.addEventListener('click', (e) => { if (e.target === detailOverlay) closePlaylistDetailModal(); });

  const addOverlay = document.getElementById('add-to-playlist-overlay');
  if (addOverlay) addOverlay.addEventListener('click', (e) => { if (e.target === addOverlay) closeAddToPlaylistModal(); });

  const importOverlayEl = document.getElementById('import-playlist-overlay');
  if (importOverlayEl) importOverlayEl.addEventListener('click', (e) => { if (e.target === importOverlayEl) importOverlayEl.classList.remove('open'); });
});

window.openPlaylistsModal = openPlaylistsModal;
window.openPlaylistDetailModal = openPlaylistDetailModal;
if (location.hash === '#playlists') {
  openPlaylistsModal(true);
} else if (location.hash.indexOf('#playlist/') === 0) {
  openPlaylistDetailModal(decodeURIComponent(location.hash.slice('#playlist/'.length)), true);
}
