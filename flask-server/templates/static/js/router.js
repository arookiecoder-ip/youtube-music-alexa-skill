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
    setHidden('.play-section, .player-section, #home-section, #idle-hero', false);
    setHidden('#results-section, #queue-section', true);
  }

  var routes = {
    '#home': function() {
      showHomeViews();
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

  function showArtistSection() {
    var section = document.getElementById('artist-section');
    if (section) {
      // Search bar and bottom playbar are persistent shell chrome — they stay
      // visible on the artist page; only the content views swap out.
      setHidden('#recs-section, #home-section, #idle-hero, #results-section, #queue-section, #artist-section', true);
      setHidden('.play-section, .player-section', false);
      section.hidden = false;
    }
  }

  window.addEventListener('hashchange', function() {
    var hash = location.hash || '#home';
    if (routes[hash]) {
      routes[hash]();
    } else if (hash.indexOf('#artist/') === 0) {
      var channelId = decodeURIComponent(hash.slice('#artist/'.length));
      if (!channelId) { location.hash = '#home'; return; }
      // Leave the search-results state properly (mini player, body class,
      // _resultsOpen flag) instead of just hiding the section.
      if (window.__appState && window.__appState._resultsOpen && window.closeResults) {
        window.closeResults();
      }
      showArtistSection();
      if (window.loadArtist) window.loadArtist(channelId);
    } else {
      location.hash = '#home';
    }
  });

  // Global delegated click handler: artist-name -> navigate to artist page
  document.addEventListener('click', function(e) {
    var target = e.target.closest('.artist-name');
    if (target) {
      e.preventDefault();
      e.stopPropagation();
      var channelId = target.getAttribute('data-channel-id');
      if (channelId) {
        location.hash = '#artist/' + encodeURIComponent(channelId);
      }
    }
  });

  // Force initial route on page load.
  window.dispatchEvent(new CustomEvent('hashchange'));
})();
