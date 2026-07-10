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
      // The main feed uses videoId; the recs-cache fallback row the server
      // serves when the feed build fails uses video_id. Accept both so the
      // fallback doesn't render as an empty page.
      const videoId = (item && (item.videoId || item.video_id)) || '';
      if (!videoId) return '';
      const title = item.title || '';
      const artist = item.artist || '';
      const thumbUrl = item.thumbnail || '';
      const thumbHtml = thumbUrl
        ? "<img src=\"" + escHtml(thumbUrl) + "\" alt=\"\" loading=\"lazy\" decoding=\"async\" onload=\"this.classList.add('loaded')\">"
        : musicNoteSvg;
      var isLikedLocal = typeof _playlistsData !== 'undefined' && _playlistsData.liked_songs && _playlistsData.liked_songs.includes(videoId);
      var heartSvgLocal = isLikedLocal
        ? '<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>'
        : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>';
      return '<div class="home-card" data-video-id="' + escHtml(videoId) + '" data-title="' + escHtml(title) + '" data-artist="' + escHtml(artist) + '" data-thumb="' + escHtml(thumbUrl) + '">' +
        '<div class="recs-tile-art home-card-art">' + thumbHtml + '</div>' +
        '<div class="recs-tile-title">' + escHtml(title) + '</div>' +
        '<div class="recs-tile-artist">' + escHtml(artist) + '</div>' +
        '<button class="result-like-btn' + (isLikedLocal ? ' liked' : '') + '" type="button" title="' + (isLikedLocal ? 'Dislike' : 'Like') + '">' + heartSvgLocal + '</button>' +
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
    // Phase 12: Performance marker for profiling home feed render time
    if (window.performance && performance.mark) {
      performance.mark('home-feed-start');
    }
    const container = document.getElementById('home-rows');
    if (!container) return;
    const greet = document.getElementById('home-greeting');
    if (greet) {
      const h = new Date().getHours();
      greet.textContent = h < 5 ? 'Good night' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
      greet.hidden = false;
    }
    const rows = Array.isArray(data && data.rows) ? data.rows : [];
    let rowsHtml = rows.map(homeRowHtml).join('');
    if (!rowsHtml) {
      // Never leave the page silently blank: show why and offer a retry.
      rowsHtml = '<div class="home-empty">' +
        '<div class="home-empty-title">No recommendations right now</div>' +
        '<div class="home-empty-sub">The feed could not be built. Play something or try again.</div>' +
        '<button type="button" id="home-retry-btn" class="btn-accent">Try again</button>' +
        '</div>';
    }
    container.innerHTML = rowsHtml;
    container.hidden = false;
    showHomeSkeleton(false);
    // Phase 12: Measure render-to-screen time
    if (window.performance && performance.mark && performance.measure) {
      performance.mark('home-feed-end');
      try { performance.measure('home-feed-render', 'home-feed-start', 'home-feed-end'); } catch (_) {}
    }
  }

  async function loadHomeFeed() {
    if (!state._loggedIn || state._homeLoaded || state._homeLoading) return;
    state._homeLoading = true;
    const section = document.getElementById('home-section');
    const artistOpen = (location.hash || '').indexOf('#artist/') === 0;
    if (section) section.hidden = !!(state._resultsOpen || artistOpen);
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
      // Empty-state retry: reset the loaded flag and rebuild the feed
      if (e.target.closest('#home-retry-btn')) {
        state._homeLoaded = false;
        loadHomeFeed();
        return;
      }

      // Like button: stop propagation so card play doesn't fire
      var likeBtn = e.target.closest('.result-like-btn');
      if (likeBtn) {
        e.stopPropagation();
        var card = likeBtn.closest('.home-card');
        if (card && card.dataset.videoId) {
          var item = {
            video_id: card.dataset.videoId,
            title: card.dataset.title,
            artist: card.dataset.artist,
            thumbnail: card.dataset.thumb,
          };
          if (typeof toggleLike === 'function') toggleLike(item, likeBtn);
        }
        return;
      }

      var card = e.target.closest('.home-card');
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
