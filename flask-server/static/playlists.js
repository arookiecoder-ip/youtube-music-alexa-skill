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

let _playlistsData = { playlists: {}, liked_songs: [] };

async function loadPlaylists() {
  try {
    const data = await api('/api/playlists/?_=' + Date.now());
    if (data && data.playlists) {
      _playlistsData = data;
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
    let thumbHtml = `
      <div class="queue-thumb" style="display: flex; align-items: center; justify-content: center; background: rgba(255, 255, 255, 0.05);">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="width: 20px; height: 20px; color: var(--text-muted, #888);"><path d="M9 18V5l12-2v13"></path><circle cx="6" cy="18" r="3"></circle><circle cx="18" cy="16" r="3"></circle></svg>
      </div>`;

    if (pl.id === 'liked') {
      thumbHtml = `
        <div class="queue-thumb" style="display: flex; align-items: center; justify-content: center; background: rgba(255, 255, 255, 0.05);">
          <svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="width: 24px; height: 24px; color: var(--primary);"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>
        </div>`;
    } else if (trackCount > 0 && pl.tracks[0].thumbnail) {
      thumbHtml = `<img class="queue-thumb loaded" src="${escHtml(pl.tracks[0].thumbnail)}" alt="">`;
    }

    // Sync lives only in the playlist detail modal's header (for imported
    // playlists) so every row here shows the same fixed set of controls.
    const playIconHtml = trackCount > 0 ? `
        <button class="clear-all-btn" title="Play playlist" style="padding: 4px 8px; margin-left: auto;" onclick="event.stopPropagation(); playPlaylist('${escHtml(pl.id)}', this)">
          <svg viewBox="0 0 24 24" fill="currentColor" style="width:16px;height:16px;"><path d="M8 5v14l11-7z"/></svg>
        </button>
      ` : '';

    html += `
      <div class="history-item" onclick="openPlaylistDetailModal('${escHtml(pl.id)}')">
        ${thumbHtml}
        <div class="history-info" style="display: flex; flex-direction: row; align-items: center; justify-content: space-between; flex: 1;">
          <div>
            <div class="history-title">${escHtml(pl.name)}</div>
            <div class="history-artist">${trackCount} ${trackCount === 1 ? 'song' : 'songs'}</div>
          </div>
          ${playIconHtml}
        </div>
      </div>
    `;
  });
  html += '</div>';
  body.innerHTML = html;
}

async function openPlaylistsModal() {
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
}

let _currentPlaylistDetailId = null;

function openPlaylistDetailModal(pl_id) {
  const pl = _playlistsData.playlists[pl_id];
  if (!pl) return;
  _currentPlaylistDetailId = pl_id;

  document.getElementById('playlist-detail-title').textContent = pl.name;

  const syncBtn = document.getElementById('playlist-sync-btn');
  if (pl.source_url) {
    syncBtn.hidden = false;
    syncBtn.onclick = () => syncPlaylist(pl_id);
  } else {
    syncBtn.hidden = true;
  }

  // "Rename" isn't offered for Liked Songs (a fixed system playlist); delete
  // is hidden for it in the same way the backend already refuses both.
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
  document.getElementById('playlist-detail-radio-btn').hidden = !pl.tracks || pl.tracks.length === 0;

  const body = document.getElementById('playlist-detail-body');
  // Rebuilding the row markup below orphans any per-track menu still
  // portaled to <body> from a previous render (its _home row no longer
  // exists) -- drop those instead of leaving stray, unclickable nodes behind.
  document.querySelectorAll('body > .playlist-more-menu').forEach(m => m.remove());
  if (!pl.tracks || pl.tracks.length === 0) {
    body.innerHTML = '<div class="history-modal-empty">Playlist is empty.</div>';
  } else {
    const trashIcon = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>`;
    const moreSvg = `<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>`;
    const queueAddSvg = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>`;
    const playNextSvg = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>`;
    const heartFilledSvg = `<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>`;

    const list = document.createElement('div');
    list.className = 'history-list';

    pl.tracks.forEach((track) => {
      // Titles/artists from a synced YouTube playlist can contain quotes,
      // backslashes, etc. -- building this row via string-interpolated
      // onclick="..." attributes (as before) meant any such character could
      // corrupt the attribute and silently break every button on the row with
      // no console error. Building real DOM nodes and attaching real event
      // listeners (passing `track` as a normal JS object, no serialization)
      // sidesteps that entirely.
      const item = { video_id: track.video_id, title: track.title, artist: track.artist,
        thumbnail: track.thumbnail || '', duration_ms: Number(track.duration_ms) || 0 };

      // Swipe wrapper: reuses the exact same underlays/gesture handler as the
      // search-results list (right = Play next, left = Add to queue) instead
      // of a bespoke implementation, so the animation and thresholds match.
      const wrapper = document.createElement('div');
      wrapper.className = 'result-swipe-wrapper';
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
        img.className = 'queue-thumb loaded';
        img.src = track.thumbnail;
        img.alt = '';
        row.appendChild(img);
      } else {
        const ph = document.createElement('div');
        ph.className = 'queue-thumb';
        ph.style.cssText = 'display:flex; align-items:center; justify-content:center; background: rgba(255,255,255,0.05);';
        ph.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="width: 20px; height: 20px; color: var(--text-muted, #888);"><path d="M9 18V5l12-2v13"></path><circle cx="6" cy="18" r="3"></circle><circle cx="18" cy="16" r="3"></circle></svg>`;
        row.appendChild(ph);
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

      // Liked Songs is a fixed system playlist: every track here is already
      // liked, so the per-track "remove" action is a heart button (un-like)
      // rather than the generic "Remove from playlist" menu item used for
      // custom playlists -- clicking it does exactly what un-liking does
      // elsewhere in the app. Deliberately does NOT re-render/remove the row
      // right away: the button just flips to "disliked" so an accidental tap
      // is easy to undo (tap again to re-like) -- the row only disappears
      // once the modal is closed and reopened, reflecting the real list.
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
      // z-index must clear .history-modal-overlay's 500: this menu gets
      // portaled to <body> while open (a sibling of the modal overlay, not a
      // descendant), so a lower z-index here renders it behind the still-open
      // playlist detail modal instead of on top of it.
      menu.style.cssText = 'position:absolute; right:8px; top:100%; background: var(--surface); border: 1px solid var(--border); z-index: 9999; min-width: 170px; box-shadow: 0 8px 24px rgba(0,0,0,.5); overflow: hidden;';

      // Reuses .queue-menu-option (not .result-menu-option) so this menu's
      // icon size/color, spacing, and hover/danger styling exactly match the
      // queue's identical 3-dot menu instead of drifting from it.
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
      if (pl_id === 'liked') {
        // Liked Songs' per-track "remove" is the heart button above instead
        // (un-like), not this menu -- see the heartBtn block above.
      } else {
        addMenuOption(trashIcon, 'Remove from playlist', true,
          () => removeFromPlaylist(pl_id, track.uuid || track.video_id));
      }

      moreContainer.appendChild(moreBtn);
      moreContainer.appendChild(menu);
      row.appendChild(moreContainer);

      // Tapping anywhere on the row plays it, same as clicking `info` used to
      // -- attached via attachQueueItemTap (not a plain click listener) so it
      // respects _swipeSuppressClick, which _attachSwipeGesture sets on this
      // same `row` element after a committed swipe. Buttons inside the row
      // (heart, more-menu) already stopPropagation in their own handlers, so
      // this doesn't double-fire when one of those is clicked instead.
      attachQueueItemTap(row, () => playResult(item));

      wrapper.appendChild(row);
      _attachSwipeGesture(wrapper, row, item);
      list.appendChild(wrapper);
    });

    body.innerHTML = '';
    body.appendChild(list);
  }

  document.getElementById('playlists-modal-overlay').classList.add('open');
  document.getElementById('playlist-detail-modal-overlay').classList.add('open');
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

