(function () {
  'use strict';
  const state = window.__appState = window.__appState || {};
  if (state._resultsOpen === undefined) state._resultsOpen = false;
  if (state._currentVideoId === undefined) state._currentVideoId = '';
  if (state._hasTrack === undefined) state._hasTrack = false;
  if (state._searchCategorized === undefined) state._searchCategorized = {};
  if (state._activeCategory === undefined) state._activeCategory = 'songs';
  if (state._resultsPage === undefined) state._resultsPage = {};
  if (state._searchSeq === undefined) state._searchSeq = 0;

const RESULTS_PER_PAGE = 10;

async function runSearch(query) {
  const mySeq = ++state._searchSeq;
  if (window.startTopProgress) window.startTopProgress();
  toast('Searching \u201c' + query + '\u201d\u2026');
  try {
    const data = await api('/alexa/search/?q=' + encodeURIComponent(query));
    if (mySeq !== state._searchSeq) return;   // a newer search won
    if (window.completeTopProgress) window.completeTopProgress();
    state._searchCategorized = data || {};
    const totalItems = (data.songs?.length || 0) + (data.artists?.length || 0) + (data.albums?.length || 0) + (data.playlists?.length || 0);
    if (!totalItems) { toast('No results found.', 'error'); return; }
    state._resultsPage = { songs: 0, artists: 0, albums: 0, playlists: 0 };
    state._activeCategory = window.JAM_GUEST ? 'songs' : 'all';
    document.querySelectorAll('.results-tab').forEach(t => t.classList.toggle('active', t.dataset.category === state._activeCategory));
    renderResults();
    openResults();
    // On mobile, collapse the expanded search panel once results are ready.
    // Desktop has no mobile-search-open class, so its layout stays unchanged.
    document.body.classList.remove('mobile-search-open');
    toast(totalItems + ' results', 'ok');
  } catch (e) {
    if (mySeq === state._searchSeq) {
      if (window.abortTopProgress) window.abortTopProgress();
      toast(e.message, 'error');
    }
  }
}

function openResults() {
  // Search results live on the Home route. Return there from every routed
  // surface so its overlay closes before results become visible.
  if (window.getRoute && window.navigateTo && window.getRoute() !== '#home') {
    window.navigateTo('#home');
  }
  const section = document.getElementById('results-section');
  // The queue column collapses while results are showing; the compact player
  // takes over at the bottom.
  const mainEl = document.querySelector('main');
  const queueSection = document.getElementById('queue-section');
  clearTimeout(section._hideTimer);
  clearTimeout(section._showTimer);
  // Views swap, they don't stack. We hide the underlying page content so the search results
  // behave as a standalone page instead of a side column or popup overlay.
  const viewsToHide = ['home-section', 'jam-home-section', 'recs-section', 'artist-section'];
  viewsToHide.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.hidden = true;
  });
  // Same for the playlist detail view: it sits above the content area (z-210),
  // so results rendered behind it would be invisible until manually closed.
  {
    const ov = document.getElementById('playlist-detail-modal-overlay');
    if (ov) ov.classList.remove('open');
  }
  animatePlaySectionLayout(() => {
    state._resultsOpen = true;
    mainEl.classList.remove('has-queue');
    queueSection.classList.remove('is-visible');
    queueSection.hidden = true;
    section.hidden = false;
    syncUiState();
  });
  section._showTimer = setTimeout(() => {
    requestAnimationFrame(() => {
      if (state._resultsOpen && !section.hidden) section.classList.add('is-visible');
    });
  }, 120);
}

function playSearchPlaylist(playlistId) {
  playlistId = String(playlistId || '').trim();
  if (!playlistId || !window.api) return;
  // Search may expose the Music browse id (VL...) instead of the underlying
  // playlist id expected by the playback endpoint.
  if (playlistId.indexOf('VL') === 0) playlistId = playlistId.slice(2);

  const serial = window.selectedSerial ? window.selectedSerial() : '';
  if (!serial) {
    if (window.toast) window.toast('Select an Alexa device before playing', 'warning');
    return;
  }

  const request = window.playCollection
    ? window.playCollection([], { playlistId: playlistId, openPlaybackPage: true })
    : window.api('/alexa/play/', {
        serial: serial,
        query: 'https://music.youtube.com/playlist?list=' + playlistId
      });
  request.catch(function (err) {
    if (window.toast) {
      window.toast((err && err.message) || 'Could not start playlist playback', 'error');
    }
  });
}

function closeResults() {
  if (!state._resultsOpen) return;
  const section = document.getElementById('results-section');
  // Fade the results panel out smoothly, then collapse it and show the queue.
  section.classList.remove('is-visible');
  clearTimeout(section._showTimer);
  clearTimeout(section._hideTimer);
  document.body.style.removeProperty('--search-theme-image');
  document.body.classList.remove('search-art-themed');

  // If a queue will be restored, pre-add has-queue BEFORE removing results-open
  // so the grid columns stay at 1fr 1fr (no shrink-then-expand bounce).
  let queue = [];
  try { queue = JSON.parse(state._lastQueueJson || '[]'); } catch (_) {}
  const willShowQueue = queue.length > 1;
  if (willShowQueue) {
    document.querySelector('main').classList.add('has-queue');
  }

  // Wait for the CSS opacity/transform transition (~280ms) before hiding.
  section._hideTimer = setTimeout(() => {
    animatePlaySectionLayout(() => {
      state._resultsOpen = false;
      section.hidden = true;
      syncUiState();
      if (typeof window.renderRoute === 'function') window.renderRoute();
      // Replay the player's reveal animation so enlarging from the compact player
      // slides the full player in instead of popping it.
      if (state._hasTrack) {
        const player = document.querySelector('.player-section');
        player.classList.remove('is-collapsed');
        player.classList.remove('is-visible');
        void player.offsetHeight;
        player.classList.add('is-visible');
      }
      // Bring the queue panel back after results have faded out.
      try { showQueue(queue, state._lastQueueIndex); } catch (_) {}
    });
  }, 300);
}

