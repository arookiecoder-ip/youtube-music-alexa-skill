let _playlistsData = { playlists: {}, liked_songs: [] };

async function loadPlaylists() {
  try {
    const data = await api('/api/playlists/');
    if (data && data.playlists) {
      _playlistsData = data;
    }
  } catch (e) {
    console.error("Failed to load playlists", e);
  }
}

function getPlaylistsList() {
  return Object.values(_playlistsData.playlists).sort((a, b) => b.updated_at - a.updated_at);
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
  lists.forEach(pl => {
    const trackCount = (pl.tracks || []).length;
    let thumbHtml = '<div class="history-thumb"></div>';
    if (trackCount > 0 && pl.tracks[0].thumbnail) {
      thumbHtml = `<img class="history-thumb loaded" src="${escHtml(pl.tracks[0].thumbnail)}" alt="">`;
    }
    
    html += `
      <div class="history-item" onclick="openPlaylistDetailModal('${escHtml(pl.id)}')">
        ${thumbHtml}
        <div class="history-info">
          <div class="history-title">${escHtml(pl.name)}</div>
          <div class="history-artist">${trackCount} ${trackCount === 1 ? 'song' : 'songs'}</div>
        </div>
        <button class="history-play-btn" title="Open Playlist" type="button">
           <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="9 18 15 12 9 6"/></svg>
        </button>
      </div>
    `;
  });
  html += '</div>';
  body.innerHTML = html;
}

function openPlaylistsModal() {
  renderPlaylists();
  document.getElementById('playlists-modal-overlay').classList.add('open');
}

function closePlaylistsModal() {
  document.getElementById('playlists-modal-overlay').classList.remove('open');
}

function openPlaylistDetailModal(pl_id) {
  const pl = _playlistsData.playlists[pl_id];
  if (!pl) return;
  
  document.getElementById('playlist-detail-title').textContent = pl.name;
  
  const syncBtn = document.getElementById('playlist-sync-btn');
  if (pl.source_url) {
    syncBtn.hidden = false;
    syncBtn.onclick = () => syncPlaylist(pl_id);
  } else {
    syncBtn.hidden = true;
  }
  
  const body = document.getElementById('playlist-detail-body');
  if (!pl.tracks || pl.tracks.length === 0) {
    body.innerHTML = '<div class="history-modal-empty">Playlist is empty.</div>';
  } else {
    let html = '<div class="history-list">';
    pl.tracks.forEach((track) => {
      let thumbHtml = '<div class="history-thumb"></div>';
      if (track.thumbnail) {
        thumbHtml = `<img class="history-thumb loaded" src="${escHtml(track.thumbnail)}" alt="">`;
      }
      
      const trashIcon = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>`;
      
      html += `
        <div class="history-item">
          ${thumbHtml}
          <div class="history-info" onclick="playResult({video_id: '${escHtml(track.video_id)}', title: '${escHtml(track.title).replace(/'/g, "\\'")}', artist: '${escHtml(track.artist).replace(/'/g, "\\'")}'})">
            <div class="history-title">${escHtml(track.title)}</div>
            <div class="history-artist">${escHtml(track.artist)}</div>
          </div>
          <button class="result-more-btn" type="button" title="Remove" onclick="removeFromPlaylist('${pl_id}', '${track.uuid || track.video_id}')">${trashIcon}</button>
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

async function toggleLike(item, btnElement) {
  const vid = item.video_id;
  const isLiked = _playlistsData.liked_songs.includes(vid);
  
  try {
    if (isLiked) {
      const res = await api('/api/playlists/liked/tracks/' + encodeURIComponent(vid), { method: 'DELETE' });
      _playlistsData.liked_songs = res.liked_songs || [];
      if (_playlistsData.playlists.liked) {
        _playlistsData.playlists.liked.tracks = _playlistsData.playlists.liked.tracks.filter(t => t.video_id !== vid);
      }
      if (btnElement) {
        btnElement.classList.remove('liked');
        btnElement.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>`;
      }
      toast('Removed from Liked Songs', 'ok');
    } else {
      const res = await api('/api/playlists/liked/tracks/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(item)
      });
      _playlistsData.liked_songs = res.liked_songs || [];
      if (!_playlistsData.playlists.liked) {
         _playlistsData.playlists.liked = {id: 'liked', name: 'Liked Songs', tracks: []};
      }
      _playlistsData.playlists.liked.tracks.push(res.track);
      if (btnElement) {
        btnElement.classList.add('liked');
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
    const res = await api('/api/playlists/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name })
    });
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
    const res = await api('/api/playlists/' + encodeURIComponent(pl_id) + '/tracks/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(_currentItemToSave)
    });
    _playlistsData.playlists[pl_id].tracks.push(res.track);
    toast('Saved to playlist', 'ok');
    closeAddToPlaylistModal();
  } catch (e) {
    toast('Error saving to playlist', 'error');
  }
}

async function removeFromPlaylist(pl_id, track_uuid) {
  try {
    await api('/api/playlists/' + encodeURIComponent(pl_id) + '/tracks/' + encodeURIComponent(track_uuid), { method: 'DELETE' });
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

async function syncPlaylist(pl_id) {
  const btn = document.getElementById('playlist-sync-btn');
  btn.disabled = true;
  btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" class="spin"><path d="M21.5 2v6h-6M2.13 15.57a9 9 0 1 0 3.44-8.8L2.5 9M2.5 22v-6h6M21.87 8.43a9 9 0 1 0-3.44 8.8L21.5 15"/></svg> Syncing...`;
  
  try {
    await api('/api/playlists/' + encodeURIComponent(pl_id) + '/sync/', { method: 'POST' });
    toast('Sync started...', 'ok');
    
    setTimeout(async () => {
      await loadPlaylists();
      if (document.getElementById('playlist-detail-modal-overlay').classList.contains('open')) {
        openPlaylistDetailModal(pl_id);
      }
      btn.disabled = false;
      btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg> Sync`;
    }, 4000);
  } catch(e) {
    btn.disabled = false;
    btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg> Sync`;
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
  
  const plDetailClose = document.getElementById('playlist-detail-close');
  if (plDetailClose) plDetailClose.addEventListener('click', closePlaylistDetailModal);
  
  const plDetailBack = document.getElementById('playlist-detail-back');
  if (plDetailBack) plDetailBack.addEventListener('click', closePlaylistDetailModal);
  
  const addClose = document.getElementById('add-to-playlist-close');
  if (addClose) addClose.addEventListener('click', closeAddToPlaylistModal);
  
  const newPlBtn = document.getElementById('new-playlist-btn');
  if (newPlBtn) newPlBtn.addEventListener('click', createNewPlaylist);
});
