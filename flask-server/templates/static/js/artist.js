(function () {
  'use strict';
  const state = window.__appState = window.__appState || {};
  if (state._artistLoading === undefined) state._artistLoading = false;
  if (state._currentChannelId === undefined) state._currentChannelId = null;
  if (state._cachedArtistData === undefined) state._cachedArtistData = null;

  function escHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function showSkeleton(show) {
    var skeleton = document.getElementById('artist-skeleton');
    var content = document.getElementById('artist-content');
    if (!skeleton || !content) return;
    if (show) {
      skeleton.innerHTML =
        '<div class="artist-skeleton-hero"></div>' +
        '<div class="artist-skeleton-songs-container">' +
          '<div class="artist-skeleton-song"><div class="skeleton-square"></div><div><div class="skeleton-line skeleton-line-title"></div><div class="skeleton-line skeleton-line-artist"></div></div></div>' +
          '<div class="artist-skeleton-song"><div class="skeleton-square"></div><div><div class="skeleton-line skeleton-line-title"></div><div class="skeleton-line skeleton-line-artist"></div></div></div>' +
          '<div class="artist-skeleton-song"><div class="skeleton-square"></div><div><div class="skeleton-line skeleton-line-title"></div><div class="skeleton-line skeleton-line-artist"></div></div></div>' +
          '<div class="artist-skeleton-song"><div class="skeleton-square"></div><div><div class="skeleton-line skeleton-line-title"></div><div class="skeleton-line skeleton-line-artist"></div></div></div>' +
          '<div class="artist-skeleton-song"><div class="skeleton-square"></div><div><div class="skeleton-line skeleton-line-title"></div><div class="skeleton-line skeleton-line-artist"></div></div></div>' +
        '</div>';
    }
    skeleton.hidden = !show;
    content.hidden = show;
  }

  function _preloadArtistImage(data) {
    var thumbs = data && data.artist && data.artist.thumbnails || [];
    var url = thumbs.length ? thumbs[thumbs.length - 1].url : '';
    if (!url) return Promise.resolve();
    return new Promise(function (resolve) {
      var img = new Image();
      img.onload = resolve;
      img.onerror = resolve;
      img.src = url;
    });
  }

  async function loadArtist(channelId) {
    if (state._artistLoading) return;

    // ── Preload-nav: consume cached data from navigateWithPreload ──
    var route = '#artist/' + encodeURIComponent(channelId);
    var preloaded = window.consumePreload ? window.consumePreload(route) : null;
    if (preloaded) {
      state._currentChannelId = channelId;
      state._cachedArtistData = preloaded;
      await _preloadArtistImage(preloaded);
      showSkeleton(false);
      renderAll(preloaded);
      return;
    }

    if (state._currentChannelId === channelId && state._cachedArtistData) {
      renderAll(state._cachedArtistData);
      return;
    }
    state._artistLoading = true;
    state._currentChannelId = channelId;
    // Drop the previous artist's data now: if this fetch fails, a retry must
    // not serve the old artist's page under the new channel id.
    state._cachedArtistData = null;
    try {
      var data = await window.api('/api/artist/' + encodeURIComponent(channelId));
      state._cachedArtistData = data;
      await _preloadArtistImage(data);
      showSkeleton(false);
      renderAll(data);
    } catch (e) {
      if (window.toast) window.toast(e.message, 'error');
    } finally {
      state._artistLoading = false;
    }
  }

  function renderAll(data) {
    if (!data || !data.artist) return;
    renderHero(data.artist);
    renderTopSongs(data.topSongs, data.topSongsBrowseId);
    renderHscrollSection('artist-albums-track', data.albums, 'album');
    renderHscrollSection('artist-singles-track', data.singles, 'album');
    renderHscrollSection('artist-related-track', data.related, 'artist');
  }

  function renderHero(artist) {
    var container = document.getElementById('artist-hero');
    if (!container) return;
    var thumbs = artist.thumbnails || [];
    var thumbUrl = thumbs.length ? (thumbs[thumbs.length - 1].url || '') : '';
    
    if (thumbUrl) {
      container.style.backgroundImage = 'url(' + escHtml(thumbUrl) + ')';
      container.style.background = ''; // Allow CSS to handle background sizing
      container.style.backgroundImage = 'url(' + escHtml(thumbUrl) + ')';
    } else {
      container.style.backgroundImage = 'none';
      container.style.background = 'var(--surface)';
    }

    var desc = artist.description || '';
    var subText = artist.subscribers || '';
    
    container.innerHTML = `
      <div class="artist-hero-content">
        <div class="artist-hero-name">${escHtml(artist.name || '')}</div>
        ${desc ? `
          <div class="artist-hero-desc-container">
            <div class="artist-hero-desc collapsed" id="artist-hero-desc">${escHtml(desc)}</div>
            ${desc.length > 150 ? '<div class="artist-hero-desc-more" id="artist-hero-more">MORE</div>' : ''}
          </div>
        ` : ''}
        <div id="artist-top-songs-actions" class="artist-hero-actions"></div>
      </div>
    `;

    var topSongsActions = document.getElementById('artist-top-songs-actions');
    if (topSongsActions) {
      topSongsActions.innerHTML = `
        <button class="artist-action-btn primary" id="artist-btn-shuffle">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 3 21 3 21 8"></polyline><line x1="4" y1="20" x2="21" y2="3"></line><polyline points="21 16 21 21 16 21"></polyline><line x1="15" y1="15" x2="21" y2="21"></line><line x1="4" y1="4" x2="9" y2="9"></line></svg>
          Shuffle
        </button>
        <button class="artist-action-btn secondary" id="artist-btn-mix">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="2"></circle><path d="M16.24 7.76a6 6 0 0 1 0 8.49m-8.48-.01a6 6 0 0 1 0-8.49m11.31-2.82a10 10 0 0 1 0 14.14m-14.14 0a10 10 0 0 1 0-14.14"></path></svg>
          Mix
        </button>
      `;
    }

    var moreBtn = document.getElementById('artist-hero-more');
    if (moreBtn) {
      moreBtn.addEventListener('click', function() {
        var d = document.getElementById('artist-hero-desc');
        if (d.classList.contains('collapsed')) {
          d.classList.remove('collapsed');
          moreBtn.textContent = 'LESS';
        } else {
          d.classList.add('collapsed');
          moreBtn.textContent = 'MORE';
        }
      });
    }

    var shuffleBtn = document.getElementById('artist-btn-shuffle');
    if (shuffleBtn) {
      shuffleBtn.addEventListener('click', function() {
        if (!state._cachedArtistData || !state._cachedArtistData.topSongs || !state._cachedArtistData.topSongs.length) return;
        var firstSong = state._cachedArtistData.topSongs[0];
        if (state._cachedArtistData.topSongsBrowseId && window.playFromQueue) {
          window.playFromQueue(firstSong, state._cachedArtistData.topSongsBrowseId);
          setTimeout(function() {
            window.api('/alexa/shuffle_queue/', {}).catch(function(){});
            var mainShuffle = document.getElementById('shuffle-btn');
            if (mainShuffle) mainShuffle.classList.add('shuffle-active');
            if (window.toast) window.toast('Artist shuffled', 'ok');
          }, 1500);
        } else if (window.playResult) {
          window.playResult(firstSong, false, true);
        }
      });
    }

    var mixBtn = document.getElementById('artist-btn-mix');
    if (mixBtn) {
      mixBtn.addEventListener('click', function() {
        if (!state._cachedArtistData || !state._cachedArtistData.topSongs || !state._cachedArtistData.topSongs.length) return;
        var firstSong = state._cachedArtistData.topSongs[0];
        if (window.playResult) {
          window.playResult(firstSong, false, true);
        }
      });
    }
  }

  function renderTopSongs(songs, browseId) {
    var list = document.getElementById('artist-top-songs-list');
    if (!list) return;
    if (!songs || !songs.length) {
      list.innerHTML = '';
      return;
    }
    list.innerHTML = '';
    
    var displaySongs = songs.slice(0, 10);
    // let, not var: the click handlers below close over `item` per iteration.
    for (let i = 0; i < displaySongs.length; i++) {
      let item = displaySongs[i];
      if (!item.video_id) continue;
      var row = document.createElement('div');
      row.className = 'artist-song-row';
      var thumbUrl = item.thumbnail || '';
      var isLiked = typeof _playlistsData !== 'undefined' && _playlistsData.liked_songs && _playlistsData.liked_songs.includes(item.video_id);
      var heartSvg = isLiked
        ? '<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>'
        : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>';
      row.innerHTML =
        (thumbUrl ? '<img class="artist-song-thumb" src="' + escHtml(thumbUrl) + '" alt="" loading="lazy" onload="this.classList.add(\'loaded\')">' : '<div class="artist-song-thumb"></div>') +
        '<div class="artist-song-info">' +
          '<div class="artist-song-title">' + escHtml(item.title) + '</div>' +
          '<div class="artist-song-artist">' + escHtml(item.artist) + '</div>' +
        '</div>' +
        '<button class="artist-song-play-btn" title="Play"><svg viewBox="0 0 24 24" fill="currentColor"><polygon points="8,5 19,12 8,19"/></svg></button>' +
        '<button class="artist-song-like-btn' + (isLiked ? ' liked' : '') + '" title="Like">' + heartSvg + '</button>';
      // Play button
      row.querySelector('.artist-song-play-btn').addEventListener('click', function(e) {
        e.stopPropagation();
        if (window.playFromQueue) {
          window.playFromQueue({video_id: item.video_id, title: item.title, artist: item.artist, thumbnail: item.thumbnail});
        }
      });
      // Like button
      row.querySelector('.artist-song-like-btn').addEventListener('click', function(e) {
        e.stopPropagation();
        if (typeof toggleLike === 'function') toggleLike(item, this);
      });
      list.appendChild(row);
    }

    if (browseId || songs.length > 10) {
      var viewAllContainer = document.createElement('div');
      viewAllContainer.className = 'artist-view-all-container';
      var viewAllBtn = document.createElement('button');
      viewAllBtn.className = 'artist-view-all-btn';
      viewAllBtn.textContent = 'View all';
      viewAllBtn.addEventListener('click', function() {
        if (browseId) {
          window.navigateTo('#playlist/' + encodeURIComponent(browseId));
        }
      });
      viewAllContainer.appendChild(viewAllBtn);
      list.appendChild(viewAllContainer);
    }
  }

  function renderHscrollSection(trackId, items, type) {
    var track = document.getElementById(trackId);
    if (!track) return;
    if (!items || !items.length) {
      track.parentElement.hidden = true;
      track.innerHTML = '';
      return;
    }
    track.parentElement.hidden = false;
    track.innerHTML = '';
    // let, not var: the click handler below closes over `item` per iteration.
    for (let i = 0; i < items.length; i++) {
      let item = items[i];
      var card = document.createElement('div');
      card.className = 'hscroll-card';
      var thumbUrl = '';
      var isRound = false;
      var title = '';
      var sub = '';
      if (type === 'album') {
        thumbUrl = item.thumbnail || '';
        title = item.title || '';
        sub = item.year || '';
      } else {
        // related artist
        thumbUrl = item.thumbnail || '';
        isRound = true;
        title = item.title || '';
        sub = item.subscribers || '';
      }
      var imgHtml = thumbUrl
        ? '<img src="' + escHtml(thumbUrl) + '" alt="" loading="lazy" onload="this.classList.add(\'loaded\')">'
        : '<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/></svg>';
      var playBtnHtml = (type === 'album') ? '<button type="button" class="hscroll-play-btn" title="Play"><svg viewBox="0 0 24 24" fill="currentColor"><polygon points="8,5 19,12 8,19"/></svg></button>' : '';
      card.innerHTML =
        '<div class="hscroll-card-art' + (isRound ? ' round' : '') + '">' + imgHtml + playBtnHtml + '</div>' +
        '<div class="hscroll-card-title">' + escHtml(title) + '</div>' +
        (sub ? '<div class="hscroll-card-sub">' + escHtml(sub) + '</div>' : '');
      if (type === 'artist' && item.browseId) {
        card.addEventListener('click', function() {
          if (window.preloadNavigateArtist) window.preloadNavigateArtist(item.browseId);
          else window.navigateTo('#artist/' + encodeURIComponent(item.browseId));
        });
      } else if (type === 'album' && item.browseId) {
        card.addEventListener('click', function(e) {
          if (e.target.closest('.hscroll-play-btn')) {
            e.stopPropagation();
            if (window.toast) window.toast('Loading album...', 'ok');
            window.api('/api/album/' + encodeURIComponent(item.browseId)).then(function(albumData) {
              if (albumData && albumData.tracks && albumData.tracks.length > 0 && window.playFromQueue) {
                window.playFromQueue(albumData.tracks[0], 0);
              }
            }).catch(function() {
              if (window.toast) window.toast('Could not play album', 'error');
            });
          } else {
            if (window.preloadNavigateAlbum) window.preloadNavigateAlbum(item.browseId);
            else window.navigateTo('#album/' + encodeURIComponent(item.browseId));
          }
        });
      }
      track.appendChild(card);
    }
    // Update initial arrow states after render
    window.setTimeout(function() { updateHscrollArrows(track); }, 50);
  }

  function updateHscrollArrows(track) {
    var container = track.closest('.hscroll-section');
    if (!container) return;
    var prev = container.querySelector('.hscroll-scroll-prev');
    var next = container.querySelector('.hscroll-scroll-next');
    if (prev) prev.disabled = track.scrollLeft <= 2;
    if (next) next.disabled = track.scrollLeft + track.clientWidth >= track.scrollWidth - 2;
  }

  var artistSection = document.getElementById('artist-section');
  if (artistSection) {
    artistSection.addEventListener('scroll', function(e) {
      if (e.target.classList && e.target.classList.contains('hscroll-track')) {
        updateHscrollArrows(e.target);
      }
    }, true);

    artistSection.addEventListener('click', function(e) {
      var scrollBtn = e.target.closest('.hscroll-scroll');
      if (scrollBtn) {
        var track = scrollBtn.closest('.hscroll-section').querySelector('.hscroll-track');
        if (!track) return;
        var direction = scrollBtn.classList.contains('hscroll-scroll-prev') ? -1 : 1;
        track.scrollBy({ left: direction * Math.max(240, track.clientWidth * .8), behavior: 'smooth' });
        window.setTimeout(function() { updateHscrollArrows(track); }, 350);
      }
    });
  }

  window.loadArtist = loadArtist;
})();