function _categoryTitle(item, cat) {
  if (cat === 'artists') return item.name || '';
  if (cat === 'albums') return item.title || '';
  if (cat === 'playlists') return item.title || '';
  return '';
}

function _createSongElement(item, existingThumbsById) {
    const wrapper = document.createElement('div');
    wrapper.className = 'result-swipe-wrapper';
    wrapper.dataset.videoId = item.video_id;
    wrapper._songContextTrack = item;

    wrapper.innerHTML = `
      <div class="result-swipe-underlay underlay-play-next">
        <svg viewBox="0 0 24 24" fill="currentColor"><path d="M4 5v14l11-7L4 5zm13 0v14h3V5h-3z"/></svg>
        Play next
      </div>
      <div class="result-swipe-underlay underlay-add-queue">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
        Add to queue
      </div>
    `;

    const isCurrent = item.video_id === state._currentVideoId;
    const inner = document.createElement('div');
    inner.className = 'result-item-inner' + (isCurrent ? ' active' : '');

    const reusableImg = item.thumbnail && existingThumbsById && existingThumbsById.get(item.video_id);
    const sameUrl = reusableImg && reusableImg.src === item.thumbnail;
    const thumbHtml = sameUrl
      ? `<div class="result-thumb-slot"></div>`
      : item.thumbnail
        ? `<img class="result-thumb" src="${escHtml(item.thumbnail)}" alt="" loading="lazy" onload="this.classList.add('loaded')">`
        : `<div class="result-thumb"></div>`;

    const queueAddSvg = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>`;
    const moreSvg = `<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>`;

    const isLiked = typeof _playlistsData !== 'undefined' && _playlistsData.liked_songs && _playlistsData.liked_songs.includes(item.video_id);
    const duration = window.formatTrackDuration ? window.formatTrackDuration(item) : '';
    const heartSvg = isLiked 
      ? `<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>`
      : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>`;

    inner.innerHTML = `
      ${thumbHtml}
      <div class="result-info">
        <div class="result-title">${escHtml(item.title)}</div>
        <div class="result-artist">${window.artistLinksHtml(item.artist, item.channelId || item.channel_id || '')}</div>
      </div>
      ${duration ? `<span class="track-duration">${escHtml(duration)}</span>` : ''}
      <button class="result-like-btn ${isLiked ? 'liked' : ''}" type="button" title="Like" data-vid="${escHtml(item.video_id)}">${heartSvg}</button>
      <button class="result-queue-btn" type="button" title="Add to queue" ${isCurrent ? 'hidden' : ''}>${queueAddSvg}</button>
      <button class="result-more-btn" type="button" title="More options">${moreSvg}</button>
      <div class="result-more-menu">
        <div class="result-menu-option" data-action="play-next" ${isCurrent ? 'hidden' : ''}>
          <svg viewBox="0 0 24 24" fill="currentColor"><path d="M4 5v14l11-7L4 5zm13 0v14h3V5h-3z"/></svg>
          Play next
        </div>
        <div class="result-menu-option" data-action="add-to-queue" ${isCurrent ? 'hidden' : ''}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
          Add to queue
        </div>
        <div class="result-menu-option" data-action="play-radio">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4.93 19.07A10 10 0 1 1 19.07 4.93 10 10 0 0 1 4.93 19.07z"/><circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="M2 12h2"/><path d="M20 12h2"/></svg>
          Play Radio
        </div>
        <div class="result-menu-option" data-action="save-playlist">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path><line x1="12" y1="11" x2="12" y2="17"></line><line x1="9" y1="14" x2="15" y2="14"></line></svg>
          Save to Playlist
        </div>
      </div>
    `;

    if (sameUrl) inner.querySelector('.result-thumb-slot').replaceWith(reusableImg);
    wrapper.appendChild(inner);

    // Artist clicks navigate independently instead of triggering the row's
    // play action. The shared helper also resolves artists that lack an id.
    if (window.wireArtistLinks) window.wireArtistLinks(inner);

    attachQueueItemTap(inner, () => {
      for (const other of wrapper.parentElement.querySelectorAll('.result-item-inner.active')) other.classList.remove('active');
      inner.classList.add('active');
      playResult(item, false, false, true);
    });

    const qBtn = inner.querySelector('.result-queue-btn');
    qBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      addToQueue(item, 'last');
    });

    const moreBtn = inner.querySelector('.result-more-btn');
    const moreMenu = inner.querySelector('.result-more-menu');
    moreMenu.addEventListener('click', (e) => e.stopPropagation());
    moreBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      // Right-click and the visible 3-dot control intentionally share the
      // same canonical song menu and option order.
      if (window.openSongContextMenu) {
        window.openSongContextMenu(e, item);
      }
      // Never fall back to Search's older, shorter popup.  The shared menu
      // is loaded before this script and is also used by right-click.
      return;
      const wasOpen = moreMenu.classList.contains('open');
      _closeAllMoreMenus();
      if (!wasOpen) {
        moreBtn.classList.add('open');
        const rect = moreBtn.getBoundingClientRect();
        const menuHeight = 132;
        const menuWidth = 180;
        
        let x = e && e.clientX ? e.clientX : rect.right - menuWidth;
        let y = e && e.clientY ? e.clientY : rect.bottom;
        
        const spaceBelow = window.innerHeight - y;
        const spaceRight = window.innerWidth - x;
        const openAbove = spaceBelow < menuHeight + 8;
        
        if (spaceRight < menuWidth + 8) {
           moreMenu.style.left = 'auto';
           moreMenu.style.right = (window.innerWidth - x) + 'px';
        } else {
           moreMenu.style.left = x + 'px';
           moreMenu.style.right = 'auto';
        }
        
        if (openAbove) {
           moreMenu.style.top = 'auto';
           moreMenu.style.bottom = (window.innerHeight - y + 4) + 'px';
        } else {
           moreMenu.style.top = (y + 4) + 'px';
           moreMenu.style.bottom = 'auto';
        }
        moreMenu._home = wrapper;
        document.body.appendChild(moreMenu);
        void moreMenu.offsetWidth;
        moreMenu.classList.add('open');
      }
    });

    return wrapper;
}