document.getElementById('playlist-detail-radio-btn').addEventListener('click', (e) => {
  const pl_id = _currentPlaylistDetailId;
  const pl = _playlistsData.playlists[pl_id];
  if (!pl || !pl.tracks || pl.tracks.length === 0) return;
  const shuffled = [...pl.tracks].sort(() => Math.random() - 0.5);
  const seeds = shuffled.slice(0, 5);
  closePlaylistDetailModal();
  document.getElementById('playlists-modal-overlay').classList.remove('open');
  if (seeds.length) {
    playResult({ video_id: seeds[0].video_id, title: seeds[0].title, artist: seeds[0].artist, thumbnail: seeds[0].thumbnail, duration_ms: seeds[0].duration_ms }, false, true);
  }
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
    }
  } catch (e) {
    console.error(e);
    toast('Error updating liked status', 'error');
  }
}

let _currentItemToSave = null;
function openAddToPlaylistModal(item) {
  _currentItemToSave = item;
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

async function createNewPlaylist() {
  const nameInput = document.getElementById('new-playlist-name');
  const name = nameInput.value.trim();
  if (!name) return;
  
  nameInput.disabled = true;
  try {
    const res = await api('/api/playlists/', { name: name });
    _playlistsData.playlists[res.id] = res;
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

async function addToPlaylist(pl_id) {
  if (!_currentItemToSave) return;
  const item = _currentItemToSave;
  // Optimistic placeholder so the row flips to a checkmark immediately; swapped
  // for the server's real track (uuid, backfilled thumbnail, etc.) on success,
  // or rolled back on failure.
  const placeholder = { uuid: 'pending-' + Date.now(), video_id: item.video_id, title: item.title,
    artist: item.artist, thumbnail: item.thumbnail, duration_ms: item.duration_ms };
  _playlistsData.playlists[pl_id].tracks.push(placeholder);
  if (document.getElementById('add-to-playlist-overlay').classList.contains('open')) {
    openAddToPlaylistModal(item);
  }
  toast('Saved to playlist', 'ok');
  try {
    const res = await api('/api/playlists/' + encodeURIComponent(pl_id) + '/tracks/', item);
    const idx = _playlistsData.playlists[pl_id].tracks.indexOf(placeholder);
    if (idx !== -1) _playlistsData.playlists[pl_id].tracks[idx] = res.track;
  } catch (e) {
    console.error(e);
    _playlistsData.playlists[pl_id].tracks = _playlistsData.playlists[pl_id].tracks.filter(t => t !== placeholder);
    if (document.getElementById('add-to-playlist-overlay').classList.contains('open')) {
      openAddToPlaylistModal(item);
    }
    toast('Error saving to playlist: ' + (e.message || 'unknown'), 'error');
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
  loadPlaylists();
  
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
