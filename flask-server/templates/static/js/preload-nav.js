/**
 * preload-nav.js — "Pre-fetch before navigate" system for Music Box
 *
 * Architecture:
 *   1. User clicks a card/link → navigateWithPreload() is called
 *   2. Progress bar appears IMMEDIATELY (synchronous, before any async work)
 *   3. Data is fetched in the background via the appropriate API endpoint
 *   4. Fetched data is stored in window.__preloadCache keyed by route
 *   5. Only AFTER the fetch resolves does window.navigateTo() fire
 *   6. loadAlbum / loadArtist / openPlaylistDetailModal detect the cache entry
 *      and skip their own fetch entirely → zero skeleton, zero spinner
 *   7. Progress bar snaps to 100% and fades exactly when navigation occurs
 *   8. If fetch fails → toast shown on CURRENT page, navigation aborted
 *   9. If user clicks another item before first fetch resolves → previous
 *      AbortController is cancelled, new request wins (no race condition)
 *
 * Drop-in: just include this file AFTER api.js and router.js.
 */
(function () {
  'use strict';

  // ─── Preload data store ────────────────────────────────────────────────────
  // Destination-page loaders check this cache FIRST before issuing their own
  // fetch. Keys are route strings (e.g. '#album/MPREb_xxx', '#artist/UCxxx').
  window.__preloadCache = window.__preloadCache || {};

  // ─── Progress bar controller ───────────────────────────────────────────────
  // Standalone from api.js's _showProgress/_hideProgress so we have full
  // lifecycle control: start → crawl → snap to 100% → fade.
  var _bar = null;
  var _crawlTimer = null;
  var _crawlPct = 0;

  function _getBar() {
    if (!_bar) _bar = document.getElementById('top-progress-bar');
    return _bar;
  }

  function _barStart() {
    var bar = _getBar();
    if (!bar) return;
    // Cancel any previous crawl
    clearInterval(_crawlTimer);
    // Hard-reset without transition so it appears instantly at 0
    bar.style.transition = 'none';
    bar.style.width = '0%';
    bar.style.opacity = '1';
    bar.classList.remove('loading', 'done');
    // Force reflow so the reset takes hold before we add the class
    void bar.offsetWidth;
    // Start crawl: grow to ~85% over time, then stall
    _crawlPct = 0;
    bar.style.transition = '';
    _tickCrawl();
    _crawlTimer = setInterval(_tickCrawl, 400);
  }

  function _tickCrawl() {
    var bar = _getBar();
    if (!bar) return;
    // Easing: fast early, slow as we approach 85%
    var remaining = 85 - _crawlPct;
    _crawlPct += remaining * 0.22;
    bar.style.transition = 'width 0.35s ease-out, opacity 0.2s ease';
    bar.style.width = Math.min(_crawlPct, 85) + '%';
    bar.style.opacity = '1';
    if (_crawlPct >= 84.5) clearInterval(_crawlTimer);
  }

  function _barComplete() {
    var bar = _getBar();
    if (!bar) return;
    clearInterval(_crawlTimer);
    // Snap to 100% quickly
    bar.style.transition = 'width 0.18s ease-out, opacity 0.35s ease 0.25s';
    bar.style.width = '100%';
    // Then fade away
    setTimeout(function () {
      bar.style.opacity = '0';
      setTimeout(function () {
        bar.style.width = '0%';
        bar.style.transition = 'none';
      }, 450);
    }, 180);
  }

  function _barAbort() {
    var bar = _getBar();
    if (!bar) return;
    clearInterval(_crawlTimer);
    bar.style.transition = 'width 0.2s ease, opacity 0.3s ease';
    bar.style.opacity = '0';
    setTimeout(function () {
      bar.style.width = '0%';
      bar.style.transition = 'none';
    }, 400);
  }

  // ─── In-flight request tracking ───────────────────────────────────────────
  // Shared request-progress hooks for non-navigation work such as search.
  window.startTopProgress = _barStart;
  window.completeTopProgress = _barComplete;
  window.abortTopProgress = _barAbort;

  var _currentController = null; // AbortController for active preload
  var _currentRoute = null;      // Route string for the active preload

  function _cancelCurrent() {
    if (_currentController) {
      _currentController.abort();
      _currentController = null;
    }
    _currentRoute = null;
  }

  // ─── Fetch strategies per route type ──────────────────────────────────────
  // Each strategy returns a Promise<data> and accepts an AbortSignal.

  function _fetchAlbum(browseId, signal) {
    return fetch('/api/album/' + encodeURIComponent(browseId), {
      credentials: 'same-origin',
      cache: 'no-store',
      signal: signal,
    }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  function _fetchArtist(channelId, signal) {
    return fetch('/api/artist/' + encodeURIComponent(channelId), {
      credentials: 'same-origin',
      cache: 'no-store',
      signal: signal,
    }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  function _fetchPlaylist(plId, signal) {
    // One detail endpoint handles library, Liked Music and public/curated
    // playlists, including the anonymous fallback for public mixes.
    return fetch('/api/library/playlists/' + encodeURIComponent(plId) + '?offset=0&limit=30', {
      credentials: 'same-origin',
      cache: 'no-store',
      signal: signal,
    }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  function _fetchArtistByName(name, signal) {
    return fetch('/alexa/search/?q=' + encodeURIComponent(name), {
      credentials: 'same-origin',
      cache: 'no-store',
      signal: signal,
    }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).then(function (result) {
      var artists = (result && result.artists) || [];
      var exact = artists.find(function (a) {
        return (a.name || '').toLowerCase() === name.toLowerCase();
      }) || artists[0];
      if (!exact || !exact.browse_id) throw new Error('Artist not found');
      return { _resolvedChannelId: exact.browse_id };
    });
  }

  function _imageUrl(value) {
    if (!value) return '';
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) {
      for (var i = value.length - 1; i >= 0; i -= 1) {
        var arrayUrl = _imageUrl(value[i]);
        if (arrayUrl) return arrayUrl;
      }
      return '';
    }
    return value.url || value.src || _imageUrl(value.thumbnails) || _imageUrl(value.images) || '';
  }

  function _preloadImage(url, signal) {
    if (!url) return Promise.resolve();
    return new Promise(function (resolve) {
      var settled = false;
      var img = new Image();
      var timer = setTimeout(done, 8000);
      function done() {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        img.onload = null;
        img.onerror = null;
        if (signal) signal.removeEventListener('abort', done);
        resolve();
      }
      img.onload = done;
      img.onerror = done;
      if (signal) signal.addEventListener('abort', done, { once: true });
      img.src = url;
    });
  }

  function _prepareArtistHero(data, signal) {
    var url = _imageUrl(data && data.artist && data.artist.thumbnails);
    return _preloadImage(url, signal).then(function () {
      if (data) data.__heroReady = true;
    });
  }

  function _preparePlaylistHero(data, signal) {
    var cover = _imageUrl(data && data.thumbnails) || _imageUrl(data && data.thumbnail);
    var image = _imageUrl(data && data.image) || _imageUrl(data && data.images);
    var urls = cover || image ? [cover || image] : (data && data.tracks || []).slice(0, 4).map(function (track) {
      return _imageUrl(track.thumbnails) || _imageUrl(track.thumbnail) || _imageUrl(track.image);
    }).filter(Boolean);
    return Promise.all(urls.map(function (url) { return _preloadImage(url, signal); })).then(function () {
      if (data) data.__heroReady = true;
    });
  }

  // ─── Core: navigateWithPreload ─────────────────────────────────────────────
  /**
   * navigateWithPreload(route, fetchFn)
   *
   * route   — the destination route string, e.g. '#album/MPREb_xxx'
   * fetchFn — function(signal) returning a Promise<data>
   *           data will be stored in window.__preloadCache[route]
   *           pass null to navigate immediately without prefetching
   */
  window.navigateWithPreload = function (route, fetchFn, readyFn) {
    if (!route) return;

    // If this exact route is already cached, navigate instantly
    if (window.__preloadCache[route] !== undefined) {
      window.navigateTo(route);
      return;
    }

    // Cancel any previous in-flight preload
    _cancelCurrent();

    // If no fetch needed (e.g. #home, #now-playing), just navigate
    if (typeof fetchFn !== 'function') {
      window.navigateTo(route);
      return;
    }

    // Start progress bar SYNCHRONOUSLY (before any async work)
    _barStart();

    var controller = new AbortController();
    _currentController = controller;
    _currentRoute = route;

    fetchFn(controller.signal).then(function (data) {
      if (typeof readyFn !== 'function') return data;
      return readyFn(data, controller.signal).then(function () { return data; });
    }).then(function (data) {
      // Guard: if another click came in while we were fetching, ignore this
      if (_currentRoute !== route || _currentController !== controller) return;

      // Store data in preload cache BEFORE navigating
      window.__preloadCache[route] = data;

      // Snap bar to 100%, THEN navigate
      _barComplete();

      // Small RAF delay so the bar visually hits 100% before the view swap
      requestAnimationFrame(function () {
        requestAnimationFrame(function () {
          _currentController = null;
          _currentRoute = null;
          window.navigateTo(route);
        });
      });

    }).catch(function (err) {
      if (err && err.name === 'AbortError') return; // Cancelled by user, no toast
      _currentController = null;
      _currentRoute = null;
      _barAbort();
      if (window.toast) {
        window.toast(err.message || 'Could not load page. Please try again.', 'error');
      }
    });
  };

  // ─── Convenience helpers ───────────────────────────────────────────────────

  window.preloadNavigateAlbum = function (browseId) {
    if (!browseId) return;
    var route = '#album/' + encodeURIComponent(browseId);
    window.navigateWithPreload(route, function (signal) {
      return _fetchAlbum(browseId, signal);
    });
  };

  window.preloadNavigateArtist = function (channelId) {
    if (!channelId) return;
    var route = '#artist/' + encodeURIComponent(channelId);
    window.navigateWithPreload(route, function (signal) {
      return _fetchArtist(channelId, signal);
    }, _prepareArtistHero);
  };

  window.preloadNavigateArtistByName = function (name) {
    if (!name) return;
    // We don't know the final route yet; fetch resolves the channelId first
    _cancelCurrent();
    _barStart();

    var controller = new AbortController();
    _currentController = controller;
    _currentRoute = '__resolving_artist__';

    _fetchArtistByName(name, controller.signal).then(function (result) {
      if (_currentRoute !== '__resolving_artist__' || _currentController !== controller) return;
      var channelId = result._resolvedChannelId;
      var route = '#artist/' + encodeURIComponent(channelId);
      // Chain into the artist fetch
      _currentController = controller; // keep same controller
      _currentRoute = route;
      return _fetchArtist(channelId, controller.signal).then(function (data) {
        return _prepareArtistHero(data, controller.signal).then(function () { return data; });
      }).then(function (data) {
        if (_currentRoute !== route || _currentController !== controller) return;
        window.__preloadCache[route] = data;
        _barComplete();
        requestAnimationFrame(function () {
          requestAnimationFrame(function () {
            _currentController = null;
            _currentRoute = null;
            window.navigateTo(route);
          });
        });
      });
    }).catch(function (err) {
      if (err && err.name === 'AbortError') return;
      _currentController = null;
      _currentRoute = null;
      _barAbort();
      if (window.toast) window.toast(err.message || 'Artist page unavailable', 'error');
    });
  };

  window.preloadNavigatePlaylist = function (plId) {
    if (!plId) return;
    var route = '#playlist/' + encodeURIComponent(plId);
    window.navigateWithPreload(route, function (signal) {
      return _fetchPlaylist(plId, signal);
    }, _preparePlaylistHero);
  };

  // ─── Expose bar controller for external view loaders (playlists, etc.) ──
  window._barStart = _barStart;
  window._barComplete = _barComplete;
  window._barAbort = _barAbort;

  // ─── Expose cache for destination pages to consume ────────────────────────
  /**
   * consumePreload(route) — destination page calls this to get cached data.
   * Returns the data and removes it from the cache (one-shot consumption).
   * Returns null if no preloaded data is available for this route.
   */
  window.consumePreload = function (route) {
    var data = window.__preloadCache[route];
    if (data !== undefined) {
      delete window.__preloadCache[route];
      return data;
    }
    return null;
  };

})();
