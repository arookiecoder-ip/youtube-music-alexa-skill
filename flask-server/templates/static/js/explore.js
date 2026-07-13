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
      if (window.preloadNavigateAlbum) window.preloadNavigateAlbum(id);
      else window.navigateTo('#album/' + encodeURIComponent(id));
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

  function renderCard(item, eager) {
    const title = item.title || item.name || 'Unknown';
    const thumb = imageUrl(item.thumbnails) || imageUrl(item.thumbnail) || imageUrl(item.images) || imageUrl(item.image);
    const card = document.createElement('article');
    card.className = 'explore-card' + ((item.type === 'Album' || String(item.browseId || item.albumId || '').startsWith('MPREb')) ? ' album-card' : '');
    card.tabIndex = 0;
    card.setAttribute('role', 'button');
    card.setAttribute('aria-label', `Open ${title}`);
    card.innerHTML = `
      <div class="explore-card-art">
        <img src="${escHtml(thumb || FALLBACK_IMG)}" alt="${escHtml(title)}" class="explore-card-image" loading="${eager ? 'eager' : 'lazy'}" decoding="async" onload="this.classList.add('is-loaded')" onerror="this.onerror=null;this.src='${FALLBACK_IMG}';this.classList.add('is-loaded')">
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
    section.innerHTML = `
      <div class="explore-section-header">
        <h2 class="explore-section-title">${escHtml(title)}</h2>
        <div class="explore-scroll-btns">
          <button class="explore-scroll-btn explore-scroll-left" type="button" aria-label="Scroll ${escHtml(title)} left"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg></button>
          <button class="explore-scroll-btn explore-scroll-right" type="button" aria-label="Scroll ${escHtml(title)} right"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg></button>
        </div>
      </div>`;
    const grid = document.createElement('div');
    grid.className = 'explore-grid';
    items.forEach(item => grid.appendChild(renderCard(item)));
    section.appendChild(grid);
    const left = section.querySelector('.explore-scroll-left');
    const right = section.querySelector('.explore-scroll-right');
    const updateScrollButtons = () => {
      const maxScroll = Math.max(0, grid.scrollWidth - grid.clientWidth);
      left.disabled = grid.scrollLeft <= 1;
      right.disabled = grid.scrollLeft >= maxScroll - 1;
    };
    left.addEventListener('click', () => grid.scrollBy({ left: -Math.max(240, grid.clientWidth * .8), behavior: 'smooth' }));
    right.addEventListener('click', () => grid.scrollBy({ left: Math.max(240, grid.clientWidth * .8), behavior: 'smooth' }));
    grid.addEventListener('scroll', updateScrollButtons, { passive: true });
    requestAnimationFrame(updateScrollButtons);
    body.appendChild(section);
    return true;
  }

  function renderMoodSongs(body, songs) {
    const playableSongs = (songs || []).filter(song => song && (song.videoId || song.video_id));
    if (!playableSongs.length) return false;
    const shelf = document.createElement('section');
    shelf.className = 'home-shelf home-layout-song_grid mood-songs-shelf';
    shelf.innerHTML = `
      <div class="home-shelf-header">
        <div class="home-shelf-title-area"><h2 class="home-shelf-title">Songs</h2></div>
        <div class="home-shelf-scroll-btns">
          <button class="home-scroll-btn mood-songs-left" type="button" aria-label="Scroll songs left"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg></button>
          <button class="home-scroll-btn mood-songs-right" type="button" aria-label="Scroll songs right"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg></button>
        </div>
      </div>`;
    const content = document.createElement('div');
    content.className = 'home-shelf-content';
    playableSongs.forEach(song => {
      const title = song.title || 'Unknown';
      const artist = subtitle(song);
      const thumbnail = imageUrl(song.thumbnails) || imageUrl(song.thumbnail) || FALLBACK_IMG;
      const track = { video_id: song.videoId || song.video_id, title: title, artist: artist, thumbnail: thumbnail };
      const row = document.createElement('article');
      row.className = 'home-item home-item-song';
      row.tabIndex = 0;
      row.setAttribute('role', 'button');
      row.setAttribute('aria-label', `Play ${title}`);
      row.innerHTML = `
        <img src="${escHtml(thumbnail)}" alt="${escHtml(title)}" class="home-item-img mood-song-image" loading="eager" decoding="async" onload="this.classList.add('is-loaded')" onerror="this.onerror=null;this.src='${FALLBACK_IMG}';this.classList.add('is-loaded')">
        <div class="home-item-text"><div class="home-item-title">${escHtml(title)}</div><div class="home-item-subtitle">${escHtml(artist)}</div></div>
        <button class="home-play-btn" type="button" aria-label="Play ${escHtml(title)}"><svg class="home-play-glyph" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><polygon points="7,4 20,12 7,20"/></svg></button>`;
      const play = event => {
        event.preventDefault();
        event.stopPropagation();
        if (window.playResult) window.playResult(track, false, true);
      };
      row.addEventListener('click', play);
      row.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') play(event);
      });
      row.querySelector('.home-play-btn').addEventListener('click', play);
      content.appendChild(row);
    });
    shelf.appendChild(content);
    const left = shelf.querySelector('.mood-songs-left');
    const right = shelf.querySelector('.mood-songs-right');
    const updateButtons = () => {
      const maxScroll = Math.max(0, content.scrollWidth - content.clientWidth);
      left.disabled = content.scrollLeft <= 1;
      right.disabled = content.scrollLeft >= maxScroll - 1;
    };
    left.addEventListener('click', () => content.scrollBy({ left: -Math.max(280, content.clientWidth * .8), behavior: 'smooth' }));
    right.addEventListener('click', () => content.scrollBy({ left: Math.max(280, content.clientWidth * .8), behavior: 'smooth' }));
    content.addEventListener('scroll', updateButtons, { passive: true });
    requestAnimationFrame(updateButtons);
    body.appendChild(shelf);
    return true;
  }

  function renderFeaturedPlaylists(body, playlists, title) {
    if (!Array.isArray(playlists) || !playlists.length) return false;
    const section = document.createElement('section');
    section.className = 'explore-section mood-featured-playlists';
    section.innerHTML = `
      <div class="explore-section-header">
        <h2 class="explore-section-title">${escHtml(title || 'Featured playlists')}</h2>
        <div class="home-shelf-scroll-btns">
          <button class="home-scroll-btn featured-left" type="button" aria-label="Previous featured playlists"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg></button>
          <button class="home-scroll-btn featured-right" type="button" aria-label="Next featured playlists"><svg viewBox="0 0 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg></button>
        </div>
      </div>`;
    const grid = document.createElement('div');
    grid.className = 'explore-grid explore-grid--featured';
    section.appendChild(grid);
    let start = 0;
    const visibleItems = 12;
    const rowSize = () => window.matchMedia('(max-width: 620px)').matches ? 2 :
      (window.matchMedia('(max-width: 1050px)').matches ? 4 : 6);
    const renderPage = direction => {
      grid.replaceChildren();
      playlists.slice(start, start + visibleItems).forEach(item => grid.appendChild(renderCard(item, true)));
      const maxStart = Math.max(0, playlists.length - visibleItems);
      section.querySelector('.featured-left').disabled = start === 0;
      section.querySelector('.featured-right').disabled = start >= maxStart;
      if (direction) {
        grid.classList.remove('featured-slide-left', 'featured-slide-right');
        requestAnimationFrame(() => grid.classList.add(direction === 'next' ? 'featured-slide-left' : 'featured-slide-right'));
      }
    };
    section.querySelector('.featured-left').addEventListener('click', () => {
      start = Math.max(0, start - rowSize());
      renderPage('previous');
    });
    section.querySelector('.featured-right').addEventListener('click', () => {
      start = Math.min(Math.max(0, playlists.length - visibleItems), start + rowSize());
      renderPage('next');
    });
    renderPage();
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
          <button class="explore-mood-arrow" type="button" aria-label="Previous moods and genres">‹</button>
          <button class="explore-mood-arrow" type="button" aria-label="Next moods and genres">›</button>
        </div>
      </div>`;
    const grid = document.createElement('div');
    grid.className = 'explore-mood-grid';
    section.appendChild(grid);
    let start = 0;
    const visibleItems = 24;
    const rowSize = () => window.matchMedia('(max-width: 620px)').matches ? 2 :
      (window.matchMedia('(max-width: 1050px)').matches ? 4 : 6);
    const renderPage = (direction) => {
      grid.replaceChildren();
      moods.slice(start, start + visibleItems).forEach((mood, index) => {
        const button = document.createElement('button');
        button.className = 'explore-mood-card';
        button.type = 'button';
        button.style.setProperty('--mood-accent', MOOD_ACCENTS[(start + index) % MOOD_ACCENTS.length]);
        button.textContent = mood.title || 'Mood';
        button.addEventListener('click', () => {
          if (window.preloadNavigateMood) window.preloadNavigateMood(mood.params, mood.title || 'Moods and genres');
          else window.navigateTo('#mood/' + encodeURIComponent(mood.params) + '?title=' + encodeURIComponent(mood.title || 'Moods and genres'));
        });
        grid.appendChild(button);
      });
      const arrows = section.querySelectorAll('.explore-mood-arrow');
      const maxStart = Math.max(0, moods.length - visibleItems);
      arrows[0].disabled = start === 0;
      arrows[1].disabled = start >= maxStart;
      if (direction) {
        grid.classList.remove('mood-grid-slide-left', 'mood-grid-slide-right');
        requestAnimationFrame(() => grid.classList.add(direction === 'next' ? 'mood-grid-slide-left' : 'mood-grid-slide-right'));
      }
    };
    section.querySelectorAll('.explore-mood-arrow')[0].addEventListener('click', () => {
      start = Math.max(0, start - rowSize());
      renderPage('previous');
    });
    section.querySelectorAll('.explore-mood-arrow')[1].addEventListener('click', () => {
      start = Math.min(Math.max(0, moods.length - visibleItems), start + rowSize());
      renderPage('next');
    });
    renderPage();
    body.appendChild(section);
    return true;
  }

  async function openMoodPage(params, title) {
    const overlay = document.getElementById('mood-modal-overlay');
    const body = document.getElementById('mood-modal-body');
    const heading = document.getElementById('mood-modal-title');
    if (!overlay || !body || !params) return;
    overlay.classList.add('open');
    heading.textContent = title || 'Moods and genres';
    const route = '#mood/' + encodeURIComponent(params) + '?title=' + encodeURIComponent(title || 'Moods and genres');
    const cached = window.consumePreload ? window.consumePreload(route) : null;
    try {
      const result = cached || await window.api('/api/explore/moods/?params=' + encodeURIComponent(params) + '&title=' + encodeURIComponent(title || 'music'));
      body.innerHTML = '';
      const hasSongs = renderMoodSongs(body, result.songs || []);
      const hasPlaylists = renderFeaturedPlaylists(body, result.playlists || [], 'Featured playlists');
      const hasAlbums = renderFeaturedPlaylists(body, result.albums || [], 'Albums');
      if (!hasSongs && !hasPlaylists && !hasAlbums) {
        body.innerHTML = '<div class="explore-empty">No playlists are available for this mood or genre right now.</div>';
      }
    } catch (error) {
      if (window.toast) window.toast(`Couldn’t load ${title || 'this genre'}.`, 'error');
      window.navigateTo('#explore');
    }
  }

  function renderQuickNav(body, available) {
    const sections = [['New releases', 'new_releases'], ['Charts', 'charts'], ['Trending', 'trending'], ['Moods & genres', 'moods']]
      .filter(([, key]) => available.has(key));
    const icons = { new_releases: '✦', charts: '↗', trending: '⚡', moods: '◉' };
    if (!sections.length) return;
    const nav = document.createElement('nav');
    nav.className = 'explore-quick-nav';
    sections.forEach(([label, key]) => {
      const button = document.createElement('button');
      button.className = 'explore-quick-card';
      button.type = 'button';
      button.innerHTML = `<span class="explore-quick-icon" aria-hidden="true">${icons[key]}</span><span>${escHtml(label)}</span>`;
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
