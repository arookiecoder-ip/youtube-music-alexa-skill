(function () {
  'use strict';

  const state = window.__appState = window.__appState || {};
  if (state._homeLoaded === undefined) state._homeLoaded = false;
  if (state._homeLoading === undefined) state._homeLoading = false;

  const musicNoteSvg = '<svg width="28" height="28" viewBox="0 0 24 24" fill="currentColor"><path d="M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z"/></svg>';

  function skeletonRowHtml() {
    const wide = window.matchMedia('(min-width: 900px)').matches;
    const count = wide ? 6 : 3;
    let cards = '';
    for (let i = 0; i < count; i++) {
      cards += '<div class="home-card home-skeleton-card"><div class="skeleton-block"></div><div class="skeleton-line skeleton-line-title"></div><div class="skeleton-line skeleton-line-artist"></div></div>';
    }
    return '<div class="home-row-container">' +
      '<div class="home-row-header"><div><div class="skeleton-line" style="width: 140px; height: 16px; margin: 0;"></div></div></div>' +
      '<div class="home-row-skeleton">' + cards + '</div>' +
    '</div>';
  }

  function homeRowHtml(row) {
    const items = Array.isArray(row && row.items) ? row.items : [];
    const tilesHtml = items.map(function(item) {
      // The main feed uses videoId; the recs-cache fallback row the server
      // serves when the feed build fails uses video_id. Accept both so the
      // fallback doesn't render as an empty page.
      const videoId = (item && (item.videoId || item.video_id)) || '';
      if (!videoId) return '';
      const title = item.title || '';
      const artist = item.artist || '';
      const channelId = item.channelId || item.channel_id || '';
      const albumId = item.albumId || item.album_id || '';
      const thumbUrl = item.thumbnail || '';
      const thumbHtml = thumbUrl
        ? "<img src=\"" + escHtml(thumbUrl) + "\" alt=\"\" loading=\"lazy\" decoding=\"async\" onload=\"this.classList.add('loaded')\">"
        : musicNoteSvg;
      var isLikedLocal = typeof _playlistsData !== 'undefined' && _playlistsData.liked_songs && _playlistsData.liked_songs.includes(videoId);
      var heartSvgLocal = isLikedLocal
        ? '<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>'
        : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>';
      const artistHtml = '<div class="recs-tile-artist">' + window.artistLinksHtml(artist, channelId) + '</div>';
      const playBtnHtml = '<button type="button" class="home-play-btn" title="Play"><svg viewBox="0 0 24 24" fill="currentColor"><polygon points="8,5 19,12 8,19"/></svg></button>';
      return '<div class="home-card" data-video-id="' + escHtml(videoId) + '" data-album-id="' + escHtml(albumId) + '" data-title="' + escHtml(title) + '" data-artist="' + escHtml(artist) + '" data-thumb="' + escHtml(thumbUrl) + '">' +
        '<div class="recs-tile-art home-card-art">' + thumbHtml + playBtnHtml + '</div>' +
        '<div class="recs-tile-title" title="Open album">' + escHtml(title) + '</div>' +
        artistHtml +
        '<button class="result-like-btn' + (isLikedLocal ? ' liked' : '') + '" type="button" title="' + (isLikedLocal ? 'Dislike' : 'Like') + '">' + heartSvgLocal + '</button>' +
        '<div class="result-more-menu">' +
          '<div class="result-menu-option" data-action="toggle-like"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg> Like</div>' +
          '<div class="result-menu-option" data-action="play-next"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg> Play next</div>' +
          '<div class="result-menu-option" data-action="add-to-queue"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg> Add to queue</div>' +
          '<div class="result-menu-option" data-action="play-radio"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4.93 19.07A10 10 0 1 1 19.07 4.93 10 10 0 0 1 4.93 19.07z"/><circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="M2 12h2"/><path d="M20 12h2"/></svg> Play Radio</div>' +
          '<div class="result-menu-option" data-action="save-playlist"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path><line x1="12" y1="11" x2="12" y2="17"></line><line x1="9" y1="14" x2="15" y2="14"></line></svg> Save to Playlist</div>' +
        '</div>' +
      '</div>';
    }).join('');
    if (!tilesHtml) return '';
    const subtitle = row && row.subtitle
      ? '<div class="home-row-subtitle">' + escHtml(row.subtitle) + '</div>'
      : '';
    return '<div class="home-row-container">' +
      '<div class="home-row-header"><div><div class="label home-row-label">' + escHtml((row && row.title) || '') + '</div>' + subtitle + '</div>' +
      '<div class="home-row-scroll-controls">' +
        '<button type="button" class="home-row-scroll home-row-scroll-prev" title="Scroll left" aria-label="Scroll left" disabled><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg></button>' +
        '<button type="button" class="home-row-scroll home-row-scroll-next" title="Scroll right" aria-label="Scroll right"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg></button>' +
      '</div></div>' +
      '<div class="home-row">' + tilesHtml + '</div>' +
    '</div>';
  }

  function showHomeSkeleton(show) {
    const container = document.getElementById('home-rows');
    if (!container) return;
    if (show) {
      let rows = '';
      for (let i = 0; i < 4; i++) rows += skeletonRowHtml();
      container.innerHTML = rows;
      container.hidden = false;
      return;
    }
    container.querySelectorAll('.home-row-skeleton').forEach(function(el) {
      if (el.closest('.home-row-container')) {
        el.closest('.home-row-container').hidden = true;
      } else {
        el.hidden = true;
      }
    });
  }

  function renderHomeFeed(data) {
    // Phase 12: Performance marker for profiling home feed render time
    if (window.performance && performance.mark) {
      performance.mark('home-feed-start');
    }
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
    const rows = Array.isArray(data && data.rows) ? data.rows : [];
    let rowsHtml = rows.map(homeRowHtml).join('');
    if (!rowsHtml) {
      // Never leave the page silently blank: show why and offer a retry.
      rowsHtml = '<div class="home-empty">' +
        '<div class="home-empty-title">No recommendations right now</div>' +
        '<div class="home-empty-sub">The feed could not be built. Play something or try again.</div>' +
        '<button type="button" id="home-retry-btn" class="btn-accent">Try again</button>' +
        '</div>';
    }
    container.innerHTML = rowsHtml;
    container.hidden = false;
    showHomeSkeleton(false);
    // Phase 12: Measure render-to-screen time
    if (window.performance && performance.mark && performance.measure) {
      performance.mark('home-feed-end');
      try { performance.measure('home-feed-render', 'home-feed-start', 'home-feed-end'); } catch (_) {}
    }
  }

  async function loadHomeFeed() {
    if (!state._loggedIn || state._homeLoaded || state._homeLoading) return;
    state._homeLoading = true;
    const section = document.getElementById('home-section');
    const idleHero = document.getElementById('idle-hero');
    if (idleHero) idleHero.hidden = true;
    const artistOpen = (window.getRoute() || '').indexOf('#artist/') === 0;
    const npOpen = (window.getRoute() || '') === '#now-playing';
    if (section) section.hidden = !!(state._resultsOpen || artistOpen || npOpen);
    showHomeSkeleton(true);
    try {
      const data = await api('/api/home/?refresh=1');
      state._homeLoaded = true;
      renderHomeFeed(data);
    } catch (e) {
      console.warn('Failed to load home feed', e);
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
    function updateShelfArrows(shelf) {
      var container = shelf.closest('.home-row-container');
      if (!container) return;
      var prev = container.querySelector('.home-row-scroll-prev');
      var next = container.querySelector('.home-row-scroll-next');
      if (prev) prev.disabled = shelf.scrollLeft <= 2;
      if (next) next.disabled = shelf.scrollLeft + shelf.clientWidth >= shelf.scrollWidth - 2;
    }
    rows.addEventListener('scroll', function(e) {
      if (e.target.classList && e.target.classList.contains('home-row')) updateShelfArrows(e.target);
      if (window._closeAllMoreMenus) window._closeAllMoreMenus();
    }, true);
    rows.addEventListener('click', function(e) {
      var scrollBtn = e.target.closest('.home-row-scroll');
      if (scrollBtn) {
        var shelf = scrollBtn.closest('.home-row-container').querySelector('.home-row');
        var direction = scrollBtn.classList.contains('home-row-scroll-prev') ? -1 : 1;
        shelf.scrollBy({ left: direction * Math.max(240, shelf.clientWidth * .8), behavior: 'smooth' });
        window.setTimeout(function() { updateShelfArrows(shelf); }, 350);
        return;
      }
      // Empty-state retry: reset the loaded flag and rebuild the feed
      if (e.target.closest('#home-retry-btn')) {
        state._homeLoaded = false;
        loadHomeFeed();
        return;
      }

      // Like button: stop propagation so card play doesn't fire
      var likeBtn = e.target.closest('.result-like-btn');
      if (likeBtn) {
        e.stopPropagation();
        var card = likeBtn.closest('.home-card');
        if (card && card.dataset.videoId) {
          var item = {
            video_id: card.dataset.videoId,
            title: card.dataset.title,
            artist: card.dataset.artist,
            thumbnail: card.dataset.thumb,
          };
          if (typeof toggleLike === 'function') toggleLike(item, likeBtn);
        }
        return;
      }

      // Artist name: navigate (or resolve by name) instead of playing the card.
      // Handled here rather than left to the document-level handler because
      // this container listener fires first and would otherwise play the card.
      var artistLink = e.target.closest('.artist-name');
      if (artistLink) {
        e.stopPropagation();
        window.openArtistLink(artistLink);
        return;
      }

      var titleLink = e.target.closest('.recs-tile-title');
      var art = e.target.closest('.home-card-art');
      var playBtn = e.target.closest('.home-play-btn');

      if (playBtn) {
        e.stopPropagation();
        var card = playBtn.closest('.home-card');
        if (!card || !rows.contains(card) || !card.dataset.videoId) return;
        if (!window.playFromQueue) return;
        window.playFromQueue({
          video_id: card.dataset.videoId,
          title: card.dataset.title || '',
          artist: card.dataset.artist || '',
          thumbnail: card.dataset.thumb || '',
        });
        return;
      }

      if (titleLink || art) {
        e.stopPropagation();
        var titleCard = (titleLink || art).closest('.home-card');
        var linkEl = titleLink || art;
        if (titleCard && titleCard.dataset.albumId) {
          window.navigateTo('#album/' + encodeURIComponent(titleCard.dataset.albumId));
        } else if (titleCard && titleCard.dataset.videoId) {
          linkEl.classList.add('is-resolving');
          var lookup = '/api/song/' + encodeURIComponent(titleCard.dataset.videoId) + '/album' +
            '?title=' + encodeURIComponent(titleCard.dataset.title || '') +
            '&artist=' + encodeURIComponent(titleCard.dataset.artist || '');
          window.api(lookup).then(function(result) {
            if (!result || !result.browseId) throw new Error('Album not found');
            titleCard.dataset.albumId = result.browseId;
            window.navigateTo('#album/' + encodeURIComponent(result.browseId));
          }).catch(function(err) {
            if (window.toast) window.toast(err.message || 'Could not open album', 'error');
          }).finally(function() {
            linkEl.classList.remove('is-resolving');
          });
        }
        return;
      }
    });

    rows.addEventListener('contextmenu', function(e) {
      var card = e.target.closest('.home-card');
      if (card && card.dataset.videoId) {
        e.preventDefault();
        e.stopPropagation();
        
        var moreMenu = card.querySelector('.result-more-menu');
        if (!moreMenu) {
          var allMenus = document.querySelectorAll('.result-more-menu');
          for (var i = 0; i < allMenus.length; i++) {
            if (allMenus[i]._home === card) {
              moreMenu = allMenus[i];
              break;
            }
          }
        }
        if (!moreMenu) return;

        var wasOpen = moreMenu.classList.contains('open');
        if (window._closeAllMoreMenus) window._closeAllMoreMenus();
        
        if (!wasOpen) {
          var track = {
            video_id: card.dataset.videoId,
            title: card.dataset.title || '',
            artist: card.dataset.artist || '',
            thumbnail: card.dataset.thumb || ''
          };
          
          if (!moreMenu.dataset.handlersBound) {
            moreMenu.dataset.handlersBound = '1';
            
            var likeOpt = moreMenu.querySelector('[data-action="toggle-like"]');
            if (likeOpt) likeOpt.addEventListener('click', function(evt) {
                evt.stopPropagation();
                if (window._closeAllMoreMenus) window._closeAllMoreMenus();
                var cardLikeBtn = card.querySelector('.result-like-btn');
                if (cardLikeBtn) cardLikeBtn.click();
            });
            
            var playNext = moreMenu.querySelector('[data-action="play-next"]');
            if (playNext) playNext.addEventListener('click', function(evt) {
                evt.stopPropagation();
                if (window._closeAllMoreMenus) window._closeAllMoreMenus();
                if (window.addToQueue) window.addToQueue(track, 'next');
            });
            
            var addQueue = moreMenu.querySelector('[data-action="add-to-queue"]');
            if (addQueue) addQueue.addEventListener('click', function(evt) {
                evt.stopPropagation();
                if (window._closeAllMoreMenus) window._closeAllMoreMenus();
                if (window.addToQueue) window.addToQueue(track, 'last');
            });
            
            var playRadio = moreMenu.querySelector('[data-action="play-radio"]');
            if (playRadio) playRadio.addEventListener('click', function(evt) {
                evt.stopPropagation();
                if (window._closeAllMoreMenus) window._closeAllMoreMenus();
                if (window.playResult) window.playResult(track, false, true);
            });
            
            var saveOpt = moreMenu.querySelector('[data-action="save-playlist"]');
            if (saveOpt) saveOpt.addEventListener('click', function(evt) {
                evt.stopPropagation();
                if (window._closeAllMoreMenus) window._closeAllMoreMenus();
                if (window.openAddToPlaylistModal) window.openAddToPlaylistModal(track);
            });
            
            moreMenu.addEventListener('click', function(evt) { evt.stopPropagation(); });
          }

          moreMenu.classList.add('open');
          var menuHeight = 132;
          var menuWidth = 160;
          var spaceBelow = window.innerHeight - e.clientY;
          var spaceRight = window.innerWidth - e.clientX;
          var openAbove = spaceBelow < menuHeight + 8;
          
          if (spaceRight < menuWidth + 8) {
             moreMenu.style.left = 'auto';
             moreMenu.style.right = (window.innerWidth - e.clientX) + 'px';
          } else {
             moreMenu.style.left = e.clientX + 'px';
             moreMenu.style.right = 'auto';
          }
          
          if (openAbove) {
             moreMenu.style.top = 'auto';
             moreMenu.style.bottom = (window.innerHeight - e.clientY + 4) + 'px';
          } else {
             moreMenu.style.top = (e.clientY + 4) + 'px';
             moreMenu.style.bottom = 'auto';
          }
          
          moreMenu._home = card;
          document.body.appendChild(moreMenu);
          void moreMenu.offsetWidth;
          moreMenu.classList.add('open');
        }
      }
    });
  }

  window.loadHomeFeed = loadHomeFeed;
  window.renderHomeFeed = renderHomeFeed;
  window.showHomeSkeleton = showHomeSkeleton;
})();
