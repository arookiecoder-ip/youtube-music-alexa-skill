(function () {
  'use strict';

  var selector = [
    'button',
    'a',
    '[role="button"]',
    '.home-card',
    '.home-item',
    '.result-item-inner',
    '.explore-card',
    '.artist-song-row',
    '.playlist-hero-shuffle',
    '.playlist-hero-add-queue',
    '.history-item',
    '.queue-item',
    '.custom-select-trigger',
    '.custom-select-option',
    '.sidebar-nav-btn'
  ].join(',');

  var HOLD_DURATION = 300;
  var holdTimer = null;
  var activeElement = null;
  var hasMoved = false;

  function createRipple(e, type) {
    var target = e.target.closest(selector);
    if (!target) return;

    var style = window.getComputedStyle(target);
    if (style.position === 'static') {
      target.style.position = 'relative';
    }
    if (style.overflow !== 'hidden') {
      target.style.overflow = 'hidden';
    }

    var rect = target.getBoundingClientRect();
    var x = e.clientX - rect.left;
    var y = e.clientY - rect.top;

    var size = Math.sqrt(Math.pow(rect.width, 2) + Math.pow(rect.height, 2)) * 2;

    var ripple = document.createElement('span');
    ripple.className = 'ripple-overlay' + (type === 'hold' ? ' ripple-hold' : '');
    ripple.style.width = size + 'px';
    ripple.style.height = size + 'px';
    ripple.style.left = (x - size / 2) + 'px';
    ripple.style.top = (y - size / 2) + 'px';

    target.querySelectorAll('.ripple-overlay').forEach(function(r) { r.remove(); });
    target.appendChild(ripple);

    ripple.addEventListener('animationend', function() {
      ripple.remove();
    });
  }

  document.addEventListener('pointerdown', function(e) {
    if (e.target.closest(selector)) {
      hasMoved = false;
      
      if (e.pointerType === 'mouse') {
        // PC: Left and Right click
        createRipple(e, 'tap');
      } else {
        activeElement = e.target.closest(selector);
        holdTimer = setTimeout(function() {
          if (activeElement) {
            createRipple(e, 'hold');
          }
        }, HOLD_DURATION);
      }
    }
  }, true);

  document.addEventListener('pointerup', function(e) {
    if (e.pointerType !== 'mouse' && !hasMoved) {
      if (holdTimer) {
        // It was a tap (released before hold timer)
        createRipple(e, 'tap');
      }
    }
    clearMobileHold();
  }, true);

  document.addEventListener('pointercancel', clearMobileHold, true);
  document.addEventListener('pointermove', function(e) {
    if (e.pointerType !== 'mouse') {
      hasMoved = true;
      clearMobileHold();
    }
  }, true);

  function clearMobileHold() {
    if (holdTimer) {
      clearTimeout(holdTimer);
      holdTimer = null;
    }
    activeElement = null;
  }

})();
