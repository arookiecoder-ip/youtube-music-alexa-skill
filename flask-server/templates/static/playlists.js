(function () {
  'use strict';
  const state = window.__appState = window.__appState || {};
  if (state._loggedIn === undefined) state._loggedIn = false;
  var _openPlaylistId = null;  // currently open playlist detail plId
  const libraryPlaylistIds = new Set();
  const trackMetadataRequests = new Map();
  const PLAYLIST_PAGE_SIZE = 100;
  const HIDDEN_PERSONAL_PLAYLIST_NAMES = new Set([
    'episodes for later',
    'Sounds from Shorts',
    'sounds from shorts',
    'new episodes',
    'new episdes'
  ]);

  function isHiddenPersonalPlaylist(playlist) {
    const title = String(playlist && (playlist.title || playlist.name) || '')
      .trim().replace(/\s+/g, ' ').toLowerCase();
    return HIDDEN_PERSONAL_PLAYLIST_NAMES.has(title);
  }

  const escapeHtml = window.escHtml || (s => String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'));

  function observeLazyImages(root) {
    if (!root) return;
    const images = root.querySelectorAll('img[data-src]');
    if (!images.length) return;
    const load = function (img) {
      const src = img.getAttribute('data-src');
      if (!src) return;
      img.src = src;
      img.removeAttribute('data-src');
    };
    if (!('IntersectionObserver' in window)) {
      images.forEach(load);
      return;
    }
    const scrollRoot = root.closest('.history-modal-body') || null;
    // The mobile playlist is a document-scrolling page, not a modal body.
    // An IntersectionObserver rooted at an overflow-visible element can
    // miss every image, leaving the data-src placeholders permanently blank.
    if (scrollRoot) {
      const overflowY = getComputedStyle(scrollRoot).overflowY;
      if (overflowY !== 'auto' && overflowY !== 'scroll') {
        images.forEach(load);
        return;
      }
    }
    const observer = new IntersectionObserver(function (entries, obs) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        load(entry.target);
        obs.unobserve(entry.target);
      });
    }, { root: scrollRoot, rootMargin: '120px 0px' });
    images.forEach(function (img) { observer.observe(img); });
  }

  function normalizeImageUrl(value) {
    if (typeof value !== 'string') return value || '';
    if (value.startsWith('//')) return 'https:' + value;
    if (!value.startsWith('http') && !value.startsWith('/') &&
        (value.includes('googleusercontent.com') || value.includes('ggpht.com'))) {
      return 'https://' + value;
    }
    return value;
  }

  function imageUrl(value) {
    if (!value) return '';
    if (typeof value === 'string') return normalizeImageUrl(value);
    if (Array.isArray(value)) {
      for (let i = value.length - 1; i >= 0; i -= 1) {
        const url = imageUrl(value[i]);
        if (url) return url;
      }
      return '';
    }
    if (typeof value === 'object') {
      return normalizeImageUrl(value.url || value.src) || imageUrl(value.thumbnails) || imageUrl(value.images) || '';
    }
    return '';
  }

  /* Format total duration like YT Music: "1 hour, 23 minutes" / "23 minutes".
     Returns an empty string for 0 (so the meta line collapses to just track count). */
  function formatTotalDuration(totalSeconds) {
    totalSeconds = Math.max(0, Math.floor(totalSeconds || 0));
    if (!totalSeconds) return '';
    const h = Math.floor(totalSeconds / 3600);
    const m = Math.floor((totalSeconds % 3600) / 60);
    if (h && m) return h + ' hour' + (h === 1 ? '' : 's') + ', ' + m + ' minute' + (m === 1 ? '' : 's');
    if (h) return h + ' hour' + (h === 1 ? '' : 's');
    if (m) return m + ' minute' + (m === 1 ? '' : 's');
    return Math.max(1, m) + ' minute' + (m === 1 ? '' : 's');
  }

  /* Per-track duration cell, e.g. "3:42" / "1:02:15". Returns "" when missing. */
  function formatTrackDuration(track) {
    const seconds = Number(track && track.duration_seconds) || 0;
    if (!seconds) return '';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    if (h) return h + ':' + String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
    return m + ':' + String(s).padStart(2, '0');
  }

  /* Detect YouTube Music-curated playlists so the hero can mute the Share
     and More icons. YT Music publishes its curated mixes under the
     "YouTube Music" channel name, and responses come in several shapes:
     - a plain `curated: true` flag
     - a string author
     - an object like `{ name: "YouTube Music" }`
     - an array of such objects (rare, but harmless to accept)
     Handle every shape defensively so an unexpected response variation
     doesn't throw a TypeError and crash the whole page load. */
  function _isCuratedPlaylist(pl) {
    if (!pl) return false;
    if (pl.curated === true) return true;
    const raw = pl.author || pl.owner || pl.ownerName;
    let name = '';
    if (typeof raw === 'string') {
      name = raw;
    } else if (Array.isArray(raw)) {
      name = raw.map(function (r) { return (r && r.name) || ''; }).join(' ');
    } else if (raw && typeof raw === 'object') {
      name = raw.name || '';
    }
    return name.toLowerCase().indexOf('youtube music') !== -1;
  }

  function preloadPlaylistHero(pl) {
    if (!pl || pl.__heroReady) return Promise.resolve();
    const cover = imageUrl(pl.thumbnails) || imageUrl(pl.thumbnail);
    const image = imageUrl(pl.image) || imageUrl(pl.images);
    const urls = (cover || image) ? [cover || image] : (pl.tracks || []).slice(0, 4).map(track => {
      return imageUrl(track.thumbnails) || imageUrl(track.thumbnail) || imageUrl(track.image);
    }).filter(Boolean);
    return Promise.all(urls.map(url => new Promise(resolve => {
      const img = new Image();
      let settled = false;
      const timer = setTimeout(done, 8000);
      function done() {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        img.onload = null;
        img.onerror = null;
        resolve();
      }
      img.onload = done;
      img.onerror = done;
      img.src = url;
    }))).then(() => { pl.__heroReady = true; });
  }

  function songActions(track, forceLiked) {
    const liked = !!forceLiked || (window._playlistsData && window._playlistsData.liked_songs &&
      window._playlistsData.liked_songs.includes(track.video_id));
    const like = `<svg viewBox="0 0 24 24" fill="${liked ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>`;
    const more = '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>';
    return `<button class="result-like-btn${liked ? ' liked' : ''}" type="button" title="${liked ? 'Dislike' : 'Like'}" data-vid="${escapeHtml(track.video_id)}">${like}</button>` +
      `<button class="result-more-btn" type="button" title="More options">${more}</button>`;
  }

  function wireSongActions(row, track) {
    row.querySelector('.result-like-btn').addEventListener('click', function (event) {
      event.stopPropagation();
      if (window.toggleLike) window.toggleLike(track, this);
    });
    row.querySelector('.result-more-btn').addEventListener('click', function (event) {
      event.stopPropagation();
      if (window.openSongContextMenu) window.openSongContextMenu(event, track);
    });
  }

  function resolveTrackMetadata(videoId) {
    if (!videoId || !window.api) return Promise.resolve({});
    if (!trackMetadataRequests.has(videoId)) {
      trackMetadataRequests.set(videoId, window.api('/api/album/resolve/' + encodeURIComponent(videoId))
        .catch(() => ({})));
    }
    return trackMetadataRequests.get(videoId);
  }

  function openResolvedAlbum(track) {
    if (!track) return;
    const navigate = albumId => {
      if (!albumId) {
        if (window.toast) window.toast('Album unavailable for this song', 'error');
        return;
      }
      if (window.preloadNavigateAlbum) window.preloadNavigateAlbum(albumId);
      else window.navigateTo('#album/' + encodeURIComponent(albumId));
    };
    if (track.album_id) {
      navigate(track.album_id);
      return;
    }
    resolveTrackMetadata(track.video_id).then(details => {
      track.album_id = details.album_id || '';
      navigate(track.album_id);
    });
  }

  async function loadLibrary() {
    if (!state._loggedIn || window.JAM_GUEST || !window.IS_AUTHENTICATED) return;
    try {
      const data = await api('/api/library/');
      const playlists = (data.playlists || []).filter(function (playlist) {
        return !isHiddenPersonalPlaylist(playlist);
      });
      playlists.forEach(function (playlist) {
        const id = playlist.playlistId || playlist.id;
        if (id) libraryPlaylistIds.add(String(id));
      });
      const container = document.getElementById('sidebar-playlist-list');
      if (container) {
        container.innerHTML = '';
        if (playlists.length > 0) {
          playlists.forEach(pl => {
            const btn = document.createElement('button');
            const cover = imageUrl(pl.thumbnails) || imageUrl(pl.thumbnail) || imageUrl(pl.image);
            const isLiked = pl.playlistId === 'LM';
            btn.className = 'sidebar-playlist-item';
            btn.title = pl.title || 'Playlist';
            btn.setAttribute('aria-label', pl.title || 'Playlist');
            btn.innerHTML = `<span class="sidebar-playlist-art${isLiked ? ' is-liked' : ''}">${cover
              ? `<img src="${escapeHtml(cover)}" alt="" loading="lazy">`
              : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"></path><circle cx="6" cy="18" r="3"></circle><circle cx="18" cy="16" r="3"></circle></svg>`
            }</span><span class="sidebar-playlist-copy"><strong>${escapeHtml(pl.title)}</strong></span>`;
            btn.onclick = () => {
              if (window._closeSidebar) window._closeSidebar();
              if (window.preloadNavigatePlaylist) window.preloadNavigatePlaylist(pl.playlistId);
              else window.navigateTo('#playlist/' + encodeURIComponent(pl.playlistId));
            };
            container.appendChild(btn);
          });
        }
      }
    } catch (e) {
      console.error('Failed to load library playlists', e);
    }
  }



  async function openLibraryPlaylist(plId) {
    // Clear the rename/delete target up front. If the load fails, the
    // handlers must be no-ops rather than operating on a stale playlist id
    // left over from the previous openLibraryPlaylist call (which would let
    // a More-menu click on a public playlist silently rename/delete a
    // completely different library entry).
    _openPlaylistId = null;
    const page = document.getElementById('collection-detail-page');
    // Use the correct element IDs that exist in remote.html
    const titleEl = document.getElementById('playlist-detail-title');
    const body = document.getElementById('playlist-detail-body');

    const route = '#playlist/' + encodeURIComponent(plId);
    // The detail overlay is persistent DOM, so an earlier playlist request
    // can finish after the user has already navigated to an artist (or to a
    // different playlist). Only the route that started this load may reveal
    // or mutate the overlay; otherwise its stale rows can reappear over the
    // destination when the document is scrolled.
    const stillOwnsRoute = () => !window.getRoute || window.getRoute() === route;
    const preloaded = window.consumePreload ? window.consumePreload(route) : null;
    const ownsProgress = !preloaded;
    if (ownsProgress && window._barStart) window._barStart();
    if (ownsProgress && body) {
      body.innerHTML = window.CollectionRenderer
        ? window.CollectionRenderer.renderLoadingState('Loading playlist…')
        : '<div class="playlist-loading-indicator visible" role="status"><span class="playlist-loading-spinner" aria-hidden="true"></span><span>Loading playlist…</span></div>';
    }
    try {
      // The router doesn't know whether a #playlist/<id> route is the
      // user's own library entry or a public/curated playlist, so dispatch
      // here. Try the library endpoint first and fall back to the public
      // endpoint on a 404. Auth errors (401/403) are NOT retried — the user
      // should see the real error rather than silently switching to a public
      // view that may not exist for that id.
      let pl = preloaded;
      let isLibrary = false;
      let isCurated = false;
      if (pl) {
        // Liked Music is the virtual library playlist and preload responses
        // do not always include the library marker.
        isCurated = _isCuratedPlaylist(pl);
        isLibrary = plId.toUpperCase() === 'LM'
          || pl.isLibraryPlaylist === true
          || libraryPlaylistIds.has(String(plId));
      } else {
        // Always request the first page explicitly. The server can then
        // return a continuation signal for very large playlists (including
        // Liked Music) instead of silently returning its first browse page.
        // The library endpoint itself already falls back to YouTube's public
        // watch-playlist API for curated IDs; there is no second browser
        // endpoint to retry here. Let every error reach the page-level catch
        // unchanged so a real 404 remains a 404.
        pl = await window.api('/api/library/playlists/' + encodeURIComponent(plId) + '?offset=0&limit=' + PLAYLIST_PAGE_SIZE);
        isLibrary = true;
      }
      // Do not let a late response from a previous playlist reclaim the
      // shared overlay after another route has become active.
      if (!stillOwnsRoute()) return;

      const canEditPlaylist = isLibrary && !isCurated && plId.toUpperCase() !== 'LM';
      // Only personal library playlists support rename/delete. Public/curated
      // playlists have no useful More menu, so the button isn't rendered
      // and the rename/delete handlers stay no-ops (they check this var).
      if (canEditPlaylist) {
        _openPlaylistId = plId;
      }
      await preloadPlaylistHero(pl);
      if (!stillOwnsRoute()) {
        if (_openPlaylistId === plId) _openPlaylistId = null;
        return;
      }
      if (page) {
        // Keep the identity of the rendered page so browser Back/Forward can
        // restore the same collection shell before refreshed data arrives.
        page.dataset.collectionId = String(plId);
        page.hidden = false;
      }
      if (titleEl) titleEl.textContent = pl.title || 'Playlist';
      if (window.syncPageTitle) window.syncPageTitle();

      if (body) {
        body.innerHTML = '';
        const tracks = pl.tracks || [];
        // Start row playback from the tracks already rendered/fetched in the
        // browser. Resolving every page again on the server delays the first
        // note of a large playlist substantially. Continuation pages extend
        // vst.tracks (the windowed list's source) as the user scrolls.
        const title = pl.title || 'Playlist';
        // Prefer the playlist's own/default thumbnail. Some API responses use
        // `thumbnail` while others use `thumbnails`; `image` is the fallback.
        const playlistCover = imageUrl(pl.thumbnails) || imageUrl(pl.thumbnail);
        const playlistImage = imageUrl(pl.image) || imageUrl(pl.images);
        const trackCoverUrls = tracks.slice(0, 4).map(track => {
          return imageUrl(track.thumbnails) || imageUrl(track.thumbnail) || imageUrl(track.image);
        }).filter(Boolean);
        const fallbackCoverUrls = playlistImage ? [playlistImage] : trackCoverUrls;
        const renderCollage = (urls, primary) => urls.length
          ? `<div class="playlist-collage${urls.length === 1 ? ' playlist-collage-single' : ''}">${urls.map(url => `<img${primary ? ' data-playlist-primary-cover' : ''} src="${escapeHtml(url)}" alt="" loading="lazy">`).join('')}</div>`
          : `<div class="playlist-collage playlist-collage-single"><div class="collage-placeholder"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg></div></div>`;
        const fallbackCollage = renderCollage(fallbackCoverUrls, false);
        const isLikedPlaylist = plId.toUpperCase() === 'LM';
        const likedBanner = `<div class="playlist-collage playlist-collage-single liked-playlist-banner" aria-label="Liked Music"><svg viewBox="0 0 24 24" fill="white" stroke="white" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 1 2-1.7l1.38-9a2 2 0 0 1-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg></div>`;
        const collage = isLikedPlaylist
          ? likedBanner
          : (playlistCover ? renderCollage([playlistCover], true) : fallbackCollage);

        const hero = document.createElement('section');
        hero.className = 'playlist-detail-hero';
        // Compute total duration once so the meta line can show e.g. "12 songs • 47 minutes"
        const totalSeconds = pl.has_more ? (Number(pl.totalDurationSeconds) || 0) : tracks.reduce((sum, t) => sum + (Number(t.duration_seconds) || 0), 0);
        const totalDuration = formatTotalDuration(totalSeconds);
        const trackCount = Number(pl.trackCount) || tracks.length;
        const metaParts = [`${trackCount} ${trackCount === 1 ? 'song' : 'songs'}`];
        if (totalDuration) metaParts.push(totalDuration);
        // Shuffle, Add to queue, Share, More in the existing action spans
        // so the 1fr-auto-1fr grid keeps Play centered. Icons are tiny so they
        // sit comfortably beside the large Play button without fighting it for
        // attention. The queue action appends the complete collection, and the
        // "more" icon matches the one that used to
        // live in the header (now moved here so the menu is closer to the
        // playlist identity).
        const playlistShareId = pl.playlistId || pl.playlist_id || plId;
        // The "more" button uses the same id as the previous header copy so
        // the existing rename / delete wiring in this file (and the inline
        // listener below) attaches to the new button. The id is shared
        // intentionally — only one #playlist-detail-more-btn exists at a time.
        // Build the right-hand action span based on playlist type:
        // - Library: Share + More (rename/delete menu)
        // - Public (other user): Share only — can't edit someone else's playlist
        // - Curated (YT Music): empty — read-only, no useful Share either
        // Conditional HTML (not CSS hiding) keeps the DOM clean: no
        // display:none buttons to focus via keyboard or click via dev tools.
        // Wrap secondary actions in the existing left/right flex spans so the
        // 1fr-auto-1fr grid stays valid (Play sits in the auto column).
        body.appendChild(hero);

        if (window.CollectionRenderer) {
          hero.outerHTML = window.CollectionRenderer.renderDetailHero({
            className: 'playlist-detail-hero',
            coverHtml: collage,
            title: title,
            description: pl.description || '',
            meta: metaParts.join(' \u2022 '),
            showActions: !!tracks.length,
            showShare: true,
            shareDisabled: isLikedPlaylist,
            showMore: true,
            // Only editable personal playlists have a useful More action;
            // normal/public/curated playlists and Liked Music stay muted.
            moreDisabled: isLikedPlaylist || !canEditPlaylist
          });
        }
        const renderedHero = body.querySelector('.playlist-detail-hero');

        // A present-but-expired YouTube thumbnail should fall back exactly as
        // a missing thumbnail does, instead of leaving a broken image tile.
        const primaryCover = renderedHero && renderedHero.querySelector('[data-playlist-primary-cover]');
        if (primaryCover) {
          primaryCover.addEventListener('error', () => {
            const cover = primaryCover.closest('.playlist-collage');
            if (cover) cover.outerHTML = fallbackCollage;
          }, { once: true });
        }

        const list = document.createElement('div');
        list.className = 'history-list';
        if (tracks.length === 0) {
          list.innerHTML = '<div style="padding:24px; color:var(--muted); text-align:center;">No tracks in this playlist</div>';
        } else {
          // ---- Virtualized (windowed) track list --------------------------
          // A long playlist can hold thousands of rows. Rendering every row
          // (each carrying its own swipe/click/artist listeners and SVG action
          // markup) and keeping them all in the DOM janks the page as scrolling
          // accumulates them. Keep only a viewport-sized window of wrappers
          // materialized and prune the rest. Two spacers pin the total height
          // to trackCount * rowHeight so the scrollbar stays correct, the
          // pagination sentinel stays at the true bottom, and pruned rows
          // replace exactly their own vertical space — nothing visibly jumps.
          const VIRTUAL_BUFFER = 40;
          const topSpacer = document.createElement('div');
          topSpacer.className = 'pl-virtual-spacer pl-virtual-top';
          topSpacer.setAttribute('aria-hidden', 'true');
          const bottomSpacer = document.createElement('div');
          bottomSpacer.className = 'pl-virtual-spacer pl-virtual-bottom';
          bottomSpacer.setAttribute('aria-hidden', 'true');
          list.appendChild(topSpacer);
          list.appendChild(bottomSpacer);

          // Build one row for an absolute track index. Elements are reused
          // across render passes (keyed by index), so a row keeps its loaded
          // thumbnail and wired listeners when it scrolls back into view.
          function buildRow(track, index) {
            const wrapper = document.createElement('div');
            wrapper.className = 'result-swipe-wrapper';
            wrapper.dataset.index = String(index);
            const row = document.createElement('div');
            row.className = 'history-item';
            row.dataset.mobileRowPlay = 'true';
            const thumbnail = imageUrl(track.thumbnails) || imageUrl(track.thumbnail) || imageUrl(track.image) || '/static/default-art.png';
            // Build parallel arrays: display names and per-artist channel IDs.
            // ytmusicapi returns artist objects with both .name and .id.
            const artistParts = [];
            const artistChannelIds = [];
            if (Array.isArray(track.artists)) {
              track.artists.forEach(a => {
                const name = typeof a === 'string' ? a : (a && a.name);
                if (name) {
                  artistParts.push(name);
                  // Personal-playlist responses can call this browse/channel id
                  // `browseId` or `channelId` instead of `id`.
                  artistChannelIds.push((a && (a.id || a.browseId || a.channelId || a.channel_id)) || '');
                }
              });
            }
            const artist = artistParts.length ? artistParts.join(', ') : (track.artist || '');
            const videoId = track.videoId || track.video_id || '';
            const album = track.album && typeof track.album === 'object' ? track.album : {};
            const albumId = track.album_id || track.albumId || album.id || album.browseId || '';
            const artistId = track.artist_id || track.channel_id || track.artistId ||
              (Array.isArray(track.artists) && track.artists[0] &&
                (track.artists[0].id || track.artists[0].browseId || track.artists[0].channelId || track.artists[0].channel_id)) || '';
            wrapper.dataset.videoId = videoId;
            wrapper.dataset.albumId = albumId;
            wrapper._songContextTrack = {
              video_id: videoId,
              title: track.title || '',
              artist,
              thumbnail,
              album_id: albumId,
              album: album,
              artist_id: artistId
            };
            const contextTrack = wrapper._songContextTrack;
            const trackDuration = formatTrackDuration(track);
            row.innerHTML = `
              <div class="playlist-track-num">${index + 1}</div>
              <div class="playlist-track-art">
                <img data-src="${escapeHtml(thumbnail)}" class="queue-thumb" loading="lazy" alt="" onload="this.classList.add('loaded')" onerror="this.style.opacity='1'">
                <span class="playlist-track-playback-indicator" aria-hidden="true">
                  <svg class="playlist-track-play-glyph" viewBox="0 0 24 24" fill="currentColor"><polygon points="7,4 20,12 7,20"/></svg>
                  <span class="music-bars"><i></i><i></i><i></i><i></i><i></i></span>
                </span>
              </div>
              <div class="queue-info">
                <div class="queue-title">${escapeHtml(track.title || '')}</div>
                <div class="queue-artist">${window.artistLinksHtml(artist, artistChannelIds.length ? artistChannelIds : (track.channelId || track.channel_id || ''))}</div>
              </div>
              ${trackDuration ? `<div class="track-duration playlist-track-duration">${escapeHtml(trackDuration)}</div>` : ''}
              ${songActions(contextTrack, plId.toUpperCase() === 'LM')}`;
            row.onclick = () => {
              // Use the tracks already loaded in the page so the selected song
              // starts immediately. This remains a collection queue, unlike
              // the single-song/radio playback path.
              if (window.playCollection) {
                window.playCollection(vst.tracks, {
                  startIndex: index,
                  startVideoId: videoId
                });
              }
            };
            // The row is the playback target; artist links and the three-dot
            // action button stop propagation in their own handlers.
            // Playlist payloads occasionally label an unknown artist as
            // "Song". Resolve the canonical music metadata in the background
            // and replace that placeholder without delaying page rendering.
            if (!artist || artist.trim().toLowerCase() === 'song') {
              resolveTrackMetadata(videoId).then(details => {
                if (!details.artist) return;
                contextTrack.artist = details.artist;
                contextTrack.artist_id = details.artist_id || contextTrack.artist_id || '';
                contextTrack.album_id = details.album_id || contextTrack.album_id || '';
                const artistNode = row.querySelector('.queue-artist');
                if (artistNode) artistNode.innerHTML = window.artistLinksHtml(
                  details.artist, details.artist_id || ''
                );
                if (window.wireArtistLinks) window.wireArtistLinks(row);
              });
            }
            if (window.wireArtistLinks) window.wireArtistLinks(row);
            wireSongActions(row, contextTrack);
            if (window.attachResultSwipeGesture) window.attachResultSwipeGesture(wrapper, row, contextTrack);
            wrapper.appendChild(row);
            return wrapper;
          }

          const vst = {
            tracks: tracks.slice(),  // all loaded tracks (grows via continuation)
            rowH: 0,
            rows: [],                // index -> wrapper (reused across windows)
            startIdx: -1,
            endIdx: -1,
            topSpacer: topSpacer,
            bottomSpacer: bottomSpacer,
            bodyIsScroll: false,
            scrollEl: null,
            loadingNode: null
          };
          list._plVirtual = vst;

          function bodyScrollsItself() {
            try {
              const ov = getComputedStyle(body).overflowY;
              return !!ov && (ov === 'auto' || ov === 'scroll');
            } catch (_) { return false; }
          }

          function measureRowHeight() {
            const first = list.querySelector('.result-swipe-wrapper');
            if (first && first.offsetHeight) return first.offsetHeight;
            return (window.matchMedia && window.matchMedia('(max-width: 899px)').matches) ? 52 : 76;
          }

          function renderWindow() {
            const total = vst.tracks.length;
            if (!total) return;
            vst.rowH = measureRowHeight();
            const rowH = vst.rowH || 70;
            let viewportTop = 0;
            let viewportH = window.innerHeight || 400;
            if (vst.bodyIsScroll && vst.scrollEl) {
              const er = vst.scrollEl.getBoundingClientRect();
              viewportTop = er.top;
              if (vst.scrollEl.clientHeight) viewportH = vst.scrollEl.clientHeight;
            }
            const listRect = list.getBoundingClientRect();
            let localTop = viewportTop - listRect.top;
            if (localTop < 0) localTop = 0;
            let start = Math.floor(localTop / rowH) - VIRTUAL_BUFFER;
            if (start < 0) start = 0;
            let end = Math.ceil((localTop + viewportH) / rowH) + VIRTUAL_BUFFER;
            if (end < start) end = start;
            if (end > total) end = total;
            // Keep the total height == total * rowH so the scrollbar and the
            // paginator's bottom sentinel stay correct. Updated on every pass
            // (not only when the window moves) so continuation pages that grow
            // the loaded set always push the sentinel to the true bottom.
            topSpacer.style.height = (start * rowH) + 'px';
            bottomSpacer.style.height = ((total - end) * rowH) + 'px';
            if (start === vst.startIdx && end === vst.endIdx) return;
            vst.startIdx = start;
            vst.endIdx = end;
            list.querySelectorAll('.result-swipe-wrapper').forEach(n => {
              if (n.parentElement === list) n.remove();
            });
            for (let i = start; i < end; i++) {
              let wrap = vst.rows[i];
              if (!wrap) wrap = vst.rows[i] = buildRow(vst.tracks[i], i);
              // Insert before the bottom spacer so the window stays between
              // the two spacers (the continuation sentinel follows it).
              bottomSpacer.before(wrap);
            }
            observeLazyImages(list);
            if (window.syncTrackPlaybackIndicators) window.syncTrackPlaybackIndicators();
          }

          function scheduleRender() {
            if (vst._raf) return;
            vst._raf = requestAnimationFrame(() => {
              vst._raf = 0;
              renderWindow();
            });
          }

          vst.bodyIsScroll = bodyScrollsItself();
          vst.scrollEl = vst.bodyIsScroll ? body : (document.scrollingElement || document.documentElement);
          const onVirtualScroll = () => scheduleRender();
          const onVirtualResize = () => scheduleRender();
          const onVirtualLoad = () => renderWindow();
          // The scroll container is shared persistent DOM. Clearing the
          // previous open's listeners prevents one callback from being added
          // per playlist visited.
          if (body.__plVirtualCleanup) body.__plVirtualCleanup();
          body.__plVirtualCleanup = () => {
            vst.scrollEl.removeEventListener('scroll', onVirtualScroll);
            if (!vst.bodyIsScroll) window.removeEventListener('scroll', onVirtualScroll);
            window.removeEventListener('resize', onVirtualResize);
            window.removeEventListener('load', onVirtualLoad);
          };
          vst.scrollEl.addEventListener('scroll', onVirtualScroll, { passive: true });
          if (!vst.bodyIsScroll) window.addEventListener('scroll', onVirtualScroll, { passive: true });
          window.addEventListener('resize', onVirtualResize, { passive: true });
          window.addEventListener('load', onVirtualLoad);

          // Some YouTube Music browse responses omit `has_more` despite a
          // larger declared track count. The count is authoritative for
          // keeping the continuation loader alive.
          if (pl.has_more || trackCount > tracks.length) {
            const loadingEl = document.createElement('div');
            loadingEl.className = 'playlist-loading-indicator';
            loadingEl.innerHTML = '<span class="playlist-loading-spinner" aria-hidden="true"></span><span>Loading more songs…</span>';
            list.appendChild(loadingEl);
            vst.loadingNode = loadingEl;
            let nextOffset = Number(pl.next_offset) || tracks.length;
            let loadingTracks = false;
            let exhausted = false;
            const scrollRoot = list.closest('.history-modal-body');
            const cleanupPagination = () => {
              exhausted = true;
              loadingObserver.disconnect();
              if (scrollRoot) scrollRoot.removeEventListener('scroll', loadWhenNearEnd);
              if (loadingEl && loadingEl.parentElement) loadingEl.remove();
              vst.loadingNode = null;
            };
            const loadMoreTracks = async () => {
              if (loadingTracks || exhausted || !list.isConnected) return;
              loadingTracks = true;
              loadingEl.classList.add('visible');
              try {
                const page = await window.api('/api/library/playlists/' + encodeURIComponent(plId) + '?offset=' + nextOffset + '&limit=' + PLAYLIST_PAGE_SIZE);
                const batch = page.tracks || [];
                // Extend the loaded set; the windowed renderer materializes
                // only what is near the viewport.
                vst.tracks.push(...batch);
                renderWindow();
                observeLazyImages(list);
                const returnedNextOffset = Number(page.next_offset) || (nextOffset + batch.length);
                const declaredTotal = Number(page.trackCount) || 0;
                const pageHasMore = page.has_more || returnedNextOffset < declaredTotal;
                nextOffset = returnedNextOffset;
                if (!pageHasMore || !batch.length) {
                  cleanupPagination();
                  return;
                }
                // If the viewport is taller than the appended page, continue
                // fetching until it is filled instead of waiting for another
                // IntersectionObserver edge transition.
                requestAnimationFrame(loadWhenNearEnd);
              } catch (e) {
                if (window.toast) window.toast('Could not load more songs', 'error');
              } finally {
                loadingEl.classList.remove('visible');
                loadingTracks = false;
                // Appending moves the sentinel down, which can suppress its
                // next observer edge. Re-check after releasing the lock so a
                // list already at the bottom continues loading.
                requestAnimationFrame(loadWhenNearEnd);
                scheduleRender();
              }
            };
            const loadWhenNearEnd = () => {
              if (!scrollRoot || exhausted || loadingTracks) return;
              if (scrollRoot.scrollTop + scrollRoot.clientHeight >= scrollRoot.scrollHeight - 240) {
                loadMoreTracks();
              }
            };
            const loadingObserver = new IntersectionObserver(function (entries) {
              entries.forEach(function (entry) {
                if (entry.isIntersecting) loadMoreTracks();
              });
            }, { root: scrollRoot, rootMargin: '240px 0px' });
            loadingObserver.observe(loadingEl);
            if (scrollRoot) scrollRoot.addEventListener('scroll', loadWhenNearEnd, { passive: true });
            requestAnimationFrame(loadWhenNearEnd);
          }
          // Render the visible window immediately; a scheduled pass reruns
          // after the list is appended to the body so sizes are measured.
          renderWindow();
          scheduleRender();
          const heroPlay = renderedHero && renderedHero.querySelector('.playlist-hero-play');
          if (heroPlay) heroPlay.addEventListener('click', () => {
            if (window.playCollection) window.playCollection([], { playlistId: plId });
          });
          // Ask the server to load the complete playlist (including pages that
          // have not been rendered yet) and randomize it before track 1 starts.
          const heroShuffle = renderedHero && renderedHero.querySelector('.playlist-hero-shuffle');
          if (heroShuffle) heroShuffle.addEventListener('click', () => {
            if (window.playCollection) window.playCollection([], { playlistId: plId, shuffle: true });
          });
          // Append every playlist track in one operation. The server resolves
          // the playlist id so unloaded continuation pages are included too.
          const heroAddQueue = renderedHero && renderedHero.querySelector('.playlist-hero-add-queue');
          if (heroAddQueue) heroAddQueue.addEventListener('click', () => {
            if (window.addCollectionToQueue) window.addCollectionToQueue([], { playlistId: plId });
          });
          // More: the rename / delete menu moved into the actions area so
          // it's reachable without scrolling back to the page header. This
          // wires the new button (id="playlist-detail-more-btn") to the
          // existing #playlist-detail-more-menu. The legacy IIFE below
          // also looks up the same id — both listeners point at the same
          // single button now, but they do different work (positioning vs
          // outside-click close), so we keep both.
          const heroMore = renderedHero && renderedHero.querySelector('.playlist-hero-more');
          if (heroMore) {
            heroMore.id = 'playlist-detail-more-btn';
            heroMore.addEventListener('click', function (e) {
              e.stopPropagation();
              e.preventDefault();
              if (isLikedPlaylist || !canEditPlaylist) return;
              const menu = document.getElementById('playlist-detail-more-menu');
              if (!menu) return;
              // On PC the legacy header-actions wrapper is hidden. Move the
              // shared menu out of that hidden parent before displaying it.
              if (menu.parentElement !== document.body) document.body.appendChild(menu);
              if (menu.classList.contains('open')) {
                menu.classList.remove('open');
                return;
              }
              if (window._closeAllMoreMenus) window._closeAllMoreMenus();
              const rect = heroMore.getBoundingClientRect();
              menu.style.position = 'fixed';
              menu.style.zIndex = '10000';
              menu.style.left = 'auto';
              menu.style.right = Math.max(8, window.innerWidth - rect.right) + 'px';
              menu.style.bottom = 'auto';
              menu.style.top = (rect.bottom + 4) + 'px';
              // Keep the menu inside the PC viewport when the button is near
              // the bottom edge of the playlist page.
              const menuHeight = 90;
              if (rect.bottom + menuHeight + 4 > window.innerHeight) {
                menu.style.top = 'auto';
                menu.style.bottom = Math.max(8, window.innerHeight - rect.top + 4) + 'px';
              }
              menu.classList.add('open');
            });
          }
          // Share the canonical YouTube Music playlist URL, never this app's
          // routed page URL. Falls back to a textarea trick when needed.
          const heroShare = renderedHero && renderedHero.querySelector('.playlist-hero-share');
          if (heroShare && !isLikedPlaylist) heroShare.addEventListener('click', async () => {
            const url = 'https://music.youtube.com/playlist?list=' + encodeURIComponent(playlistShareId);
            if (navigator.share) {
              try {
                await navigator.share({ title: title, text: `Listen to ${title}`, url: url });
                return;
              } catch (e) {
                if (e && e.name === 'AbortError') return;
              }
            }
            let copied = false;
            try {
              if (navigator.clipboard && navigator.clipboard.writeText) {
                await navigator.clipboard.writeText(url);
                copied = true;
              }
            } catch (e) { /* fall through to legacy path */ }
            if (!copied) {
              const tmp = document.createElement('textarea');
              tmp.value = url;
              tmp.style.position = 'fixed';
              tmp.style.opacity = '0';
              document.body.appendChild(tmp);
              tmp.select();
              try { copied = document.execCommand('copy'); } catch (e) { copied = false; }
              document.body.removeChild(tmp);
            }
            if (window.toast) {
              window.toast(copied ? 'Link copied to clipboard' : 'Could not copy link', copied ? 'ok' : 'error');
            }
          });
        }
        body.appendChild(list);
      }
    } catch (e) {
      if (!stillOwnsRoute()) return;
      if (window._isNotFoundError && window._isNotFoundError(e) && window._showNotFoundPage) {
        window._showNotFoundPage();
        return;
      }
      console.error('Failed to load playlist', e);
      if (titleEl) titleEl.textContent = 'Error loading playlist';
      if (body) body.innerHTML = '<div style="padding:24px; color:var(--muted); text-align:center;">Failed to load playlist</div>';
      if (window._barAbort) window._barAbort();
    }
    if (ownsProgress && stillOwnsRoute() && window._barComplete) window._barComplete();
  }
  window.openPlaylistDetailModal = openLibraryPlaylist;

  /* ---- Add the selected song to a YouTube Music library playlist ---- */
  (function () {
    const overlay = document.getElementById('add-to-playlist-overlay');
    const closeBtn = document.getElementById('add-to-playlist-close');
    const listEl = document.getElementById('add-to-playlist-list');
    const nameInput = document.getElementById('new-playlist-name');
    const createBtn = document.getElementById('new-playlist-btn');
    let selectedTrack = null;
    let requestVersion = 0;

    if (!overlay || !listEl) return;

    function closeModal() {
      requestVersion += 1;
      overlay.classList.remove('open');
      selectedTrack = null;
    }

    function playlistId(playlist) {
      return String(playlist && (playlist.playlistId || playlist.id) || '').trim();
    }

    function playlistTitle(playlist) {
      return String(playlist && (playlist.title || playlist.name) || 'Untitled playlist');
    }

    function renderPlaylists(playlists, version) {
      if (version !== requestVersion || !overlay.classList.contains('open')) return;
      listEl.innerHTML = '';
      const writable = (playlists || []).filter(function (playlist) {
        return playlistId(playlist) && playlist.editable === true && !isHiddenPersonalPlaylist(playlist);
      });
      if (!writable.length) {
        listEl.innerHTML = '<div style="color:var(--muted); padding:12px;">No playlists found. Create one above.</div>';
        return;
      }

      writable.forEach(function (playlist) {
        const id = playlistId(playlist);
        const row = document.createElement('button');
        row.type = 'button';
        row.style.cssText = 'width:100%; padding:12px; border:0; border-bottom:1px solid var(--border); cursor:pointer; display:flex; align-items:center; gap:12px; color:var(--text); background:transparent; text-align:left;';
        const cover = imageUrl(playlist.thumbnails) || imageUrl(playlist.thumbnail) || imageUrl(playlist.image);
        row.innerHTML = cover
          ? '<img class="queue-thumb loaded" src="' + escapeHtml(cover) + '" alt="">'
          : '<div class="queue-thumb" style="display:flex;align-items:center;justify-content:center;background:rgba(255,255,255,.05);"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:20px;height:20px;"><path d="M9 18V5l12-2v13"></path><circle cx="6" cy="18" r="3"></circle><circle cx="18" cy="16" r="3"></circle></svg></div>';
        const label = document.createElement('span');
        label.style.cssText = 'flex:1;font-weight:500;';
        label.textContent = playlistTitle(playlist);
        row.appendChild(label);

        row.addEventListener('click', async function () {
          const track = selectedTrack;
          if (!track || !track.video_id || row.disabled) return;
          row.disabled = true;
          try {
            await window.api('/api/library/playlists/' + encodeURIComponent(id) + '/tracks', {
              video_id: track.video_id
            });
            if (window.toast) window.toast('Added to “' + playlistTitle(playlist) + '”', 'ok');
            closeModal();
            await loadLibrary();
          } catch (error) {
            console.error('Failed to add track to playlist', error);
            if (window.toast) window.toast(error.message || 'Failed to add to playlist', 'error');
            row.disabled = false;
          }
        });
        listEl.appendChild(row);
      });
    }

    async function loadPlaylistChoices(version) {
      listEl.innerHTML = '<div style="color:var(--muted); padding:12px;">Loading playlists…</div>';
      try {
        const data = await window.api('/api/library/');
        renderPlaylists(data.playlists || [], version);
      } catch (error) {
        if (version !== requestVersion) return;
        console.error('Failed to load playlist choices', error);
        listEl.innerHTML = '<div style="color:var(--muted); padding:12px;">Could not load playlists.</div>';
        if (window.toast) window.toast(error.message || 'Could not load playlists', 'error');
      }
    }

    window.openAddToPlaylistModal = function (track) {
      const videoId = String(track && (track.video_id || track.videoId) || '').trim();
      if (!videoId) {
        if (window.toast) window.toast('This song cannot be added to a playlist', 'error');
        return;
      }
      selectedTrack = Object.assign({}, track, { video_id: videoId });
      if (nameInput) nameInput.value = '';
      overlay.classList.add('open');
      const version = ++requestVersion;
      loadPlaylistChoices(version);
    };

    if (closeBtn) closeBtn.addEventListener('click', closeModal);
    overlay.addEventListener('click', function (event) {
      if (event.target === overlay) closeModal();
    });

    if (nameInput) {
      nameInput.addEventListener('keydown', function (event) {
        if (event.key === 'Enter' && createBtn) createBtn.click();
      });
    }

    if (createBtn) {
      createBtn.addEventListener('click', async function () {
        const name = String(nameInput && nameInput.value || '').trim();
        const track = selectedTrack;
        if (!name || !track || !track.video_id || createBtn.disabled) {
          if (!name && nameInput) nameInput.focus();
          return;
        }
        createBtn.disabled = true;
        createBtn.textContent = 'Creating…';
        try {
          const created = await window.api('/api/library/playlists/', { name: name });
          await window.api('/api/library/playlists/' + encodeURIComponent(created.id) + '/tracks', {
            video_id: track.video_id
          });
          if (window.toast) window.toast('Created “' + name + '” and added the song', 'ok');
          closeModal();
          await loadLibrary();
        } catch (error) {
          console.error('Failed to create playlist and add track', error);
          if (window.toast) window.toast(error.message || 'Failed to create playlist', 'error');
        } finally {
          createBtn.disabled = false;
          createBtn.textContent = 'Create';
        }
      });
    }
  })();

  /* ---- New Playlist button (sidebar) ---- */
  (function () {
    const newBtn = document.getElementById('sidebar-new-playlist-btn');
    const overlay = document.getElementById('new-playlist-overlay');
    const closeBtn = document.getElementById('new-playlist-overlay-close');
    const nameInput = document.getElementById('new-playlist-overlay-name');
    const descInput = document.getElementById('new-playlist-overlay-desc');
    const createBtn = document.getElementById('new-playlist-overlay-create');

    if (!newBtn || !overlay) return;

    function openModal() {
      if (nameInput) nameInput.value = '';
      if (descInput) descInput.value = '';
      overlay.classList.add('open');
      if (nameInput) setTimeout(() => nameInput.focus(), 80);
    }

    function closeModal() {
      overlay.classList.remove('open');
    }

    newBtn.addEventListener('click', () => {
      if (window._closeSidebar) window._closeSidebar();
      openModal();
    });

    if (closeBtn) closeBtn.addEventListener('click', closeModal);

    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) closeModal();
    });

    if (nameInput) {
      nameInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') createBtn && createBtn.click();
      });
    }

    if (createBtn) {
      createBtn.addEventListener('click', async () => {
        const name = (nameInput ? nameInput.value : '').trim();
        if (!name) {
          if (nameInput) nameInput.focus();
          return;
        }
        const desc = (descInput ? descInput.value : '').trim();
        createBtn.disabled = true;
        createBtn.textContent = 'Creating…';
        try {
          await window.api('/api/library/playlists/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, description: desc })
          });
          closeModal();
          if (window.showToast) window.showToast('Playlist "' + name + '" created');
          // Refresh the sidebar playlist list
          await loadLibrary();
        } catch (e) {
          console.error('Failed to create playlist', e);
          if (window.showToast) window.showToast('Failed to create playlist');
        } finally {
          createBtn.disabled = false;
          createBtn.textContent = 'Create Playlist';
        }
      });
    }
  })();

  // ---- Playlist detail 3-dot more menu: outside-click + scroll close ----
  // The button's own click handler lives inline in openLibraryPlaylist (so
  // the menu's position tracks the freshly rendered hero). This IIFE only
  // owns the document-level close behaviours — it must NOT add a second
  // click handler on the button, or the two toggle checks race and the
  // menu never stays open (the first handler opens it, the second sees
  // it as open and closes it in the same tick).
  (function () {
    const menu = document.getElementById('playlist-detail-more-menu');
    if (!menu) return;

    // Close when clicking outside the menu.
    document.addEventListener('click', function (e) {
      if (menu.classList.contains('open') &&
          !e.target.closest('#playlist-detail-more-btn') &&
          !e.target.closest('#playlist-detail-more-menu')) {
        menu.classList.remove('open');
      }
    });

    // Close on scroll of the playlist body.
    var body = document.getElementById('playlist-detail-body');
    if (body) {
      body.addEventListener('scroll', function () {
        if (menu.classList.contains('open')) menu.classList.remove('open');
      }, { passive: true });
    }
  })();

  // ---- Playlist detail rename option ----
  (function () {
    var renameOpt = document.getElementById('playlist-detail-rename-opt');
    var renameOverlay = document.getElementById('rename-playlist-overlay');
    var renameInput = document.getElementById('rename-playlist-input');
    var renameBtn = document.getElementById('rename-playlist-btn');
    var renameClose = document.getElementById('rename-playlist-close');
    var menu = document.getElementById('playlist-detail-more-menu');
    if (!renameOpt || !renameOverlay || !renameInput || !renameBtn) return;

    renameOpt.addEventListener('click', function () {
      if (menu) menu.classList.remove('open');
      // Pre-fill with the current playlist title
      var titleEl = document.getElementById('playlist-detail-title');
      renameInput.value = titleEl ? titleEl.textContent.trim() : '';
      renameOverlay.classList.add('open');
      setTimeout(function () { renameInput.focus(); renameInput.select(); }, 80);
    });

    if (renameClose) {
      renameClose.addEventListener('click', function () {
        renameOverlay.classList.remove('open');
      });
    }

    renameOverlay.addEventListener('click', function (e) {
      if (e.target === renameOverlay) renameOverlay.classList.remove('open');
    });

    renameInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') renameBtn.click();
    });

    renameBtn.addEventListener('click', async function () {
      var newTitle = renameInput.value.trim();
      if (!newTitle || !_openPlaylistId) return;
      renameBtn.disabled = true;
      renameBtn.textContent = 'Saving…';
      try {
        await window.apiPatch('/api/library/playlists/' + encodeURIComponent(_openPlaylistId), { title: newTitle });
        renameOverlay.classList.remove('open');
        // Update the title in the UI
        var titleEl = document.getElementById('playlist-detail-title');
        if (titleEl) titleEl.textContent = newTitle;
        if (window.syncPageTitle) window.syncPageTitle();
        if (window.showToast) window.showToast('Playlist renamed');
        // Refresh sidebar playlists
        if (window.loadLibrary) window.loadLibrary();
      } catch (e) {
        console.error('Failed to rename playlist', e);
        if (window.showToast) window.showToast('Failed to rename playlist', 'error');
      } finally {
        renameBtn.disabled = false;
        renameBtn.textContent = 'Save';
      }
    });
  })();

  // ---- Playlist detail delete option ----
  (function () {
    var deleteOpt = document.getElementById('playlist-detail-delete-opt');
    var confirmOverlay = document.getElementById('confirm-dialog-overlay');
    var confirmMsg = document.getElementById('confirm-dialog-message');
    var confirmCancel = document.getElementById('confirm-dialog-cancel');
    var confirmOk = document.getElementById('confirm-dialog-ok');
    var menu = document.getElementById('playlist-detail-more-menu');
    if (!deleteOpt || !confirmOverlay || !confirmMsg || !confirmCancel || !confirmOk) return;

    var _pendingDeleteOk = null;  // one-shot callback for the OK button

    deleteOpt.addEventListener('click', function () {
      if (menu) menu.classList.remove('open');
      confirmMsg.textContent = 'Delete this playlist? This cannot be undone.';
      confirmOk.textContent = 'Delete';
      confirmOk.style.background = 'var(--danger, #e53935)';
      _pendingDeleteOk = async function () {
        if (!_openPlaylistId) return;
        confirmOk.disabled = true;
        confirmOk.style.background = '';
        try {
          await window.apiDelete('/api/library/playlists/' + encodeURIComponent(_openPlaylistId));
          confirmOverlay.classList.remove('open');
          // Leave the collection detail page and go back to Library.
          var page = document.getElementById('collection-detail-page');
          if (page) page.hidden = true;
          if (window.showToast) window.showToast('Playlist deleted');
          // Refresh sidebar playlists
          if (window.loadLibrary) window.loadLibrary();
          // Navigate back to library
          if (window.navigateTo) window.navigateTo('#library');
        } catch (e) {
          console.error('Failed to delete playlist', e);
          if (window.showToast) window.showToast('Failed to delete playlist', 'error');
        } finally {
          confirmOk.disabled = false;
        }
      };
      confirmOverlay.classList.add('open');
    });

    confirmCancel.addEventListener('click', function () {
      confirmOverlay.classList.remove('open');
      confirmOk.style.background = '';
      _pendingDeleteOk = null;
    });

    confirmOverlay.addEventListener('click', function (e) {
      if (e.target === confirmOverlay) {
        confirmOverlay.classList.remove('open');
        confirmOk.style.background = '';
        _pendingDeleteOk = null;
      }
    });

    confirmOk.addEventListener('click', function () {
      if (_pendingDeleteOk) {
        var fn = _pendingDeleteOk;
        _pendingDeleteOk = null;
        fn();
      }
    });
  })();

  // Load immediately if logged in, else device.js will call it when auth is verified.
  if (state._loggedIn && window.IS_AUTHENTICATED) {
    loadLibrary();
  }

  // Attach to window so device.js can trigger it on login
  window.loadLibrary = loadLibrary;
})();
