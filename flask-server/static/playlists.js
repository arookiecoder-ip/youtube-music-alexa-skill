window.togglePlaylistMoreMenu = function(btn) {
  const menus = document.querySelectorAll('.playlist-more-menu');
  menus.forEach(m => {
    if (m !== btn.nextElementSibling) m.style.display = 'none';
  });
  const menu = btn.nextElementSibling;
  
  if (menu.style.display === 'none') {
    menu.style.display = 'block';
    const rect = btn.getBoundingClientRect();
    menu.style.position = 'fixed';
    menu.style.top = (rect.bottom + 4) + 'px';
    menu.style.right = (window.innerWidth - rect.right) + 'px';
    menu.style.bottom = 'auto';
    menu.style.left = 'auto';
  } else {
    menu.style.display = 'none';
  }
};

document.addEventListener('click', () => {
  document.querySelectorAll('.playlist-more-menu').forEach(m => m.style.display = 'none');
});

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
  return lists.sort((a, b) => b.updated_at - a.updated_at);
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
    let thumbHtml = '<div class="queue-thumb"></div>';
    
    if (pl.id === 'liked') {
      thumbHtml = `
        <div class="queue-thumb" style="display: flex; align-items: center; justify-content: center; background: rgba(255, 255, 255, 0.05);">
          <svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="width: 24px; height: 24px; color: var(--primary);"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>
        </div>`;
    } else if (trackCount > 0 && pl.tracks[0].thumbnail) {
      thumbHtml = `<img class="queue-thumb loaded" src="${escHtml(pl.tracks[0].thumbnail)}" alt="">`;
    }

    let syncIconHtml = '';
    if (pl.source_url) {
      syncIconHtml = `
        <button class="clear-all-btn" title="Sync Playlist" style="padding: 4px 8px;" onclick="event.stopPropagation(); syncPlaylist('${escHtml(pl.id)}', this)">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="width:16px;height:16px;">
            <polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
          </svg>
        </button>
      `;
    }

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
          ${syncIconHtml}
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

  const body = document.getElementById('playlist-detail-body');
  if (!pl.tracks || pl.tracks.length === 0) {
    body.innerHTML = '<div class="history-modal-empty">Playlist is empty.</div>';
  } else {
    let html = '<div class="history-list">';
    pl.tracks.forEach((track) => {
      let thumbHtml;
      if (track.thumbnail) {
        thumbHtml = `<img class="queue-thumb loaded" src="${escHtml(track.thumbnail)}" alt="">`;
      } else {
        thumbHtml = `
          <div class="queue-thumb" style="display: flex; align-items: center; justify-content: center; background: rgba(255, 255, 255, 0.05);">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="width: 20px; height: 20px; color: var(--text-muted, #888);"><path d="M9 18V5l12-2v13"></path><circle cx="6" cy="18" r="3"></circle><circle cx="18" cy="16" r="3"></circle></svg>
          </div>`;
      }
      
      const trashIcon = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="width: 100%; height: 100%;"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>`;
      const moreSvg = `<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>`;
      
      html += `
        <div class="history-item" style="position: relative;">
          ${thumbHtml}
          <div class="history-info" style="flex: 1; min-width: 0; cursor: pointer;" onclick="playResult({video_id: '${escHtml(track.video_id)}', title: '${escHtml(track.title).replace(/'/g, "\\'")}', artist: '${escHtml(track.artist).replace(/'/g, "\\'")}'})">
            <div class="history-title">${escHtml(track.title)}</div>
            <div class="history-artist">${escHtml(track.artist)}</div>
          </div>
          <div class="playlist-more-container" style="position: relative; display: flex; align-items: center;">
            <button class="result-more-btn" type="button" title="More options" onclick="event.stopPropagation(); window.togglePlaylistMoreMenu(this)" style="background: none; border: none; color: var(--text-muted, #aaa); cursor: pointer; padding: 8px;">
              ${moreSvg}
            </button>
            <div class="playlist-more-menu" style="display: none; position: absolute; right: 8px; top: 100%; background: var(--bg-elevated, #2a2a2a); border: 1px solid var(--border, #444); border-radius: 8px; z-index: 100; min-width: 180px; box-shadow: 0 4px 12px rgba(0,0,0,0.5); overflow: hidden;">
              <div class="result-menu-option" style="padding: 12px 16px; display: flex; align-items: center; color: #ff4d4d; cursor: pointer; transition: background 0.2s; font-size: 14px;" onclick="event.stopPropagation(); removeFromPlaylist('${pl_id}', '${track.uuid || track.video_id}')" onmouseover="this.style.background='rgba(255,255,255,0.1)'" onmouseout="this.style.background='transparent'">
                <div style="width: 18px; height: 18px; margin-right: 12px; display: flex;">${trashIcon}</div>
                Remove from playlist
              </div>
            </div>
          </div>
        </div>
      `;
    });
    html += '</div>';
    body.innerHTML = html;
  }
  
  document.getElementById('playlists-modal-overlay').classList.add('open');
  document.getElementById('playlist-detail-modal-overlay').classList.add('open');
}

function closePlaylistDetailModal() {
  document.getElementById('playlist-detail-modal-overlay').classList.remove('open');
}

document.getElementById('playlist-detail-more-btn').addEventListener('click', (e) => {
  e.stopPropagation();
  const menu = document.getElementById('playlist-detail-more-menu');
  const wasOpen = menu.style.display === 'block';
  menu.style.display = 'none';
  if (!wasOpen) {
    const rect = e.currentTarget.getBoundingClientRect();
    menu.style.top = (rect.bottom + 4) + 'px';
    menu.style.right = (window.innerWidth - rect.right) + 'px';
    menu.style.left = 'auto';
    menu.style.display = 'block';
  }
});
document.addEventListener('click', () => {
  document.getElementById('playlist-detail-more-menu').style.display = 'none';
});

document.getElementById('playlist-detail-rename-opt').addEventListener('click', () => {
  document.getElementById('playlist-detail-more-menu').style.display = 'none';
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

document.getElementById('playlist-detail-delete-opt').addEventListener('click', async () => {
  document.getElementById('playlist-detail-more-menu').style.display = 'none';
  const pl_id = _currentPlaylistDetailId;
  if (!pl_id) return;
  const pl = _playlistsData.playlists[pl_id];
  if (!pl) return;
  if (!confirm('Delete playlist "' + pl.name + '"? This cannot be undone.')) return;
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

async function playPlaylist(pl_id, btn) {
  const pl = _playlistsData.playlists[pl_id];
  if (!pl || !pl.tracks || pl.tracks.length === 0) return;
  if (btn) btn.disabled = true;
  try {
    const [first, ...rest] = pl.tracks;
    await playResult({ video_id: first.video_id, title: first.title, artist: first.artist, thumbnail: first.thumbnail });
    for (const track of rest) {
      await addToQueue({ video_id: track.video_id, title: track.title, artist: track.artist, thumbnail: track.thumbnail, duration_ms: track.duration_ms }, 'last');
    }
    closePlaylistDetailModal();
    document.getElementById('playlists-modal-overlay').classList.remove('open');
  } catch (e) {
    toast(e.message || 'Error playing playlist', 'error');
  }
  if (btn) btn.disabled = false;
}

document.getElementById('playlist-detail-play-all-btn').addEventListener('click', (e) => {
  if (_currentPlaylistDetailId) playPlaylist(_currentPlaylistDetailId, e.currentTarget);
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
      _playlistsData.playlists.liked.tracks.push(res.track);
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
    html += `
      <div style="padding: 12px; border-bottom: 1px solid var(--border); cursor: pointer; display:flex; align-items:center;" onclick="addToPlaylist('${escHtml(pl.id)}')">
         <div style="flex:1; font-weight: 500;">${escHtml(pl.name)}</div>
         <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
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
  try {
    const res = await api('/api/playlists/' + encodeURIComponent(pl_id) + '/tracks/', _currentItemToSave);
    _playlistsData.playlists[pl_id].tracks.push(res.track);
    toast('Saved to playlist', 'ok');
    closeAddToPlaylistModal();
  } catch (e) {
    console.error(e);
    toast('Error saving to playlist: ' + (e.message || 'unknown'), 'error');
  }
}