function renderResults() {
  const list = document.getElementById('results-list');
  const sectionHead = document.querySelector('#results-section .section-head');
  const data = state._searchCategorized || {};
  
  if (!sectionHead.querySelector('.results-tabs')) {
    sectionHead.innerHTML = `
      <div class="results-tabs">
        <button class="results-tab" data-category="all">All</button>
        <button class="results-tab" data-category="songs">Songs</button>
        <button class="results-tab" data-category="artists">Artists</button>
        <button class="results-tab" data-category="albums">Albums</button>
        <button class="results-tab" data-category="playlists">Playlists</button>
      </div>
    `;
    sectionHead.querySelectorAll('.results-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        const category = tab.dataset.category;
        if (category === state._activeCategory) return;
        state._activeCategory = category;
        sectionHead.querySelectorAll('.results-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        renderResults();
        document.getElementById('results-section').scrollTop = 0;
      });
    });
  }
  
  sectionHead.querySelectorAll('.results-tab').forEach(t => t.classList.toggle('active', t.dataset.category === state._activeCategory));

  const existingThumbsById = new Map();
  for (const img of list.querySelectorAll('img.result-thumb.loaded')) {
    const w = img.closest('.result-swipe-wrapper');
    if (w && w.dataset.videoId && !existingThumbsById.has(w.dataset.videoId)) {
      existingThumbsById.set(w.dataset.videoId, img);
    }
  }

  list.innerHTML = '';
  _closeAllMoreMenus();

  function renderSearchRow(title, items, type) {
    if (!items || !items.length) return;
    const section = document.createElement('div');
    section.className = 'hscroll-section';
    section.style.marginTop = 'var(--space-5)';
    
    section.innerHTML = `
      <div class="section-head">
        <div class="label">${escHtml(title)}</div>
        <div class="hscroll-controls">
          <button type="button" class="hscroll-scroll hscroll-scroll-prev" disabled><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg></button>
          <button type="button" class="hscroll-scroll hscroll-scroll-next"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg></button>
        </div>
      </div>
      <div class="hscroll-track"></div>
    `;
    
    const track = section.querySelector('.hscroll-track');
    const albumPlayBtnHtml = '<button type="button" class="hscroll-play-btn home-play-btn search-album-play" title="Play"><svg class="home-play-glyph" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><polygon points="7,4 20,12 7,20"/></svg></button>';
    // Keep playlist cards visually and behaviorally aligned with homepage
    // playlist tiles instead of the search-specific album play treatment.
    const playlistPlayBtnHtml = '<button type="button" class="home-play-btn search-playlist-play" title="Play"><svg class="home-play-glyph" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><polygon points="7,4 20,12 7,20"/></svg></button>';
    const isAlbumOrPlaylist = type === 'album' || type === 'playlist';

    items.forEach(item => {
      const card = document.createElement('div');
      card.className = 'hscroll-card' + (type === 'album' ? ' album-card' : '');
      // The "all" tab maps items to camelCase browseId; the category-tab
      // endpoints return snake_case browse_id/playlist_id. Accept both.
      const browseId = type === 'playlist'
        ? (item.playlistId || item.playlist_id || item.browseId || item.browse_id || '')
        : (item.browseId || item.browse_id || item.playlistId || item.playlist_id || '');
      if (type === 'artist') {
        card.dataset.channelId = browseId;
      } else if (type === 'album') {
        card.dataset.albumId = browseId;
        card.dataset.videoId = item.video_id || '';
      } else if (type === 'playlist') {
        card.dataset.playlistId = browseId;
      }
      card.dataset.title = item.title || item.name || '';
      
      const thumb = item.thumbnails && item.thumbnails.length ? item.thumbnails[item.thumbnails.length - 1].url : item.thumbnail || '';
      const thumbHtml = thumb ? `<img src="${escHtml(thumb)}" alt="" loading="lazy" onload="this.classList.add('loaded')">` : '';
      
      const subtitle = (item.artist || item.owner || item.year || '');
      const subtitleHtml = subtitle ? `<div class="hscroll-card-artist">${type === 'album' && window.artistLinksHtml
        ? window.artistLinksHtml(subtitle, item.channelId || item.channel_id || item.artistChannelId || '')
        : escHtml(subtitle)}</div>` : '';
      
      const artClass = type === 'artist' ? 'hscroll-card-art round' : 'hscroll-card-art';

      card.innerHTML = `
        <div class="${artClass}">${thumbHtml}${type === 'album' ? albumPlayBtnHtml : type === 'playlist' ? playlistPlayBtnHtml : ''}</div>
        <div class="hscroll-card-title">${escHtml(item.title || item.name || '')}</div>
        ${subtitleHtml}
      `;
      if (type === 'album' && window.wireArtistLinks) window.wireArtistLinks(card);

      card.addEventListener('click', (e) => {
        if (e.target.closest('.artist-name')) {
           e.stopPropagation();
           return;
        }
        if (e.target.closest('.hscroll-play-btn, .search-playlist-play') && isAlbumOrPlaylist) {
           e.stopPropagation();
           if (!browseId) return;
           if (type === 'album') {
               window.api('/api/album/' + encodeURIComponent(browseId)).then(function(albumData) {
                 if (albumData && albumData.tracks && albumData.tracks.length && window.playFromQueue) {
                    if (window.playCollection) window.playCollection(albumData.tracks, { openPlaybackPage: true });
                    else window.playFromQueue(albumData.tracks[0], 0, true);
                 }
               });
           } else if (type === 'playlist') {
               playSearchPlaylist(browseId);
           }
        } else {
           if (type === 'artist') {
             if (browseId) {
               if (window.preloadNavigateArtist) window.preloadNavigateArtist(browseId);
               else window.navigateTo('#artist/' + encodeURIComponent(browseId));
             } else if (window.preloadNavigateArtistByName && (item.name || item.title)) {
               // Some search results come back without a channel id; resolve
               // the artist page by name instead of dropping the click.
               window.preloadNavigateArtistByName(item.name || item.title);
             }
           } else if (type === 'album' && browseId) {
             if (window.preloadNavigateAlbum) window.preloadNavigateAlbum(browseId);
             else window.navigateTo('#album/' + encodeURIComponent(browseId));
           } else if (type === 'playlist' && browseId) {
             if (window.preloadNavigatePlaylist) window.preloadNavigatePlaylist(browseId);
             else window.navigateTo('#playlist/' + encodeURIComponent(browseId));
           }
        }
      });
      track.appendChild(card);
    });

    const btnPrev = section.querySelector('.hscroll-scroll-prev');
    const btnNext = section.querySelector('.hscroll-scroll-next');
    function updateHscrollBtns() {
      const maxScroll = track.scrollWidth - track.clientWidth;
      const boundedMaxScroll = Math.max(0, maxScroll);
      btnPrev.disabled = track.scrollLeft <= 5;
      btnNext.disabled = track.scrollLeft >= boundedMaxScroll - 5;
    }
    track.addEventListener('scroll', updateHscrollBtns, { passive: true });
    window.addEventListener('resize', updateHscrollBtns, { passive: true });
    btnPrev.addEventListener('click', () => track.scrollBy({ left: -track.clientWidth * 0.8, behavior: 'smooth' }));
    btnNext.addEventListener('click', () => track.scrollBy({ left: track.clientWidth * 0.8, behavior: 'smooth' }));
    setTimeout(updateHscrollBtns, 100);

    list.appendChild(section);
  }

  function renderTopResultCard(item, topSongs) {
    const card = document.createElement('div');
    card.className = 'top-result-card ' + (item.resultType === 'artist' ? 'is-artist' : (item.resultType === 'album' ? 'is-album' : 'is-song'));
    
    let thumb = item.thumbnail || '';
    if (item.thumbnails && item.thumbnails.length) thumb = item.thumbnails[item.thumbnails.length - 1].url;
    
    const thumbHtml = thumb ? `<img src="${escHtml(thumb)}" alt="" loading="lazy">` : '';

    const topArtists = Array.isArray(item.artists) ? item.artists.filter(a => a && a.name) : [];
    const artistStr = topArtists.length ? topArtists.map(a => a.name).join(' and ') : (item.artist || '');
    const artistCredits = topArtists.length
      ? topArtists.map(a => {
          const artistId = a.id || a.browseId || a.channelId || '';
          const idAttr = artistId ? ` data-channel-id="${escHtml(artistId)}"` : '';
          return `<span class="artist-name" data-artist-name="${escHtml(a.name)}"${idAttr}>${escHtml(a.name)}</span>`;
        }).join(' and ')
      : (window.artistLinksHtml ? window.artistLinksHtml(artistStr, item.channelId || item.channel_id || '') : escHtml(artistStr));
    const topVideoId = item.videoId || item.video_id || '';
    const topPlaylistId = item.resultType === 'playlist'
      ? (item.playlistId || item.playlist_id || item.browseId || item.browse_id || '') : '';
    if (topPlaylistId) {
      card.dataset.playlistContext = topPlaylistId;
      card.dataset.playlistTitle = item.title || item.name || 'Playlist';
    }
    if (topVideoId) {
      card.dataset.videoId = topVideoId;
      card._songContextTrack = {
        video_id: topVideoId,
        title: item.title || '',
        artist: artistStr,
        thumbnail: thumb
      };
    }
    let subtitle = '';
    if (item.resultType === 'artist') {
      subtitle = 'Artist' + (item.subscribers ? ' • ' + item.subscribers : '');
    } else if (item.resultType === 'album') {
      subtitle = 'Album • ' + artistCredits;
    } else if (item.resultType === 'playlist') {
      subtitle = 'Playlist' + (artistStr ? ' • ' + artistCredits : '');
    } else {
      subtitle = 'Song • ' + artistCredits + (item.duration ? ' • ' + escHtml(item.duration) : '');
    }
    
    let rightSide = '';
    if (item.resultType === 'artist' && topSongs && topSongs.length) {
       rightSide = `<div class="top-result-songs"></div>`;
    }

    card.innerHTML = `
      <div class="top-result-main">
        <div class="top-result-art ${item.resultType === 'artist' ? 'round' : ''}">${thumbHtml}</div>
        <div class="top-result-info">
          <div class="top-result-title">${escHtml(item.title || item.name || (item.resultType === 'artist' ? artistStr : ''))}</div>
          <div class="top-result-subtitle">${subtitle}</div>
          <div class="top-result-actions">
            ${item.resultType === 'artist' 
               ? `<button class="btn btn-primary top-result-shuffle"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="16 3 21 3 21 8"></polyline><line x1="4" y1="20" x2="21" y2="3"></line><polyline points="21 16 21 21 16 21"></polyline><line x1="15" y1="15" x2="21" y2="21"></line><line x1="4" y1="4" x2="9" y2="9"></line></svg> Shuffle</button>
                  <button class="btn btn-secondary top-result-radio"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4.93 19.07A10 10 0 1 1 19.07 4.93 10 10 0 0 1 4.93 19.07z"/><circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="M2 12h2"/><path d="M20 12h2"/></svg> Radio</button>`
               : `<button class="btn btn-primary top-result-play"><svg viewBox="0 0 24 24" fill="currentColor"><polygon points="8,5 19,12 8,19"/></svg> Play</button>`
            }
          </div>
        </div>
      </div>
      ${rightSide}
    `;

    if (item.resultType === 'artist' && item.browseId) {
      card.querySelector('.top-result-main').addEventListener('click', (e) => {
        if (!e.target.closest('button')) {
          if (window.preloadNavigateArtist) window.preloadNavigateArtist(item.browseId);
          else window.navigateTo('#artist/' + encodeURIComponent(item.browseId));
        }
      });
      card.querySelector('.top-result-shuffle').addEventListener('click', () => {
         window.api('/api/artist/' + encodeURIComponent(item.browseId)).then(function(artistData) {
            if (artistData && artistData.songs && artistData.songs.length && window.playFromQueue) {
               window.playFromQueue(artistData.songs[Math.floor(Math.random() * artistData.songs.length)], 'shuffle', true);
            }
         });
      });
      card.querySelector('.top-result-radio').addEventListener('click', () => {
         window.playResult({ video_id: topSongs[0]?.video_id || '' }, false, true, true);
      });
    } else {
      // Non-artist top results: songs, videos, albums, playlists. YT Music
      // frequently marks the top result as resultType 'video' even for music,
      // so anything with a videoId is treated as playable.
      const playableId = item.videoId || item.video_id || '';
      const playItem = { video_id: playableId, title: item.title, artist: artistStr, thumbnail: thumb };
      const browseId = item.resultType === 'playlist'
        ? (item.playlistId || item.playlist_id || item.browseId || item.browse_id || '')
        : (item.browseId || item.browse_id || item.playlistId || item.playlist_id || '');

      function playTopResult() {
        if (item.resultType === 'album' && item.browseId) {
          window.api('/api/album/' + encodeURIComponent(item.browseId)).then(function (albumData) {
            if (albumData && albumData.tracks && albumData.tracks.length && window.playFromQueue) {
              if (window.playCollection) window.playCollection(albumData.tracks, { openPlaybackPage: true });
              else window.playFromQueue(albumData.tracks[0], 0, true);
            }
          });
        } else if (item.resultType === 'playlist' && browseId) {
          playSearchPlaylist(browseId);
        } else if (playableId) {
          window.playResult(playItem, false, false, true);
        }
      }

      const playBtn = card.querySelector('.top-result-play');
      if (playBtn) playBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        playTopResult();
      });

      card.querySelector('.top-result-main').addEventListener('click', (e) => {
        if (e.target.closest('button, .artist-name')) return;
        if (item.resultType === 'album' && item.browseId) {
          if (window.preloadNavigateAlbum) window.preloadNavigateAlbum(item.browseId);
          else window.navigateTo('#album/' + encodeURIComponent(item.browseId));
        } else if (item.resultType === 'playlist' && browseId) {
          if (window.preloadNavigatePlaylist) window.preloadNavigatePlaylist(browseId);
          else window.navigateTo('#playlist/' + encodeURIComponent(browseId));
        } else {
          playTopResult();
        }
      });
    }

    if (rightSide) {
       const songsContainer = card.querySelector('.top-result-songs');
       topSongs.forEach(song => {
          const songArtist = song.artist || (song.artists && song.artists.length ? song.artists.map(a => a.name).join(' and ') : '');
          const songItem = {
             video_id: song.videoId,
             title: song.title,
             artist: !songArtist || songArtist.trim().toLowerCase() === 'song' ? artistStr : songArtist,
             channelId: song.channelId || song.channel_id || (song.artists && song.artists[0] && (song.artists[0].id || song.artists[0].browseId)) || item.browseId || '',
             thumbnail: song.thumbnail || (song.thumbnails && song.thumbnails.length ? song.thumbnails[song.thumbnails.length-1].url : '')
          };
          const el = _createSongElement(songItem, existingThumbsById);
          songsContainer.appendChild(el);
       });
    }
    
    return card;
  }

  if (state._activeCategory === 'all' && data.all && data.all.length) {
     let topResult = null;
     let topSongs = [];
     let remaining = [];
     
     if (data.all[0].category === 'Top result' || data.all[0].resultType === 'artist' || data.all[0].resultType === 'song') {
        topResult = data.all[0];
        let idx = 1;
        if (topResult.resultType === 'artist') {
           while(idx < data.all.length && data.all[idx].resultType === 'song' && topSongs.length < 3) {
              topSongs.push(data.all[idx]);
              idx++;
           }
        }
        remaining = data.all.slice(idx);
     } else {
        remaining = data.all;
     }

     if (topResult) {
        list.appendChild(renderTopResultCard(topResult, topSongs));
     }
     
     const songs = remaining.filter(i => i.resultType === 'song');
     if (songs.length) {
        const head = document.createElement('div');
        head.className = 'section-head';
        head.style.marginTop = 'var(--space-4)';
        head.innerHTML = '<div class="label">Songs</div>';
        list.appendChild(head);
        songs.slice(0, 4).forEach(song => {
           const songItem = {
              video_id: song.videoId,
              title: song.title,
              artist: song.artist || (song.artists && song.artists.length ? song.artists.map(a=>a.name).join(' and ') : ''),
              channelId: song.channelId || song.channel_id || (song.artists && song.artists[0] && (song.artists[0].id || song.artists[0].browseId)) || '',
              thumbnail: song.thumbnail || (song.thumbnails && song.thumbnails.length ? song.thumbnails[song.thumbnails.length-1].url : '')
           };
           list.appendChild(_createSongElement(songItem, existingThumbsById));
        });
     }
     
     const artists = remaining.filter(i => i.resultType === 'artist').map(a => ({
         name: a.title, browseId: a.browseId, thumbnail: a.thumbnail || (a.thumbnails && a.thumbnails.length ? a.thumbnails[a.thumbnails.length-1].url : '')
     }));
     const albums = remaining.filter(i => i.resultType === 'album').map(a => ({
         title: a.title, artist: a.artist || (a.artists && a.artists.length ? a.artists.map(ar=>ar.name).join(' and ') : ''), browseId: a.browseId, thumbnail: a.thumbnail || (a.thumbnails && a.thumbnails.length ? a.thumbnails[a.thumbnails.length-1].url : '')
     }));
     const playlists = remaining.filter(i => i.resultType === 'playlist').map(a => ({
         title: a.title, browseId: a.browseId, thumbnail: a.thumbnail || (a.thumbnails && a.thumbnails.length ? a.thumbnails[a.thumbnails.length-1].url : '')
     }));
     
     renderSearchRow('Artists', artists, 'artist');
     renderSearchRow('Albums', albums, 'album');
     renderSearchRow('Playlists', playlists, 'playlist');

  } else if (state._activeCategory === 'songs' && data.songs && data.songs.length) {
    data.songs.slice(0, 20).forEach(item => {
      list.appendChild(_createSongElement(item, existingThumbsById));
    });
  } else if (state._activeCategory === 'artists') {
    renderSearchRow('Artists', data.artists, 'artist');
  } else if (state._activeCategory === 'albums') {
    renderSearchRow('Albums', data.albums, 'album');
  } else if (state._activeCategory === 'playlists') {
    renderSearchRow('Playlists', data.playlists, 'playlist');
  }
}


// Removing updateCountLabel completely, we don't need it.

// Close all open more-menus
function _closeAllMoreMenus() {
  for (const m of document.querySelectorAll('.result-more-menu.open')) {
    m.classList.remove('open');
    // See _closeAllQueueMenus: defer the position reset/reparent until the
    // fade-out finishes so it doesn't jump to (0,0) mid-transition.
    setTimeout(() => {
      if (m.classList.contains('open')) return;
      m.style.top = '';
      m.style.bottom = '';
      m.style.left = '';
      m.style.right = '';
      if (m._home && m.parentElement !== m._home) m._home.appendChild(m);
    }, 150);
  }
  for (const b of document.querySelectorAll('.result-more-btn.open')) b.classList.remove('open');
  for (const w of document.querySelectorAll('.result-swipe-wrapper.menu-open')) w.classList.remove('menu-open');
}
document.addEventListener('click', _closeAllMoreMenus);
// A context menu is anchored to the item that opened it. Dismiss it as soon
// as the user starts interacting with a different item, even when that item
// stops its click event before it reaches the document bubble phase.
document.addEventListener('pointerdown', (e) => {
  if (!e.target.closest('.result-more-menu')) _closeAllMoreMenus();
  if (!e.target.closest('.queue-more-menu') && window._closeAllQueueMenus) window._closeAllQueueMenus();
}, true);
document.addEventListener('contextmenu', (e) => {
  // Only close if right-clicking outside of any menu
  if (!e.target.closest('.result-more-menu') && !e.target.closest('.queue-more-menu')) {
    _closeAllMoreMenus();
  }
});
// Same staleness issue as the queue menu: the open menu is fixed-positioned
// at its row's coordinates at open time, then portaled to <body>. Scrolling
// the results list afterward moves the row but not the menu, so close it
// instead of trying to keep it live-repositioned.
(function () {
  const list = document.getElementById('results-list');
  if (list) list.addEventListener('scroll', () => _closeAllMoreMenus(), { passive: true });
})();
// Any scrolling surface can move its source row. A fixed menu must never be
// left floating over unrelated content.
document.addEventListener('scroll', () => {
  _closeAllMoreMenus();
  if (window._closeAllQueueMenus) window._closeAllQueueMenus();
}, true);

// Highlight the currently playing track in the visible results page.
function updateResultsActive() {
  for (const el of document.querySelectorAll('#results-list .result-item-inner')) {
    el.classList.toggle('active', !!state._currentVideoId && el.closest('.result-swipe-wrapper')?.dataset.videoId === state._currentVideoId);
  }
}

/* After paging, jump back to the top of the results. On desktop the list
   scrolls inside .results-list — scrolling the document there would shove the
   whole (barely-overflowing) page upward, so only the inner container moves.
   On mobile the document is the scroll container, so scrollIntoView is right. */
function scrollResultsToTop() {
  document.getElementById('results-list').scrollTop = 0;
  if (window.matchMedia('(max-width: 899px)').matches) {
    document.getElementById('results-section').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}
const prevBtn = document.getElementById('results-prev');
if (prevBtn) {
  prevBtn.addEventListener('click', () => {
    state._resultsPage[state._activeCategory]--;
    renderResults();
    scrollResultsToTop();
  });
}
const nextBtn = document.getElementById('results-next');
if (nextBtn) {
  nextBtn.addEventListener('click', () => {
    state._resultsPage[state._activeCategory]++;
    renderResults();
    scrollResultsToTop();
  });
}
  document.querySelectorAll('.results-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      const category = tab.dataset.category;
      if (category === state._activeCategory) return;
      state._activeCategory = category;
      document.querySelectorAll('.results-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      renderResults();
      scrollResultsToTop();
    });
  });
/* ---- search suggestions ---- */
(function () {
  const input = document.getElementById('query');
  const listEl = document.getElementById('suggest-list');
  const clearBtn = document.getElementById('query-clear');
  let items = [];        // current suggestion strings
  let activeIdx = -1;    // highlighted item (-1 = none)
  let debounceTimer = null;
  let seq = 0;           // request sequencer, drops stale responses
  let showingHistory = false; // list currently shows recent searches, not live suggestions

  const searchSvg =
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="2" stroke-linecap="round">' +
    '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>';
  const clockSvg =
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="2" stroke-linecap="round">' +
    '<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15.5 14"/></svg>';

  /* Recent searches, newest first. Shown (max 7) when the empty search bar is
     focused; every submitted text search is recorded. */
  const HISTORY_KEY = 'searchHistory';
  const HISTORY_MAX_SHOWN = 7;
  const HISTORY_MAX_STORED = 25;

  function getHistory() {
    if (window.JAM_GUEST) return [];
    try {
      const a = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
      return Array.isArray(a) ? a.filter(h => typeof h === 'string') : [];
    } catch (_) { return []; }
  }

  function recordSearch(q) {
    if (window.JAM_GUEST) return;
    q = (q || '').trim();
    if (!q || isYoutubeLinkLike(q)) return;
    // De-dupe case-insensitively so re-searching moves the entry to the top.
    const hist = getHistory().filter(h => h.toLowerCase() !== q.toLowerCase());
    hist.unshift(q);
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(hist.slice(0, HISTORY_MAX_STORED))); } catch (_) {}
  }
  // The GO button handler lives outside this closure; let it record searches.
  window._recordSearchHistory = recordSearch;

  function closeList() {
    // Cancel any pending debounce AND invalidate any in-flight request, so a
    // late suggestion response can't reopen the list after we've closed it
    // (e.g. right after Enter submits the query).
    clearTimeout(debounceTimer);
    seq++;
    listEl.hidden = true;
    listEl.innerHTML = '';
    items = [];
    activeIdx = -1;
    showingHistory = false;
    input.setAttribute('aria-expanded', 'false');
  }

  const removeSvg =
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="2.5" stroke-linecap="round">' +
    '<path d="M18 6 6 18M6 6l12 12"/></svg>';

  function removeHistoryEntry(text) {
    if (window.JAM_GUEST) return;
    const updated = getHistory().filter(h => h.toLowerCase() !== text.toLowerCase());
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(updated)); } catch (_) {}
    items = updated.slice(0, HISTORY_MAX_SHOWN);
    if (!items.length) { closeList(); input.focus(); return; }
    activeIdx = -1;
    render();
  }

  function render() {
    if (!items.length) { closeList(); return; }
    listEl.innerHTML = '';
    items.forEach((text, i) => {
      const li = document.createElement('li');
      li.className = 'suggest-item' + (i === activeIdx ? ' active' : '');
      li.setAttribute('role', 'option');
      li.innerHTML = (showingHistory ? clockSvg : searchSvg) + '<span></span>';
      li.querySelector('span').textContent = text;
      // mousedown (not click) so it fires before the input's blur
      li.addEventListener('mousedown', e => { e.preventDefault(); choose(i); });

      if (showingHistory) {
        const removeBtn = document.createElement('button');
        removeBtn.className = 'suggest-item-remove';
        removeBtn.type = 'button';
        removeBtn.setAttribute('aria-label', 'Remove ' + text + ' from history');
        removeBtn.innerHTML = removeSvg;
        removeBtn.addEventListener('mousedown', e => {
          e.preventDefault();
          e.stopPropagation();
          removeHistoryEntry(text);
        });
        li.appendChild(removeBtn);
      }

      listEl.appendChild(li);
    });
    listEl.hidden = false;
    input.setAttribute('aria-expanded', 'true');
  }

  function showHistory() {
    const hist = getHistory().slice(0, HISTORY_MAX_SHOWN);
    if (!hist.length) { closeList(); return; }
    // Invalidate any in-flight suggestion fetch so it can't overwrite history.
    clearTimeout(debounceTimer);
    seq++;
    items = hist;
    activeIdx = -1;
    showingHistory = true;
    render();
  }

  window._suggestHistory = showHistory;

  function choose(i) {
    if (i < 0 || i >= items.length) return;
    input.value = items[i];
    syncClearBtn();
    closeList();
    document.getElementById('play-query').click();
  }

  function syncClearBtn() { clearBtn.hidden = !input.value; syncUiState(); }

  // mousedown (not click) so it fires before the input's blur; keep focus in
  // the box so the user can type a new query right away.
  clearBtn.addEventListener('mousedown', e => {
    e.preventDefault();
    input.value = '';
    syncClearBtn();
    input.focus();
    showHistory();    // cleared + focused: offer recent searches
  });
  // Mobile: touchend fires instead of mousedown; mirror the same behaviour.
  clearBtn.addEventListener('touchend', e => {
    e.preventDefault();
    input.value = '';
    syncClearBtn();
    input.focus();
    showHistory();
  });
  syncClearBtn();

  async function fetchSuggestions(q) {
    const mySeq = ++seq;
    try {
      const data = await window.api('/alexa/suggest/?q=' + encodeURIComponent(q));
      if (mySeq !== seq) return;            // a newer keystroke won
      items = (data.suggestions || []).slice(0, 8);
      activeIdx = -1;
      showingHistory = false;
      render();
    } catch (_) {
      // Suggestions are best-effort; stay silent on failure.
    }
  }

  window.fetchSuggestions = fetchSuggestions;

  input.addEventListener('input', () => {
    const q = input.value.trim();
    syncClearBtn();
    clearTimeout(debounceTimer);
    // Empty box: fall back to recent searches; links get no suggestions.
    // Only when the box is actually focused — synthetic input events (e.g.
    // clearUiAfterPlaybackReset emptying the box after "Clear") must not
    // pop the history dropdown open on an unfocused input.
    if (!q) {
      if (document.activeElement === input) showHistory();
      else closeList();
      return;
    }
    if (isYoutubeLinkLike(q)) { closeList(); return; }
    debounceTimer = setTimeout(() => fetchSuggestions(q), 180);
  });

  // Clicking/tabbing into the empty search bar surfaces recent searches.
  input.addEventListener('focus', () => {
    if (!input.value.trim()) showHistory();
  });

  input.addEventListener('keydown', e => {
    const open = !listEl.hidden && items.length;
    if (e.key === 'ArrowDown' && open) {
      e.preventDefault();
      activeIdx = (activeIdx + 1) % items.length;
      render();
    } else if (e.key === 'ArrowUp' && open) {
      e.preventDefault();
      activeIdx = (activeIdx - 1 + items.length) % items.length;
      render();
    } else if (e.key === 'Enter') {
      if (open && activeIdx >= 0) { e.preventDefault(); choose(activeIdx); }
      else { closeList(); document.getElementById('play-query').click(); }
    } else if (e.key === 'Escape') {
      closeList();
    }
  });

  input.addEventListener('blur', () => setTimeout(closeList, 120));
})();

  window.runSearch = runSearch;
  window.openResults = openResults;
  window.closeResults = closeResults;
  window.renderResults = renderResults;
  window.updateResultsActive = updateResultsActive;
  window.scrollResultsToTop = scrollResultsToTop;
  window._closeAllMoreMenus = _closeAllMoreMenus;
})();
