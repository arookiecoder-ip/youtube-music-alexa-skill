(function () {
  'use strict';
  const state = window.__appState = window.__appState || {};
  if (state._loggedIn === undefined) state._loggedIn = false;
  if (state._hasTrack === undefined) state._hasTrack = false;
  if (state._resultsOpen === undefined) state._resultsOpen = false;
  if (state._recsLoaded === undefined) state._recsLoaded = false;
  if (state._recsLoading === undefined) state._recsLoading = false;
  if (state._recsItems === undefined) state._recsItems = null;
  if (state._recsResizeTimer === undefined) state._recsResizeTimer = null;
  if (state._recsShownCols === undefined) state._recsShownCols = 0;

function showRecsSkeleton(show) {
  const skeleton = document.getElementById('recs-skeleton');
  if (show) {
    const cols = recsColumns();
    const rows = recsRows();
    const tile = `<div class="recs-skeleton-tile"><div class="skeleton-block"></div><div class="skeleton-line skeleton-line-title"></div><div class="skeleton-line skeleton-line-artist"></div></div>`;
    skeleton.innerHTML = tile.repeat(cols * rows);
  }
  skeleton.hidden = !show;
  document.getElementById('recs-list').hidden = show;
}

async function loadRecommendations() {
  if (!state._loggedIn || state._recsLoaded || state._recsLoading) return;
  state._recsLoading = true;
  const section = document.getElementById('recs-section');
  section.hidden = !(!state._hasTrack && !state._resultsOpen);
  showRecsSkeleton(true);
  try {
    // refresh=1 so each visit rebuilds from current history (a fresh mix) and
    // never serves a stale cached list from an earlier fallback.
    const items = await api('/recommendations/?refresh=1');
    state._recsLoaded = true;
    state._recsItems = Array.isArray(items) ? items : [];
    // renderRecommendations keeps the skeleton up until the thumbnails have
    // loaded, then swaps to the real grid — so no empty flash in between.
    renderRecommendations(state._recsItems);
  } catch (e) {
    console.warn('Failed to load recommendations', e);
    section.hidden = true;
    showRecsSkeleton(false);
  } finally {
    state._recsLoading = false;
  }
}

// Mobile: always 3 columns. Desktop: fill width with tiles ≥132px.
function recsColumns() {
  const wide = window.matchMedia('(min-width: 900px)').matches;
  if (!wide) return 3;
  const gap = 18, pad = 80;
  const avail = Math.max(0, window.innerWidth - pad);
  return Math.max(2, Math.floor((avail + gap) / (132 + gap)));
}

// How many rows fit in the visible area (including footer) without scrolling.
function recsRows() {
  const wide = window.matchMedia('(min-width: 900px)').matches;
  const cols = recsColumns();
  const hGap = wide ? 18 : 10;
  const pad  = wide ? 80 : 24;
  const vGap = wide ? 22 : 10;
  const tileW = Math.floor((window.innerWidth - pad - hGap * (cols - 1)) / cols);
  // tile height = square art + title + artist + internal gap (~40px desktop, ~34px mobile)
  const tileH = tileW + (wide ? 40 : 34);
  // overhead: header + idle-hero + search bar + section label + footer
  const overhead = wide ? 320 : 280;
  const avail = Math.max(tileH, window.innerHeight - overhead);
  return Math.max(2, Math.floor((avail + vGap) / (tileH + vGap)));
}

function renderRecommendations(items) {
  const section = document.getElementById('recs-section');
  const list = document.getElementById('recs-list');
  const hasItems = Array.isArray(items) && items.length > 0;
  section.hidden = !(!state._hasTrack && !state._resultsOpen && hasItems);
  if (!hasItems) { 
    list.innerHTML = ''; 
    showRecsSkeleton(false);
    return; 
  }
  list.innerHTML = '';
  const cols = recsColumns();
  const rows = recsRows();
  state._recsShownCols = cols;
  list.style.setProperty('--recs-cols', cols);
  const shown = items.filter(it => it && it.video_id).slice(0, cols * rows);
  const tiles = [];
  for (const item of shown) {
    if (!item || !item.video_id) continue;
    const thumbUrl = (item.thumbnail && item.thumbnail.url) || item.thumbnail || '';
    const el = document.createElement('div');
    // Each tile starts hidden and reveals only once its own thumbnail has
    // finished loading (or failed), so no tile ever flashes as an empty/
    // half-loaded box. .is-ready is added per-tile below.
    el.className = 'recs-tile';
    el.dataset.videoId = item.video_id;
    el._songContextTrack = {
      video_id: item.video_id,
      title: item.title || '',
      artist: item.artist || '',
      thumbnail: thumbUrl
    };
    const thumbHtml = thumbUrl
      ? `<img src="${escHtml(thumbUrl)}" alt="" loading="lazy" onload="this.classList.add('loaded')">`
      : `<svg width="28" height="28" viewBox="0 0 24 24" fill="currentColor">
           <path d="M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z"/>
         </svg>`;
    var isLikedTile = typeof _playlistsData !== 'undefined' && _playlistsData.liked_songs && _playlistsData.liked_songs.includes(item.video_id);
    var heartSvgTile = isLikedTile
      ? '<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>'
      : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>';
    el.innerHTML = `
      <div class="recs-tile-art">${thumbHtml}</div>
      <div class="recs-tile-title">${escHtml(item.title || '')}</div>
      <div class="recs-tile-artist">${window.artistLinksHtml(item.artist, item.channelId)}</div>
      <button class="result-like-btn recs-like-btn${isLikedTile ? ' liked' : ''}" type="button" title="${isLikedTile ? 'Dislike' : 'Like'}">${heartSvgTile}</button>
    `;
    // Artist name clicks: stop propagation to prevent tile's play action
    window.wireArtistLinks(el);

    // Like button: stop propagation to prevent tile's play action
    var lb = el.querySelector('.recs-like-btn');
    if (lb) {
      lb.addEventListener('click', function(e) {
        e.stopPropagation();
        if (typeof toggleLike === 'function') toggleLike(item, this);
      });
    }

    el.addEventListener('click', () => playFromQueue({
      video_id: item.video_id,
      title: item.title || '',
      artist: item.artist || '',
      thumbnail: thumbUrl,
    }));
    list.appendChild(el);
    tiles.push({ el, thumbUrl });
  }

  // Make the (still-empty-looking, tiles hidden) grid live so its <img>s start
  // fetching; the skeleton stays on top until enough thumbnails are ready.
  document.getElementById('recs-skeleton').hidden = false;
  list.hidden = false;

  let readyCount = 0;
  let skeletonHidden = false;
  const hideSkeletonOnce = () => {
    if (skeletonHidden) return;
    skeletonHidden = true;
    document.getElementById('recs-skeleton').hidden = true;
  };

  // Reveal the art once its thumbnail is decoded. Text is already visible.
  const revealTile = (t, i) => {
    if (t.el.dataset.ready) return;
    t.el.dataset.ready = '1';
    const art = t.el.querySelector('.recs-tile-art');
    if (art) art.style.transitionDelay = Math.min(i * 25, 400) + 'ms';
    t.el.classList.add('is-ready');
    readyCount++;
    if (readyCount >= Math.max(1, tiles.length - 2)) hideSkeletonOnce();
  };
  tiles.forEach((t, i) => {
    const img = t.el.querySelector('img');
    if (!img) { revealTile(t, i); return; }   // placeholder tile, no image
    const done = () => revealTile(t, i);
    if (img.complete && img.naturalWidth > 0) { done(); return; }  // cached
    img.addEventListener('load', done, { once: true });
    img.addEventListener('error', done, { once: true });
  });
  // Safety nets: never leave the skeleton up forever, and reveal any tiles
  // still waiting on slow/hung images.
  setTimeout(hideSkeletonOnce, 2500);
  setTimeout(() => { tiles.forEach((t, i) => revealTile(t, i)); hideSkeletonOnce(); }, 5000);
}

// Re-flow the 2-row grid when the column count changes on resize (e.g. window
// resized, orientation change) so it always fills the width in exactly 2 rows.
window.addEventListener('resize', () => {
  clearTimeout(state._recsResizeTimer);
  state._recsResizeTimer = setTimeout(() => {
    if (!state._recsLoaded || !state._recsItems) return;
    if (document.getElementById('recs-section').hidden) return;
    if (recsColumns() === state._recsShownCols) return;   // no column change
    renderRecommendations(state._recsItems);
  }, 200);
});

/* ---- Open on YouTube Music ----
   The link carries the current playback position (?t=Ns) so YouTube resumes
   where the Echo left off. Clicking it also pauses the Echo, since the user
   is about to keep listening through the browser tab instead. */

  window.loadRecommendations = loadRecommendations;
  window.renderRecommendations = renderRecommendations;
  window.showRecsSkeleton = showRecsSkeleton;
})();
