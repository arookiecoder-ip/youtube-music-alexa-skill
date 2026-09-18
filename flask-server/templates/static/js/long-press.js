(function () {
  'use strict';

  var selector = [
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
  var timer = null;
  var activeButton = null;
  var activeSurface = null;
  var startX = 0;
  var startY = 0;
  var dispatchingLongPress = false;
  var suppressNextClick = null;

  function clearPress() {
    if (timer) clearTimeout(timer);
    timer = null;
    activeButton = null;
    activeSurface = null;
  }

  document.addEventListener('pointerdown', function (event) {
    if (event.pointerType === 'mouse') return;
    var button = event.target.closest(selector);
    var row = event.target.closest('.result-item-inner');
    var isRowHold = row && !event.target.closest('button, a, .result-more-menu');
    var albumCard = (!button && !isRowHold) ? event.target.closest(albumCardSelector) : null;
    if (albumCard && event.target.closest('button, a, .result-more-menu')) albumCard = null;
    if (!button && !isRowHold && !albumCard) return;
    if (button && (button.disabled || button.getAttribute('aria-disabled') === 'true')) return;
    clearPress();
    startX = event.clientX;
    startY = event.clientY;
    activeButton = button || (row && row.querySelector('.result-more-btn')) || albumCard;
    activeSurface = isRowHold ? row : (button || albumCard);
    if (!activeButton) return;
    timer = setTimeout(function () {
      if (!activeButton) return;
      suppressNextClick = activeSurface;
      if (albumCard) {
        albumCard.dispatchEvent(new MouseEvent('contextmenu', {
          bubbles: true, cancelable: true, clientX: startX, clientY: startY
        }));
      } else {
        dispatchingLongPress = true;
        activeButton.click();
        dispatchingLongPress = false;
      }
      clearPress();
    }, 350);
  }, true);

  document.addEventListener('pointerup', clearPress, true);
  document.addEventListener('pointercancel', clearPress, true);
  document.addEventListener('pointermove', function (event) {
    if (event.pointerType === 'mouse' || !activeButton) return;
    if (event.buttons === 0 ||
        Math.hypot(event.clientX - startX, event.clientY - startY) > 10) {
      clearPress();
    }
  }, true);

  document.addEventListener('click', function (event) {
    if (dispatchingLongPress) return;
    var clickedSurface = event.target.closest(selector) || event.target.closest('.result-item-inner') || event.target.closest(albumCardSelector);
    if (suppressNextClick && clickedSurface === suppressNextClick) {
      event.preventDefault();
      event.stopPropagation();
      suppressNextClick = null;
    }
  }, true);
})();
