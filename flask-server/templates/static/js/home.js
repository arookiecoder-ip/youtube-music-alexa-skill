(function () {
  'use strict';

  const state = window.__appState = window.__appState || {};
  if (state._homeLoaded === undefined) state._homeLoaded = false;
  if (state._homeLoading === undefined) state._homeLoading = false;

  let homeFeedData = null;
  let renderedShelves = [];
  let deferredShelfObserver = null;
  let currentFilter = 'all';
  let abortController = null;

  function updateShelfArrows(shelfContent) {
    const shelf = shelfContent && shelfContent.closest('.home-shelf');
    if (!shelf) return;
    const maxScroll = Math.max(0, shelfContent.scrollWidth - shelfContent.clientWidth);
    const left = shelf.querySelector('.home-scroll-left');
    const right = shelf.querySelector('.home-scroll-right');
    if (left) left.disabled = shelfContent.scrollLeft <= 2;
    if (right) right.disabled = shelfContent.scrollLeft >= maxScroll - 2;
  }

  function showHomeSkeleton(show) {
    const container = document.getElementById('home-rows');
    if (!container) return;
    if (show) {
      // Rough skeleton matching layouts, defaulting to 4 rows
      let rows = '';
      for (let i = 0; i < 4; i++) {
        rows += `
          <div class="home-shelf home-skeleton-shelf">
            <div class="home-shelf-header">
                <div class="home-shelf-title-area"><div class="skeleton-line" style="width: 140px; height: 16px; margin: 0;"></div></div>
            </div>
            <div class="home-shelf-content">
                ${Array(6).fill('<div class="home-item home-skeleton-card"><div class="skeleton-block"></div><div class="skeleton-line skeleton-line-title"></div></div>').join('')}
            </div>
          </div>
        `;
      }
      container.innerHTML = rows;
      container.hidden = false;
    } else {
      container.querySelectorAll('.home-skeleton-shelf').forEach(el => el.remove());
    }
  }

  function renderFilters() {
    const filterContainer = document.getElementById('home-filter-chips');
    if (!filterContainer) return;
    if (!homeFeedData || !homeFeedData.filters || homeFeedData.filters.length <= 1) {
        filterContainer.hidden = true;
        return;
    }

    const html = homeFeedData.filters.map(f => {
        const isSelected = f.id === currentFilter;
        return `<button class="home-filter-chip ${isSelected ? 'selected' : ''}" data-filter-id="${HomeRenderers.escapeHtml(f.id)}">${HomeRenderers.escapeHtml(f.label)}</button>`;
    }).join('');

    filterContainer.innerHTML = html;
    filterContainer.hidden = false;
  }

  function addReleaseTypeShelves(shelves) {
    const result = (shelves || []).slice();
    const seen = new Set(result.map(shelf => shelf.id));
    const groups = [
      ['singles', 'Singles', item => item.kind === 'track' && /^single(?:\s|$)/i.test(item.subtitle || '')],
      ['albums', 'Albums', item => item.kind === 'album'],
      ['playlists', 'Playlists', item => item.kind === 'playlist']
    ];
    groups.forEach(([id, title, matches]) => {
      if (seen.has('home-' + id)) return;
      const items = [];
      const itemKeys = new Set();
      (shelves || []).forEach(shelf => (shelf.items || []).forEach(item => {
        if (matches(item) && !itemKeys.has(item.key)) {
          itemKeys.add(item.key);
          items.push(item);
        }
      }));
      if (items.length) {
        result.push({
          id: 'home-' + id,
          title: title,
          subtitle: '',
          layout: 'cards',
          source: 'home-categories',
          actions: { playAll: false, showAll: false },
          filters: ['all'],
          items: items.slice(0, 20)
        });
      }
    });
    return result;
  }

  function deferredShelfMarkup(shelf) {
    const esc = HomeRenderers.escapeHtml;
    const cards = Array(6).fill('<div class="home-item home-skeleton-card"><div class="skeleton-block"></div><div class="skeleton-line skeleton-line-title"></div><div class="skeleton-line skeleton-line-artist"></div></div>').join('');
    return `<section class="home-shelf home-skeleton-shelf home-shelf-deferred" data-deferred-shelf-id="${esc(shelf.id)}">
      <div class="home-shelf-header"><div class="home-shelf-title-area"><h2 class="home-shelf-title">${esc(shelf.title || '')}</h2></div></div>
      <div class="home-shelf-content home-shelf-deferred-content" aria-busy="true">${cards}</div>
    </section>`;
  }

  function renderShelvesWhenVisible(container, shelves) {
    if (deferredShelfObserver) deferredShelfObserver.disconnect();
    const shelfById = new Map(shelves.map(shelf => [String(shelf.id), shelf]));
    container.innerHTML = shelves.map(deferredShelfMarkup).join('');

    const render = shell => {
      if (!shell || shell.dataset.rendered) return;
      const shelf = shelfById.get(shell.dataset.deferredShelfId);
      if (!shelf) return;
      shell.dataset.rendered = 'true';
      const holder = document.createElement('div');
      holder.innerHTML = HomeRenderers.renderShelf(shelf).trim();
      const rendered = holder.firstElementChild;
      if (!rendered) return;
      shell.replaceWith(rendered);
      rendered.querySelectorAll('.home-shelf-content').forEach(updateShelfArrows);
      if (window.syncTrackPlaybackIndicators) window.syncTrackPlaybackIndicators();
    };

    const pending = Array.from(container.querySelectorAll('.home-shelf-deferred'));
    // The opening viewport must never depend on IntersectionObserver. Some
    // browser/WebView combinations defer its first callback until after a
    // scroll, leaving Home apparently blank even though data has arrived.
    // Render the first two shelves synchronously; lower shelves remain lazy.
    pending.slice(0, 2).forEach(render);
    const deferred = Array.from(container.querySelectorAll('.home-shelf-deferred'));
    if (!('IntersectionObserver' in window)) {
      deferred.forEach(render);
      return;
    }
    deferredShelfObserver = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (!entry.isIntersecting) return;
        deferredShelfObserver.unobserve(entry.target);
        render(entry.target);
      });
    }, { root: null, rootMargin: '240px 0px' });
    deferred.forEach(shelf => deferredShelfObserver.observe(shelf));
  }

  function renderHomeFeed() {
    if (window.performance && performance.mark) performance.mark('home-feed-start');

    const container = document.getElementById('home-rows');
    if (!container) return;
    const idleHero = document.getElementById('idle-hero');
    if (idleHero) idleHero.hidden = true;
    const greet = document.getElementById('home-greeting');
    if (greet) {
      const h = new Date().getHours();
      greet.textContent = h < 5 ? 'Good night' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
      greet.hidden = false;
    }

    renderFilters();

    const statusEl = document.getElementById('home-status');
    if (statusEl) {
        if (homeFeedData && homeFeedData.stale) {
            statusEl.textContent = "Showing older data while updating...";
            statusEl.hidden = false;
        } else if (homeFeedData && homeFeedData.partial) {
            statusEl.textContent = "Some recommendations are unavailable right now.";
            statusEl.hidden = false;
        } else {
            statusEl.hidden = true;
        }
    }

    if (!homeFeedData || !homeFeedData.shelves || homeFeedData.shelves.length === 0) {
      container.innerHTML = '<div class="home-empty">' +
        '<div class="home-empty-title">No recommendations right now</div>' +
        '<div class="home-empty-sub">The feed could not be built. Play something or try again.</div>' +
        '<button type="button" id="home-retry-btn" class="btn-accent">Try again</button>' +
        '</div>';
      container.hidden = false;
      showHomeSkeleton(false);
      return;
    }

    renderedShelves = addReleaseTypeShelves(HomeRenderers.filterShelves(homeFeedData, currentFilter));
    if (renderedShelves.length) {
      renderShelvesWhenVisible(container, renderedShelves);
    } else {
      container.innerHTML = '<div class="home-empty"><div class="home-empty-title">No items match this filter</div></div>';
    }
    container.hidden = false;
    showHomeSkeleton(false);

    if (window.performance && performance.mark && performance.measure) {
      performance.mark('home-feed-end');
      try { performance.measure('home-feed-render', 'home-feed-start', 'home-feed-end'); } catch (_) {}
    }
  }

  async function loadHomeFeed() {
    if (!state._loggedIn || state._homeLoaded || state._homeLoading) return;
    state._homeLoading = true;

    if (abortController) {
        abortController.abort();
    }
    abortController = new AbortController();
    const signal = abortController.signal;

    const section = document.getElementById('home-section');
    const idleHero = document.getElementById('idle-hero');
    if (idleHero) idleHero.hidden = true;
    const artistOpen = (window.getRoute() || '').indexOf('#artist/') === 0;
    const npOpen = (window.getRoute() || '') === '#now-playing';
    if (section) section.hidden = !!(state._resultsOpen || artistOpen || npOpen);

    showHomeSkeleton(true);
    try {
      // Use existing window.api logic which returns json, but we need to pass signal if it supported it.
      // Since window.api uses fetch, we'll just use window.api and check signal.aborted later
      const data = await window.api('/api/home/?refresh=1&filter=all');
      if (signal.aborted) return;

      if (data && data.schemaVersion === 2) {
          homeFeedData = data;
      } else {
          homeFeedData = null;
      }
      state._homeLoaded = true;
      renderHomeFeed();
    } catch (e) {
      if (signal.aborted) return;
      console.warn('Failed to load home feed', e);
      homeFeedData = null;
      const container = document.getElementById('home-rows');
      if (container) {
        container.innerHTML = '';
        container.hidden = true;
      }
      showHomeSkeleton(false);
      if (section) section.hidden = true;
    } finally {
      state._homeLoading = false;
    }
  }

  const rows = document.getElementById('home-rows');
  if (rows) {
    rows.addEventListener('scroll', function(e) {
      if (e.target.classList && e.target.classList.contains('home-shelf-content')) updateShelfArrows(e.target);
      if (window._closeAllMoreMenus) window._closeAllMoreMenus();
    }, true);

    rows.addEventListener('click', function(e) {
      var scrollLeftBtn = e.target.closest('.home-scroll-left');
      var scrollRightBtn = e.target.closest('.home-scroll-right');
      if (scrollLeftBtn || scrollRightBtn) {
          var shelf = (scrollLeftBtn || scrollRightBtn).closest('.home-shelf');
          if (shelf) {
              var content = shelf.querySelector('.home-shelf-content');
              if (content) {
                  var scrollAmount = content.clientWidth * 0.8;
                  content.scrollBy({
                      left: scrollLeftBtn ? -scrollAmount : scrollAmount,
                      behavior: 'smooth'
                  });
                  updateShelfArrows(content);
              }
          }
          return;
      }

      if (e.target.closest('#home-retry-btn')) {
        state._homeLoaded = false;
        loadHomeFeed();
        return;
      }

      var playAllBtn = e.target.closest('.home-shelf-play-all');
      if (playAllBtn) {
          var shelfId = playAllBtn.dataset.shelfId;
          var shelf = renderedShelves.find(s => s.id === shelfId);
          if (shelf) {
              // Build a rich queue — carry title/artist/thumbnail so metadata
              // is available without a blocking lookup later.
              var queueItems = (shelf.items || [])
                  .filter(i => i.capabilities && i.capabilities.play && i.play && i.play.videoId)
                  .map(i => ({
                      video_id: i.play.videoId,
                      title: i.title || '',
                      artist: i.subtitle || '',
                      thumbnail: i.image || '',
                      duration_ms: 0
                  }));
              if (queueItems.length > 0) {
                  // Step 1: push the full queue to the server (no serial needed,
                  // returns 200 even without a device selected).
                  window.api('/alexa/play_queue/', {
                      serial: window.selectedSerial ? window.selectedSerial() : '',
                      queue_items: queueItems.map(i => i.video_id)
                  }).then(() => {
                      // Step 2: dispatch playback of the first song (handles
                      // Alexa, progress bar, now-playing update, etc.).
                      if (window.playFromQueue) {
                          window.playFromQueue(queueItems[0], 0, false);
                      }
                  }).catch(err => {
                      // 502 = Alexa device offline; the queue IS set on the
                      // server so retrying later will work. Surface a softer msg.
                      var msg = (err && err.message) || '';
                      if (msg.includes('offline') || msg.includes('unreachable') || msg.includes('502')) {
                          if (window.toast) window.toast('Queue loaded — device may be offline', 'warning');
                      } else {
                          if (window.toast) window.toast('Failed to play shelf', 'error');
                      }
                  });
              }
          }
          return;
      }

      // Let the router's delegated artist-link handler own this click. If it
      // reaches the card handler, the song starts playing before navigation.
      if (e.target.closest('.artist-name')) return;

      var playBtn = e.target.closest('.home-play-btn');
      var itemCard = (playBtn || e.target).closest('.home-item');

      if (itemCard) {
        e.stopPropagation();
        var videoId = itemCard.dataset.videoId;
        var playlistId = itemCard.dataset.playlistId;
        var targetId = itemCard.dataset.targetId;
        var kind = itemCard.dataset.kind;

        // A song title navigates to its album; artwork and the explicit play
        // button retain their normal direct-play behavior.
        if (e.target.closest('.home-item-title') && kind === 'track') {
            var songAlbumId = itemCard.dataset.albumId;
            if (songAlbumId) {
                if (window.preloadNavigateAlbum) window.preloadNavigateAlbum(songAlbumId);
                else window.navigateTo('#album/' + encodeURIComponent(songAlbumId));
                return;
            }
        }

        // YouTube exposes Liked Music as a station-like home card, but the
        // card itself should open the user's Liked Music collection. Keep the
        // overlaid play button's direct-play behavior unchanged.
        if (playlistId === 'LM' && !playBtn) {
            if (window.preloadNavigatePlaylist) window.preloadNavigatePlaylist('LM');
            else window.navigateTo('#playlist/LM');
            return;
        }

        // Station cards should open their browsable station/playlist view on
        // card click. Keep the explicit overlay Play button as direct play.
        if (kind === 'station' && !playBtn) {
            var stationId = targetId || playlistId;
            if (stationId) {
                if (window.preloadNavigatePlaylist) window.preloadNavigatePlaylist(stationId);
                else window.navigateTo('#playlist/' + encodeURIComponent(stationId));
            }
            return;
        }

        // If it's a play button click OR item click and it's a track/station/playlist to play directly
        if (playBtn || kind === 'track' || kind === 'station' || (!targetId && playlistId)) {
            if (!videoId && !playlistId) return;
            if (window.playFromQueue) {
                // If it's just a track
                if (videoId) {
                    window.playFromQueue({
                        video_id: videoId,
                        title: itemCard.querySelector('.home-item-title')?.textContent || '',
                        artist: itemCard.querySelector('.home-item-subtitle')?.textContent || '',
                        thumbnail: itemCard.querySelector('img')?.src || ''
                    });
                } else if (playlistId) {
                    // Start playlist playback
                    window.api('/alexa/play/', {
                        serial: window.selectedSerial ? window.selectedSerial() : '',
                        query: 'https://music.youtube.com/playlist?list=' + playlistId
                    });
                }
            }
            return;
        }

        // Navigate based on target ID
        if (targetId) {
            if (kind === 'artist') {
                if (window.openArtistLink) {
                    var fauxLink = document.createElement('a');
                    fauxLink.dataset.channelId = targetId;
                    window.openArtistLink(fauxLink);
                }
            } else if (kind === 'album') {
                if (window.preloadNavigateAlbum) window.preloadNavigateAlbum(targetId);
                else window.navigateTo('#album/' + encodeURIComponent(targetId));
            } else if (kind === 'playlist') {
                if (window.preloadNavigatePlaylist) window.preloadNavigatePlaylist(targetId);
                else window.navigateTo('#playlist/' + encodeURIComponent(targetId));
            }
        }
      }
    });

    let activeMenuCardId = null;
    let sharedMoreMenu = null;

    document.addEventListener('click', function(e) {
      if (sharedMoreMenu && sharedMoreMenu.classList.contains('open')) {
        if (!e.target.closest('.result-more-menu')) {
          sharedMoreMenu.classList.remove('open');
          activeMenuCardId = null;
        }
      }
    });

    // Close the context menu when the user scrolls the page or resizes.
    // The _closeAllMoreMenus call in the home-rows scroll handler only
    // catches scrolls inside the shelf containers — not the document body.
    window.addEventListener('scroll', function() {
      if (sharedMoreMenu && sharedMoreMenu.classList.contains('open')) {
        sharedMoreMenu.classList.remove('open');
        activeMenuCardId = null;
      }
    }, { passive: true });
    window.addEventListener('resize', function() {
      if (sharedMoreMenu && sharedMoreMenu.classList.contains('open')) {
        sharedMoreMenu.classList.remove('open');
        activeMenuCardId = null;
      }
    });

    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape' && sharedMoreMenu && sharedMoreMenu.classList.contains('open')) {
        sharedMoreMenu.classList.remove('open');
        activeMenuCardId = null;
      }
    });

    rows.addEventListener('contextmenu', function(e) {
      var itemCard = e.target.closest('.home-item');
      if (itemCard && (itemCard.dataset.videoId || itemCard.dataset.playlistId)) {
        e.preventDefault();
        e.stopPropagation();

        var videoId = itemCard.dataset.videoId;
        var playlistId = itemCard.dataset.playlistId || '';
        var cardId = videoId || playlistId;

        if (activeMenuCardId === cardId && sharedMoreMenu && sharedMoreMenu.classList.contains('open')) {
          sharedMoreMenu.classList.remove('open');
          activeMenuCardId = null;
          return;
        }

        if (window._closeAllMoreMenus) window._closeAllMoreMenus();
        if (sharedMoreMenu) sharedMoreMenu.classList.remove('open');

        if (!sharedMoreMenu) {
          sharedMoreMenu = document.createElement('div');
          sharedMoreMenu.className = 'result-more-menu';
          sharedMoreMenu.innerHTML =
            '<div class="result-menu-option" data-action="shuffle-play" hidden><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/><polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/><line x1="4" y1="4" x2="9" y2="9"/></svg> Shuffle play</div>' +
            '<div class="result-menu-option" data-action="play-home" hidden><svg viewBox="0 0 24 24" fill="currentColor"><polygon points="7,4 20,12 7,20"/></svg> Play</div>' +
            '<div class="result-menu-option" data-action="toggle-like"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg> Like</div>' +
            '<div class="result-menu-option" data-action="play-next"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg> Play next</div>' +
            '<div class="result-menu-option" data-action="add-to-queue"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg> Add to queue</div>' +
            '<div class="result-menu-option" data-action="play-radio"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4.93 19.07A10 10 0 1 1 19.07 4.93 10 10 0 0 1 4.93 19.07z"/><circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="M2 12h2"/><path d="M20 12h2"/></svg> Play Radio</div>' +
            '<div class="result-menu-option" data-action="save-playlist"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path><line x1="12" y1="11" x2="12" y2="17"></line><line x1="9" y1="14" x2="15" y2="14"></line></svg> Save to Playlist</div>';
          document.body.appendChild(sharedMoreMenu);
          sharedMoreMenu.addEventListener('click', function(evt) { evt.stopPropagation(); });

          sharedMoreMenu.querySelector('[data-action="toggle-like"]').addEventListener('click', function(evt) {
              evt.stopPropagation();
              if (window._closeAllMoreMenus) window._closeAllMoreMenus();
              sharedMoreMenu.classList.remove('open');
              activeMenuCardId = null;
              if (sharedMoreMenu._track && typeof toggleLike === 'function') {
                toggleLike(sharedMoreMenu._track, null);
              }
          });
          sharedMoreMenu.querySelector('[data-action="play-next"]').addEventListener('click', function(evt) {
              evt.stopPropagation();
              if (window._closeAllMoreMenus) window._closeAllMoreMenus();
              sharedMoreMenu.classList.remove('open');
              activeMenuCardId = null;
              if (window.addToQueue && sharedMoreMenu._track) window.addToQueue(sharedMoreMenu._track, 'next');
          });
          sharedMoreMenu.querySelector('[data-action="add-to-queue"]').addEventListener('click', function(evt) {
              evt.stopPropagation();
              if (window._closeAllMoreMenus) window._closeAllMoreMenus();
              sharedMoreMenu.classList.remove('open');
              activeMenuCardId = null;
              if (window.addToQueue && sharedMoreMenu._track) window.addToQueue(sharedMoreMenu._track, 'last');
          });
          sharedMoreMenu.querySelector('[data-action="play-radio"]').addEventListener('click', function(evt) {
              evt.stopPropagation();
              if (window._closeAllMoreMenus) window._closeAllMoreMenus();
              sharedMoreMenu.classList.remove('open');
              activeMenuCardId = null;
              if (window.playResult && sharedMoreMenu._track) window.playResult(sharedMoreMenu._track, false, true);
          });
          sharedMoreMenu.querySelector('[data-action="save-playlist"]').addEventListener('click', function(evt) {
              evt.stopPropagation();
              if (window._closeAllMoreMenus) window._closeAllMoreMenus();
              sharedMoreMenu.classList.remove('open');
              activeMenuCardId = null;
              if (window.openAddToPlaylistModal && sharedMoreMenu._track) window.openAddToPlaylistModal(sharedMoreMenu._track);
          });
          sharedMoreMenu.querySelector('[data-action="play-home"]').addEventListener('click', function(evt) {
              evt.stopPropagation();
              if (window._closeAllMoreMenus) window._closeAllMoreMenus();
              sharedMoreMenu.classList.remove('open');
              activeMenuCardId = null;
              var t = sharedMoreMenu._track;
              if (t && t._playlistId && window.playFromQueue) {
                window.api('/alexa/play/', {
                  serial: window.selectedSerial ? window.selectedSerial() : '',
                  query: 'https://music.youtube.com/playlist?list=' + t._playlistId
                }).catch(function() {
                  if (window.toast) window.toast('Failed to start playlist', 'error');
                });
              }
          });
          sharedMoreMenu.querySelector('[data-action="shuffle-play"]').addEventListener('click', function(evt) {
              evt.stopPropagation();
              if (window._closeAllMoreMenus) window._closeAllMoreMenus();
              sharedMoreMenu.classList.remove('open');
              activeMenuCardId = null;
              var t = sharedMoreMenu._track;
              if (t && t._playlistId) {
                window.api('/alexa/play/', {
                  serial: window.selectedSerial ? window.selectedSerial() : '',
                  query: 'https://music.youtube.com/playlist?list=' + t._playlistId
                }).then(function() {
                  // Give the device time to confirm playback before shuffling.
                  // Without this delay the shuffle command interrupts the
                  // device while it's still buffering the first track,
                  // causing a "stopped" event and a broken queue.
                  setTimeout(function() {
                    window.api('/alexa/shuffle_queue/', {}).then(function() {
                      var mainShuffle = document.getElementById('shuffle-btn');
                      if (mainShuffle) mainShuffle.classList.add('shuffle-active');
                      if (window.toast) window.toast('Queue shuffled', 'ok');
                    }).catch(function() {});
                  }, 2000);
                }).catch(function() {
                  if (window.toast) window.toast('Failed to start playlist', 'error');
                });
              }
          });
        }

        var playlistId = itemCard.dataset.playlistId || '';
        // A track may carry a playlist id only as its playback source.  That
        // does not make the card a playlist; use the normalized entity kind.
        var isPlaylist = kind === 'playlist' || kind === 'station';
        sharedMoreMenu._track = {
          video_id: videoId,
          title: itemCard.querySelector('.home-item-title')?.textContent || '',
          artist: itemCard.querySelector('.home-item-subtitle')?.textContent || '',
          thumbnail: itemCard.querySelector('img')?.src || '',
          _playlistId: playlistId
        };
        sharedMoreMenu._triggerCard = itemCard;
        activeMenuCardId = cardId;

        // For playlist / Liked Songs cards, only show Shuffle play + Play
        // For regular tracks, show the 5 standard options
        sharedMoreMenu.querySelector('[data-action="shuffle-play"]').hidden = !isPlaylist;
        sharedMoreMenu.querySelector('[data-action="play-home"]').hidden = !isPlaylist;
        sharedMoreMenu.querySelector('[data-action="toggle-like"]').hidden = isPlaylist;
        sharedMoreMenu.querySelector('[data-action="play-next"]').hidden = isPlaylist;
        sharedMoreMenu.querySelector('[data-action="add-to-queue"]').hidden = isPlaylist;
        sharedMoreMenu.querySelector('[data-action="play-radio"]').hidden = isPlaylist;
        sharedMoreMenu.querySelector('[data-action="save-playlist"]').hidden = isPlaylist;

        var menuHeight = 200;
        var menuWidth = 180;
        var mouseX = e.clientX;
        var mouseY = e.clientY;

        var spaceBelow = window.innerHeight - mouseY;
        var spaceRight = window.innerWidth - mouseX;
        var openAbove = spaceBelow < menuHeight + 8;

        if (spaceRight < menuWidth + 8) {
           sharedMoreMenu.style.left = 'auto';
           sharedMoreMenu.style.right = (window.innerWidth - mouseX) + 'px';
        } else {
           sharedMoreMenu.style.left = mouseX + 'px';
           sharedMoreMenu.style.right = 'auto';
        }

        if (openAbove) {
           sharedMoreMenu.style.top = 'auto';
           sharedMoreMenu.style.bottom = (window.innerHeight - mouseY) + 'px';
        } else {
           sharedMoreMenu.style.top = mouseY + 'px';
           sharedMoreMenu.style.bottom = 'auto';
        }

        sharedMoreMenu._home = null;
        void sharedMoreMenu.offsetWidth;
        sharedMoreMenu.classList.add('open');
      }
    });

    window.addEventListener('resize', function() {
      rows.querySelectorAll('.home-shelf-content').forEach(updateShelfArrows);
    });
  }

  const filterChips = document.getElementById('home-filter-chips');
  if (filterChips) {
      filterChips.addEventListener('click', function(e) {
          const chip = e.target.closest('.home-filter-chip');
          if (chip) {
              const filterId = chip.dataset.filterId;
              if (filterId !== currentFilter) {
                  currentFilter = filterId;
                  renderHomeFeed();
              }
          }
      });
  }

  window.loadHomeFeed = loadHomeFeed;
  window.renderHomeFeed = renderHomeFeed;
  window.showHomeSkeleton = showHomeSkeleton;
})();
