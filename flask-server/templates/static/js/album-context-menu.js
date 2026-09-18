(function () {
  'use strict';

  // Right-click / hold menu for album cards. Song rows/cards are owned by
  // song-context-menu.js and playlist cards by playlist-context-menu.js;
  // album cards (search shelves, artist discography, home album tiles, the
  // album Top Result hero) had no menu at all, so right-click and hold did
  // nothing on them. This mirrors the playlist menu: Play, Shuffle play and
  // Open album. The shared `result-more-menu` class keeps the mobile
  // bottom-sheet presenter (mobile-context-sheet.js) and the shared
  // `_closeAllMoreMenus` closer working without extra wiring.

  var menu = null;

  function closeMenu() {
    if (menu) menu.classList.remove('open');
  }

  function openAlbumPage(id) {
    if (window.preloadNavigateAlbum) window.preloadNavigateAlbum(id);
    else if (window.navigateTo) window.navigateTo('#album/' + encodeURIComponent(id));
  }

  function ensureMenu() {
    if (menu) return menu;
    menu = document.createElement('div');
    menu.className = 'result-more-menu album-context-menu';
    menu.innerHTML =
      '<div class="result-menu-option" role="menuitem" data-action="play"><svg viewBox="0 0 24 24" fill="currentColor"><polygon points="7,4 20,12 7,20"/></svg><span>Play</span></div>' +
      '<div class="result-menu-option" role="menuitem" data-action="shuffle"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/><polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/><line x1="4" y1="4" x2="9" y2="9"/></svg><span>Shuffle play</span></div>' +
      '<div class="result-menu-option" role="menuitem" data-action="open"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6"/></svg><span>Open album</span></div>';
    document.body.appendChild(menu);
    menu.addEventListener('click', function (event) {
      var action = event.target.closest('[data-action]');
      var album = menu._album;
      if (!action || !album || !album.id) return;
      event.stopPropagation();
      closeMenu();
      var name = action.dataset.action;
      if (name === 'open') {
        openAlbumPage(album.id);
        return;
      }
      if (!window.api) return;
      var shuffle = name === 'shuffle';
      if (window.toast) window.toast(shuffle ? 'Shuffling album\u2026' : 'Playing album\u2026');
      window.api('/api/album/' + encodeURIComponent(album.id)).then(function (albumData) {
        var tracks = (albumData && albumData.tracks) || [];
        if (!tracks.length) {
          if (window.toast) window.toast('Could not play album', 'error');
          return;
        }
        if (window.playCollection) window.playCollection(tracks, { shuffle: shuffle, openPlaybackPage: true });
        else if (window.playFromQueue) window.playFromQueue(tracks[0], 0, true);
      }).catch(function () {
        if (window.toast) window.toast('Could not play album', 'error');
      });
    });
    return menu;
  }

  window.openAlbumContextMenu = function (event, album) {
    if (!album || !album.id) return;
    event.preventDefault();
    event.stopPropagation();
    if (window._closeAllMoreMenus) window._closeAllMoreMenus();
    if (window._closeAllQueueMenus) window._closeAllQueueMenus();
    var popup = ensureMenu();
    popup._album = album;
    // Mobile bottom-sheet header reads this shape (same as the song menu).
    popup._track = { title: album.title || 'Album', artist: album.subtitle || '', thumbnail: album.thumbnail || '' };
    popup.style.left = event.clientX + 'px';
    popup.style.right = 'auto';
    popup.style.top = event.clientY + 'px';
    popup.style.bottom = 'auto';
    popup.classList.add('open');
    var rect = popup.getBoundingClientRect();
    if (rect.right > window.innerWidth - 8) {
      popup.style.left = 'auto';
      popup.style.right = Math.max(8, window.innerWidth - event.clientX) + 'px';
    }
    if (rect.bottom > window.innerHeight - 8) {
      popup.style.top = 'auto';
      popup.style.bottom = Math.max(8, window.innerHeight - event.clientY) + 'px';
    }
  };
  window.closeAlbumContextMenu = closeMenu;

  document.addEventListener('contextmenu', function (event) {
    if (event.target.closest('.result-more-menu, .queue-more-menu')) return;
    // Track rows/cards also carry an album id (title navigation), so only
    // album entities match: album-kind home tiles, album shelf cards and the
    // album Top Result hero.
    var card = event.target.closest('.home-item[data-kind="album"][data-album-id], .hscroll-card[data-album-id], .top-result-card.is-album[data-album-id]');
    if (!card) return;
    var id = card.dataset.albumId || '';
    if (!id) return;
    var title = card.dataset.title ||
      ((card.querySelector('.home-item-title, .hscroll-card-title, .top-result-title') || {}).textContent || 'Album');
    var art = card.querySelector('img');
    var sub = card.querySelector('.home-item-subtitle, .hscroll-card-artist, .hscroll-card-sub, .top-result-subtitle');
    window.openAlbumContextMenu(event, {
      id: id,
      title: String(title).trim(),
      thumbnail: (art && (art.currentSrc || art.src)) || '',
      subtitle: (sub && sub.textContent ? sub.textContent.trim() : '') || 'Album'
    });
  });
  document.addEventListener('click', function (event) {
    if (menu && !event.target.closest('.album-context-menu')) closeMenu();
  });
  document.addEventListener('scroll', closeMenu, true);
})();
