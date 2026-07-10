(function() {
  'use strict';

  function setHidden(selector, hidden) {
    document.querySelectorAll(selector).forEach(function(el) {
      el.hidden = hidden;
    });
  }

  function hideAllViews() {
    setHidden('.play-section, .player-section, #recs-section, #home-section, #idle-hero, #results-section, #queue-section', true);
  }

  function showHomeViews() {
    setHidden('.play-section, .player-section, #recs-section, #home-section, #idle-hero', false);
    setHidden('#results-section, #queue-section', true);
  }

  var routes = {
    '#home': function() {
      showHomeViews();
    },
    '#artist': function() {
      // Phase 7 wires the artist view; this stub prevents page-reload fallback to #home.
      hideAllViews();
    },
    '#playlist': function() {
      var overlay = document.getElementById('playlists-modal-overlay');
      if (overlay) {
        overlay.classList.add('open');
      }
    },
    '#queue': function() {
      var queueSection = document.getElementById('queue-section');
      var resultsSection = document.getElementById('results-section');
      if (queueSection) {
        queueSection.hidden = false;
      }
      if (resultsSection) {
        resultsSection.hidden = true;
      }
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