async function removeFromPlaylist(pl_id, track_uuid) {
  try {
    await apiDelete('/api/playlists/' + encodeURIComponent(pl_id) + '/tracks/' + encodeURIComponent(track_uuid));
    if (_playlistsData.playlists[pl_id]) {
      _playlistsData.playlists[pl_id].tracks = _playlistsData.playlists[pl_id].tracks.filter(t => (t.uuid || t.video_id) !== track_uuid);
      if (document.getElementById('playlist-detail-modal-overlay').classList.contains('open')) {
        openPlaylistDetailModal(pl_id);
      }
    }
    toast('Removed from playlist', 'ok');
  } catch (e) {
    toast('Error removing song', 'error');
  }
}

async function syncPlaylist(pl_id, btnEl = null) {
  const btn = btnEl || document.getElementById('playlist-sync-btn');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" class="spin" style="width:16px;height:16px;"><path d="M21.5 2v6h-6M2.13 15.57a9 9 0 1 0 3.44-8.8L2.5 9M2.5 22v-6h6M21.87 8.43a9 9 0 1 0-3.44 8.8L21.5 15"/></svg>` + (btnEl ? '' : ' Syncing...');
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
      document.getElementById('import-playlist-name').value = '';
      const submitBtn = document.getElementById('import-playlist-submit');
      submitBtn.innerHTML = 'Import';
      submitBtn.disabled = false;
      importOverlay.classList.add('open');
    });
    
    document.getElementById('import-playlist-close').addEventListener('click', () => {
      importOverlay.classList.remove('open');
    });

    document.getElementById('import-playlist-submit').addEventListener('click', async () => {
      const url = document.getElementById('import-playlist-url').value.trim();
      const name = document.getElementById('import-playlist-name').value.trim();
      if (!url || !name) return;
      
      const submitBtn = document.getElementById('import-playlist-submit');
      submitBtn.disabled = true;
      submitBtn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" class="spin" style="width:18px;height:18px;"><path d="M21.5 2v6h-6M2.13 15.57a9 9 0 1 0 3.44-8.8L2.5 9M2.5 22v-6h6M21.87 8.43a9 9 0 1 0-3.44 8.8L21.5 15"/></svg> Importing...`;

      try {
        const res = await api('/api/playlists/', { name: name, source_url: url });
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
        toast('Error importing playlist', 'error');
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
