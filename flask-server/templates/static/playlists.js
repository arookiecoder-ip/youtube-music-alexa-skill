(function () {
  'use strict';
  const state = window.__appState = window.__appState || {};
  if (state._loggedIn === undefined) state._loggedIn = false;
  var _openPlaylistId = null;  // currently open playlist detail plId
  const libraryPlaylistIds = new Set();
  const trackMetadataRequests = new Map();

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
    const observer = new IntersectionObserver(function (entries, obs) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        load(entry.target);
        obs.unobserve(entry.target);
      });
    }, { root: scrollRoot, rootMargin: '120px 0px' });
    images.forEach(function (img) { observer.observe(img); });
  }

  function imageUrl(value) {
    if (!value) return '';
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) {
      for (let i = value.length - 1; i >= 0; i -= 1) {
        const url = imageUrl(value[i]);
        if (url) return url;
      }
      return '';
    }
    if (typeof value === 'object') {
      return value.url || value.src || imageUrl(value.thumbnails) || imageUrl(value.images) || '';
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
      (data.playlists || []).forEach(function (playlist) {
        const id = playlist.playlistId || playlist.id;
        if (id) libraryPlaylistIds.add(String(id));
      });
      const container = document.getElementById('sidebar-playlist-list');
      if (container) {
        container.innerHTML = '';
        if (data.playlists && data.playlists.length > 0) {
          data.playlists.forEach(pl => {
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
      console.warn('Failed to load library playlists', e);
    }
  }



  async function openLibraryPlaylist(plId) {
    // Clear the rename/delete target up front. If the load fails, the
    // handlers must be no-ops rather than operating on a stale playlist id
    // left over from the previous openLibraryPlaylist call (which would let
    // a More-menu click on a public playlist silently rename/delete a
    // completely different library entry).
    _openPlaylistId = null;
    const overlay = document.getElementById('playlist-detail-modal-overlay');
    // Use the correct element IDs that exist in remote.html
    const titleEl = document.getElementById('playlist-detail-title');
    const body = document.getElementById('playlist-detail-body');

    const route = '#playlist/' + encodeURIComponent(plId);
    const preloaded = window.consumePreload ? window.consumePreload(route) : null;
    const ownsProgress = !preloaded;
    if (ownsProgress && window._barStart) window._barStart();
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
        try {
          pl = await window.api('/api/library/playlists/' + encodeURIComponent(plId));
          isLibrary = true;
        } catch (e1) {
          const status = e1 && (e1.status || (e1.response && e1.response.status));
          if (status !== 404) throw e1;
          pl = await window.api('/api/playlists/' + encodeURIComponent(plId));
          isLibrary = false;
          isCurated = _isCuratedPlaylist(pl);
        }
      }
      const canEditPlaylist = isLibrary && !isCurated && plId.toUpperCase() !== 'LM';
      // Only personal library playlists support rename/delete. Public/curated
      // playlists have no useful More menu, so the button isn't rendered
      // and the rename/delete handlers stay no-ops (they check this var).
      if (canEditPlaylist) {
        _openPlaylistId = plId;
      }
      await preloadPlaylistHero(pl);
      if (overlay) overlay.classList.add('open');
      if (titleEl) titleEl.textContent = pl.title || 'Playlist';

      if (body) {
        body.innerHTML = '';
        const tracks = pl.tracks || [];
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
        // Shuffle, Play next, Share, More in the existing left/right action spans
        // so the 1fr-auto-1fr grid keeps Play centered. Icons are tiny so they
        // sit comfortably beside the large Play button without fighting it for
        // attention. The "play next" icon matches the one used in the result
        // row context menu, and the "more" icon matches the one that used to
        // live in the header (now moved here so the menu is closer to the
        // playlist identity).
        const shuffleBtn = `<button class="playlist-hero-btn playlist-hero-shuffle" type="button" title="Shuffle" aria-label="Shuffle"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/><polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/><line x1="4" y1="4" x2="9" y2="9"/></svg></button>`;
        const playNextBtn = `<button class="playlist-hero-btn playlist-hero-play-next" type="button" title="Play next" aria-label="Play next"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M4 5v14l11-7L4 5zm13 0v14h3V5h-3z"/></svg></button>`;
        const shareMuted = isCurated || isLikedPlaylist;
        const shareBtn = `<button class="playlist-hero-btn playlist-hero-share${shareMuted ? ' is-muted' : ''}" type="button" title="${shareMuted ? 'Sharing unavailable for this playlist' : 'Share'}" aria-label="${shareMuted ? 'Sharing unavailable for this playlist' : 'Share'}"${shareMuted ? ' aria-disabled="true"' : ''}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/></svg></button>`;
        // The "more" button uses the same id as the previous header copy so
        // the existing rename / delete wiring in this file (and the inline
        // listener below) attaches to the new button. The id is shared
        // intentionally — only one #playlist-detail-more-btn exists at a time.
        const moreBtn = `<button class="playlist-hero-btn playlist-hero-more${canEditPlaylist ? '' : ' is-muted'}" id="playlist-detail-more-btn" type="button" title="${canEditPlaylist ? 'More options' : 'Options unavailable for this playlist'}" aria-label="${canEditPlaylist ? 'More options' : 'Options unavailable for this playlist'}"${canEditPlaylist ? '' : ' aria-disabled="true"'}><svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg></button>`;
        // Build the right-hand action span based on playlist type:
        // - Library: Share + More (rename/delete menu)
        // - Public (other user): Share only — can't edit someone else's playlist
        // - Curated (YT Music): empty — read-only, no useful Share either
        // Conditional HTML (not CSS hiding) keeps the DOM clean: no
        // display:none buttons to focus via keyboard or click via dev tools.
        const actionsRightHtml = shareBtn + moreBtn;
        // Wrap secondary actions in the existing left/right flex spans so the
        // 1fr-auto-1fr grid stays valid (Play sits in the auto column).
        const actionsRow = `<div class="playlist-detail-hero-actions"><div class="playlist-hero-actions-left">${shuffleBtn}${playNextBtn}</div><button class="playlist-hero-play" type="button" aria-label="Play ${escapeHtml(title)}"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg></button><div class="playlist-hero-actions-right">${actionsRightHtml}</div></div>`;
        hero.innerHTML = `${collage}
          <div class="playlist-detail-hero-info">
            <h2 class="playlist-detail-page-title playlist-detail-hero-name">${escapeHtml(title)}</h2>
            ${pl.description ? `<div class="playlist-detail-hero-desc">${escapeHtml(pl.description)}</div>` : ''}
            <div class="playlist-detail-hero-meta">${escapeHtml(metaParts.join(' \u2022 '))}</div>
            ${tracks.length ? actionsRow : ''}
          </div>`;
        body.appendChild(hero);

        // A present-but-expired YouTube thumbnail should fall back exactly as
        // a missing thumbnail does, instead of leaving a broken image tile.
        const primaryCover = hero.querySelector('[data-playlist-primary-cover]');
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
          const appendTracks = (batch, startIndex) => {
            batch.forEach((track, index) => {
            const wrapper = document.createElement('div');
            wrapper.className = 'result-swipe-wrapper';
            const row = document.createElement('div');
            row.className = 'history-item';
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
              <div class="playlist-track-num">${startIndex + index + 1}</div>
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
              if (window.playResult) {
                window.playResult({
                  video_id: videoId,
                  title: track.title,
                  artist,
                  thumbnail
                }, false, false, true);
              }
            };
            const titleNode = row.querySelector('.queue-title');
            if (titleNode) titleNode.addEventListener('click', event => {
              event.preventDefault();
              event.stopPropagation();
              openResolvedAlbum(contextTrack);
            });
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
            wrapper.appendChild(row);
            list.appendChild(wrapper);
            });
            if (window.syncTrackPlaybackIndicators) window.syncTrackPlaybackIndicators();
          };
          appendTracks(tracks, 0);
          observeLazyImages(list);
          if (pl.has_more) {
            const loading = document.createElement('div');
            loading.className = 'playlist-loading-indicator';
            loading.innerHTML = '<span class="playlist-loading-spinner" aria-hidden="true"></span><span>Loading more songs…</span>';
            list.appendChild(loading);
            let nextOffset = Number(pl.next_offset) || tracks.length;
            let loadingTracks = false;
            const loadingObserver = new IntersectionObserver(function (entries) {
              entries.forEach(async function (entry) {
                if (!entry.isIntersecting || loadingTracks) return;
                loadingTracks = true;
                loading.classList.add('visible');
                try {
                  const page = await window.api('/api/library/playlists/' + encodeURIComponent(plId) + '?offset=' + nextOffset + '&limit=30');
                  const batch = page.tracks || [];
                  appendTracks(batch, nextOffset);
                  observeLazyImages(list);
                  nextOffset = Number(page.next_offset) || (nextOffset + batch.length);
                  if (!page.has_more || !batch.length) {
                    loadingObserver.disconnect();
                    loading.remove();
                  }
                } catch (e) {
                  if (window.toast) window.toast('Could not load more songs', 'error');
                } finally {
                  loading.classList.remove('visible');
                  loadingTracks = false;
                }
              });
            }, { root: list.closest('.history-modal-body'), rootMargin: '160px 0px' });
            loadingObserver.observe(loading);
          }
          const heroPlay = hero.querySelector('.playlist-hero-play');
          if (heroPlay) heroPlay.addEventListener('click', () => list.querySelector('.history-item')?.click());
          // Shuffle plays the same first track but with the device's shuffle
          // mode flipped on (the same path the playbar Shuffle button uses).
          const heroShuffle = hero.querySelector('.playlist-hero-shuffle');
          if (heroShuffle) heroShuffle.addEventListener('click', async () => {
            const firstRow = list.querySelector('.history-item');
            if (!firstRow) return;
            try {
              const sb = document.getElementById('shuffle-btn');
              if (sb && window.api && !sb.classList.contains('shuffle-active')) {
                await window.api('/alexa/shuffle_queue/', {});
                sb.classList.add('shuffle-active');
              }
            } catch (e) { /* best-effort: still play the row even if shuffle toggle fails */ }
            firstRow.click();
          });
          // Play next: queues the first track of the playlist at the
          // "next" slot (right after the currently-playing track). Reuses
          // the existing addToQueue helper from queue.js, which already
          // handles busy-locking and toasting.
          const heroPlayNext = hero.querySelector('.playlist-hero-play-next');
          if (heroPlayNext) heroPlayNext.addEventListener('click', () => {
            const firstWrapper = list.querySelector('.result-swipe-wrapper');
            const track = firstWrapper && firstWrapper._songContextTrack;
            if (track && window.addToQueue) window.addToQueue(track, 'next');
          });
          // More: the rename / delete menu moved into the actions area so
          // it's reachable without scrolling back to the page header. This
          // wires the new button (id="playlist-detail-more-btn") to the
          // existing #playlist-detail-more-menu. The legacy IIFE below
          // also looks up the same id — both listeners point at the same
          // single button now, but they do different work (positioning vs
          // outside-click close), so we keep both.
          const heroMore = hero.querySelector('.playlist-hero-more');
          if (heroMore) {
            heroMore.addEventListener('click', function (e) {
              e.stopPropagation();
              e.preventDefault();
              if (!canEditPlaylist) return;
              const menu = document.getElementById('playlist-detail-more-menu');
              if (!menu) return;
              if (menu.classList.contains('open')) {
                menu.classList.remove('open');
                return;
              }
              if (window._closeAllMoreMenus) window._closeAllMoreMenus();
              const rect = heroMore.getBoundingClientRect();
              menu.style.top = (rect.bottom + 4) + 'px';
              menu.style.right = (window.innerWidth - rect.right) + 'px';
              menu.style.left = 'auto';
              menu.classList.add('open');
            });
          }
          // Share copies the current page URL to the clipboard. The detail
          // view is a routed page (#playlist/<id>), so the URL is the share
          // link. Falls back to a textarea trick on browsers without async
          // clipboard API support.
          const heroShare = hero.querySelector('.playlist-hero-share');
          if (heroShare) heroShare.addEventListener('click', async () => {
            if (shareMuted) return;
            const url = window.location.href;
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
      console.warn('Failed to load playlist', e);
      if (titleEl) titleEl.textContent = 'Error loading playlist';
      if (body) body.innerHTML = '<div style="padding:24px; color:var(--muted); text-align:center;">Failed to load playlist</div>';
      if (window._barAbort) window._barAbort();
    }
    if (ownsProgress && window._barComplete) window._barComplete();
  }
  window.openPlaylistDetailModal = openLibraryPlaylist;
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
          // Close the playlist detail modal and go back
          var overlay = document.getElementById('playlist-detail-modal-overlay');
          if (overlay) overlay.classList.remove('open');
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
