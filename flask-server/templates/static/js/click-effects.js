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

  // Artist/album credit lines navigate away from the row, so a press there
  // must not flash the whole enclosing block. Album names share these lines
  // with artist links ("Artist • Album"), so skip all of them together.
  var CREDIT_SELECTOR = [
    '.artist-name',
    '.home-item-subtitle',
    '.result-artist',
    '.queue-artist',
    '.artist-song-artist',
    '.artist-song-album',
    '.np-artist',
    '.np-page-artist',
    '.explore-card-sub',
    '.recs-tile-artist'
  ].join(',');

  // Tiny 3-dot / more-option buttons have their own open-state affordance
  // (background + border). Rippling them too stacks a second visual effect on
  // the small circle and reads as a flash. Skip them entirely.
  var MORE_BTN_SELECTOR = [
    '.result-more-btn',
    '.queue-more-btn',
    '.track-more-btn',
    '.playlist-more-btn',
    '.playlist-hero-more',
    '.np-more-btn',
    '.mobile-player-more'
  ].join(',');

  var HOLD_DURATION = 300;
  var holdTimer = null;
  var activeElement = null;
  var hasMoved = false;

  function createRipple(e, type) {
    var target = e.target.closest(selector);
    if (!target) return;

    // Nested interactive credits (artist and album names) have their own
    // hover/active affordance and each navigates to a separate page. Rippling
    // the enclosing block on their click reads as a whole-card flash, so skip
    // it exactly like artist-name clicks already do.
    if (e.target.closest(CREDIT_SELECTOR)) return;

    // Capture the element's original inline styles once, before any ripple
    // mutates them, so rapid successive clicks all restore to the same base.
    if (!target._rippleRestore) {
      target._rippleRestore = {
        position: target.style.position,
        overflow: target.style.overflow
      };
    }
    var computed = window.getComputedStyle(target);
    if (computed.position === 'static') {
      target.style.position = 'relative';
    }
    if (computed.overflow !== 'hidden') {
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
      // Undo the styles we forced once the last ripple is gone, so repeated
      // clicks never permanently mutate the block's layout.
      if (!target.querySelector('.ripple-overlay') && target._rippleRestore) {
        target.style.position = target._rippleRestore.position;
        target.style.overflow = target._rippleRestore.overflow;
        delete target._rippleRestore;
      }
    });
  }

  document.addEventListener('pointerdown', function(e) {
    if (e.target.closest(MORE_BTN_SELECTOR)) return;
    if (e.target.closest(selector)) {
      hasMoved = false;

      if (e.pointerType === 'mouse') {
        // PC: Left and Right click
        createRipple(e, 'tap');
      } else {
        activeElement = e.target.closest(selector);
        holdTimer = setTimeout(function() {
          // Consume the timer so pointerup below does not stack a second
          // (tap) ripple on top of the hold ripple already shown.
          holdTimer = null;
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
