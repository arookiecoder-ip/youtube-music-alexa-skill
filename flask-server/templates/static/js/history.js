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
    // Keep the history page current if it is visible.
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
        // Modal open but no list yet — full render.
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

function _historyViewportImages(page) {
  const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
  return Array.from(page.querySelectorAll('img')).filter(function (img) {
    const rect = img.getBoundingClientRect();
    return rect.bottom > 0 && rect.top < viewportHeight;
  });
}

function _waitForHistoryImage(img) {
  return new Promise(function (resolve) {
    let settled = false;
    const finish = function () {
      if (settled) return;
      settled = true;
      img.removeEventListener('load', finish);
      img.removeEventListener('error', finish);
      resolve();
    };
    const check = function () {
      if (img.dataset.imageReady === 'true') {
        finish();
        return;
      }
      if (img.complete && (img.currentSrc || img.src)) {
        if (typeof img.decode === 'function') img.decode().catch(function () {}).then(finish);
        else finish();
        return;
      }
      if (!settled) requestAnimationFrame(check);
    };
    img.addEventListener('load', check, { once: true });
    img.addEventListener('error', finish, { once: true });
    check();
    setTimeout(finish, 10000);
  });
}

function _waitForHistoryViewport(page) {
  const images = _historyViewportImages(page);
  if (!images.length) return Promise.resolve();
  return Promise.all(images.map(_waitForHistoryImage));
}

(function () {
  const page = document.getElementById('history-page');
  const openBtn = document.getElementById('history-modal-btn');

  function openHistoryModal(fromRoute) {
    if (!fromRoute && window.matchMedia('(min-width: 900px)').matches) {
      window.navigateTo('#history');
      return;
    }
    const waitForViewport = window.matchMedia('(max-width: 899px)').matches;
    document.body.classList.remove('drag-lock');
    document.documentElement.style.removeProperty('overflow');
    document.body.style.removeProperty('overflow');
    if (waitForViewport && window.startTopProgress) window.startTopProgress();
    page.hidden = true;
    page.classList.toggle('history-page-loading', waitForViewport);
    renderHistoryModalList(state._historyCache);
    page.hidden = false;
    if (waitForViewport) {
      _waitForHistoryViewport(page).then(function () {
        page.classList.remove('history-page-loading');
        if (window.getRoute() === '#history' && !page.hidden && window.completeTopProgress) window.completeTopProgress();
      });
    }
  }

  function closeHistoryModal() {
    page.hidden = true;
    if (window.getRoute() === '#history') window.navigateTo('#home');
  }

  openBtn.addEventListener('click', openHistoryModal);
  window._closeHistoryModal = closeHistoryModal;
  window.openHistoryPage = openHistoryModal;
  if (window.getRoute() === '#history') openHistoryModal(true);
})();

/* ---- Recommendations (blank-state, mixed history + discovery) ---- */

  window.loadHistory = loadHistory;
  window.renderHistoryModalList = renderHistoryModalList;
  window.syncHistoryTriggerVisibility = syncHistoryTriggerVisibility;
  window._buildHistoryRow = _buildHistoryRow;
})();
