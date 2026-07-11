(function() {
  'use strict';

  function setHidden(selector, hidden) {
    document.querySelectorAll(selector).forEach(function(el) {
      el.hidden = hidden;
    });
  }

  function hideAllViews() {
    setHidden('.play-section, .player-section, #recs-section, #home-section, #idle-hero, #results-section, #queue-section, #artist-section, #album-section', true);
  }

  function showHomeViews() {
    setHidden('.play-section, #home-section', false);
    setHidden('#idle-hero', true);
    setHidden('.player-section', true);
    setHidden('#results-section, #queue-section, #artist-section, #album-section', true);
  }

  var routes = {
    '#home': function() {
      showHomeViews();
    },
    '#playlists': function() {
      if (window.openPlaylistsModal) window.openPlaylistsModal(true);
    },
    '#history': function() {
      if (window.openHistoryPage) window.openHistoryPage(true);
    },
    '#now-playing': function() {
      // Now Playing owns the viewport. Do not leave Home/results underneath;
      // they can peek through during resizing and keep the document scrollable.
      hideAllViews();
      setHidden('.player-section', false);
      if (window._openMiniPopup) window._openMiniPopup(true);
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
      setHidden('.play-section', false);
      setHidden('.player-section', true);
      section.hidden = false;
    }
  }

  window.addEventListener('hashchange', function() {
    var hash = location.hash || '#home';
    document.body.classList.toggle('now-playing-route', hash === '#now-playing');
    document.body.classList.toggle('playlists-route', hash === '#playlists' || hash.indexOf('#playlist/') === 0);
    document.body.classList.toggle('history-route', hash === '#history');

    // Routed desktop pages reuse overlay markup, so explicitly dismiss layers
    // belonging to the previous route. Otherwise an invisible full-screen
    // layer can keep intercepting sidebar clicks after navigation.
    if (hash !== '#playlists' && hash.indexOf('#playlist/') !== 0) {
      var playlistsOverlay = document.getElementById('playlists-modal-overlay');
      var detailOverlay = document.getElementById('playlist-detail-modal-overlay');
      if (playlistsOverlay) playlistsOverlay.classList.remove('open');
      if (detailOverlay) detailOverlay.classList.remove('open');
    }
    if (hash !== '#history') {
      var historyOverlay = document.getElementById('history-modal-overlay');
      if (historyOverlay) historyOverlay.classList.remove('open');
    }
    if (hash !== '#now-playing') {
      setHidden('.player-section, #queue-section', true);
      var main = document.querySelector('main');
      if (main) main.classList.remove('has-queue');
    }
    if (hash !== '#now-playing' && window._closeMiniPopup) window._closeMiniPopup();
    if (routes[hash]) {
      routes[hash]();
    } else if (hash.indexOf('#playlist/') === 0) {
      var playlistId = decodeURIComponent(hash.slice('#playlist/'.length));
      if (playlistId && window.openPlaylistDetailModal) window.openPlaylistDetailModal(playlistId, true);
    } else if (hash.indexOf('#album/') === 0) {
      var albumId = decodeURIComponent(hash.slice('#album/'.length));
      if (!albumId) { location.hash = '#home'; return; }
      hideAllViews();
      var albumSection = document.getElementById('album-section');
      if (albumSection) albumSection.hidden = false;
      if (window.loadAlbum) window.loadAlbum(albumId);
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
