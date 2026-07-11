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
    // Keep the modal current if it's open.
    const overlay = document.getElementById('history-modal-overlay');
    if (overlay.classList.contains('open')) {
      const isNewTop = newTopId && newTopId !== prevTopId;
      const list = overlay.querySelector('.history-list');
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
        // Modal open but no list yet — full render.
        renderHistoryModalList(state._historyCache);
      }
      // If only metadata changed (same top), leave the list as-is.
    }
  } catch (e) {
    console.warn('Failed to load history', e);
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

  const thumbHtml = entry.thumbnail_url
    ? `<img class="queue-thumb" src="${escHtml(entry.thumbnail_url)}" alt="" loading="lazy" onload="this.classList.add('loaded')">`
    : `<div class="queue-thumb history-thumb-placeholder">
         <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
           <path d="M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z"/>
         </svg>
       </div>`;

  var isLikedHistory = typeof _playlistsData !== 'undefined' && _playlistsData.liked_songs && _playlistsData.liked_songs.includes(entry.video_id);
  var heartSvgHistory = isLikedHistory
    ? '<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>'
    : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>';
  el.innerHTML = `
    ${thumbHtml}
    <div class="queue-info">
      <div class="queue-title">${escHtml(entry.title || 'Unknown title')}</div>
      <div class="queue-artist">${entry.channelId ? '<span class="artist-name" data-channel-id="' + escHtml(entry.channelId) + '">' + escHtml(entry.artist) + '</span>' : escHtml(entry.artist)}${entry.play_count > 1 ? '<span class="play-count-badge">\u00d7' + entry.play_count + '</span>' : ''}</div>
    </div>
    <button class="result-like-btn history-like-btn${isLikedHistory ? ' liked' : ''}" type="button" title="${isLikedHistory ? 'Dislike' : 'Like'}">${heartSvgHistory}</button>
  `;

  // Artist name click: stop propagation to prevent parent row's play action
  var an = el.querySelector('.artist-name');
  if (an) {
    an.addEventListener('click', function(e) {
      e.stopPropagation();
      var cid = this.getAttribute('data-channel-id');
      if (cid) location.hash = '#artist/' + encodeURIComponent(cid);
    });
  }

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
  const body = document.getElementById('history-modal-body');
  const clearBtn = document.getElementById('clear-history-btn');
  const items = Array.isArray(history) ? history.filter(e => e && e.video_id) : [];
  clearBtn.hidden = items.length === 0;
  if (items.length === 0) {
    body.innerHTML = '<div class="history-modal-empty">No listening history yet</div>';
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

async function doClearHistory() {
  try {
    await apiDelete('/history/');
    state._historyCache = [];
    renderHistoryModalList([]);
    syncHistoryTriggerVisibility();
    window._closeHistoryModal();
    toast('History cleared', 'ok');
  } catch (e) {
    toast(e.message, 'error');
  }
}

function clearHistory() {
  return doClearHistory();
}

(function () {
  const overlay = document.getElementById('history-modal-overlay');
  const closeBtn = document.getElementById('history-modal-close');
  const openBtn = document.getElementById('history-modal-btn');

  function openHistoryModal(fromRoute) {
    if (!fromRoute && window.matchMedia('(min-width: 900px)').matches) {
      location.hash = '#history';
      return;
    }
    // Render immediately from the pre-fetched cache — no fetch-on-click wait.
    renderHistoryModalList(state._historyCache);
    overlay.classList.add('open');
  }

  function closeHistoryModal() {
    overlay.classList.remove('open');
    if (location.hash === '#history') location.hash = '#home';
  }

  openBtn.addEventListener('click', openHistoryModal);
  if (closeBtn) closeBtn.addEventListener('click', closeHistoryModal);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) closeHistoryModal(); });

  window._closeHistoryModal = closeHistoryModal;
  window.openHistoryPage = openHistoryModal;
  if (location.hash === '#history') openHistoryModal(true);
})();

(function () {
  const overlay = document.getElementById('confirm-clear-history');
  const cancelBtn = document.getElementById('confirm-clear-history-cancel');
  const yesBtn = document.getElementById('confirm-clear-history-yes');
  document.getElementById('clear-history-btn').addEventListener('click', () => {
    overlay.classList.add('open');
  });
  cancelBtn.addEventListener('click', () => overlay.classList.remove('open'));
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.classList.remove('open'); });
  yesBtn.addEventListener('click', () => { overlay.classList.remove('open'); doClearHistory(); });
})();

/* ---- Recommendations (blank-state, mixed history + discovery) ---- */

  window.loadHistory = loadHistory;
  window.renderHistoryModalList = renderHistoryModalList;
  window.syncHistoryTriggerVisibility = syncHistoryTriggerVisibility;
  window._buildHistoryRow = _buildHistoryRow;
  window.doClearHistory = doClearHistory;
  window.clearHistory = clearHistory;
})();
