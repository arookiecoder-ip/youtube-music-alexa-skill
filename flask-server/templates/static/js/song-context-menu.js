(function () {
  'use strict';

  const ROOT_SELECTOR = [
    '.home-item[data-video-id]',
    '.recs-tile',
    '.artist-song-row',
    '.history-item',
    '.queue-swipe-wrapper[data-video-id]',
    '.result-swipe-wrapper[data-video-id]',
    '.top-result-card[data-video-id]'
  ].join(',');

  const icon = {
    likeFilled: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M2 10h4v12H2zM8 22V10l3.5-7.5c.3-.7 1.1-1.1 1.8-.8l.2.1c1.1.5 1.6 1.7 1.3 2.8L14 10h6.2c1.3 0 2.3 1.2 2 2.5l-1.5 7.5c-.2 1.2-1.2 2.4-2.4 2.4H8z"/></svg>',
    like: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>',
    next: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M4 5v14l11-7L4 5zm13 0v14h3V5h-3z"/></svg>',
    add: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
    radio: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4.93 19.07A10 10 0 1 1 19.07 4.93 10 10 0 0 1 4.93 19.07z"/><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2"/></svg>',
    album: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r="1" fill="currentColor"/></svg>',
    artist: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-7 8-7s8 3 8 7"/></svg>',
    save: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/><line x1="12" y1="11" x2="12" y2="17"/><line x1="9" y1="14" x2="15" y2="14"/></svg>',
    remove: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18"/><path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>',
    play: '<svg viewBox="0 0 24 24" fill="currentColor"><polygon points="7,4 20,12 7,20"/></svg>'
  };

  const menu = document.createElement('div');
  menu.className = 'result-more-menu song-context-menu';
  menu.innerHTML =
    '<div class="result-menu-option" data-action="play" hidden>' + icon.play + '<span>Play</span></div>' +
    '<div class="result-menu-option" data-action="toggle-like">' + icon.like + '<span>Like</span></div>' +
    '<div class="result-menu-option" data-action="play-next">' + icon.next + '<span>Play next</span></div>' +
    '<div class="result-menu-option" data-action="add-to-queue">' + icon.add + '<span>Add to queue</span></div>' +
    '<div class="result-menu-option" data-action="play-radio">' + icon.radio + '<span>Play Radio</span></div>' +
    '<div class="result-menu-option" data-action="open-album">' + icon.album + '<span>Go to album</span></div>' +
    '<div class="result-menu-option" data-action="open-artist">' + icon.artist + '<span>Go to artist</span></div>' +
    '<div class="result-menu-option" data-action="save-playlist">' + icon.save + '<span>Save to Playlist</span></div>' +
    '<div class="result-menu-option" data-action="remove-from-queue" hidden>' + icon.remove + '<span>Remove from queue</span></div>';
  document.body.appendChild(menu);

  const albumResolutionCache = new Map();
  const artistResolutionCache = new Map();

  function resolveAlbumId(track, root) {
    if (track.album_id) return Promise.resolve(track.album_id);
    if (!track.video_id || typeof window.api !== 'function') return Promise.resolve('');
    if (!albumResolutionCache.has(track.video_id)) {
      const request = window.api('/api/album/resolve/' + encodeURIComponent(track.video_id))
        .then(function (data) { return (data && data.album_id) || ''; })
        .catch(function () { return ''; });
      albumResolutionCache.set(track.video_id, request);
    }
    return albumResolutionCache.get(track.video_id).then(function (albumId) {
      if (albumId) {
        track.album_id = albumId;
        if (root) root.dataset.albumId = albumId;
      }
      return albumId;
    });
  }

  function navigateTrackAlbum(track, root) {
    return resolveAlbumId(track, root).then(function (albumId) {
      if (!albumId) {
        if (typeof window.toast === 'function') window.toast('Album unavailable for this song', 'error');
        return false;
      }
      if (window.preloadNavigateAlbum) window.preloadNavigateAlbum(albumId);
      else if (window.navigateTo) window.navigateTo('#album/' + encodeURIComponent(albumId));
      return true;
    });
  }

  function resolveArtistId(track) {
    if (track.artist_id) return Promise.resolve(track.artist_id);
    if (!track.video_id || typeof window.api !== 'function') return Promise.resolve('');
    if (!artistResolutionCache.has(track.video_id)) {
      artistResolutionCache.set(track.video_id,
        window.api('/api/album/resolve/' + encodeURIComponent(track.video_id))
          .then(function (data) {
            if (data && data.artist_id) {
              track.artist_id = data.artist_id;
              if (data.artist) track.artist = data.artist;
              return data.artist_id;
            }
            return '';
          })
          .catch(function () { return ''; })
      );
    }
    return artistResolutionCache.get(track.video_id).then(function (artistId) {
      if (artistId) track.artist_id = artistId;
      return artistId;
    });
  }

  function navigateTrackArtist(track) {
    // Some Explore tracks omit their artist channel ID, so resolving it takes
    // a network request. Start feedback in this menu click, not afterwards.
    if (window.startTopProgress) window.startTopProgress();
    return resolveArtistId(track).then(function (artistId) {
      if (!artistId) {
        if (window.abortTopProgress) window.abortTopProgress();
        if (typeof window.toast === 'function') window.toast('Artist unavailable for this song', 'error');
        return false;
      }
      if (window.preloadNavigateArtist) window.preloadNavigateArtist(artistId);
      else if (window.navigateTo) window.navigateTo('#artist/' + encodeURIComponent(artistId));
      return true;
    });
  }

  function text(root, selectors) {
    for (const selector of selectors) {
      const element = root.querySelector(selector);
      if (element && element.textContent.trim()) return element.textContent.trim();
    }
    return '';
  }

  function image(root) {
    const img = root.querySelector('img');
    if (img && img.src) return img.src;
    return '';
  }

  function trackFrom(root) {
    if (root._songContextTrack) {
      const track = Object.assign({}, root._songContextTrack);
      if (!track.album_id) track.album_id = track.albumId || track.album_browse_id || root.dataset.albumId || root.dataset.albumBrowseId || '';
      if (!track.album_id && track.album) {
        track.album_id = typeof track.album === 'object'
          ? (track.album.id || track.album.browseId || '')
          : '';
      }
      if (!track.artist_id) track.artist_id = track.channel_id || track.channelId || track.artistId || '';
      return track;
    }
    const state = window.__appState || {};
    const isNowPlaying = root.id === 'now-playing' || root.classList.contains('np-page-left');
    const videoId = isNowPlaying
      ? state._currentVideoId
      : root.dataset.videoId || root.closest('[data-video-id]')?.dataset.videoId || '';
    const albumId = root.dataset.albumId || root.dataset.albumBrowseId ||
      (root.dataset.kind === 'album' ? root.dataset.targetId : '');
    const artistId = root.dataset.channelId || root.dataset.artistId || '';
    return {
      video_id: videoId || '',
      title: text(root, ['.home-item-title', '.recs-tile-title', '.artist-song-title', '.queue-title', '.result-title', '.top-result-title', '.np-title', '.np-page-title']),
      artist: text(root, ['.home-item-subtitle', '.recs-tile-artist', '.artist-song-artist', '.queue-artist', '.result-artist', '.top-result-subtitle', '.np-artist', '.np-page-artist']),
      thumbnail: isNowPlaying ? (state._currentThumbnail || '') : image(root),
      album_id: albumId,
      artist_id: artistId
    };
  }

  function closeMenu() {
    menu.classList.remove('open');
    menu._track = null;
  }

  function openMenu(event, track) {
    if (window._closeAllMoreMenus) window._closeAllMoreMenus();
    if (window._closeAllQueueMenus) window._closeAllQueueMenus();

    menu._track = track;
    const liked = typeof window._playlistsData !== 'undefined' &&
      window._playlistsData.liked_songs &&
      window._playlistsData.liked_songs.includes(track.video_id);
    const likeOption = menu.querySelector('[data-action="toggle-like"]');
    likeOption.classList.toggle('liked', !!liked);
    likeOption.querySelector('svg').outerHTML = liked ? icon.likeFilled : icon.like;
    likeOption.querySelector('span').textContent = liked ? 'Dislike' : 'Like';

    // For station cards, only show Play — hide everything else
    const isStation = track._isStation;
    const albumOption = menu.querySelector('[data-action="open-album"]');
    const artistOption = menu.querySelector('[data-action="open-artist"]');
    // Keep the canonical menu shape everywhere. Missing catalog metadata makes
    // a link unavailable, never silently removes it and changes the menu.
    function setUnavailable(option, unavailable) {
      option.classList.toggle('is-unavailable', unavailable);
      option.setAttribute('aria-disabled', unavailable ? 'true' : 'false');
    }
    albumOption.hidden = false;
    artistOption.hidden = false;
    setUnavailable(albumOption, !track.album_id && !track.video_id);
    setUnavailable(artistOption, !track.artist_id && !track.video_id);
    menu.querySelector('[data-action="play"]').hidden = !isStation;
    menu.querySelector('[data-action="remove-from-queue"]').hidden =
      isStation || track._queueIndex === undefined || track._queueIsActive;
    if (isStation) {
      menu.querySelector('[data-action="toggle-like"]').hidden = true;
      menu.querySelector('[data-action="play-next"]').hidden = true;
      menu.querySelector('[data-action="add-to-queue"]').hidden = true;
      menu.querySelector('[data-action="play-radio"]').hidden = true;
      menu.querySelector('[data-action="save-playlist"]').hidden = true;
    } else {
      // Jam guests only retain the server-authorized radio action. They never
      // receive account-writing or host-queue mutation controls.
      menu.querySelector('[data-action="toggle-like"]').hidden = !!window.JAM_GUEST;
      menu.querySelector('[data-action="play-next"]').hidden = !!window.JAM_GUEST;
      menu.querySelector('[data-action="add-to-queue"]').hidden = !!window.JAM_GUEST;
      menu.querySelector('[data-action="save-playlist"]').hidden = !!window.JAM_GUEST;
    }

    // Keep the menu tied to the row/button that opened it.  The queue and
    // result lists can scroll in their own containers, so a fixed menu needs
    // to be repositioned as the source row moves.
    menu._anchor = event.currentTarget && event.currentTarget.getBoundingClientRect
      ? event.currentTarget
      : event.target;
    let x = event.clientX;
    let y = event.clientY;
    if ((!x && !y) && event.currentTarget && event.currentTarget.getBoundingClientRect) {
      const buttonRect = event.currentTarget.getBoundingClientRect();
      x = buttonRect.right;
      y = buttonRect.bottom;
    }
    menu.style.left = x + 'px';
    menu.style.right = 'auto';
    menu.style.top = y + 'px';
    menu.style.bottom = 'auto';
    menu.classList.add('open');

    const rect = menu.getBoundingClientRect();
    if (rect.right > window.innerWidth - 8) {
      menu.style.left = 'auto';
      menu.style.right = Math.max(8, window.innerWidth - x) + 'px';
    }
    if (rect.bottom > window.innerHeight - 8) {
      menu.style.top = 'auto';
      menu.style.bottom = Math.max(8, window.innerHeight - y) + 'px';
    }
  }

  function repositionMenu() {
    if (!menu.classList.contains('open') || !menu._anchor) return;
    const anchor = menu._anchor.getBoundingClientRect();
    const x = anchor.right;
    const y = anchor.bottom;
    menu.style.left = x + 'px';
    menu.style.right = 'auto';
    menu.style.top = y + 'px';
    menu.style.bottom = 'auto';
    const rect = menu.getBoundingClientRect();
    if (rect.right > window.innerWidth - 8) {
      menu.style.left = 'auto';
      menu.style.right = Math.max(8, window.innerWidth - x) + 'px';
    }
    if (rect.bottom > window.innerHeight - 8) {
      menu.style.top = 'auto';
      menu.style.bottom = Math.max(8, window.innerHeight - anchor.top) + 'px';
    }
  }

  document.addEventListener('contextmenu', function (event) {
    // The main now-playing banner is a playback control, not a song-list row.
    // Suppress both the custom actions menu and the browser image menu there.
    if (event.target.closest('#now-playing, .np-page-left')) {
      event.preventDefault();
      event.stopImmediatePropagation();
      closeMenu();
      return;
    }
    if (event.target.closest('.result-more-menu, .queue-more-menu')) return;
    const root = event.target.closest(ROOT_SELECTOR);
    if (!root) {
      closeMenu();
      return;
    }
    const track = trackFrom(root);
    if (!track.video_id) return;
    if (root.classList.contains('queue-swipe-wrapper')) {
      track._queueIndex = Number(root.dataset.index);
      track._queueIsActive = !!root.querySelector('.queue-item.active');
    }
    // Tag station cards so the menu knows to only show Play
    track._isStation = root.dataset.kind === 'station';
    event.preventDefault();
    event.stopImmediatePropagation();
    openMenu(event, track);
  }, true);

  menu.addEventListener('click', function (event) {
    const option = event.target.closest('[data-action]');
    const track = menu._track;
    if (!option || !track || option.getAttribute('aria-disabled') === 'true') return;
    event.stopPropagation();
    closeMenu();
    switch (option.dataset.action) {
      case 'toggle-like':
        if (typeof window.toggleLike === 'function') window.toggleLike(track, null);
        break;
      case 'play-next':
        if (typeof window.addToQueue === 'function') window.addToQueue(track, 'next');
        break;
      case 'add-to-queue':
        if (typeof window.addToQueue === 'function') window.addToQueue(track, 'last');
        break;
      case 'play-radio':
        if (typeof window.playResult === 'function') window.playResult(track, false, true);
        break;
      case 'play':
        if (typeof window.playResult === 'function') window.playResult(track, false, false);
        break;
      case 'save-playlist':
        if (typeof window.openAddToPlaylistModal === 'function') window.openAddToPlaylistModal(track);
        break;
      case 'open-album':
        navigateTrackAlbum(track, null);
        break;
      case 'open-artist':
        navigateTrackArtist(track);
        break;
      case 'remove-from-queue':
        if (track._queueIndex !== undefined && typeof window.removeFromQueue === 'function') {
          window.removeFromQueue(track._queueIndex, track.title, track.video_id);
        }
        break;
    }
  });

  document.addEventListener('click', function (event) {
    // Song rows and cards own playback through their local click handlers.
    // Album navigation is intentionally explicit (the context menu or the
    // dedicated now-playing banner title), never a generic song-title route.
    if (!event.target.closest('.song-context-menu')) closeMenu();
  }, true);
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') closeMenu();
  });
  window.addEventListener('blur', closeMenu);
  window.addEventListener('scroll', repositionMenu, true);

  window.openSongContextMenu = openMenu;
  window.closeSongContextMenu = closeMenu;
})();
