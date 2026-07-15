(function () {
  'use strict';

  const state = window.__appState = window.__appState || {};
  const MOOD_ACCENTS = ['#ff8c3a', '#e80000', '#8a3ffc', '#ffe264', '#00a928', '#ffe264', '#b764ff', '#ff6500', '#00a9d7', '#9ebfff', '#b8b8b8', '#2d7cff', '#ffe264', '#8cff9b', '#666', '#ef62f5', '#9ff5a7', '#ff5700'];
  const FALLBACK_IMG = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23444'%3E%3Cpath d='M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z'/%3E%3C/svg%3E";
  let loaded = false;
  let loading = false;
  let cardContextMenu = null;
  const albumResolutionCache = new Map();

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

  function artistSubtitleHtml(item) {
    const artists = Array.isArray(item.artists) ? item.artists.filter(Boolean) : [];
    const names = artists.map(artist => typeof artist === 'string' ? artist : artist.name).filter(Boolean);
    const ids = artists.map(artist => typeof artist === 'object'
      ? (artist.id || artist.browseId || artist.channelId || artist.channel_id || '')
      : '');
    // Explore/genre feeds often provide credits only as description/subtitle
    // text. Treat that fallback as an artist credit too so it receives the
    // same link and hover behavior as structured artist arrays.
    const fallbackName = item.artist || item.author || subtitle(item) || '';
    const artistText = names.length ? names.join(', ') : fallbackName;
    if (!artistText) return '';
    const fallbackId = item.artistId || item.artist_id || item.channelId || item.channel_id || '';
    return window.artistLinksHtml
      ? window.artistLinksHtml(artistText, ids.some(Boolean) ? ids : fallbackId)
      : escHtml(artistText);
  }

  function openTrackAlbum(item) {
    const videoId = item.videoId || item.video_id || '';
    const album = item.album || {};
    const existingAlbumId = item.albumId || item.album_id || item.albumBrowseId ||
      (typeof album === 'object' && (album.id || album.browseId)) || '';
    const navigate = albumId => {
      if (!albumId) {
        if (window.toast) window.toast('Album unavailable for this song', 'error');
        return;
      }
      item.albumId = albumId;
      if (window.preloadNavigateAlbum) window.preloadNavigateAlbum(albumId);
      else window.navigateTo('#album/' + encodeURIComponent(albumId));
    };
    if (existingAlbumId) { navigate(existingAlbumId); return; }
    if (!videoId || typeof window.api !== 'function') { navigate(''); return; }
    if (!albumResolutionCache.has(videoId)) {
      albumResolutionCache.set(videoId,
        window.api('/api/album/resolve/' + encodeURIComponent(videoId))
          .then(details => (details && details.album_id) || '')
          .catch(() => ''));
    }
    albumResolutionCache.get(videoId).then(navigate);
  }

  function openItem(item) {
    const videoId = item.videoId || item.video_id;
    if (videoId && window.playResult) {
      window.playResult(Object.assign({}, item, { video_id: videoId }), true, false);
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
      window.playResult(Object.assign({}, item, { video_id: videoId }), true, false);
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

  function closeCardContextMenu() {
    if (cardContextMenu) cardContextMenu.classList.remove('open');
  }

  function openCardContextMenu(event, item) {
    event.preventDefault();
    event.stopPropagation();
    if (window._closeAllMoreMenus) window._closeAllMoreMenus();
    if (!cardContextMenu) {
      cardContextMenu = document.createElement('div');
      cardContextMenu.className = 'result-more-menu explore-context-menu';
      cardContextMenu.innerHTML =
        '<button type="button" class="result-menu-option" data-action="play"><svg viewBox="0 0 24 24" fill="currentColor"><polygon points="7,4 20,12 7,20"/></svg><span>Play</span></button>' +
        '<button type="button" class="result-menu-option" data-action="open"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6"/></svg><span>Open playlist</span></button>';
      document.body.appendChild(cardContextMenu);
      cardContextMenu.addEventListener('click', menuEvent => {
        const action = menuEvent.target.closest('[data-action]');
        const target = cardContextMenu._item;
        if (!action || !target) return;
        menuEvent.stopPropagation();
        closeCardContextMenu();
        if (action.dataset.action === 'play') playItem({ stopPropagation() {} }, target);
        if (action.dataset.action === 'open') openItem(target);
      });
    }
    const id = item.browseId || item.playlistId || item.albumId || item.audioPlaylistId || '';
    const isAlbum = item.type === 'Album' || String(id).startsWith('MPREb');
    cardContextMenu._item = item;
    cardContextMenu.querySelector('[data-action="open"] span').textContent = isAlbum ? 'Open album' : 'Open playlist';
    cardContextMenu.style.left = event.clientX + 'px';
    cardContextMenu.style.right = 'auto';
    cardContextMenu.style.top = event.clientY + 'px';
    cardContextMenu.style.bottom = 'auto';
    cardContextMenu.classList.add('open');
    const rect = cardContextMenu.getBoundingClientRect();
    if (rect.right > window.innerWidth - 8) {
      cardContextMenu.style.left = 'auto';
      cardContextMenu.style.right = Math.max(8, window.innerWidth - event.clientX) + 'px';
    }
    if (rect.bottom > window.innerHeight - 8) {
      cardContextMenu.style.top = 'auto';
      cardContextMenu.style.bottom = Math.max(8, window.innerHeight - event.clientY) + 'px';
    }
  }

  function renderCard(item, eager) {
    const title = item.title || item.name || 'Unknown';
    const thumb = imageUrl(item.thumbnails) || imageUrl(item.thumbnail) || imageUrl(item.images) || imageUrl(item.image);
    const cardSubtitle = subtitle(item) || item.artist || item.author ||
      (Array.isArray(item.artists) && item.artists.length ? item.artists.map(artist =>
        typeof artist === 'string' ? artist : artist.name).filter(Boolean).join(', ') : '');
    const card = document.createElement('article');
    card.className = 'explore-card' + ((item.type === 'Album' || String(item.browseId || item.albumId || '').startsWith('MPREb')) ? ' album-card' : '');
    card.tabIndex = 0;
    card.setAttribute('role', 'button');
    card.setAttribute('aria-label', `Open ${title}`);
    const itemType = String(item.type || item.resultType || '').toLowerCase();
    // Songs often include an audioPlaylistId as a radio/playback seed. It is
    // not a playlist entity and must never change the card's click/menu type.
    const isPlaylist = itemType === 'playlist' || itemType === 'community playlist';
    const playlistId = isPlaylist
      ? (item.playlistId || item.playlist_id || item.audioPlaylistId || item.browseId || item.browse_id || '')
      : '';
    if (playlistId) {
      card.dataset.playlistContext = playlistId;
      card.dataset.playlistTitle = title;
    }
    card.innerHTML = `
      <div class="explore-card-art">
        <img src="${escHtml(thumb || FALLBACK_IMG)}" alt="${escHtml(title)}" class="explore-card-image" loading="${eager ? 'eager' : 'lazy'}" decoding="async" onload="this.classList.add('is-loaded')" onerror="this.onerror=null;this.src='${FALLBACK_IMG}';this.classList.add('is-loaded')">
        <button class="home-play-btn explore-card-play" type="button" aria-label="Play ${escHtml(title)}">
          <svg class="home-play-glyph" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><polygon points="7,4 20,12 7,20"/></svg>
        </button>
      </div>
      <div class="explore-card-info">
        <div class="explore-card-title">${escHtml(title)}</div>
        ${cardSubtitle ? `<div class="explore-card-sub">${artistSubtitleHtml(item)}</div>` : ''}
      </div>`;
    if (window.wireArtistLinks) window.wireArtistLinks(card);
    card.querySelector('.explore-card-title').addEventListener('click', event => {
      if (!(item.videoId || item.video_id)) return;
      event.preventDefault();
      event.stopPropagation();
      openTrackAlbum(item);
    });
    card.addEventListener('click', event => {
      if (event.target.closest('.artist-name')) return;
      openItem(item);
    });
    card.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openItem(item); }
    });
    card.addEventListener('contextmenu', event => {
      const videoId = item.videoId || item.video_id;
      if (videoId && window.openSongContextMenu) {
        const track = Object.assign({}, item, {
          video_id: videoId,
          title: title,
          artist: subtitle(item),
          thumbnail: thumb || '',
          album_id: item.albumId || item.album_id || '',
          artist_id: item.channelId || item.channel_id || item.artistId || ''
        });
        event.preventDefault();
        event.stopPropagation();
        window.openSongContextMenu(event, track);
      } else {
        if (playlistId && window.openPlaylistContextMenu) {
          window.openPlaylistContextMenu(event, { id: playlistId, title: title });
        } else {
          openCardContextMenu(event, item);
        }
      }
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
          <button class="home-scroll-btn explore-scroll-btn explore-scroll-left" type="button" aria-label="Scroll ${escHtml(title)} left"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg></button>
          <button class="home-scroll-btn explore-scroll-btn explore-scroll-right" type="button" aria-label="Scroll ${escHtml(title)} right"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg></button>
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
      const artists = Array.isArray(song.artists) ? song.artists.filter(Boolean) : [];
      const artistNames = artists.map(artist => typeof artist === 'string' ? artist : artist.name).filter(Boolean);
      const artistIds = artists.map(artist => typeof artist === 'object'
        ? (artist.id || artist.browseId || artist.channelId || artist.channel_id || '')
        : '');
      const artist = artistNames.length ? artistNames.join(', ') : subtitle(song);
      const fallbackArtistId = song.artistId || song.artist_id || song.channelId || song.channel_id || '';
      const artistHtml = window.artistLinksHtml
        ? window.artistLinksHtml(artist, artistIds.some(Boolean) ? artistIds : fallbackArtistId)
        : escHtml(artist);
      const thumbnail = imageUrl(song.thumbnails) || imageUrl(song.thumbnail) || FALLBACK_IMG;
      const track = {
        video_id: song.videoId || song.video_id,
        title: title,
        artist: artist,
        thumbnail: thumbnail,
        albumId: song.albumId || song.album_id || song.albumBrowseId || '',
        album: song.album || null,
        artist_id: artistIds.find(Boolean) || fallbackArtistId,
        channelId: artistIds.find(Boolean) || fallbackArtistId
      };
      const row = document.createElement('article');
      row.className = 'home-item home-item-song';
      row.dataset.videoId = track.video_id;
      row._songContextTrack = track;
      row.tabIndex = 0;
      row.setAttribute('role', 'button');
      row.setAttribute('aria-label', `Play ${title}`);
      row.innerHTML = `
        <img src="${escHtml(thumbnail)}" alt="${escHtml(title)}" class="home-item-img mood-song-image" loading="eager" decoding="async" onload="this.classList.add('is-loaded')" onerror="this.onerror=null;this.src='${FALLBACK_IMG}';this.classList.add('is-loaded')">
        <div class="home-item-text"><div class="home-item-title">${escHtml(title)}</div><div class="home-item-subtitle">${artistHtml}</div></div>
        <button class="home-play-btn" type="button" aria-label="Play ${escHtml(title)}"><svg class="home-play-glyph" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><polygon points="7,4 20,12 7,20"/></svg></button>`;
      // Desktop text opens its album. Mobile is touch-first: title/artist text
      // plays the song like the rest of the row. Register before artist links
      // so this rule wins for artist-credit taps on desktop.
      const openAlbumFromText = event => {
        event.preventDefault();
        event.stopImmediatePropagation();
        if (window.matchMedia && window.matchMedia('(max-width: 899px)').matches) {
          play(event);
          return;
        }
        openTrackAlbum(track);
      };
      row.querySelector('.home-item-title').addEventListener('click', openAlbumFromText);
      row.querySelectorAll('.home-item-subtitle .artist-name').forEach(artistLink => {
        artistLink.addEventListener('click', openAlbumFromText);
      });
      if (window.wireArtistLinks) window.wireArtistLinks(row);
      const play = event => {
        event.preventDefault();
        event.stopPropagation();
        if (window.playResult) window.playResult(track, true, false);
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
    section.className = 'explore-section mood-featured-playlists' +
      (title === 'Albums' ? ' mood-albums-playlists' : '');
    section.innerHTML = `
      <div class="explore-section-header">
        <h2 class="explore-section-title">${escHtml(title || 'Featured playlists')}</h2>
        <div class="home-shelf-scroll-btns">
          <button class="home-scroll-btn featured-left" type="button" aria-label="Previous featured playlists"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg></button>
          <button class="home-scroll-btn featured-right" type="button" aria-label="Next featured playlists"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg></button>
        </div>
      </div>`;
    const grid = document.createElement('div');
    grid.className = 'explore-grid explore-grid--featured';
    section.appendChild(grid);
    let dragStartX = 0;
    let dragScrollLeft = 0;
    let suppressCardClick = false;
    grid.addEventListener('pointerdown', event => {
      if (!window.matchMedia('(max-width: 899px)').matches) return;
      dragStartX = event.clientX;
      dragScrollLeft = grid.scrollLeft;
    });
    grid.addEventListener('pointermove', event => {
      if (!dragStartX || !window.matchMedia('(max-width: 899px)').matches) return;
      const distance = event.clientX - dragStartX;
      if (Math.abs(distance) > 6) {
        grid.scrollLeft = dragScrollLeft - distance;
        suppressCardClick = true;
      }
    });
    grid.addEventListener('pointerup', () => {
      dragStartX = 0;
      setTimeout(() => { suppressCardClick = false; }, 0);
    });
    grid.addEventListener('pointercancel', () => { dragStartX = 0; });
    grid.addEventListener('click', event => {
      if (!suppressCardClick) return;
      event.preventDefault();
      event.stopPropagation();
      suppressCardClick = false;
    }, true);
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
          <button class="home-scroll-btn explore-mood-arrow" type="button" aria-label="Previous moods and genres"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="15 18 9 12 15 6"/></svg></button>
          <button class="home-scroll-btn explore-mood-arrow" type="button" aria-label="Next moods and genres"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg></button>
        </div>
      </div>`;
    const viewport = document.createElement('div');
    viewport.className = 'explore-mood-viewport';
    let grid = document.createElement('div');
    grid.className = 'explore-mood-grid';
    viewport.appendChild(grid);
    section.appendChild(viewport);
    let start = 0;
    let sliding = false;
    const visibleItems = 24;
    // The grid is row-major, so advancing one item moves the visible tiles
    // by one visual column. Do not jump a whole row on every arrow click.
    const columnStep = 1;
    const fillGrid = (target, pageStart) => {
      const mobile = window.matchMedia('(max-width: 899px)').matches;
      const items = mobile ? moods : moods.slice(pageStart, pageStart + visibleItems);
      items.forEach((mood, index) => {
        const button = document.createElement('button');
        button.className = 'explore-mood-card';
        button.type = 'button';
        button.style.setProperty('--mood-accent', MOOD_ACCENTS[(pageStart + index) % MOOD_ACCENTS.length]);
        button.textContent = mood.title || 'Mood';
        button.addEventListener('click', () => {
          if (window.preloadNavigateMood) window.preloadNavigateMood(mood.params, mood.title || 'Moods and genres');
          else window.navigateTo('#mood/' + encodeURIComponent(mood.params) + '?title=' + encodeURIComponent(mood.title || 'Moods and genres'));
        });
        target.appendChild(button);
      });
    };
    const updateArrows = () => {
      const arrows = section.querySelectorAll('.explore-mood-arrow');
      const maxStart = Math.max(0, moods.length - visibleItems);
      arrows[0].disabled = start === 0;
      arrows[1].disabled = start >= maxStart;
    };
    const renderPage = () => {
      grid.replaceChildren();
      fillGrid(grid, start);
      updateArrows();
    };
    const slidePage = (direction) => {
      const maxStart = Math.max(0, moods.length - visibleItems);
      const nextStart = direction === 'next'
        ? Math.min(maxStart, start + columnStep)
        : Math.max(0, start - columnStep);
      if (sliding || nextStart === start) return;
      sliding = true;
      start = nextStart;
      const arrows = section.querySelectorAll('.explore-mood-arrow');
      arrows.forEach(arrow => { arrow.disabled = true; });

      const outgoing = grid;
      const incoming = document.createElement('div');
      incoming.className = 'explore-mood-grid mood-grid-incoming ' +
        (direction === 'next' ? 'mood-grid-enter-right' : 'mood-grid-enter-left');
      fillGrid(incoming, start);
      viewport.style.height = outgoing.getBoundingClientRect().height + 'px';
      // Advance exactly one visual column. Using the measured tile width keeps
      // the transition aligned at every responsive breakpoint.
      const columns = window.matchMedia('(max-width: 899px)').matches ? 4 : 6;
      const styles = window.getComputedStyle(outgoing);
      const gap = parseFloat(styles.columnGap || styles.gap) || 0;
      const slideDistance = (outgoing.clientWidth - gap * (columns - 1)) / columns + gap;
      viewport.style.setProperty('--mood-slide-distance', slideDistance + 'px');
      viewport.appendChild(incoming);

      requestAnimationFrame(() => {
        outgoing.classList.add(direction === 'next' ? 'mood-grid-exit-left' : 'mood-grid-exit-right');
        incoming.classList.remove(direction === 'next' ? 'mood-grid-enter-right' : 'mood-grid-enter-left');
      });
      window.setTimeout(() => {
        outgoing.remove();
        incoming.classList.remove('mood-grid-incoming');
        viewport.style.height = '';
        viewport.style.removeProperty('--mood-slide-distance');
        grid = incoming;
        sliding = false;
        updateArrows();
      }, 300);
    };
    section.querySelectorAll('.explore-mood-arrow')[0].addEventListener('click', () => {
      slidePage('previous');
    });
    section.querySelectorAll('.explore-mood-arrow')[1].addEventListener('click', () => {
      slidePage('next');
    });

    let dragStartX = 0;
    let dragActive = false;
    let suppressMoodClick = false;
    viewport.addEventListener('pointerdown', event => {
      if (!window.matchMedia('(max-width: 899px)').matches) return;
      dragStartX = event.clientX;
      dragActive = false;
    });
    viewport.addEventListener('pointermove', event => {
      if (!window.matchMedia('(max-width: 899px)').matches || !dragStartX) return;
      if (Math.abs(event.clientX - dragStartX) > 8) dragActive = true;
    });
    viewport.addEventListener('pointerup', event => {
      if (!window.matchMedia('(max-width: 899px)').matches || !dragStartX) return;
      const distance = event.clientX - dragStartX;
      if (Math.abs(distance) >= 8) {
        suppressMoodClick = true;
        window.setTimeout(() => { suppressMoodClick = false; }, 0);
        dragStartX = 0;
        dragActive = false;
        return;
      }
      if (Math.abs(distance) >= 40) {
        suppressMoodClick = true;
        slidePage(distance < 0 ? 'next' : 'previous');
        window.setTimeout(() => { suppressMoodClick = false; }, 0);
      } else if (dragActive) {
        suppressMoodClick = true;
        window.setTimeout(() => { suppressMoodClick = false; }, 0);
      }
      dragStartX = 0;
      dragActive = false;
    });
    viewport.addEventListener('pointercancel', () => {
      dragStartX = 0;
      dragActive = false;
    });
    viewport.addEventListener('click', event => {
      if (!suppressMoodClick) return;
      event.preventDefault();
      event.stopPropagation();
      suppressMoodClick = false;
    }, true);
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
      const hasFeaturedPlaylists = renderFeaturedPlaylists(
        body, result.featured_playlists || result.playlists || [], 'Featured playlists'
      );
      const hasCommunityPlaylists = renderFeaturedPlaylists(
        body, result.community_playlists || [], 'Community playlists'
      );
      const hasAlbums = renderFeaturedPlaylists(body, result.albums || [], 'Albums');
      if (!hasSongs && !hasFeaturedPlaylists && !hasCommunityPlaylists && !hasAlbums) {
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
    const preloaded = !force && window.consumePreload && window.consumePreload('#explore');
    if (!preloaded) body.innerHTML = '<div class="loading-spinner" role="status" aria-label="Loading"></div>';
    try {
      const explore = preloaded || await window.api('/api/explore/');
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
      console.error('[explore] Failed to load Explore', error);
    } finally {
      loading = false;
    }
  }

  window.openExplorePage = function (force) {
    const overlay = document.getElementById('explore-modal-overlay');
    if (overlay && window.matchMedia('(min-width: 900px)').matches) overlay.classList.add('open');
    loadExplore(force);
  };
  window.closeExplorePage = function () {
    const overlay = document.getElementById('explore-modal-overlay');
    if (overlay) overlay.classList.remove('open');
  };
  window.openMoodPage = openMoodPage;
  window.closeExploreCardContextMenu = closeCardContextMenu;
  document.addEventListener('click', event => {
    if (event.target.closest('#explore-modal-close')) window.navigateTo('#home');
  });
}());
