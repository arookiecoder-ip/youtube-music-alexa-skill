(function () {
  'use strict';

  const state = window.__appState = window.__appState || {};
  const MOOD_ACCENTS = ['#ff8c3a', '#e80000', '#8a3ffc', '#ffe264', '#00a928', '#ffe264', '#b764ff', '#ff6500', '#00a9d7', '#9ebfff', '#b8b8b8', '#2d7cff', '#ffe264', '#8cff9b', '#666', '#ef62f5', '#9ff5a7', '#ff5700'];
  const FALLBACK_IMG = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23444'%3E%3Cpath d='M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z'/%3E%3C/svg%3E";
  let loaded = false;
  let loading = false;

  function escHtml(value) {
    return String(value || '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
  }

  function imageUrl(value) {
    if (!value) return '';
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) return imageUrl(value[value.length - 1]);
    return value.url || imageUrl(value.thumbnails) || imageUrl(value.thumbnail) || '';
  }

  function subtitle(item) {
    if (item.description || item.subtitle) return item.description || item.subtitle;
    if (item.artists && item.artists.length) return item.artists.map(a => a.name).filter(Boolean).join(', ');
    return item.album && item.album.name || item.views || '';
  }

  function openItem(item) {
    const videoId = item.videoId || item.video_id;
    if (videoId && window.playResult) {
      window.playResult(Object.assign({}, item, { video_id: videoId }), false, true);
      return;
    }
    const id = item.browseId || item.playlistId || item.albumId || item.audioPlaylistId;
    if (!id) return;
    if (item.type === 'Album' || String(id).startsWith('MPREb')) {
      window.navigateTo('#album/' + encodeURIComponent(id));
    } else if (window.preloadNavigatePlaylist) {
      window.preloadNavigatePlaylist(id);
    } else {
      window.navigateTo('#playlist/' + encodeURIComponent(id));
    }
  }

  function playItem(event, item) {
    event.stopPropagation();
    const videoId = item.videoId || item.video_id;
    if (videoId && window.playResult) {
      window.playResult(Object.assign({}, item, { video_id: videoId }), false, true);
      return;
    }
    const playlistId = item.audioPlaylistId || item.playlistId;
    if (playlistId && window.api) {
      window.api('/alexa/play/', {
        serial: window.selectedSerial ? window.selectedSerial() : '',
        query: 'https://music.youtube.com/playlist?list=' + playlistId
      });
      return;
    }
    openItem(item);
  }

  function renderCard(item) {
    const title = item.title || item.name || 'Unknown';
    const thumb = imageUrl(item.thumbnails) || imageUrl(item.thumbnail) || imageUrl(item.images) || imageUrl(item.image);
    const card = document.createElement('article');
    card.className = 'explore-card' + ((item.type === 'Album' || String(item.browseId || item.albumId || '').startsWith('MPREb')) ? ' album-card' : '');
    card.tabIndex = 0;
    card.setAttribute('role', 'button');
    card.setAttribute('aria-label', `Open ${title}`);
    card.innerHTML = `
      <div class="explore-card-art">
        <img src="${escHtml(thumb || FALLBACK_IMG)}" alt="${escHtml(title)}" loading="lazy" onerror="this.onerror=null;this.src='${FALLBACK_IMG}'">
        <button class="home-play-btn explore-card-play" type="button" aria-label="Play ${escHtml(title)}">
          <svg class="home-play-glyph" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><polygon points="7,4 20,12 7,20"/></svg>
        </button>
      </div>
      <div class="explore-card-info">
        <div class="explore-card-title">${escHtml(title)}</div>
        ${subtitle(item) ? `<div class="explore-card-sub">${escHtml(subtitle(item))}</div>` : ''}
      </div>`;
    card.addEventListener('click', () => openItem(item));
    card.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openItem(item); }
    });
    card.querySelector('.explore-card-play').addEventListener('click', event => playItem(event, item));
    return card;
  }

  function renderSection(body, title, items, key) {
    if (!Array.isArray(items) || !items.length) return false;
    const section = document.createElement('section');
    section.className = 'explore-section';
    section.dataset.exploreKey = key;
    section.innerHTML = `<div class="explore-section-header"><h2 class="explore-section-title">${escHtml(title)}</h2></div>`;
    const grid = document.createElement('div');
    grid.className = 'explore-grid';
    items.forEach(item => grid.appendChild(renderCard(item)));
    section.appendChild(grid);
    body.appendChild(section);
    return true;
  }

  function renderMoodSection(body, moods) {
    if (!Array.isArray(moods) || !moods.length) return false;
    const section = document.createElement('section');
    section.className = 'explore-section explore-section--moods';
    section.dataset.exploreKey = 'moods';
    section.innerHTML = `
      <div class="explore-section-header explore-mood-header">
        <h2 class="explore-section-title">Moods and genres</h2>
        <div class="explore-mood-controls">
          <button class="explore-mood-more" type="button">More</button>
          <button class="explore-mood-arrow" type="button" aria-label="Previous moods and genres">‹</button>
          <button class="explore-mood-arrow" type="button" aria-label="Next moods and genres">›</button>
        </div>
      </div>`;
    const grid = document.createElement('div');
    grid.className = 'explore-mood-grid';
    section.appendChild(grid);
    let page = 0;
    const pageSize = 24;
    const renderPage = () => {
      const start = page * pageSize;
      grid.replaceChildren();
      moods.slice(start, start + pageSize).forEach((mood, index) => {
        const button = document.createElement('button');
        button.className = 'explore-mood-card';
        button.type = 'button';
        button.style.setProperty('--mood-accent', MOOD_ACCENTS[(start + index) % MOOD_ACCENTS.length]);
        button.textContent = mood.title || 'Mood';
        button.addEventListener('click', () => {
          window.navigateTo('#mood/' + encodeURIComponent(mood.params) + '?title=' + encodeURIComponent(mood.title || 'Moods and genres'));
        });
        grid.appendChild(button);
      });
      const canPage = moods.length > pageSize;
      section.querySelector('.explore-mood-more').disabled = !canPage;
      const arrows = section.querySelectorAll('.explore-mood-arrow');
      arrows[0].disabled = !canPage;
      arrows[1].disabled = !canPage;
    };
    section.querySelector('.explore-mood-more').addEventListener('click', () => {
      page = page === 0 ? 1 : 0;
      renderPage();
    });
    section.querySelectorAll('.explore-mood-arrow')[0].addEventListener('click', () => {
      page = page > 0 ? page - 1 : Math.ceil(moods.length / pageSize) - 1;
      renderPage();
    });
    section.querySelectorAll('.explore-mood-arrow')[1].addEventListener('click', () => {
      page = page < Math.ceil(moods.length / pageSize) - 1 ? page + 1 : 0;
      renderPage();
    });
    renderPage();
    body.appendChild(section);
    return true;
  }

  function moodLoadingPlaceholder(title) {
    return `<div class="mood-page-loading" role="status" aria-live="polite">
      <div class="explore-loading-status">Loading ${escHtml(title)}…</div>
      <div class="mood-page-skeleton-grid">${Array(12).fill('<div class="mood-page-skeleton"><div></div><span></span><span></span></div>').join('')}</div>
    </div>`;
  }

  async function openMoodPage(params, title) {
    const overlay = document.getElementById('mood-modal-overlay');
    const body = document.getElementById('mood-modal-body');
    const heading = document.getElementById('mood-modal-title');
    if (!overlay || !body || !params) return;
    overlay.classList.add('open');
    heading.textContent = title || 'Moods and genres';
    body.innerHTML = moodLoadingPlaceholder(title || 'Moods and genres');
    try {
      const result = await window.api('/api/explore/moods/?params=' + encodeURIComponent(params));
      body.innerHTML = '';
      if (!renderSection(body, title || 'Moods and genres', result.playlists || [], 'mood-playlists')) {
        body.innerHTML = '<div class="explore-empty">No playlists are available for this mood or genre right now.</div>';
      }
    } catch (error) {
      body.innerHTML = `<div class="explore-empty"><div>Couldn’t load ${escHtml(title || 'this mood')}.</div><button type="button" class="btn-accent mood-retry">Try again</button></div>`;
      body.querySelector('.mood-retry').addEventListener('click', () => openMoodPage(params, title));
    }
  }

  function renderQuickNav(body, available) {
    const sections = [['New releases', 'new_releases'], ['Charts', 'charts'], ['Trending', 'trending'], ['Moods & genres', 'moods']]
      .filter(([, key]) => available.has(key));
    if (!sections.length) return;
    const nav = document.createElement('nav');
    nav.className = 'explore-quick-nav';
    sections.forEach(([label, key]) => {
      const button = document.createElement('button');
      button.className = 'explore-quick-card';
      button.type = 'button';
      button.textContent = label;
      button.addEventListener('click', () => body.querySelector(`[data-explore-key="${key}"]`).scrollIntoView({ behavior: 'smooth', block: 'start' }));
      nav.appendChild(button);
    });
    body.prepend(nav);
  }

  async function loadExplore(force) {
    if (!state._loggedIn || window.JAM_GUEST || !window.IS_AUTHENTICATED || loading || (loaded && !force)) return;
    const body = document.getElementById('explore-modal-body');
    if (!body) return;
    loading = true;
    body.innerHTML = '<div class="explore-loading-status" role="status">Loading Explore…</div>';
    try {
      const explore = await window.api('/api/explore/');
      if (!explore || typeof explore !== 'object') throw new Error('Empty response');
      body.innerHTML = '';
      const available = new Set();
      if (renderSection(body, 'New releases', explore.new_releases, 'new_releases')) available.add('new_releases');
      if (renderSection(body, 'Top songs', explore.top_songs && explore.top_songs.items, 'charts')) available.add('charts');
      if (renderSection(body, 'Trending', explore.trending && explore.trending.items, 'trending')) available.add('trending');
      if (renderSection(body, 'New music videos', explore.new_videos, 'new_videos')) available.add('new_videos');
      if (renderMoodSection(body, explore.moods_and_genres)) available.add('moods');
      renderQuickNav(body, available);
      if (!available.size) body.innerHTML = '<div class="explore-empty">Nothing to explore right now. Please try again later.</div>';
      loaded = true;
    } catch (error) {
      loaded = false;
      body.innerHTML = '<div class="explore-empty">Couldn’t load Explore. Please try again.</div>';
      console.warn('[explore] Failed to load Explore', error);
    } finally {
      loading = false;
    }
  }

  window.openExplorePage = function (force) {
    const overlay = document.getElementById('explore-modal-overlay');
    if (overlay) overlay.classList.add('open');
    loadExplore(force);
  };
  window.closeExplorePage = function () {
    const overlay = document.getElementById('explore-modal-overlay');
    if (overlay) overlay.classList.remove('open');
  };
  window.openMoodPage = openMoodPage;
  document.addEventListener('click', event => {
    if (event.target.closest('#explore-modal-close')) window.navigateTo('#home');
    if (event.target.closest('#mood-modal-close')) window.navigateTo('#explore');
  });
}());
