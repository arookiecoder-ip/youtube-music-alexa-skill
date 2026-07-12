(function () {
  'use strict';

  const ROOT_SELECTOR = [
    '.home-item[data-video-id]',
    '.recs-tile',
    '.artist-song-row',
    '.album-track',
    '.history-item',
    '.queue-swipe-wrapper[data-video-id]',
    '.result-swipe-wrapper[data-video-id]',
    '.top-result-card[data-video-id]',
    '#now-playing',
    '.np-page-left'
  ].join(',');

  const icon = {
    like: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>',
    next: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>',
    add: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
    radio: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4.93 19.07A10 10 0 1 1 19.07 4.93 10 10 0 0 1 4.93 19.07z"/><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2"/></svg>',
    save: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/><line x1="12" y1="11" x2="12" y2="17"/><line x1="9" y1="14" x2="15" y2="14"/></svg>'
  };

  const menu = document.createElement('div');
  menu.className = 'result-more-menu song-context-menu';
  menu.innerHTML =
    '<div class="result-menu-option" data-action="toggle-like">' + icon.like + '<span>Like</span></div>' +
    '<div class="result-menu-option" data-action="play-next">' + icon.next + '<span>Play next</span></div>' +
    '<div class="result-menu-option" data-action="add-to-queue">' + icon.add + '<span>Add to queue</span></div>' +
    '<div class="result-menu-option" data-action="play-radio">' + icon.radio + '<span>Play Radio</span></div>' +
    '<div class="result-menu-option" data-action="save-playlist">' + icon.save + '<span>Save to Playlist</span></div>';
  document.body.appendChild(menu);

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
    if (root._songContextTrack) return Object.assign({}, root._songContextTrack);
    const state = window.__appState || {};
    const isNowPlaying = root.id === 'now-playing' || root.classList.contains('np-page-left');
    const videoId = isNowPlaying
      ? state._currentVideoId
      : root.dataset.videoId || root.closest('[data-video-id]')?.dataset.videoId || '';
    return {
      video_id: videoId || '',
      title: text(root, ['.home-item-title', '.recs-tile-title', '.artist-song-title', '.queue-title', '.result-title', '.top-result-title', '.np-title', '.np-page-title']),
      artist: text(root, ['.home-item-subtitle', '.recs-tile-artist', '.artist-song-artist', '.queue-artist', '.result-artist', '.top-result-subtitle', '.np-artist', '.np-page-artist']),
      thumbnail: isNowPlaying ? (state._currentThumbnail || '') : image(root)
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
    menu.querySelector('[data-action="toggle-like"] span').textContent = liked ? 'Dislike' : 'Like';

    // Jam guests only retain the server-authorized radio action. They never
    // receive account-writing or host-queue mutation controls.
    menu.querySelector('[data-action="toggle-like"]').hidden = !!window.JAM_GUEST;
    menu.querySelector('[data-action="play-next"]').hidden = !!window.JAM_GUEST;
    menu.querySelector('[data-action="add-to-queue"]').hidden = !!window.JAM_GUEST;
    menu.querySelector('[data-action="save-playlist"]').hidden = !!window.JAM_GUEST;

    menu.style.left = event.clientX + 'px';
    menu.style.right = 'auto';
    menu.style.top = event.clientY + 'px';
    menu.style.bottom = 'auto';
    menu.classList.add('open');

    const rect = menu.getBoundingClientRect();
    if (rect.right > window.innerWidth - 8) {
      menu.style.left = 'auto';
      menu.style.right = Math.max(8, window.innerWidth - event.clientX) + 'px';
    }
    if (rect.bottom > window.innerHeight - 8) {
      menu.style.top = 'auto';
      menu.style.bottom = Math.max(8, window.innerHeight - event.clientY) + 'px';
    }
  }

  document.addEventListener('contextmenu', function (event) {
    if (event.target.closest('.result-more-menu, .queue-more-menu')) return;
    const root = event.target.closest(ROOT_SELECTOR);
    if (!root) {
      closeMenu();
      return;
    }
    const track = trackFrom(root);
    if (!track.video_id) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    openMenu(event, track);
  }, true);

  menu.addEventListener('click', function (event) {
    const option = event.target.closest('[data-action]');
    const track = menu._track;
    if (!option || !track) return;
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
      case 'save-playlist':
        if (typeof window.openAddToPlaylistModal === 'function') window.openAddToPlaylistModal(track);
        break;
    }
  });

  document.addEventListener('click', function (event) {
    if (!event.target.closest('.song-context-menu')) closeMenu();
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') closeMenu();
  });
  window.addEventListener('blur', closeMenu);
  window.addEventListener('scroll', closeMenu, true);

  window.openSongContextMenu = openMenu;
  window.closeSongContextMenu = closeMenu;
})();
