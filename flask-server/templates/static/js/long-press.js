(function () {
  'use strict';

  // 3-dot buttons: a hold re-fires the button's own click, which already
  // opens the right menu with the right data.
  var moreBtnSelector = [
    '.result-more-btn',
    '.queue-more-btn',
    '.playlist-hero-more',
    '.playlist-more-btn',
    '.artist-song-more-btn',
    '.np-more-btn',
    '#mobile-player-more'
  ].join(',');
  // Album cards have no 3-dot button: a hold opens their menu through the
  // same contextmenu path as a PC right-click (album-context-menu.js).
  var albumCardSelector = [
    '.home-item[data-kind="album"][data-album-id]',
    '.hscroll-card[data-album-id]',
    '.top-result-card.is-album[data-album-id]'
  ].join(',');
  // Playlist cards have no 3-dot button either: a hold opens their menu
  // through the same contextmenu path as a PC right-click
  // (playlist-context-menu.js). Mirrors that module's own card selector plus
  // the home rows handler so a hold works everywhere a right-click does:
  // home tiles (including station cards, which the home handler routes to
  // the playlist menu), search shelves, library cards and the playlist Top
  // Result hero.
  var playlistCardSelector = [
    '.home-item[data-kind="playlist"][data-playlist-id]',
    '.home-item[data-kind="station"][data-playlist-id]',
    '.hscroll-card[data-playlist-id]',
    '[data-playlist-context]'
  ].join(',');
  // Every other song row / card whose press-and-hold must behave exactly like
  // a PC right-click. The hold dispatches a synthetic contextmenu on the
  // held element, so handler resolution (song / queue-row / home-rows /
  // explore-card / album / playlist modules) is identical to a real
  // right-click at that spot by construction and can never drift from it.
  var songSurfaceSelector = [
    '.result-item-inner',               // search + detail song rows
    '.result-swipe-wrapper[data-video-id]',
    '.artist-song-row',                 // artist top songs
    '.queue-item',                      // queue rows (own contextmenu -> menu)
    '.queue-swipe-wrapper[data-video-id]',
    '.history-item',                    // history page + album/detail rows
    '.home-item[data-video-id]',        // home track cards, explore mood rows
    '.recs-tile',                       // recommendation tiles
    '.explore-card',                    // explore cards (own contextmenu handler)
    '.top-result-card[data-video-id]'   // search top-result song hero
  ].join(',');
  // A press starting on an interactive descendant is that control's own
  // gesture (tap the artist link, like button, play button, ...), never a
  // hold-to-open-menu.
  var interactiveSelector =
    'button, a, input, textarea, select, [contenteditable], .result-more-menu, .queue-more-menu';

  var timer = null;
  var activeSurface = null;
  var startX = 0;
  var startY = 0;
  var dispatchingLongPress = false;
  var suppressNextClick = null;
  var suppressArmedAt = 0;
  // A by-product swallow belongs to the gesture that armed it. If its click
  // never arrives on the surface (release drifted onto a blank area), the
  // entry must expire instead of eating a later genuine tap on that surface.
  var SUPPRESS_TTL_MS = 1000;

  function clearPress() {
    if (timer) clearTimeout(timer);
    timer = null;
    activeSurface = null;
  }

  document.addEventListener('pointerdown', function (event) {
    if (event.pointerType === 'mouse') return;
    // A new gesture ends any previous by-product window: the release click of
    // the old gesture always precedes the next gesture's pointerdown.
    suppressNextClick = null;
    var button = event.target.closest(moreBtnSelector);
    var surface = null;
    if (!button && !event.target.closest(interactiveSelector)) {
      surface = event.target.closest(songSurfaceSelector) ||
        event.target.closest(albumCardSelector) ||
        event.target.closest(playlistCardSelector);
    }
    if (!button && !surface) return;
    if (button && (button.disabled || button.getAttribute('aria-disabled') === 'true')) return;
    clearPress();
    startX = event.clientX;
    startY = event.clientY;
    activeSurface = button || surface;
    timer = setTimeout(function () {
      if (!activeSurface) return;
      suppressNextClick = activeSurface;
      suppressArmedAt = Date.now();
      // Arm the bottom-sheet by-product swallow SYNCHRONOUSLY while the
      // finger is still down. The sheet module otherwise arms only via its
      // async MutationObserver/rAF reconcile, so a release landing within a
      // frame of the open (or a hold held past the grace window, re-armed on
      // pointerup) would close the just-opened menu. Guarded: safe when the
      // sheet module is absent or older.
      if (typeof window._armHoldReleaseSwallow === 'function') {
        try { window._armHoldReleaseSwallow(); } catch (_) { /* plain hold */ }
      }
      if (surface) {
        surface.dispatchEvent(new MouseEvent('contextmenu', {
          bubbles: true, cancelable: true, clientX: startX, clientY: startY
        }));
      } else {
        dispatchingLongPress = true;
        activeSurface.click();
        dispatchingLongPress = false;
      }
      clearPress();
    }, 350);
  }, true);

  document.addEventListener('pointerup', clearPress, true);
  document.addEventListener('pointercancel', clearPress, true);
  document.addEventListener('pointermove', function (event) {
    if (event.pointerType === 'mouse' || !activeSurface) return;
    if (event.buttons === 0 ||
        Math.hypot(event.clientX - startX, event.clientY - startY) > 10) {
      clearPress();
    }
  }, true);

  document.addEventListener('click', function (event) {
    if (dispatchingLongPress) return;
    if (suppressNextClick && Date.now() - suppressArmedAt > SUPPRESS_TTL_MS) {
      suppressNextClick = null;
    }
    if (!suppressNextClick) return;
    var clickedSurface = event.target.closest(moreBtnSelector) ||
      event.target.closest(songSurfaceSelector) ||
      event.target.closest(albumCardSelector) ||
      event.target.closest(playlistCardSelector);
    if (suppressNextClick && clickedSurface === suppressNextClick) {
      event.preventDefault();
      event.stopPropagation();
      suppressNextClick = null;
    }
  }, true);
})();
