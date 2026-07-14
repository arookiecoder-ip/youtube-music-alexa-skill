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
    if (!button && !isRowHold) return;
    if (button && (button.disabled || button.getAttribute('aria-disabled') === 'true')) return;
    clearPress();
    startX = event.clientX;
    startY = event.clientY;
    activeButton = button || row.querySelector('.result-more-btn');
    activeSurface = isRowHold ? row : button;
    if (!activeButton) return;
    timer = setTimeout(function () {
      if (!activeButton) return;
      suppressNextClick = activeSurface;
      dispatchingLongPress = true;
      activeButton.click();
      dispatchingLongPress = false;
      clearPress();
    }, 550);
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
    var clickedSurface = event.target.closest(selector) || event.target.closest('.result-item-inner');
    if (suppressNextClick && clickedSurface === suppressNextClick) {
      event.preventDefault();
      event.stopPropagation();
      suppressNextClick = null;
    }
  }, true);
})();
