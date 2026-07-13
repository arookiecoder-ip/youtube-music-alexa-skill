(function () {
  'use strict';

  var menu = null;

  function closeMenu() {
    if (menu) menu.classList.remove('open');
  }

  function ensureMenu() {
    if (menu) return menu;
    menu = document.createElement('div');
    menu.className = 'result-more-menu playlist-context-menu';
    menu.innerHTML =
      '<button type="button" class="result-menu-option" data-action="play"><svg viewBox="0 0 24 24" fill="currentColor"><polygon points="7,4 20,12 7,20"/></svg><span>Play</span></button>' +
      '<button type="button" class="result-menu-option" data-action="shuffle"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/><polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/><line x1="4" y1="4" x2="9" y2="9"/></svg><span>Shuffle play</span></button>' +
      '<button type="button" class="result-menu-option" data-action="open"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6"/></svg><span>Open playlist</span></button>';
    document.body.appendChild(menu);
    menu.addEventListener('click', function (event) {
      var action = event.target.closest('[data-action]');
      var playlist = menu._playlist;
      if (!action || !playlist || !playlist.id) return;
      event.stopPropagation();
      closeMenu();
      if (action.dataset.action === 'open') {
        if (window.preloadNavigatePlaylist) window.preloadNavigatePlaylist(playlist.id);
        else if (window.navigateTo) window.navigateTo('#playlist/' + encodeURIComponent(playlist.id));
        return;
      }
      if (!window.api) return;
      window.api('/alexa/play/', {
        serial: window.selectedSerial ? window.selectedSerial() : '',
        query: 'https://music.youtube.com/playlist?list=' + playlist.id
      }).then(function () {
        if (action.dataset.action !== 'shuffle') return;
        setTimeout(function () {
          window.api('/alexa/shuffle_queue/', {}).catch(function () {});
        }, 1200);
      }).catch(function () {
        if (window.toast) window.toast('Could not start playlist', 'error');
      });
    });
    return menu;
  }

  window.openPlaylistContextMenu = function (event, playlist) {
    if (!playlist || !playlist.id) return;
    event.preventDefault();
    event.stopPropagation();
    if (window._closeAllMoreMenus) window._closeAllMoreMenus();
    var popup = ensureMenu();
    popup._playlist = playlist;
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

  document.addEventListener('contextmenu', function (event) {
    var card = event.target.closest('.home-item[data-kind="playlist"][data-playlist-id], .hscroll-card[data-playlist-id], [data-playlist-context]');
    if (!card) return;
    var id = card.dataset.playlistId || card.dataset.playlistContext || '';
    if (!id) return;
    var title = card.dataset.playlistTitle || card.dataset.title ||
      (card.querySelector('.home-item-title, .hscroll-card-title, .library-card-title, .explore-card-title') || {}).textContent || 'Playlist';
    window.openPlaylistContextMenu(event, { id: id, title: title.trim() });
  });
  document.addEventListener('click', function (event) {
    if (menu && !event.target.closest('.playlist-context-menu')) closeMenu();
  });
  document.addEventListener('scroll', closeMenu, true);
})();
