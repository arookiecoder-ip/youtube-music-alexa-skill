(function () {
  'use strict';

  const state = window.__appState = window.__appState || {};
  if (state._homeLoaded === undefined) state._homeLoaded = false;
  if (state._homeLoading === undefined) state._homeLoading = false;

  const musicNoteSvg = '<svg width="28" height="28" viewBox="0 0 24 24" fill="currentColor"><path d="M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z"/></svg>';

  function skeletonRowHtml() {
    const wide = window.matchMedia('(min-width: 900px)').matches;
    const count = wide ? 6 : 3;
    let cards = '';
    for (let i = 0; i < count; i++) {
      cards += '<div class="home-card home-skeleton-card"><div class="skeleton-block"></div><div class="skeleton-line skeleton-line-title"></div><div class="skeleton-line skeleton-line-artist"></div></div>';
    }
    return '<div class="home-row-skeleton">' + cards + '</div>';
  }

  function homeRowHtml(row) {
    const items = Array.isArray(row && row.items) ? row.items : [];
    const tilesHtml = items.map(function(item) {
      if (!item || !item.videoId) return '';
      const videoId = item.videoId || '';
      const title = item.title || '';
      const artist = item.artist || '';
      const thumbUrl = item.thumbnail || '';
      const thumbHtml = thumbUrl
        ? "<img src=\"" + escHtml(thumbUrl) + "\" alt=\"\" loading=\"lazy\" decoding=\"async\" onload=\"this.classList.add('loaded')\">"
        : musicNoteSvg;
      return '<div class="home-card" data-video-id="' + escHtml(videoId) + '" data-title="' + escHtml(title) + '" data-artist="' + escHtml(artist) + '" data-thumb="' + escHtml(thumbUrl) + '">' +
        '<div class="recs-tile-art home-card-art">' + thumbHtml + '</div>' +
        '<div class="recs-tile-title">' + escHtml(title) + '</div>' +
        '<div class="recs-tile-artist">' + escHtml(artist) + '</div>' +
      '</div>';
    }).join('');
    if (!tilesHtml) return '';
    const subtitle = row && row.subtitle
      ? '<div class="home-row-subtitle">' + escHtml(row.subtitle) + '</div>'
      : '';
    return '<div class="home-row-container">' +
      '<div class="home-row-header"><div class="label home-row-label">' + escHtml((row && row.title) || '') + '</div>' + subtitle + '</div>' +
      '<div class="home-row">' + tilesHtml + '</div>' +
    '</div>';
  }

  function showHomeSkeleton(show) {
    const container = document.getElementById('home-rows');
    if (!container) return;
    if (show) {
      let rows = '';
      for (let i = 0; i < 4; i++) rows += skeletonRowHtml();
      container.innerHTML = rows;
      container.hidden = false;
      return;
    }
    container.querySelectorAll('.home-row-skeleton').forEach(function(el) {
      el.hidden = true;
    });
  }

  function renderHomeFeed(data) {
    const container = document.getElementById('home-rows');
    if (!container) return;
    const rows = Array.isArray(data && data.rows) ? data.rows : [];
    const rowsHtml = rows.map(homeRowHtml).join('');
    container.innerHTML = rowsHtml;
    container.hidden = false;
    showHomeSkeleton(false);
  }

  async function loadHomeFeed() {
    if (!state._loggedIn || state._homeLoaded || state._homeLoading) return;
    state._homeLoading = true;
    const section = document.getElementById('home-section');
    if (section) section.hidden = !(!state._hasTrack && !state._resultsOpen);
    showHomeSkeleton(true);
    try {
      const data = await api('/api/home/?refresh=1');
      state._homeLoaded = true;
      renderHomeFeed(data);
    } catch (e) {
      console.warn('Failed to load home feed', e);
      const container = document.getElementById('home-rows');
      if (container) {
        container.innerHTML = '';
        container.hidden = true;
      }
      showHomeSkeleton(false);
      if (section) section.hidden = true;
    } finally {
      state._homeLoading = false;
    }
  }

  const rows = document.getElementById('home-rows');
  if (rows) {
    rows.addEventListener('click', function(e) {
      const card = e.target.closest('.home-card');
      if (!card || !rows.contains(card) || !card.dataset.videoId) return;
      if (!window.playFromQueue) return;
      window.playFromQueue({
        video_id: card.dataset.videoId,
        title: card.dataset.title || '',
        artist: card.dataset.artist || '',
        thumbnail: card.dataset.thumb || '',
      });
    });
  }

  window.loadHomeFeed = loadHomeFeed;
  window.renderHomeFeed = renderHomeFeed;
  window.showHomeSkeleton = showHomeSkeleton;
})();
