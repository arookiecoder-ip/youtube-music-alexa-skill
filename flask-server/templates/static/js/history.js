(function () {
  'use strict';
  const state = window.__appState = window.__appState || {};
  if (state._loggedIn === undefined) state._loggedIn = false;
  if (state._historyCache === undefined) state._historyCache = [];

async function loadHistory() {
  if (!state._loggedIn || window.JAM_GUEST) return;
  try {
    const history = await api('/history/?limit=100');
    const fresh = Array.isArray(history) ? history.filter(e => e && e.video_id) : [];
    const prevTopId = state._historyCache.length ? state._historyCache[0].video_id : null;
    const newTopId  = fresh.length ? fresh[0].video_id : null;
    state._historyCache = fresh;
    syncHistoryTriggerVisibility();
    // Keep the page current if it is visible.
    const page = document.getElementById('history-page');
    if (page && !page.hidden) {
      const isNewTop = newTopId && newTopId !== prevTopId;
      const list = page.querySelector('.history-list');
      if (isNewTop && list) {
        // A genuinely new song appeared at the top — prepend it animated and
        // remove any existing row for the same id (avoids duplicates from the
        // optimistic insert above).
        list.querySelectorAll('.history-item').forEach(el => {
          if (el.dataset.videoId === newTopId) el.remove();
        });
        const row = _buildHistoryRow(fresh[0]);
        row.classList.add('history-item-new');
        row.dataset.videoId = newTopId;
        list.prepend(row);
      } else if (!list) {
        // Page open but no list yet — full render.
        renderHistoryModalList(state._historyCache);
      }
      // If only metadata changed (same top), leave the list as-is.
    }
  } catch (e) {
    console.error('Failed to load history', e);
  }
}

function _historyDateBucket(now, playedAt) {
  const today = new Date(now * 1000);
  today.setHours(0, 0, 0, 0);
  const todaySec = today.getTime() / 1000;
  const yesterdayStart = todaySec - 86400;
  const dayOfWeek = today.getDay();
  const mondayOffset = dayOfWeek === 0 ? 6 : dayOfWeek - 1;
  const weekStart = todaySec - mondayOffset * 86400;
  if (playedAt >= todaySec) return 'Today';
  if (playedAt >= yesterdayStart) return 'Yesterday';
  if (playedAt >= weekStart) return 'This Week';
  return 'Older';
}

function syncHistoryTriggerVisibility() {
  const show = state._loggedIn && state._historyCache.length > 0;
  document.getElementById('history-modal-btn').hidden = !show;
}

function _buildHistoryRow(entry) {
  const el = document.createElement('div');
  el.className = 'history-item';
  el.dataset.videoId = entry.video_id || '';
  el._songContextTrack = {
    video_id: entry.video_id || '',
    title: entry.title || '',
    artist: entry.artist || '',
    thumbnail: entry.thumbnail_url || '',
    duration: entry.duration || '',
    duration_seconds: entry.duration_seconds || 0
  };

  const thumbHtml = entry.thumbnail_url
    ? `<img class="queue-thumb" src="${escHtml(entry.thumbnail_url)}" alt="" loading="lazy" onload="this.classList.add('loaded')">`
    : `<div class="queue-thumb history-thumb-placeholder">
         <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
           <path d="M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z"/>
         </svg>
       </div>`;

  var isLikedHistory = typeof _playlistsData !== 'undefined' && _playlistsData.liked_songs && _playlistsData.liked_songs.includes(entry.video_id);
  var duration = window.formatTrackDuration ? window.formatTrackDuration(entry) : '';
  var heartSvgHistory = isLikedHistory
    ? '<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>'
    : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>';
  el.innerHTML = `
    ${thumbHtml}
    <div class="queue-info">
      <div class="queue-title">${escHtml(entry.title || 'Unknown title')}</div>
      <div class="queue-artist">${window.artistLinksHtml(entry.artist, entry.channelId)}${entry.play_count > 1 ? '<span class="play-count-badge">\u00d7' + entry.play_count + '</span>' : ''}</div>
    </div>
    ${duration ? `<span class="track-duration">${escHtml(duration)}</span>` : ''}
    <button class="result-like-btn history-like-btn${isLikedHistory ? ' liked' : ''}" type="button" title="${isLikedHistory ? 'Dislike' : 'Like'}">${heartSvgHistory}</button>
  `;

  // Artist name clicks: stop propagation to prevent parent row's play action
  window.wireArtistLinks(el);

  // Like button: stop propagation to prevent history item's play action
  var historyLikeBtn = el.querySelector('.history-like-btn');
  if (historyLikeBtn) {
    historyLikeBtn.addEventListener('click', function(e) {
      e.stopPropagation();
      // History rows carry thumbnail_url; toggleLike expects .thumbnail.
      if (typeof toggleLike === 'function') toggleLike({
        video_id: entry.video_id,
        title: entry.title || '',
        artist: entry.artist || '',
        thumbnail: entry.thumbnail_url || '',
      }, this);
    });
  }

  // No per-row remove control by design — Clear (with confirmation) is the
  // only way to modify history from this popup.
  el.addEventListener('click', () => {
    window._closeHistoryModal();
    playFromQueue({
      video_id: entry.video_id,
      title: entry.title || '',
      artist: entry.artist || '',
      thumbnail: entry.thumbnail_url || '',
    });
  });
  return el;
}

function renderHistoryModalList(history) {
  const body = document.getElementById('history-page-body');
  const items = Array.isArray(history) ? history.filter(e => e && e.video_id) : [];
  if (!body) return;
  if (items.length === 0) {
    body.innerHTML = '<div class="history-page-empty">No listening history yet</div>';
    return;
  }
  const now = Date.now() / 1000;
  const buckets = { 'Today': [], 'Yesterday': [], 'This Week': [], 'Older': [] };
  for (const entry of items) {
    const bucket = _historyDateBucket(now, entry.played_at);
    buckets[bucket].push(entry);
  }
  const list = document.createElement('div');
  list.className = 'history-list';
  for (const label of ['Today', 'Yesterday', 'This Week', 'Older']) {
    const group = buckets[label];
    if (!group.length) continue;
    const header = document.createElement('div');
    header.className = 'history-date-header';
    header.textContent = label;
    list.appendChild(header);
    for (const entry of group) list.appendChild(_buildHistoryRow(entry));
  }
  body.innerHTML = '';
  body.appendChild(list);
}

(function () {
  const page = document.getElementById('history-page');
  const openBtn = document.getElementById('history-modal-btn');

  function openHistoryPage(fromRoute) {
    renderHistoryModalList(state._historyCache);
    page.hidden = false;
  }

  function closeHistoryPage() {
    page.hidden = true;
    if (window.getRoute() === '#history') window.navigateTo('#home');
  }

  openBtn.addEventListener('click', () => window.navigateTo('#history'));
  window._closeHistoryModal = closeHistoryPage;
  window.openHistoryPage = openHistoryPage;
  if (window.getRoute() === '#history') openHistoryPage(true);
})();

/* ---- Recommendations (blank-state, mixed history + discovery) ---- */

  window.loadHistory = loadHistory;
  window.renderHistoryModalList = renderHistoryModalList;
  window.syncHistoryTriggerVisibility = syncHistoryTriggerVisibility;
  window._buildHistoryRow = _buildHistoryRow;
})();
