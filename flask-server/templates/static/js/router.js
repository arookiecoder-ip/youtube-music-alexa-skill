(function() {
  'use strict';

  function setHidden(selector, hidden) {
    document.querySelectorAll(selector).forEach(function(el) {
      el.hidden = hidden;
    });
  }

  function hideAllViews() {
    setHidden('.play-section, #recs-section, #home-section, #idle-hero, #results-section, #queue-section, #artist-section, #album-section', true);
  }

  function showHomeViews() {
    var state = window.__appState;
    var homeReady = !!(state && state._loggedIn && state._homeLoaded);
    var homeSection = document.getElementById('home-section');
    if (homeReady && homeSection) homeSection.classList.add('home-cached');
    setHidden('.play-section', false);
    setHidden('#home-section', !homeReady);
    setHidden('#idle-hero', true);
    setHidden('#results-section, #queue-section, #artist-section, #album-section', true);
  }

  var routes = {
    '#home': function() {
      // Search results live on the #home route (searching never navigates).
      // If they were open when another view (e.g. the expanded player) took
      // over, restore them instead of the home feed — otherwise both stay
      // hidden (_resultsOpen keeps syncUiState from showing home) and the
      // page goes blank.
      if (window.__appState && window.__appState._resultsOpen) {
        setHidden('.play-section, #results-section', false);
        setHidden('#home-section, #idle-hero, #queue-section, #artist-section, #album-section', true);
      } else {
        showHomeViews();
      }
    },
    '#explore': function() {
      if (window.openExplorePage) window.openExplorePage();
    },
    '#library': function() {
      if (window.openLibraryPage) window.openLibraryPage();
    },
    '#history': function() {
      if (window.openHistoryPage) window.openHistoryPage(true);
    },
    '#now-playing': function() {
      // Show the dedicated now-playing page (album art + queue) in the main content area.
      // This replaces the old mini-popup overlay approach.
      hideAllViews();
      // Search is persistent top-bar chrome on desktop, including while the
      // expanded player is open.
      setHidden('.play-section', false);
      setHidden('.player-section', false);
      var npSection = document.getElementById('now-playing-section');
      if (npSection) npSection.hidden = false;
      // Re-apply the current track artwork whenever the page opens. Player
      // updates can happen while this view is hidden, so the route must not
      // fall back to the default app tint.
      var npPage = npSection && npSection.querySelector('.np-page');
      var currentThumb = window.__appState && window.__appState._currentThumbnail;
      if (npPage) {
        if (currentThumb) {
          npPage.style.setProperty('--np-cover', 'url(' + JSON.stringify(currentThumb) + ')');
        } else {
          npPage.style.removeProperty('--np-cover');
        }
      }
      // Populate the in-page queue from the last known queue data
      if (window._lastQueueJson && window.renderNpQueue) {
        try {
          var queue = JSON.parse(window._lastQueueJson);
          window.renderNpQueue(queue, window._lastQueueIndex || 0);
        } catch(_) {}
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
      setHidden('.play-section', false);
      section.hidden = false;
    }
  }

  /* Clean-URL routing: the current route lives in history.state (and the
     window.__route mirror), never in the address bar. Route tokens keep the
     legacy '#name' format so all existing comparisons still work. */
  window.__route = '#home';
  window.getRoute = function() { return window.__route || '#home'; };

  var _scrollCache = {};
  function _routeScrollId(route) {
    if (!route) return null;
    if (route.indexOf('#artist/') === 0) return 'artist-section';
    if (route.indexOf('#album/') === 0) return 'album-section';
    if (route === '#now-playing') return 'now-playing-section';
    if (route === '#history') return 'history-modal';
    if (route === '#explore') return 'explore-modal';
    if (route === '#library') return 'library-modal';
    if (route.indexOf('#playlist/') === 0) return 'playlist-detail-modal-overlay';
    return 'main'; // #home, search results
  }
  function _saveScroll() {
    var id = _routeScrollId(window.__route);
    if (!id) return;
    var el = document.getElementById(id) || document.querySelector(id);
    if (el) _scrollCache[window.__route] = el.scrollTop;
  }
  function _restoreScroll() {
    var id = _routeScrollId(window.__route);
    if (!id) return;
    if (_scrollCache[window.__route] != null) {
      var el = document.getElementById(id) || document.querySelector(id);
      if (el) el.scrollTop = _scrollCache[window.__route];
    }
  }
  function resetRouteScroll() {
    window.scrollTo(0, 0);
    document.documentElement.scrollTop = 0;
    document.body.scrollTop = 0;
    [
      'main', 'results-section', 'results-list', 'artist-section',
      'album-section', 'now-playing-section', 'playlist-detail-modal',
      'playlist-detail-modal-overlay', 'history-modal', 'history-modal-overlay',
      'explore-modal', 'explore-modal-overlay', 'library-modal', 'library-modal-overlay'
    ].forEach(function(id) {
      var el = id === 'main' ? document.querySelector('main') : document.getElementById(id);
      if (el) el.scrollTop = 0;
    });
  }
  // Close the now-playing overlay without re-applying the underlying route.
  // navigateTo() would re-run the return route's handler (e.g., re-fetching
  // playlist data), but the overlay should just slide away and leave the page
  // underneath exactly as it was.
  window.closeNowPlayingOverlay = function() {
    if (!document.body.classList.contains('now-playing-route')) return;
    var npSection = document.getElementById('now-playing-section');
    var returnRoute = window.__npReturnRoute || '#home';

    // Update route state without triggering applyRoute
    window.__route = returnRoute;
    history.replaceState({ route: returnRoute }, '', location.pathname + location.search);

    // Restore the return route's body classes
    document.body.classList.toggle('home-route', returnRoute === '#home');
    document.body.classList.toggle('playlists-route', returnRoute.indexOf('#playlist/') === 0);
    document.body.classList.toggle('artist-route', returnRoute.indexOf('#artist/') === 0);
    document.body.classList.toggle('album-route', returnRoute.indexOf('#album/') === 0);
    document.body.classList.toggle('history-route', returnRoute === '#history');
    document.body.classList.toggle('explore-route', returnRoute === '#explore');
    document.body.classList.toggle('library-route', returnRoute === '#library');

    // Restore visibility of the underlying page NOW, before the slide-down
    // animation starts. The overlay is on top (position:fixed, z-index:210)
    // so the user won't see any layout jumps — they just see the previous
    // page already rendered behind the sliding overlay.
    if (returnRoute === '#home') {
      var state = window.__appState;
      if (state && state._resultsOpen) {
        setHidden('.play-section, #results-section', false);
        setHidden('#home-section, #idle-hero, #queue-section, #artist-section, #album-section', true);
      } else {
        showHomeViews();
      }
    } else if (returnRoute.indexOf('#playlist/') === 0) {
      var po = document.getElementById('playlist-detail-modal-overlay');
      if (po) po.classList.add('open');
    } else if (returnRoute === '#history') {
      var ho = document.getElementById('history-modal-overlay');
      if (ho) ho.classList.add('open');
    } else if (returnRoute === '#explore') {
      var eo = document.getElementById('explore-modal-overlay');
      if (eo) eo.classList.add('open');
    } else if (returnRoute === '#library') {
      var lo = document.getElementById('library-modal-overlay');
      if (lo) lo.classList.add('open');
    } else if (returnRoute.indexOf('#artist/') === 0) {
      showArtistSection();
    } else if (returnRoute.indexOf('#album/') === 0) {
      setHidden('#recs-section, #home-section, #idle-hero, #results-section, #queue-section, #artist-section', true);
      setHidden('.play-section', false);
      var albumSection = document.getElementById('album-section');
      if (albumSection) albumSection.hidden = false;
    }

    // Trigger the closing slide-out animation
    document.body.classList.remove('now-playing-route');
    document.body.classList.add('now-playing-closing');

    // Clean up queue panel state
    var queueSection = document.getElementById('queue-section');
    if (queueSection) queueSection.hidden = true;
    var main = document.querySelector('main');
    if (main) main.classList.remove('has-queue');

    if (npSection) {
      if (npSection._closeTimer) clearTimeout(npSection._closeTimer);
      if (npSection._closeCleanup) npSection.removeEventListener('transitionend', npSection._closeCleanup);
      var finishClose = function(event) {
        if (event && (event.target !== npSection || event.propertyName !== 'transform')) return;
        if (npSection._closeTimer) clearTimeout(npSection._closeTimer);
        npSection.removeEventListener('transitionend', finishClose);

        // Animation complete: only cleanup remains. The underlying page is
        // already visible (restored before the animation started).
        npSection.hidden = true;
        npSection._closeTimer = null;
        npSection._closeCleanup = null;
        document.body.classList.remove('now-playing-closing');
        document.documentElement.style.removeProperty('overflow');
        document.body.style.removeProperty('overflow');
      };
      npSection._closeCleanup = finishClose;
      npSection.addEventListener('transitionend', finishClose);
      npSection._closeTimer = setTimeout(finishClose, 450);
    }
  };

  window.navigateTo = function(route) {
    route = route || '#home';
    var changedRoute = route !== window.__route;
    if (changedRoute) {
      _saveScroll();
      if (route === '#now-playing') window.__npReturnRoute = window.__route;
      window.__route = route;
      history.pushState({ route: route }, '', location.pathname + location.search);
    }
    applyRoute(route);
    if (changedRoute) {
      resetRouteScroll();
      _restoreScroll();
      requestAnimationFrame(_restoreScroll);
    }
  };
  window.addEventListener('popstate', function(e) {
    _saveScroll();
    window.__route = (e.state && e.state.route) || '#home';
    applyRoute(window.__route);
    resetRouteScroll();
    _restoreScroll();
    requestAnimationFrame(_restoreScroll);
  });

  function applyRoute(hash) {
    hash = hash || '#home';
    var wasNowPlaying = document.body.classList.contains('now-playing-route');
    var isClosingNowPlaying = wasNowPlaying && hash !== '#now-playing' &&
      window.matchMedia('(min-width: 900px)').matches;
    var routedNpSection = document.getElementById('now-playing-section');
    if (routedNpSection && routedNpSection._closeTimer) {
      clearTimeout(routedNpSection._closeTimer);
      routedNpSection._closeTimer = null;
    }
    if (routedNpSection && routedNpSection._closeCleanup) {
      routedNpSection.removeEventListener('transitionend', routedNpSection._closeCleanup);
      routedNpSection._closeCleanup = null;
    }
    // Unhide the now-playing section BEFORE the body class toggles so the
    // CSS transition has a visible start state (translateY(103%)) to animate from.
    if (hash === '#now-playing' && routedNpSection) {
      routedNpSection.hidden = false;
      void routedNpSection.offsetHeight;
    }
    document.body.classList.toggle('home-route', hash === '#home');
    document.body.classList.toggle('now-playing-route', hash === '#now-playing');
    document.body.classList.toggle('now-playing-closing', isClosingNowPlaying);
    document.body.classList.toggle('playlists-route', hash.indexOf('#playlist/') === 0);
    document.body.classList.toggle('history-route', hash === '#history');
    document.body.classList.toggle('explore-route', hash === '#explore');
    document.body.classList.toggle('library-route', hash === '#library');
    document.body.classList.toggle('artist-route', hash.indexOf('#artist/') === 0);
    document.body.classList.toggle('album-route', hash.indexOf('#album/') === 0);

    // Routed desktop pages reuse overlay markup, so explicitly dismiss layers
    // belonging to the previous route. Otherwise an invisible full-screen
    // layer can keep intercepting sidebar clicks after navigation.
    if (hash.indexOf('#playlist/') !== 0) {
      var detailOverlay = document.getElementById('playlist-detail-modal-overlay');
      if (detailOverlay) detailOverlay.classList.remove('open');
    }
    if (hash !== '#history') {
      var historyOverlay = document.getElementById('history-modal-overlay');
      if (historyOverlay) historyOverlay.classList.remove('open');
    }
    if (hash !== '#explore') {
      var exploreOverlay = document.getElementById('explore-modal-overlay');
      if (exploreOverlay) exploreOverlay.classList.remove('open');
    }
    if (hash !== '#library') {
      var libraryOverlay = document.getElementById('library-modal-overlay');
      if (libraryOverlay) libraryOverlay.classList.remove('open');
    }
    if (hash !== '#now-playing') {
      var npSection = document.getElementById('now-playing-section');
      if (npSection && !isClosingNowPlaying) npSection.hidden = true;
      if (npSection && isClosingNowPlaying) {
        // Keep the layer rendered until its transform transition finishes.
        npSection.hidden = false;
        var finishClose = function(event) {
          if (event && (event.target !== npSection || event.propertyName !== 'transform')) return;
          if (npSection._closeTimer) clearTimeout(npSection._closeTimer);
          npSection.removeEventListener('transitionend', finishClose);
          npSection.hidden = true;
          npSection._closeTimer = null;
          npSection._closeCleanup = null;
          document.body.classList.remove('now-playing-closing');
          // Belt-and-suspenders: restore scroll in case overflow got stuck.
          document.documentElement.style.removeProperty('overflow');
          document.body.style.removeProperty('overflow');
        };
        npSection._closeCleanup = finishClose;
        npSection.addEventListener('transitionend', finishClose);
        // Fallback for reduced motion, background tabs, or interrupted CSS.
        npSection._closeTimer = setTimeout(finishClose, 450);
      }
      setHidden('#queue-section', true);
      var main = document.querySelector('main');
      if (main) main.classList.remove('has-queue');
    }
    // Safety: when landing on home, always ensure scroll is not locked.
    if (hash === '#home') {
      // Do not clear the close class here: it is the state that drives the
      // slide-out. transitionend owns cleanup when returning from playback.
      if (!isClosingNowPlaying) document.body.classList.remove('now-playing-closing');
      document.documentElement.style.removeProperty('overflow');
      document.body.style.removeProperty('overflow');
      // Restore search results if they were open before navigating to sub-page
      if (window.__appState && window.__appState._resultsOpen) {
        var rs = document.getElementById('results-section');
        if (rs) rs.hidden = false;
      }
    }
    if (hash !== '#now-playing' && window._closeMiniPopup) window._closeMiniPopup();
    if (routes[hash]) {
      routes[hash]();
    } else if (hash.indexOf('#playlist/') === 0) {
      var playlistId = decodeURIComponent(hash.slice('#playlist/'.length));
      if (playlistId && window.openPlaylistDetailModal) window.openPlaylistDetailModal(playlistId, true);
    } else if (hash.indexOf('#album/') === 0) {
      var albumId = decodeURIComponent(hash.slice('#album/'.length));
      if (!albumId) { window.navigateTo('#home'); return; }
      hideAllViews();
      setHidden('.play-section', false);
      if (window.loadAlbum) window.loadAlbum(albumId);
    } else if (hash.indexOf('#artist/') === 0) {
      var channelId = decodeURIComponent(hash.slice('#artist/'.length));
      if (!channelId) { window.navigateTo('#home'); return; }
      showArtistSection();
      if (window.loadArtist) window.loadArtist(channelId);
    } else {
      window.navigateTo('#home');
      return;
    }

    // View routing may set `hidden` on the persistent playbar. Reconcile the
    // shell after every route change so an already-playing track is restored
    // even when no new playback event arrives afterward.
    if (window.syncUiState) window.syncUiState();
  }

  // ---- Artist links: multi-artist aware ----
  // A track's stored channelId belongs to its primary (first-credited) artist,
  // so a combined credit like "PDNY and Hydr and YDV" must not send every
  // click to the first artist's page. artistLinksHtml splits the credit into
  // one clickable span per artist: the first keeps the known channelId, the
  // rest carry only their name and are resolved via search on click.
  var ARTIST_SEP_RE = /(,\s*|\s*&\s*|\s+and\s+|\s*·\s*|\s+(?:feat\.?|ft\.?|featuring)\s+)/i;

  window.artistLinksHtml = function(artist, channelIds) {
    var esc = window.escHtml;
    var s = String(artist || '').trim();
    if (!s) return '';
    // Normalise: a single channelId string → array, so the same position-
    // based lookup works for both old callers and new array-aware callers.
    if (!Array.isArray(channelIds)) channelIds = channelIds ? [channelIds] : [];
    // split with a capture group keeps the separators at odd indices so the
    // displayed text stays byte-for-byte what the metadata said
    var parts = s.split(new RegExp(ARTIST_SEP_RE.source, 'gi'));
    var artistIdx = 0;
    return parts.map(function(p, i) {
      if (i % 2 === 1) return esc(p); // separator text, not clickable
      if (!p) return '';
      var attrs = ' data-artist-name="' + esc(p) + '"';
      var cid = channelIds[artistIdx];
      if (cid) attrs += ' data-channel-id="' + esc(cid) + '"';
      artistIdx++;
      return '<span class="artist-name"' + attrs + '>' + esc(p) + '</span>';
    }).join('');
  };

  // Navigate to an artist page from a .artist-name element. Falls back to a
  // search lookup for artists that have no stored channel id (secondary
  // credits, older history rows).
  window.openArtistLink = function(el) {
    var channelId = el.getAttribute('data-channel-id');
    if (channelId) {
      if (window.preloadNavigateArtist) window.preloadNavigateArtist(channelId);
      else window.navigateTo('#artist/' + encodeURIComponent(channelId));
      return;
    }
    var name = (el.getAttribute('data-artist-name') || el.textContent || '').trim();
    if (!name) return;
    // Use the preload variant that resolves name → channelId → fetches artist data
    if (window.preloadNavigateArtistByName) {
      window.preloadNavigateArtistByName(name);
    } else {
      window.api('/alexa/search/?q=' + encodeURIComponent(name)).then(function(result) {
        var artists = (result && result.artists) || [];
        var exact = artists.find(function(a) {
          return (a.name || '').toLowerCase() === name.toLowerCase();
        }) || artists[0];
        if (exact && exact.browse_id) window.navigateTo('#artist/' + encodeURIComponent(exact.browse_id));
        else if (window.toast) toast('Artist page unavailable', 'error');
      }).catch(function() { if (window.toast) toast('Artist page unavailable', 'error'); });
    }
  };

  // Binds artist-name clicks directly on the spans inside `container` so the
  // click never bubbles up to the row's play-on-tap handler.
  window.wireArtistLinks = function(container) {
    container.querySelectorAll('.artist-name').forEach(function(an) {
      an.addEventListener('click', function(e) {
        e.preventDefault();
        e.stopPropagation();
        window.openArtistLink(an);
      });
    });
  };

  // Global delegated click handler: artist-name -> navigate to artist page
  document.addEventListener('click', function(e) {
    var target = e.target.closest('.artist-name');
    if (target) {
      e.preventDefault();
      e.stopPropagation();
      window.openArtistLink(target);
    }
  });

  // Initial route: honor a legacy #hash bookmark once, then strip it from the
  // URL for good.
  window.__route = location.hash || '#home';
  function syncHeaderScrollState() {
    document.body.classList.toggle('header-scrolled', window.scrollY > 12);
  }
  window.addEventListener('scroll', syncHeaderScrollState, { passive: true });
  syncHeaderScrollState();
  history.replaceState({ route: window.__route }, '', location.pathname + location.search);
  applyRoute(window.__route);
})();
