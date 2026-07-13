(function () {
  'use strict';
  const state = window.__appState = window.__appState || {};
  const pageConfig = window.ARTIST_PAGE_CONFIG || { hero: { descriptionLimit: 150 } };
  if (state._artistLoading === undefined) state._artistLoading = false;
  if (state._currentChannelId === undefined) state._currentChannelId = null;
  if (state._cachedArtistData === undefined) state._cachedArtistData = null;
  if (!state._artistCache) state._artistCache = Object.create(null);
  if (state._artistLoadToken === undefined) state._artistLoadToken = 0;
  if (state._renderedArtistChannelId === undefined) state._renderedArtistChannelId = null;

  function escHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function artistCreditsHtml(item) {
    var artists = Array.isArray(item.artists) ? item.artists.filter(function(artist) {
      return artist && artist.name;
    }) : [];
    if (artists.length) {
      return artists.map(function(artist) {
        var id = artist.id ? ' data-channel-id="' + escHtml(artist.id) + '"' : '';
        return '<span class="artist-name" data-artist-name="' + escHtml(artist.name) + '"' + id + '>' +
          escHtml(artist.name) + '</span>';
      }).join(', ');
    }
    if (window.artistLinksHtml) return window.artistLinksHtml(item.artist || '', item.channelId || '');
    return escHtml(item.artist || '');
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

  function showArtistSongsLoading(show) {
    var content = document.getElementById('artist-songs-content');
    if (!content) return;
    // The destination stays blank while its top progress bar preloads; this
    // prevents the artist page from briefly turning into a modal-like card.
    content.hidden = show;
  }

  function _preloadArtistImage(data) {
    if (data && data.__heroReady) return Promise.resolve();
    var thumbs = data && data.artist && data.artist.thumbnails || [];
    // Paint the smallest supplied thumbnail first. renderHero upgrades it to
    // the largest image once that download completes.
    var url = thumbs.length ? thumbs[0].url : '';
    if (!url) return Promise.resolve();
    return new Promise(function (resolve) {
      var settled = false;
      var img = new Image();
      var timer = setTimeout(done, 8000);
      function done() {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        img.onload = null;
        img.onerror = null;
        resolve();
      }
      img.onload = done;
      img.onerror = done;
      img.src = url;
    });
  }

  async function ensureExpandedTopSongs(channelId, data) {
    if (!data || data.__allTopSongsLoaded) return;
    var browseId = data.topSongsBrowseId || '';
    if (!browseId || !window.api) {
      data.__allTopSongsLoaded = true;
      return;
    }
    var result = await window.api(
      '/api/artist/' + encodeURIComponent(channelId) + '/songs?browse_id=' +
      encodeURIComponent(browseId)
    );
    if (result && Array.isArray(result.songs) && result.songs.length) {
      data.topSongs = result.songs;
    }
    data.__allTopSongsLoaded = true;
  }

  async function loadArtist(channelId, topSongsOnly) {
    // An empty array can be a stale pre-login/failure result. Refresh the
    // subscription snapshot periodically instead of treating [] as loaded
    // forever, otherwise an already-followed artist renders as Subscribe.
    if ((!Array.isArray(state._subscribedArtists) ||
        Date.now() - (state._subscribedArtistsFetchedAt || 0) > 60000) && window.api) {
      try {
        var subscriptionData = await window.api('/api/subscribed_artists/');
        state._subscribedArtists = subscriptionData.artists || [];
        state._subscribedArtistsFetchedAt = Date.now();
      } catch (e) {
        state._subscribedArtists = [];
        state._subscribedArtistsFetchedAt = 0;
      }
    }
    // ── Preload-nav: consume cached data from navigateWithPreload ──
    var route = '#artist/' + encodeURIComponent(channelId) + (topSongsOnly ? '?view=top-songs' : '');
    var token = ++state._artistLoadToken;
    var preloaded = window.consumePreload ? window.consumePreload(route) : null;
    var cached = preloaded || state._artistCache[channelId] || null;
    var sameArtistVisible = state._renderedArtistChannelId === channelId && state._cachedArtistData;

    function requestIsCurrent() {
      return token === state._artistLoadToken &&
        (!window.getRoute || window.getRoute() === route);
    }

    // One DOM tree serves every artist. Hide the old tree before any await,
    // otherwise routing can paint the previous banner while new data loads.
    if (!sameArtistVisible || preloaded) showSkeleton(true);
    state._artistLoading = true;
    state._currentChannelId = channelId;
    if (cached) {
      if (topSongsOnly) {
        try {
          await ensureExpandedTopSongs(channelId, cached);
        } catch (e) {
          if (requestIsCurrent() && window.toast) {
            window.toast(e.message || 'Unable to load all songs', 'error');
          }
        }
      }
      await _preloadArtistImage(cached);
      if (!requestIsCurrent()) {
        if (token === state._artistLoadToken) state._artistLoading = false;
        return;
      }
      cached.__heroReady = true;
      state._artistCache[channelId] = cached;
      state._cachedArtistData = cached;
      renderAll(cached, topSongsOnly);
      state._renderedArtistChannelId = channelId;
      showSkeleton(false);
      state._artistLoading = false;
      return;
    }

    // Drop the previous artist's data now: if this fetch fails, a retry must
    // not serve the old artist's page under the new channel id.
    state._cachedArtistData = null;
    try {
      var data = await window.api('/api/artist/' + encodeURIComponent(channelId));
      if (topSongsOnly) {
        try {
          await ensureExpandedTopSongs(channelId, data);
        } catch (e) {
          if (requestIsCurrent() && window.toast) {
            window.toast(e.message || 'Unable to load all songs', 'error');
          }
        }
      }
      await _preloadArtistImage(data);
      if (!requestIsCurrent()) return;
      data.__heroReady = true;
      state._artistCache[channelId] = data;
      state._cachedArtistData = data;
      renderAll(data, topSongsOnly);
      state._renderedArtistChannelId = channelId;
      showSkeleton(false);
    } catch (e) {
      if (requestIsCurrent() && window.toast) window.toast(e.message, 'error');
    } finally {
      if (token === state._artistLoadToken) state._artistLoading = false;
    }
  }

  function renderAll(data, topSongsOnly) {
    if (!data || !data.artist) return;
    renderHero(data.artist);
    renderTopSongs(data.topSongs, data.topSongsBrowseId, topSongsOnly);
    renderHscrollSection('artist-albums-track', data.albums, 'album');
    renderHscrollSection('artist-singles-track', data.singles, 'album');
    renderHscrollSection('artist-related-track', data.related, 'artist');
    var sectionItems = {
      'artist-albums': data.albums,
      'artist-singles': data.singles,
      'artist-related': data.related
    };
    ['artist-albums', 'artist-singles', 'artist-related'].forEach(function (id) {
      var section = document.getElementById(id);
      // Do not leave a heading and arrow controls for an empty shelf.
      if (section) section.hidden = !!topSongsOnly || !Array.isArray(sectionItems[id]) || !sectionItems[id].length;
    });
  }

  function renderArtistSongsPage(data) {
    if (!data || !data.artist) return;
    var title = document.getElementById('artist-songs-title');
    if (title) title.textContent = data.artist.name || 'Songs';
    renderTopSongs(data.topSongs || [], '', true, 'artist-songs-list');
  }

  function renderHero(artist) {
    var container = document.getElementById('artist-hero');
    if (!container) return;
    var thumbs = artist.thumbnails || [];
    var previewUrl = thumbs.length ? (thumbs[0].url || '') : '';
    var fullUrl = thumbs.length ? (thumbs[thumbs.length - 1].url || '') : '';
    var imageToken = (container._artistHeroImageToken || 0) + 1;
    container._artistHeroImageToken = imageToken;
    container.style.backgroundImage = 'none';
    container.style.background = 'var(--surface)';

    var desc = artist.description || '';
    var subText = artist.subscribers || '';
    // get_artist responses may omit channelId; route state still contains the
    // ID used to load this page. Keep other common API names as fallbacks.
    var channelId = artist.channelId || artist.channel_id || artist.id ||
      artist.browseId || artist.browse_id || state._currentChannelId || '';
    // YouTube's subscription endpoint can expose the artist's browse ID while
    // get_artist returns its channel ID (or vice versa). Match every known ID
    // and then the exact artist name as a safe fallback for that API mismatch.
    var artistIds = [channelId, state._currentChannelId, artist.channelId,
      artist.channel_id, artist.id, artist.browseId, artist.browse_id]
      .filter(Boolean).map(function (id) { return String(id).trim(); });
    var artistName = String(artist.name || '').trim().toLowerCase();
    var subscribed = (state._subscribedArtists || []).some(function (a) {
      var subscriptionId = a && (a.channel_id || a.channelId || a.id || a.browseId || a.browse_id);
      var subscriptionName = String((a && (a.name || a.artist || a.title)) || '').trim().toLowerCase();
      return (subscriptionId && artistIds.indexOf(String(subscriptionId).trim()) !== -1) ||
        (artistName && subscriptionName === artistName);
    });
    
    container.innerHTML = `
      <div class="artist-hero-art${previewUrl ? ' artist-hero-art-blurred' : ''}"${previewUrl ? ` style="background-image:url('${escHtml(previewUrl)}')"` : ''}></div>
      <div class="artist-hero-content">
        <div class="artist-hero-name">${escHtml(artist.name || '')}</div>
        ${desc ? `
          <div class="artist-hero-desc-container">
            <div class="artist-hero-desc collapsed" id="artist-hero-desc">${escHtml(desc)}</div>
            ${desc.length > (pageConfig.hero.descriptionLimit || 150) ? '<div class="artist-hero-desc-more" id="artist-hero-more">MORE</div>' : ''}
          </div>
        ` : ''}
        <div id="artist-top-songs-actions" class="artist-hero-actions"></div>
      </div>
    `;

    // Use the low-resolution thumbnail as an immediate visual preview, then
    // replace it only after the full-size artwork is decoded. The CSS
    // transition clears the blur instead of flashing between images.
    var art = container.querySelector('.artist-hero-art');
    if (art && fullUrl && fullUrl !== previewUrl) {
      var fullImage = new Image();
      fullImage.onload = function () {
        if (container._artistHeroImageToken !== imageToken) return;
        art.style.backgroundImage = 'url(' + escHtml(fullUrl) + ')';
        requestAnimationFrame(function () {
          if (container._artistHeroImageToken === imageToken) {
            art.classList.remove('artist-hero-art-blurred');
          }
        });
      };
      fullImage.src = fullUrl;
    } else if (art && previewUrl) {
      requestAnimationFrame(function () { art.classList.remove('artist-hero-art-blurred'); });
    }

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
        <button class="artist-action-btn secondary" id="artist-btn-subscribe" aria-pressed="${subscribed}">
          ${subscribed ? 'Subscribed' : 'Subscribe'}
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

    var subscribeBtn = document.getElementById('artist-btn-subscribe');
    if (subscribeBtn) subscribeBtn.addEventListener('click', async function() {
      if (!channelId || !window.api) {
        if (window.toast) window.toast('Artist ID unavailable', 'error');
        return;
      }
      var isSubscribed = subscribeBtn.getAttribute('aria-pressed') === 'true';
      var body = { channel_id: channelId, name: artist.name || '', thumbnail: thumbUrl };
      try {
        var result = isSubscribed
          ? await window.apiDelete('/api/subscribed_artists/', body)
          : await window.api('/api/subscribed_artists/', body);
        state._subscribedArtists = result.artists || [];
        state._subscribedArtistsFetchedAt = Date.now();
        subscribeBtn.setAttribute('aria-pressed', String(!isSubscribed));
        subscribeBtn.textContent = isSubscribed ? 'Subscribe' : 'Subscribed';
        if (window.toast) window.toast(isSubscribed ? 'Unsubscribed' : 'Artist subscribed', 'ok');
      } catch (e) { if (window.toast) window.toast(e.message || 'Unable to update subscription', 'error'); }
    });
  }

  function renderTopSongs(songs, browseId, topSongsOnly, listId) {
    var list = document.getElementById(listId || 'artist-top-songs-list');
    if (!list) return;
    if (!songs || !songs.length) {
      list.innerHTML = '';
      return;
    }
    list.innerHTML = '';
    
    var displaySongs = topSongsOnly ? songs : songs.slice(0, 10);
    // let, not var: the click handlers below close over `item` per iteration.
    for (let i = 0; i < displaySongs.length; i++) {
      let item = displaySongs[i];
      if (!item.video_id) continue;
      var row = document.createElement('div');
      row.className = 'artist-song-row';
      row.dataset.videoId = item.video_id || '';
      row._songContextTrack = item;
      if (!row._songContextTrack.artist_id) {
        row._songContextTrack.artist_id = item.channel_id || item.artistId ||
          (Array.isArray(item.artists) && item.artists[0] && item.artists[0].id) || '';
      }
      var thumbUrl = item.thumbnail || '';
      var artistCredits = artistCreditsHtml(item);
      var albumName = typeof item.album === 'string' ? item.album :
        (item.album && item.album.name) || item.album_name || '';
      var duration = window.formatTrackDuration ? window.formatTrackDuration(item) : '';
      var isLiked = typeof _playlistsData !== 'undefined' && _playlistsData.liked_songs && _playlistsData.liked_songs.includes(item.video_id);
      var heartSvg = isLiked
        ? '<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>'
        : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>';
      var moreSvg = '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>';
      row.innerHTML =
        (thumbUrl ? '<img class="artist-song-thumb" src="' + escHtml(thumbUrl) + '" alt="" loading="lazy" onload="this.classList.add(\'loaded\')">' : '<div class="artist-song-thumb"></div>') +
        '<div class="artist-song-info">' +
          '<div class="artist-song-title">' + escHtml(item.title) + '</div>' +
          '<div class="artist-song-artist">' + artistCredits + '</div>' +
          (albumName ? '<div class="artist-song-album">' + escHtml(albumName) + '</div>' : '') +
        '</div>' +
        '<button class="artist-song-play-btn" title="Play"><svg viewBox="0 0 24 24" fill="currentColor"><polygon points="8,5 19,12 8,19"/></svg></button>' +
        (duration ? '<span class="track-duration artist-song-duration">' + escHtml(duration) + '</span>' : '') +
        '<button class="artist-song-like-btn' + (isLiked ? ' liked' : '') + '" title="Like">' + heartSvg + '</button>' +
        '<button class="result-more-btn artist-song-more-btn" type="button" title="More options">' + moreSvg + '</button>';
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
      row.querySelector('.artist-song-more-btn').addEventListener('click', function(e) {
        e.stopPropagation();
        if (window.openSongContextMenu) window.openSongContextMenu(e, item);
      });
      if (window.wireArtistLinks) window.wireArtistLinks(row);
      list.appendChild(row);
    }

    if (!topSongsOnly && (browseId || songs.length > 10)) {
      var viewAllContainer = document.createElement('div');
      viewAllContainer.className = 'artist-view-all-container';
      var viewAllBtn = document.createElement('button');
      viewAllBtn.className = 'artist-view-all-btn';
      viewAllBtn.textContent = 'View all';
      viewAllBtn.addEventListener('click', function() {
        var channelId = state._currentChannelId;
        if (channelId && window.navigateArtistTopSongs) window.navigateArtistTopSongs(channelId);
        else if (channelId && window.navigateTo) window.navigateTo('#artist/' + encodeURIComponent(channelId) + '/songs');
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
      card.className = 'hscroll-card' + (type === 'artist' ? ' related-artist-card' : '') + (type === 'album' ? ' album-card' : '');
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
    var maxScroll = Math.max(0, track.scrollWidth - track.clientWidth);
    if (prev) prev.disabled = track.scrollLeft <= 2;
    if (next) next.disabled = track.scrollLeft >= maxScroll - 2;
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
    window.addEventListener('resize', function() {
      artistSection.querySelectorAll('.hscroll-track').forEach(updateHscrollArrows);
    });
  }

  async function loadArtistSongs(channelId) {
    var route = '#artist/' + encodeURIComponent(channelId) + '/songs';
    var token = ++state._artistLoadToken;
    state._artistLoading = true;
    state._currentChannelId = channelId;
    var preloaded = window.consumePreload ? window.consumePreload(route) : null;
    var cached = preloaded || state._artistCache[channelId] || null;
    var needsFetch = !preloaded && (!cached || !cached.__allTopSongsLoaded);
    if (needsFetch && window.startTopProgress) window.startTopProgress();
    showArtistSongsLoading(true);
    function requestIsCurrent() {
      return token === state._artistLoadToken && (!window.getRoute || window.getRoute() === route);
    }
    try {
      var data = cached || await window.api('/api/artist/' + encodeURIComponent(channelId));
      await ensureExpandedTopSongs(channelId, data);
      if (!requestIsCurrent()) return;
      state._artistCache[channelId] = data;
      state._cachedArtistData = data;
      renderArtistSongsPage(data);
      showArtistSongsLoading(false);
      if (needsFetch && window.completeTopProgress) window.completeTopProgress();
    } catch (e) {
      if (requestIsCurrent() && window.toast) window.toast(e.message || 'Unable to load songs', 'error');
      if (needsFetch && window.abortTopProgress) window.abortTopProgress();
    } finally {
      if (token === state._artistLoadToken) state._artistLoading = false;
    }
  }

  window.loadArtist = loadArtist;
  window.loadArtistSongs = loadArtistSongs;
})();
