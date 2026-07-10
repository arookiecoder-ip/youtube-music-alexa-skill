(function() {
  'use strict';

  var routes = {
    '#home': function() {
      // Phase 4: no-op. Future phases add #artist, #playlist, #queue.
    },
  };

  window.addEventListener('hashchange', function() {
    var hash = location.hash || '#home';
    if (routes[hash]) {
      routes[hash]();
    } else {
      location.hash = '#home';
    }
  });

  // Force initial route on page load.
  window.dispatchEvent(new CustomEvent('hashchange'));
})();
