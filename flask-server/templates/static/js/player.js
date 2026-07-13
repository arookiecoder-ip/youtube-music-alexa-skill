(function () {
  'use strict';
  const playerTrace = (event, details) => window.__playerDebugLog && window.__playerDebugLog(event, details);
  function escHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  const state = window.__appState = window.__appState || {};
  if (state.isPlaying === undefined) state.isPlaying = false;
  if (state.lastActionAt === undefined) state.lastActionAt = 0;
  if (state.lastActionIntent === undefined) state.lastActionIntent = null;
  if (state._lastPlayAttemptVideoId === undefined) state._lastPlayAttemptVideoId = '';
  if (state.GRACE_MS === undefined) state.GRACE_MS = 8000;
  if (state._currentVideoId === undefined) state._currentVideoId = '';
  if (state._currentThumbnail === undefined) state._currentThumbnail = '';
  if (state._hasTrack === undefined) state._hasTrack = false;
  if (state._resultsOpen === undefined) state._resultsOpen = false;
  if (state._searchResults === undefined) state._searchResults = [];
  if (state._searchSeq === undefined) state._searchSeq = 0;
  if (state._lastQueueJson === undefined) state._lastQueueJson = '';
  if (state._lastQueueIndex === undefined) state._lastQueueIndex = -1;
  if (state.volumeUserActive === undefined) state.volumeUserActive = false;
  if (state.volumeGraceUntil === undefined) state.volumeGraceUntil = 0;
  if (state.VOLUME_GRACE_MS === undefined) state.VOLUME_GRACE_MS = 4000;
  if (state._volCommandSeq === undefined) state._volCommandSeq = 0;

const deviceEl = document.getElementById('device');
const volumeEl = document.getElementById('volume');

function syncTrackPlaybackIndicators() {
  const currentId = state._currentVideoId || '';
  for (const card of document.querySelectorAll(
    '.home-item[data-video-id], .result-swipe-wrapper[data-video-id]'
  )) {
    const isCurrent = !!currentId && card.dataset.videoId === currentId;
    card.classList.toggle('current-track', isCurrent);
    card.classList.toggle('playing', isCurrent && state.isPlaying);
  }
}

function syncPlayPause() {
  for (const btn of [document.getElementById('pp-btn'), document.getElementById('mini-pp'), document.getElementById('mp-pp-btn'), document.getElementById('np-page-art-overlay')]) {
    if (!btn) continue;
    const p = btn.querySelector('.icon-play');
    if (p) p.style.display = state.isPlaying ? 'none' : '';
    const pa = btn.querySelector('.icon-pause');
    if (pa) pa.style.display = state.isPlaying ? '' : 'none';
    btn.title = state.isPlaying ? 'Pause' : 'Play';
  }
  syncTrackPlaybackIndicators();
  if (window.updateQueuePlaying) window.updateQueuePlaying(state.isPlaying);
}

/* ---- now-playing display (single element, no dual placeholder bug) ---- */
// Last-rendered track fingerprint — used to skip redundant DOM writes.
let _lastNpFingerprint = '';
// Keep the sharp foreground artwork and the first ambient-preview artwork
// separately. A repeated now-playing update must not replay the blur simply
// because the server sent the original small thumbnail again.
const _resolvedNowPlayingArt = new Map();
const _ambientNowPlayingArt = new Map();

function upgradeLowResNowPlayingArt(info, fingerprint, artwork, npPageArt) {
  if (!info.video_id || typeof window.api !== 'function') return Promise.resolve(false);

  return window.api('/api/track/' + encodeURIComponent(info.video_id) + '/artwork')
    .then((result) => {
      const highResUrl = result && result.thumbnail;
      if (!highResUrl || highResUrl === info.thumbnail || _lastNpFingerprint !== fingerprint) return false;

      return new Promise((resolve) => {
        const highResImage = new Image();
        highResImage.onload = () => {
          if (_lastNpFingerprint !== fingerprint) return resolve(false);
          const url = 'url(' + highResUrl + ')';
          artwork.forEach((el) => {
            el.style.backgroundImage = url;
            el.classList.remove('image-loading');
          });
          if (npPageArt) {
            const npPage = npPageArt.closest('.np-page');
            // The page backdrop deliberately stays on the first preview that
            // arrived for this track. Upgrading only the foreground artwork
            // avoids a distracting full-screen background change mid-play.
            npPage.classList.remove('image-loading');
          }
          _resolvedNowPlayingArt.set(info.video_id, highResUrl);
          state._currentThumbnail = highResUrl;
          if (state._currentTrack) state._currentTrack.thumbnail = highResUrl;
          resolve(true);
        };
        highResImage.onerror = () => resolve(false);
        highResImage.src = highResUrl;
      });
    })
    .catch(() => false);
}

function showNowPlaying(info) {
  const np = document.getElementById('now-playing');
  const miniArt = document.getElementById('mini-art');
  if (!info || (!info.title && !info.video_id)) {
    // Only update if we had a track before.
    if (state._hasTrack || _lastNpFingerprint) {
      np.classList.add('visible');
      document.getElementById('np-title').textContent = 'Nothing is playing';
      document.getElementById('np-artist').textContent = '';
      document.getElementById('mini-title').textContent = 'Nothing is playing';
      miniArt.style.backgroundImage = '';
      miniArt.classList.remove('has-thumb', 'image-loading');
      // Legacy mobile popup has been retired. Keep this null-safe for Jam's
      // deliberately small shell and for stale cached markup during upgrades.
      const mpTitle = document.getElementById('mp-np-title');
      const mpArtist = document.getElementById('mp-np-artist');
      if (mpTitle) mpTitle.textContent = 'Nothing is playing';
      if (mpArtist) mpArtist.textContent = '';
      const mpArt = document.getElementById('mp-np-art');
      mpArt.style.backgroundImage = '';
      mpArt.classList.remove('has-thumb', 'image-loading');
      const art = document.getElementById('np-art');
      if (art) {
        art.style.backgroundImage = '';
        art.classList.remove('has-thumb', 'image-loading');
      }
      // Clear now-playing-section elements
      const npPageArt = document.getElementById('np-page-art');
      if (npPageArt) {
        npPageArt.style.backgroundImage = '';
        npPageArt.classList.remove('has-thumb', 'image-loading');
        const npPage = npPageArt.closest('.np-page');
        npPage.style.removeProperty('--np-cover');
        npPage.classList.remove('image-loading');
        document.body.style.removeProperty('--np-cover');
      }
      const npPageTitle = document.getElementById('np-page-title');
      if (npPageTitle) npPageTitle.textContent = 'Nothing is playing';
      const npPageArtist = document.getElementById('np-page-artist');
      if (npPageArtist) npPageArtist.textContent = '';
      state._hasTrack = false;
      state._currentVideoId = '';
      state._currentThumbnail = '';
      state._currentTrack = null;
      syncTrackPlaybackIndicators();
      _lastNpFingerprint = '';
      // Playback is gone — don't leave an empty expanded player on screen.
      if (window.getRoute && window.getRoute() === '#now-playing') {
        window.closeNowPlayingOverlay();
      }
    }
    return;
  }
  // Fingerprint: skip all DOM work when nothing changed.
  const fp = (info.video_id || '') + '|' + info.title + '|' + (info.artist || '') + '|' + (info.thumbnail || '');
  const changed = fp !== _lastNpFingerprint;
  if (changed) {
    _lastNpFingerprint = fp;
    np.classList.add('visible');
    document.getElementById('np-title').textContent = info.title;
    document.getElementById('np-artist').innerHTML = window.artistLinksHtml(info.artist, info.channelId);
    document.getElementById('mini-title').textContent = info.title;
    const mpTitle = document.getElementById('mp-np-title');
    const mpArtist = document.getElementById('mp-np-artist');
    if (mpTitle) mpTitle.textContent = info.title;
    if (mpArtist) mpArtist.innerHTML = window.artistLinksHtml(info.artist, info.channelId);
    const art = document.getElementById('np-art');
    const mpArt = document.getElementById('mp-np-art');
    const npPageArt = document.getElementById('np-page-art');
    const npPageTitle = document.getElementById('np-page-title');
    const npPageArtist = document.getElementById('np-page-artist');
    if (npPageTitle) npPageTitle.textContent = info.title;
    if (npPageArtist) {
      npPageArtist.innerHTML = window.artistLinksHtml(info.artist, info.channelId);
    }
    if (info.thumbnail) {
      const cachedHighRes = info.video_id && _resolvedNowPlayingArt.get(info.video_id);
      const displayThumbnail = cachedHighRes || info.thumbnail;
      if (info.video_id && !_ambientNowPlayingArt.has(info.video_id)) {
        _ambientNowPlayingArt.set(info.video_id, info.thumbnail);
      }
      const ambientThumbnail = (info.video_id && _ambientNowPlayingArt.get(info.video_id)) || info.thumbnail;
      const url = 'url(' + displayThumbnail + ')';
      const ambientUrl = 'url(' + ambientThumbnail + ')';
      const artwork = [art, miniArt, mpArt, npPageArt].filter(Boolean);
      // Compact player artwork must stay sharp. Only the large full-player
      // cover uses the soft preview while its HD replacement is fetched.
      artwork.forEach((el) => {
        el.style.backgroundImage = url;
        el.classList.remove('image-loading');
        el.classList.add('has-thumb');
      });
      if (npPageArt) {
        const npPage = npPageArt.closest('.np-page');
        npPageArt.classList.toggle('image-loading', !cachedHighRes);
        npPage.style.setProperty('--np-cover', ambientUrl);
        npPage.classList.toggle('image-loading', !cachedHighRes);
        document.body.style.setProperty('--np-cover', ambientUrl);
      }
      // The HD image was decoded during an earlier playback update. It is
      // already safe to paint sharply, so do not briefly blur it again.
      if (!cachedHighRes) {
      const img = new Image();
      img.onload = () => {
        if (_lastNpFingerprint !== fp) return;
        // Small shelf thumbnails look hazy when enlarged in the player. Keep
        // the preview blurred while the server resolves the track's best art.
        const isLowResolution = img.naturalWidth < 640 || img.naturalHeight < 640;
        if (!isLowResolution) {
          if (info.video_id) _resolvedNowPlayingArt.set(info.video_id, info.thumbnail);
          artwork.forEach((el) => el.classList.remove('image-loading'));
          if (npPageArt) npPageArt.closest('.np-page').classList.remove('image-loading');
          return;
        }
        upgradeLowResNowPlayingArt(info, fp, artwork, npPageArt)
          .then((upgraded) => {
            if (!upgraded && _lastNpFingerprint === fp) {
              artwork.forEach((el) => el.classList.remove('image-loading'));
              if (npPageArt) npPageArt.closest('.np-page').classList.remove('image-loading');
            }
          });
      };
      img.onerror = () => {
        if (_lastNpFingerprint === fp) {
          artwork.forEach((el) => el.classList.remove('image-loading'));
          if (npPageArt) npPageArt.closest('.np-page').classList.remove('image-loading');
        }
      };
      img.src = info.thumbnail;
      }
    } else {
      art.style.backgroundImage = '';
      art.classList.remove('has-thumb', 'image-loading');
      miniArt.style.backgroundImage = '';
      miniArt.classList.remove('has-thumb', 'image-loading');
      mpArt.style.backgroundImage = '';
      mpArt.classList.remove('has-thumb', 'image-loading');
      if (npPageArt) {
        npPageArt.style.backgroundImage = '';
        npPageArt.classList.remove('has-thumb', 'image-loading');
        const npPage = npPageArt.closest('.np-page');
        npPage.style.removeProperty('--np-cover');
        npPage.classList.remove('image-loading');
        document.body.style.removeProperty('--np-cover');
      }
    }
    // Track video_id for the URL button. Clear it when the new track's id is
    // unknown (optimistic plain-text play) so the "Open on YouTube Music"
    // link never keeps pointing at the previous song.
    state._currentVideoId = info.video_id || '';
    state._currentThumbnail = (info.video_id && _resolvedNowPlayingArt.get(info.video_id)) || info.thumbnail || '';
    state._currentTrack = {
      video_id: info.video_id || '', title: info.title || '', artist: info.artist || '',
      thumbnail: state._currentThumbnail, channelId: info.channelId || '',
      artist_id: info.artist_id || info.artistId || info.channelId || '',
      album_id: info.album_id || info.albumId || info.album_browse_id || ''
    };
    updateUrlBar();
    syncTrackPlaybackIndicators();
  }

  refreshNpLikeButton();

  const wasTrack = state._hasTrack;
  state._hasTrack = true;
  if (changed || !wasTrack) {
    syncUiState();
    updateResultsActive();
  }
}

function refreshNpLikeButton() {
  if (!state._currentVideoId || typeof _playlistsData === 'undefined' || !_playlistsData.liked_songs) return;
  const isLiked = _playlistsData.liked_songs.includes(state._currentVideoId);
  // Thumbs-up (like), filled when liked — playbar + now-playing page buttons
  const svg = isLiked
    ? `<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="1" stroke-linecap="round" stroke-linejoin="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>`
    : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>`;
  for (const id of ['np-like-btn', 'np-page-like-btn', 'np-menu-like']) {
    const btn = document.getElementById(id);
    if (!btn) continue;
    btn.classList.toggle('liked', isLiked);
    btn.title = isLiked ? 'Dislike' : 'Like';
    btn.innerHTML = id === 'np-menu-like'
      ? svg + `<span>${isLiked ? 'Dislike' : 'Like'}</span>`
      : svg;
  }
}

// Liked Songs changed somewhere else (voice "like this song", another open
// device): the SSE snapshot carries a liked_version counter; on change,
// re-fetch playlists so heart icons reflect the new state. null = baseline
// not yet seen (the first snapshot must not trigger a refetch).
let _lastLikedVersion = null;
function checkLikedVersion(np) {
  if (!np || np.liked_version === undefined) return;
  const first = _lastLikedVersion === null;
  if (np.liked_version === _lastLikedVersion) return;
  _lastLikedVersion = np.liked_version;
  if (first || window.JAM_GUEST || typeof loadLibrary !== 'function') return;
  loadLibrary().then(() => refreshNpLikeButton()).catch(() => {});
}

/* ---- Progress bar ----
   The server sends an anchor (position_ms at started_at, plus duration_ms); we
   tick locally for a smooth bar and only talk to the server when the user drags
   to seek. Opening the app partway through a song still shows the right spot
   because started_at is server truth, not a local guess. */
const progress = window.progress = (function () {
  const wrap = document.getElementById('progress');
  const track = document.getElementById('progress-track');
  const fill = document.getElementById('progress-fill');
  const handle = document.getElementById('progress-handle');
  const elapsedEl = document.getElementById('progress-elapsed');
  const totalEl = document.getElementById('progress-total');
  const barElapsedEl = document.getElementById('playbar-elapsed');
  const barTotalEl = document.getElementById('playbar-total');

  let durationMs = 0;
  let positionMs = 0;    // anchor position (ms into the track)
  let anchorClientMs = 0;// client Date.now() when positionMs was captured
  let lastServerAnchor = 0; // server started_at of the last update (change detector)
  let playing = false;
  let dragging = false;
  let dragMs = 0;        // previewed position while dragging
  let rafId = null;
  const FALLBACK_SEEK_MS = 5 * 60 * 1000;
  // While waiting for the device to actually start a freshly-requested track we
  // hold the bar at 0:00 (see resetPending). The local ticker must not run in
  // this window, otherwise the timer climbs against a track that hasn't started
  // and then visibly snaps back to 0 when the real PlaybackStarted anchor lands.
  let awaitingStart = false;
  // video_id of the track resetPending() was called for, when the caller
  // already knows it (next/prev, queue clicks, direct links). A confirmed
  // SSE/poll snapshot can only end awaitingStart if it's reporting on *this*
  // track — otherwise a stale "playback_confirmed" push that's still in-flight
  // for the *previous* (still-playing) track would prematurely clear
  // awaitingStart and let the bar tick against the old position for a moment,
  // before the real confirmation for the new track lands and snaps it to 0.
  // null = identity unknown (plain-text search, where the server picks the
  // track); then any confirmed snapshot is accepted. Titles can't be used as
  // a fallback identity: the optimistic title is the raw query text and the
  // server replaces it with the canonical track title before confirming, so
  // strict comparison would never match and the bar would stay stuck at 0:00.
  let pendingVideoId = null;
  let pendingSince = 0;      // when resetPending() started waiting
  // After a local drag-to-seek, snapshots generated before the server
  // processed the seek still carry the old position; re-anchoring to them
  // snaps the bar back to where it was before jumping to the seek target.
  // Until this deadline, only accept a server anchor that agrees with the
  // local (sought) position.
  let localSeekUntil = 0;
  let _inactivityTimer = null;
  const _INACTIVITY_TIMEOUT_MS = 30000;

  function resetInactivityTimer() {
    clearTimeout(_inactivityTimer);
    if (document.hidden) return;  // hidden already handled by syncLoop
    _inactivityTimer = setTimeout(() => {
      // No state change for 30s while visible — fully stop the loop.
      // RAF will auto-restart on next track state change via update().
      if (rafId != null) { clearTimeout(rafId); rafId = null; }
    }, _INACTIVITY_TIMEOUT_MS);
  }

  function fmt(ms) {
    if (!isFinite(ms) || ms < 0) ms = 0;
    const total = Math.floor(ms / 1000);
    const m = Math.floor(total / 60);
    const s = total % 60;
    return m + ':' + String(s).padStart(2, '0');
  }

  // Live position from the anchor. We measure elapsed time against the *client*
  // clock (anchorClientMs), so a client/server clock difference never skews the
  // bar — the server's position_ms is treated as "position as of when this
  // update arrived". While paused, the position is frozen.
  function livePosition() {
    if (dragging) return dragMs;
    // Waiting for the real track to start: pin at the anchor (0) and don't let
    // wall-clock time advance the timer.
    if (awaitingStart) return Math.max(0, positionMs);
    let p = positionMs;
    if (playing && anchorClientMs) {
      p += Date.now() - anchorClientMs;
    }
    if (durationMs) p = Math.min(p, durationMs);
    return Math.max(0, p);
  }

  function paint() {
    const pos = livePosition();
    const visualMax = durationMs || seekLimitMs();
    const pct = visualMax ? Math.min(100, (pos / visualMax) * 100) : 0;
    fill.style.width = pct + '%';
    handle.style.left = pct + '%';
    elapsedEl.textContent = fmt(pos);
    totalEl.textContent = durationMs ? fmt(durationMs) : '--:--';
    // Playbar timer (next to the transport buttons, desktop)
    if (barElapsedEl) barElapsedEl.textContent = fmt(pos);
    if (barTotalEl) barTotalEl.textContent = durationMs ? fmt(durationMs) : '--:--';
    track.setAttribute('aria-valuenow', String(Math.floor(pos / 1000)));
    track.setAttribute('aria-valuemax', String(Math.floor(visualMax / 1000)));
    // Mirror to mini popup progress
    const mpFill = document.getElementById('mp-progress-fill');
    const mpHandle = document.getElementById('mp-progress-handle');
    const mpElapsed = document.getElementById('mp-progress-elapsed');
    const mpTotal = document.getElementById('mp-progress-total');
    const miniFill = document.getElementById('mini-player-progress-fill');
    if (mpFill) {
      mpFill.style.width = pct + '%';
      mpHandle.style.left = pct + '%';
      mpElapsed.textContent = fmt(pos);
      mpTotal.textContent = durationMs ? fmt(durationMs) : '--:--';
    }
    if (miniFill) miniFill.style.width = pct + '%';
  }

  function loop() {
    paint();
    rafId = setTimeout(loop, 250);
  }
  function syncLoop() {
    const shouldRun = (playing || dragging) && !awaitingStart && !wrap.hidden && !document.hidden;
    if (shouldRun && rafId == null) rafId = setTimeout(loop, 250);
    else if (!shouldRun && rafId != null) { clearTimeout(rafId); rafId = null; }
  }

  // Fed from now-playing updates (SSE / poll).
  function update(np) {
    const hasTrack = !!(np && np.title);
    wrap.hidden = !hasTrack;
    if (!hasTrack) {
      durationMs = 0;
      positionMs = 0;
      anchorClientMs = 0;
      lastServerAnchor = 0;
      playing = false;
      paint();
      syncLoop();
      return;
    }
    // While waiting on a specific track, a snapshot that's still describing a
    // *different* one (a stale push still in flight for the track that was
    // playing before this one) must not leak its duration into the display —
    // otherwise the total-time label flashes the old song's length for a
    // moment before the real confirmation for the new track arrives.
    //
    // When the pending track's id is unknown (plain-text search — the server
    // picks the track), identity can't be compared, so use position instead:
    // a freshly started track confirms near 0:00, while a stale push for the
    // previous track carries its old mid-song position. Without this, that
    // stale push briefly flashes the old song's position/duration on the bar
    // before the real confirmation snaps it back to 0. The 15s escape hatch
    // accepts anything after a long wait so the bar can never wedge at 0:00.
    const serverPosNow = Number(np.position_ms) || 0;
    const matchesPending =
      (pendingVideoId ? np.video_id === pendingVideoId : serverPosNow < 10000) ||
      (awaitingStart && pendingSince && Date.now() - pendingSince > 15000);
    if (!awaitingStart || matchesPending) {
      durationMs = Number(np.duration_ms) || 0;
    }
    // Duration can arrive before the Echo actually starts fetching audio. Only
    // a proxy fetch / PlaybackStarted webhook confirms that the timer may tick
    // -- and only if that confirmation is actually for the track we're
    // waiting on, not a stale push for the previous track.
    if (awaitingStart && np.playback_confirmed && matchesPending) {
      awaitingStart = false;
      pendingVideoId = null;
      pendingSince = 0;
      // Force-anchor to the server's position right now to avoid using a stale
      // anchor from pre-playback SSE updates.
      positionMs = Number(np.position_ms) || 0;
      anchorClientMs = Date.now();
      lastServerAnchor = Number(np.started_at) || 0;
    }
    // Re-anchor to the server's reported position when it sends a new anchor
    // (started_at changes on every snapshot / seek / pause). Anchor against the
    // client clock so there's no skew. Skip mid-drag so we don't fight the user,
    // and skip while still awaiting the real start so we don't snap to a stale
    // server position for the previous track.
    const serverAnchor = Number(np.started_at) || 0;
    if (!dragging && !awaitingStart && (serverAnchor !== lastServerAnchor || !anchorClientMs)) {
      const serverPos = Number(np.position_ms) || 0;
      if (Date.now() < localSeekUntil && Math.abs(serverPos - livePosition()) > 3000) {
        // Stale pre-seek snapshot: consume the anchor but keep our position,
        // so the next (fresher) anchor still triggers a re-check.
        lastServerAnchor = serverAnchor;
      } else {
        localSeekUntil = 0;
        lastServerAnchor = serverAnchor;
        positionMs = serverPos;
        anchorClientMs = Date.now();
      }
    }
    if (typeof np.playing === 'boolean' && !awaitingStart) {
      const reported = np.playing && !!np.playback_confirmed;
      // Mirror the top-level grace guard: a snapshot that contradicts the
      // user's just-clicked play/pause intent can be a stale confirmation for
      // the *start* of playback (already in flight when pause was clicked) --
      // accepting it would resume the ticking bar right after the user paused.
      const inGrace = (Date.now() - state.lastActionAt) < state.GRACE_MS;
      const contradictsIntent = inGrace && state.lastActionIntent !== null && reported !== state.lastActionIntent;
      if (!contradictsIntent) playing = reported;
    }
    syncLoop();
    paint();
    resetInactivityTimer();
  }

  // Called on an optimistic play (GO button / queue click): show the bar reset
  // to 0:00 with no duration yet, but DON'T tick — hold until the server confirms
  // the track actually started. This kills the phantom pre-start timer that used
  // to climb and then snap back to 0.
  //
  // videoId identifies the track being requested, so a confirmed snapshot
  // that's still describing the *previous* track (a stale SSE push already in
  // flight, or a slow poll response) can't prematurely end the wait — only a
  // snapshot for this specific track can. Pass it when the caller already
  // resolved it (next/prev, queue clicks, direct links); omit it for
  // plain-text searches, where the server picks the track and any confirmed
  // snapshot is accepted.
  function resetPending(videoId) {
    awaitingStart = true;
    pendingVideoId = videoId || null;
    pendingSince = Date.now();
    localSeekUntil = 0;
    durationMs = 0;
    positionMs = 0;
    anchorClientMs = Date.now();
    lastServerAnchor = 0;
    playing = false;
    wrap.hidden = false;
    syncLoop();
    paint();
  }
  // ---- drag to seek (fires on release) ----
  function seekLimitMs() {
    return durationMs || Math.max(FALLBACK_SEEK_MS, livePosition() + 60 * 1000);
  }
  let _activeTrack = track; // which progress-track is being dragged
  function posFromEvent(e) {
    const rect = _activeTrack.getBoundingClientRect();
    const x = (e.touches ? e.touches[0].clientX : e.clientX) - rect.left;
    const frac = Math.min(1, Math.max(0, x / rect.width));
    return frac;
  }
  function updateTooltip(e, container) {
    const tooltip = container.querySelector('.progress-tooltip');
    if (!tooltip) return;
    const trackEl = container.classList.contains('progress-track') ? container : container.querySelector('.progress-track');
    const rect = trackEl.getBoundingClientRect();
    const xClamped = Math.max(0, Math.min(rect.width, (e.touches ? e.touches[0].clientX : e.clientX) - rect.left));
    const frac = xClamped / rect.width;
    const ms = frac * seekLimitMs();
    tooltip.style.left = xClamped + 'px';
    tooltip.textContent = fmt(Math.round(ms));
  }
  function handleTooltipMove(e) {
    updateTooltip(e, e.currentTarget);
  }
  function beginDrag(e) {
    if (awaitingStart) return;
    // Track which progress-track was touched
    _activeTrack = e.currentTarget;
    dragging = true;
    _activeTrack.classList.add('dragging');
    dragMs = posFromEvent(e) * seekLimitMs();
    paint();
    updateTooltip(e, _activeTrack);
    e.preventDefault();
  }
  function moveDrag(e) {
    if (!dragging) return;
    dragMs = posFromEvent(e) * seekLimitMs();
    paint();
    updateTooltip(e, _activeTrack);
    e.preventDefault();
  }
  async function endDrag(e) {
    if (!dragging) return;
    dragging = false;
    _activeTrack.classList.remove('dragging');
    const target = Math.round(dragMs);
    // Hold the bar at the target and do NOT tick yet -- the device hasn't
    // actually moved to this position until the seek round-trips and the
    // real PlaybackStarted webhook confirms it. Ticking immediately (as if
    // already playing from `target`) made the bar visibly race ahead of the
    // real audio, then snap back to `target` a beat later when the genuine
    // confirmation landed. Mirrors the server, which also drops
    // playing/playback_confirmed to false for the same window (see
    // alexa_seek). syncLoop() naturally stops the RAF loop since `playing`
    // is now false, so livePosition() just returns the frozen target.
    positionMs = target;
    anchorClientMs = Date.now();
    playing = false;
    syncLoop();
    paint();
    const serial = deviceEl.value;
    if (!serial) { toast('Pick a device first.', 'error'); return; }
    state.lastActionAt = Date.now();
    localSeekUntil = Date.now() + 8000;
    toast('Seeking to ' + fmt(target) + '\u2026');
    try {
      const res = await api('/alexa/seek/', { serial, position_ms: target });
      // paused: the server only moved the frozen anchor (no playback dispatch);
      // the track stays paused and resume will pick up from here.
      toast(res && res.paused
        ? 'Paused at ' + fmt(target) + ' — press play to resume here'
        : 'Seeked to ' + fmt(target), 'ok');
    } catch (err) {
      // Seek failed: drop the hold so the next server push restores truth.
      localSeekUntil = 0;
      toast(err.message, 'error');
    }
  }

  if (track) {
    track.addEventListener('mousedown', beginDrag);
    track.addEventListener('touchstart', beginDrag, { passive: false });
  }
  if (wrap) {
    wrap.addEventListener('mousemove', handleTooltipMove);
    wrap.addEventListener('touchmove', handleTooltipMove, { passive: true });
  }
  // Also bind the mini popup progress track
  const mpTrack = document.getElementById('mp-progress-track');
  const mpWrap = document.getElementById('mp-progress');
  if (mpTrack) {
    mpTrack.addEventListener('mousedown', beginDrag);
    mpTrack.addEventListener('touchstart', beginDrag, { passive: false });
  }
  if (mpWrap) {
    mpWrap.addEventListener('mousemove', handleTooltipMove);
    mpWrap.addEventListener('touchmove', handleTooltipMove, { passive: true });
  }
  // Global move/end handlers work for both tracks
  window.addEventListener('mousemove', moveDrag);
  window.addEventListener('mouseup', endDrag);
  window.addEventListener('touchmove', moveDrag, { passive: false });
  window.addEventListener('touchend', endDrag);
  // Keyboard: arrow keys nudge +/- 5s. If duration is unknown, use a temporary
  // seek window so the scrubber still works while metadata catches up.
  if (track) {
    track.addEventListener('keydown', (e) => {
      let delta = 0;
      if (e.key === 'ArrowRight') delta = 5000;
      else if (e.key === 'ArrowLeft') delta = -5000;
      else return;
      e.preventDefault();
      const target = Math.min(seekLimitMs(), Math.max(0, Math.round(livePosition()) + delta));
      dragMs = target;
      dragging = true;
      endDrag();
    });
  }

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      // Tab became visible: restart loop + reset inactivity timer
      syncLoop();
      resetInactivityTimer();
    } else {
      clearTimeout(_inactivityTimer);
    }
  });

  return { update, resetPending, livePosition, getDuration: () => durationMs, syncLoop };
})();

/* ---- API ---- */
// Shared 401 handling for api/apiDelete/apiPatch. For a jam guest a 401
// usually means the host ended the jam (or it expired) — but it can also be
// an endpoint guests simply aren't allowed to hit. Probe an always-allowed
// endpoint to tell them apart: only a genuinely dead jam gets the
// full-screen ended state; a mere permission refusal gets a toast.

async function playResult(item, suppressRadio, forceRadio, openPlaybackPage) {
  const serial = selectedSerial();
  if (!serial) return;
  state.lastActionAt = Date.now();
  toast(forceRadio
    ? 'Starting radio from \u201c' + item.title + '\u201d\u2026'
    : 'Playing \u201c' + item.title + '\u201d\u2026');
  try {
    await api('/alexa/play_queue/', {
      serial,
      video_id: item.video_id,
      title: item.title,
      artist: item.artist,
      thumbnail: item.thumbnail,
      duration_ms: item.duration_ms,
      suppress_radio: !!suppressRadio,
      // "Play Radio" on a track already in the current queue: force a fresh
      // queue seeded from just this track instead of silently reusing the
      // existing one (see alexa_play_queue's force_radio handling).
      force_radio: !!forceRadio,
    });
    state._lastPlayAttemptVideoId = item.video_id;
    showNowPlaying(item);
    progress.resetPending(item.video_id);
    state.isPlaying = true;
    state.lastActionIntent = true;
    syncPlayPause();
    toast(forceRadio ? 'Radio started' : 'Playing', 'ok');
    // Only search-result plays opt into opening the expanded playback page.
    if (openPlaybackPage && window.matchMedia('(min-width: 900px)').matches) window.navigateTo('#now-playing');
    state._lastQueueJson = '';
    schedulePollNowPlaying(3000);
  } catch (e) {
    toast(e.message, 'error');
  }
}

/* Lock page scroll while any bottom-sheet overlay is open. Re-checked on
   every open/close so stacked sheets (now-playing popup + queue modal on top)
   keep the lock until the last one closes. */
function syncModalScrollLock() {
  const anyOpen = ['mini-popup-overlay', 'queue-modal-overlay'].some((id) => {
    const el = document.getElementById(id);
    return el && el.classList.contains('open');
  });
  document.body.classList.toggle('modal-open', anyOpen);
}

/* ---- mini player controls & popup ---- */
(function () {
  const miniPlayer = document.getElementById('mini-player');
  const overlay = document.getElementById('mini-popup-overlay');
  const popup = document.getElementById('mini-popup');
  const dragArea = document.getElementById('mini-popup-drag');
  const closeBtn = document.getElementById('mini-popup-close');
  const queueBtn = document.getElementById('mini-popup-queue-btn');
  const mpPpBtn = document.getElementById('mp-pp-btn');
  const mpPrev = document.getElementById('mp-prev');
  const mpNext = document.getElementById('mp-next');
  const mpShuffleBtn = document.getElementById('mp-shuffle-btn');
  const mpVolume = document.getElementById('mp-volume');
  let _miniPopupOpen = false;

  function openMiniPopup(fromRoute) {
    playerTrace('mini:open-request', { fromRoute: !!fromRoute, desktop: window.matchMedia('(min-width: 900px)').matches });
    // Nothing playing: never open — but if the view is somehow up (e.g. the
    // queue ended while expanded), the toggle must still be able to close it.
    if (!state._hasTrack) {
      if (fromRoute || (window.getRoute && window.getRoute() === '#now-playing')) {
        if (window.navigateTo) window.navigateTo('#home');
      }
      return;
    }
    // One expanded-player path at every viewport. The legacy bottom sheet is
    // retained in cached templates for compatibility, but is never opened.
    if (window.getRoute && window.getRoute() === '#now-playing') {
      if (!fromRoute && window.closeNowPlayingOverlay) window.closeNowPlayingOverlay();
    } else if (window.navigateTo) {
      window.navigateTo('#now-playing');
    }
    return;
    // On desktop, toggle the #now-playing overlay: expand, or collapse back
    // to the view it was opened from.
    if (window.matchMedia('(min-width: 900px)').matches) {
      if (window.getRoute && window.getRoute() === '#now-playing') {
        playerTrace('mini:desktop-close-full');
        window.closeNowPlayingOverlay(); // Collapse
      } else {
        playerTrace('mini:desktop-open-full');
        window.navigateTo('#now-playing'); // Expand
      }
      return;
    }
    // Mobile: open the bottom sheet popup
    if (_miniPopupOpen) return;
    _miniPopupOpen = true;
    // Sync volume from main slider
    mpVolume.value = volumeEl.value;
    // Sync shuffle state
    const mainShuffle = document.getElementById('shuffle-btn');
    mpShuffleBtn.classList.toggle('shuffle-active', mainShuffle.classList.contains('shuffle-active'));
    overlay.classList.add('open');
    syncModalScrollLock();
    if (window.matchMedia('(min-width: 900px)').matches) {
      if (!state._queueOpen) {
        const queueToggle = document.getElementById('queue-toggle-btn');
        if (queueToggle) queueToggle.click();
      } else if (window.showQueue) {
        let queue = [];
        try { queue = JSON.parse(state._lastQueueJson || '[]'); } catch (_) {}
        window.showQueue(queue, state._lastQueueIndex || 0);
      }
    }
  }

  function closeMiniPopup() {
    playerTrace('mini:close-popup');
    if (!_miniPopupOpen) return;
    _miniPopupOpen = false;
    overlay.classList.remove('open');
    syncModalScrollLock();
    if (window.getRoute && window.getRoute() === '#now-playing' && window.navigateTo) {
      window.navigateTo('#home');
    }
  }

  // Tap on mini player: play/pause button stays functional, everything else opens popup
  miniPlayer.addEventListener('click', (e) => {
    if (e.target.closest('.mini-pp')) {
      // Proxy to the main play/pause
      document.getElementById('pp-btn').click();
      return;
    }
    openMiniPopup();
  });

  // Compact playbar: the expand toggle, the track-info cluster, and any empty
  // area of the player bar all open the full now-playing sheet.
  // Buttons/links inside keep their own behavior.
  const expandBtn = document.getElementById('player-expand-btn');
  if (expandBtn) expandBtn.addEventListener('click', openMiniPopup);
  const npCluster = document.getElementById('now-playing');
  if (npCluster) {
    npCluster.addEventListener('click', (e) => {
      if (e.target.closest('button, a, .artist-name')) return;
      // The cluster lives inside .player-section, which also has an expand
      // handler. Do not let one click toggle the route twice (open, then
      // immediately close), which appears as a full-screen blink.
      e.stopPropagation();
      openMiniPopup();
    });
  }
  // Clicking any empty area of the player bar (transport row, progress strip, etc.)
  // also opens the now-playing sheet, so the user can tap anywhere on the bar.
  const playerBar = document.querySelector('.player-section');
  if (playerBar) {
    playerBar.addEventListener('click', (e) => {
      // Let interactive elements (buttons, links, inputs, range sliders,
      // artist-name spans) keep their own click.
      if (e.target.closest('button, a, input, [role="slider"], .progress-track, .artist-name')) return;
      openMiniPopup();
    });
  }

  // Close popup
  closeBtn.addEventListener('click', closeMiniPopup);
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) closeMiniPopup();
  });

  // Transport: play/pause, prev, next
  mpPpBtn.addEventListener('click', () => {
    document.getElementById('pp-btn').click();
  });
  mpPrev.addEventListener('click', () => {
    const mainPrev = document.querySelector('[data-action="previous"]');
    if (mainPrev) mainPrev.click();
  });
  mpNext.addEventListener('click', () => {
    const mainNext = document.querySelector('[data-action="next"]');
    if (mainNext) mainNext.click();
  });

  // Shuffle
  mpShuffleBtn.addEventListener('click', () => {
    document.getElementById('shuffle-btn').click();
    // Sync active state after a tick (the main handler toggles it)
    setTimeout(() => {
      const mainShuffle = document.getElementById('shuffle-btn');
      mpShuffleBtn.classList.toggle('shuffle-active', mainShuffle.classList.contains('shuffle-active'));
    }, 50);
  });

  // Volume
  let mpVolTimer;
  mpVolume.addEventListener('pointerdown', () => { state.volumeUserActive = true; });
  mpVolume.addEventListener('pointerup', () => { state.volumeUserActive = false; });
  mpVolume.addEventListener('touchend', () => { state.volumeUserActive = false; });
  mpVolume.addEventListener('change', () => { state.volumeUserActive = false; });
  mpVolume.oninput = (e) => {
    state.volumeUserActive = true;
    state.volumeGraceUntil = Date.now() + state.VOLUME_GRACE_MS;
    // Sync main slider visually
    volumeEl.value = e.target.value;
    clearTimeout(mpVolTimer);
    // Same fix as the main slider's handler: a paused/resumed drag (touch
    // input can fire in bursts) lets more than one debounced call survive,
    // each sending its own real volume command whose confirmations can then
    // arrive out of order. Only the most recent command's result may act.
    const mySeq = ++state._volCommandSeq;
    mpVolTimer = setTimeout(() => {
      const serial = selectedSerial();
      if (!serial) { state.volumeUserActive = false; state.volumeGraceUntil = 0; return; }
      const value = +e.target.value;
      state.volumeGraceUntil = Date.now() + state.VOLUME_GRACE_MS;
      toast('Volume ' + value + '\u2026');
      api('/alexa/command/', { serial, action: 'volume', value })
        .then(() => {
          if (mySeq !== state._volCommandSeq) return;
          state.volumeUserActive = false;
          state.volumeGraceUntil = Date.now() + state.VOLUME_GRACE_MS;
          syncVolume(value, true);
          toast('Volume ' + value, 'ok');
        })
        .catch(err => {
          if (mySeq !== state._volCommandSeq) return;
          state.volumeUserActive = false;
          state.volumeGraceUntil = 0;    // let server truth restore the slider
          refreshVolume(true);
          toast(err.message, 'error');
        });
    }, 300);
  };

  // Queue button: desktop shows the side panel (the bottom-sheet is
  // display:none'd there), mobile opens the queue bottom-sheet on top.
  queueBtn.addEventListener('click', () => {
    if (window.matchMedia('(min-width: 900px)').matches) {
      closeMiniPopup();
      const t = document.getElementById('queue-toggle-btn');
      if (t) t.click();
    } else if (window._openQueueModal) {
      window._openQueueModal();
    }
  });

  // Drag-to-dismiss
  let startY = 0, currentY = 0, dragging = false;
  function onDragStart(e) {
    if (e.target.closest('.mini-popup-close') || e.target.closest('.mini-popup-queue-btn')) return;
    dragging = true;
    startY = e.touches[0].clientY;
    currentY = 0;
    popup.style.transition = 'none';
  }
  dragArea.addEventListener('touchstart', onDragStart, { passive: true });
  const popupHeader = popup.querySelector('.mini-popup-header');
  if (popupHeader) popupHeader.addEventListener('touchstart', onDragStart, { passive: true });

  window.addEventListener('touchmove', (e) => {
    if (!dragging || !_miniPopupOpen) return;
    currentY = e.touches[0].clientY - startY;
    if (currentY < 0) currentY = 0;
    popup.style.transform = 'translateY(' + currentY + 'px)';
  }, { passive: true });

  window.addEventListener('touchend', () => {
    if (!dragging) return;
    dragging = false;
    popup.style.transition = '';
    if (currentY > 100) {
      closeMiniPopup();
    }
    popup.style.transform = '';
  });

  // Expose for external use
  window._openMiniPopup = openMiniPopup;
  window._closeMiniPopup = closeMiniPopup;
  if (window.getRoute && window.getRoute() === '#now-playing') openMiniPopup(true);
})();


function clearUiAfterPlaybackReset() {
  const mainEl = document.querySelector('main');
  const resultsSection = document.getElementById('results-section');
  const queueSection = document.getElementById('queue-section');
  const input = document.getElementById('query');
  const wasShowingResults = state._resultsOpen && !resultsSection.hidden;
  const wasShowingQueue = mainEl.classList.contains('has-queue') && !queueSection.hidden;
  const shouldStageExit = wasShowingResults || wasShowingQueue;

  input.value = '';
  input.dispatchEvent(new Event('input'));  // hides the X, closes suggestions
  state._searchSeq++;
  state._searchResults = [];
  state._lastQueueJson = '';
  state._lastQueueIndex = -1;

  clearTimeout(resultsSection._hideTimer);
  clearTimeout(resultsSection._showTimer);
  clearTimeout(queueSection._hideTimer);
  resultsSection.classList.remove('is-visible');
  queueSection.classList.remove('is-visible');

  const finish = () => animatePlaySectionLayout(() => {
    state._resultsOpen = false;
    resultsSection.hidden = true;
    queueSection.hidden = true;
    mainEl.classList.remove('has-queue');
    showNowPlaying(null);
    progress.update({});     // hides the progress bar
    syncUiState();
  });

  if (shouldStageExit) setTimeout(finish, 320);
  else finish();
}

/* ---- clear everything (confirmed) ---- */
async function doClearAll() {
  const serial = deviceEl.value || null;
  toast('Clearing\u2026');
  try {
    const data = await api('/alexa/clear/', serial ? { serial } : {});
    state.isPlaying = false;
    state.lastActionIntent = false;
    syncPlayPause();
    clearUiAfterPlaybackReset();
    if (window._closeQueueModal) window._closeQueueModal();
    if (window._closeMiniPopup) window._closeMiniPopup();
    if (data.stop_error) toast('Cleared here, but the device may still be playing: ' + data.stop_error, 'error');
    else toast('Cleared', 'ok');
  } catch (e) {
    toast(e.message, 'error');
  }
}

(function () {
  // The standalone Clear button was removed from the search bar; the confirm
  // dialog wiring only attaches if some entry point for it still exists.
  const overlay = document.getElementById('confirm-clear');
  const trigger = document.getElementById('clear-all-btn');
  if (!overlay || !trigger) return;
  const cancelBtn = document.getElementById('confirm-clear-cancel');
  const yesBtn = document.getElementById('confirm-clear-yes');
  trigger.addEventListener('click', () => overlay.classList.add('open'));
  cancelBtn.addEventListener('click', () => overlay.classList.remove('open'));
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.classList.remove('open'); });
  yesBtn.addEventListener('click', () => { overlay.classList.remove('open'); doClearAll(); });
})();

document.getElementById('pp-btn').onclick = () => {
  const serial = selectedSerial();
  if (!serial) return;
  state.lastActionAt = Date.now();
  const action = state.isPlaying ? 'pause' : 'play';
  toast((action === 'pause' ? 'Pausing' : 'Resuming') + '\u2026');
  // Update the visual state immediately.  Waiting for the device response
  // leaves the banner showing the old glyph during the overlay animation and
  // can make both play/pause icons flash in sequence.
  const previousPlaying = state.isPlaying;
  state.isPlaying = action === 'play';
  state.lastActionIntent = action === 'play';
  syncPlayPause();
  api('/alexa/command/', { serial, action })
    .then(() => {
      state.isPlaying = action === 'play';
      state.lastActionIntent = state.isPlaying;
      syncPlayPause();
      toast(action === 'pause' ? 'Paused' : 'Resumed', 'ok');
    })
    .catch(e => {
      state.isPlaying = previousPlaying;
      state.lastActionIntent = previousPlaying;
      syncPlayPause();
      toast(e.message, 'error');
    });
};

const npPageArt = document.getElementById('np-page-art');
if (npPageArt) {
  npPageArt.onclick = (e) => {
    e.stopPropagation();
    const overlay = document.getElementById('np-page-art-overlay');
    if (overlay) {
      overlay.classList.remove('flash');
      void overlay.offsetWidth;
      overlay.classList.add('flash');
      setTimeout(() => overlay.classList.remove('flash'), 520);
    }
    document.getElementById('pp-btn').click();
  };
}

document.getElementById('shuffle-btn').addEventListener('click', async (e) => {
  const btn = e.currentTarget;
  if (btn.disabled) return;
  btn.disabled = true;
  try {
    await api('/alexa/shuffle_queue/', {});
    btn.classList.add('shuffle-active');
    state._lastQueueJson = '';
    schedulePollNowPlaying(300);
    toast('Queue shuffled', 'ok');
  } catch (err) {
    toast(err.message || 'Shuffle failed', 'error');
  } finally {
    btn.disabled = false;
  }
});

/* ---- compact player more menu ---- */
(function () {
  const wrap = document.querySelector('.np-more-wrap');
  const button = document.getElementById('np-more-btn');
  const menu = document.getElementById('np-more-menu');
  if (!wrap || !button || !menu) return;
  const close = () => { wrap.classList.remove('open'); button.setAttribute('aria-expanded', 'false'); };
  button.addEventListener('click', (e) => {
    e.stopPropagation();
    const open = !wrap.classList.contains('open');
    close();
    if (open) { wrap.classList.add('open'); button.setAttribute('aria-expanded', 'true'); }
  });
  document.addEventListener('click', (e) => { if (!wrap.contains(e.target)) close(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
  // Shared by the "..." menu entry, the playbar thumb, and the now-playing
  // page thumb. toggleLike updates the clicked button itself; refresh after
  // so every like button reflects the new state.
  function likeCurrentTrack(btn) {
    if (!(state._currentTrack && state._currentTrack.video_id && typeof toggleLike === 'function')) return;
    Promise.resolve(toggleLike(state._currentTrack, btn)).then(() => {
      if (window.refreshNpLikeButton) window.refreshNpLikeButton();
    });
  }
  const npMenuLike = document.getElementById('np-menu-like');
  if (npMenuLike) npMenuLike.addEventListener('click', () => {
    likeCurrentTrack(document.getElementById('np-like-btn'));
    close();
  });
  for (const id of ['np-like-btn', 'np-page-like-btn']) {
    const btn = document.getElementById(id);
    if (btn) btn.addEventListener('click', (e) => {
      e.stopPropagation(); // .np cluster click would toggle the player view
      likeCurrentTrack(btn);
    });
  }
  const npMenuPlaylist = document.getElementById('np-menu-playlist');
  if (npMenuPlaylist) npMenuPlaylist.addEventListener('click', () => {
    if (state._currentTrack && state._currentTrack.video_id && typeof openAddToPlaylistModal === 'function')
      openAddToPlaylistModal(state._currentTrack);
    close();
  });
  function resolveCurrentTrackDetails() {
    const track = state._currentTrack;
    if (!track || !track.video_id) return Promise.resolve(null);
    if (track.album_id && track.artist_id) return Promise.resolve(track);
    if (typeof window.api !== 'function') return Promise.resolve(track);
    return window.api('/api/album/resolve/' + encodeURIComponent(track.video_id))
      .then((details) => {
        if (details) {
          track.album_id = track.album_id || details.album_id || '';
          track.artist_id = track.artist_id || details.artist_id || '';
        }
        return track;
      })
      .catch(() => track);
  }
  const npMenuAlbum = document.getElementById('np-menu-album');
  if (npMenuAlbum) npMenuAlbum.addEventListener('click', () => {
    resolveCurrentTrackDetails().then((track) => {
      if (!track || !track.album_id) {
        if (window.toast) window.toast('Album unavailable for this song', 'error');
        return;
      }
      if (window.preloadNavigateAlbum) window.preloadNavigateAlbum(track.album_id);
      else if (window.navigateTo) window.navigateTo('#album/' + encodeURIComponent(track.album_id));
    });
    close();
  });
  const npMenuArtist = document.getElementById('np-menu-artist');
  if (npMenuArtist) npMenuArtist.addEventListener('click', () => {
    resolveCurrentTrackDetails().then((track) => {
      const artistId = track && (track.artist_id || track.channelId);
      if (!artistId) {
        if (window.toast) window.toast('Artist unavailable for this song', 'error');
        return;
      }
      if (window.preloadNavigateArtist) window.preloadNavigateArtist(artistId);
      else if (window.navigateTo) window.navigateTo('#artist/' + encodeURIComponent(artistId));
    });
    close();
  });
  const npMenuRadio = document.getElementById('np-menu-radio');
  if (npMenuRadio) npMenuRadio.addEventListener('click', (e) => {
    if (!state._currentTrack) { e.preventDefault(); return; }
    if (typeof playRadio === 'function') {
      playRadio(state._currentTrack);
    }
    close();
  });
})();

/* ---- prev / next ----
   Alexa processes each spoken command in turn (speech recognition + NLU +
   skill round-trip), which takes a few seconds. Clicking again before that
   finishes overwrites the pending arm (server._arm_play is single-slot per
   device) and races a new spoken command against one Alexa hasn't acted on
   yet, so the device silently stays on the old track. Disable the buttons
   for the duration of one round-trip so clicks queue up as intent (via the
   disabled state) rather than firing concurrently. */
let _navBusy = false;
for (const btn of document.querySelectorAll('[data-action="previous"], [data-action="next"]')) {
  btn.onclick = () => {
    if (_navBusy) return;
    const serial = selectedSerial();
    if (!serial) return;
    _navBusy = true;
    document.querySelectorAll('[data-action="previous"], [data-action="next"]')
      .forEach(b => b.disabled = true);
    toast(btn.title + '\u2026');
    // Guard the optimistic state.isPlaying=true below from the server's own
    // playing:false push (set synchronously by /alexa/command/ while the new
    // track is still loading) \u2014 without this, that SSE message arrives before
    // playback is confirmed and immediately flips the UI back to "paused".
    state.lastActionAt = Date.now();
    api('/alexa/command/', { serial, action: btn.dataset.action })
      .then((data) => {
        if (data.now_playing) showNowPlaying(data.now_playing);
        state.isPlaying = true;
        state.lastActionIntent = true;
        syncPlayPause();
        // New track incoming: hold the bar at 0:00 until PlaybackStarted
        // confirms *this* video_id (not a stale push for the track we just left).
        progress.resetPending(data.now_playing && data.now_playing.video_id);
        // Schedule one fallback poll; SSE remains the primary transition path.
        state._lastQueueJson = '';
        schedulePollNowPlaying(2000);
        schedulePollNowPlaying(5000);
        schedulePollNowPlaying(8000);
      })
      .catch(e => toast(e.message, 'error'))
      .finally(() => {
        // Alexa's own round-trip outlasts our HTTP call, so keep the buttons
        // disabled a bit longer than the request itself before allowing the
        // next command.
        setTimeout(() => {
          _navBusy = false;
          document.querySelectorAll('[data-action="previous"], [data-action="next"]')
            .forEach(b => b.disabled = false);
        }, 3000);
      });
  };
}

/* ---- volume ---- */

function updateUrlBar() {
  const ytmBtn = document.getElementById('np-url-toggle');
  const mpYtmBtn = document.getElementById('mp-url-toggle');
  if (state._currentVideoId) {
    const seconds = Math.floor(progress.livePosition() / 1000);
    const url = 'https://music.youtube.com/watch?v=' + encodeURIComponent(state._currentVideoId)
      + (seconds > 0 ? '&t=' + seconds : '');
    if (ytmBtn) { ytmBtn.href = url; ytmBtn.style.display = ''; }
    if (mpYtmBtn) { mpYtmBtn.href = url; mpYtmBtn.style.display = ''; }
  } else {
    if (ytmBtn) { ytmBtn.removeAttribute('href'); ytmBtn.style.display = 'none'; }
    if (mpYtmBtn) { mpYtmBtn.removeAttribute('href'); mpYtmBtn.style.display = 'none'; }
  }
}

(function () {
  const ytmBtn = document.getElementById('np-url-toggle');
  const mpYtmBtn = document.getElementById('mp-url-toggle');
  updateUrlBar();

  const onClick = (e) => {
    if (!state._currentVideoId) {
      e.preventDefault();
      toast('No song playing.', 'error');
      return;
    }
    // Refresh the href with the latest position right before navigating,
    // since livePosition() keeps ticking while the button just sits there.
    const seconds = Math.floor(progress.livePosition() / 1000);
    e.currentTarget.href = 'https://music.youtube.com/watch?v=' + encodeURIComponent(state._currentVideoId)
      + (seconds > 0 ? '&t=' + seconds : '');

    const serial = selectedSerial();
    if (serial && state.isPlaying) {
      state.lastActionAt = Date.now();
      state.lastActionIntent = false;
      api('/alexa/command/', { serial, action: 'pause' })
        .then(() => { state.isPlaying = false; state.lastActionIntent = false; syncPlayPause(); })
        .catch(() => {});
    }
  };
  if (ytmBtn) ytmBtn.addEventListener('click', onClick);
  if (mpYtmBtn) mpYtmBtn.addEventListener('click', onClick);
})();

  window.syncPlayPause = syncPlayPause;
  window.showNowPlaying = showNowPlaying;
  window.syncTrackPlaybackIndicators = syncTrackPlaybackIndicators;
  window.refreshNpLikeButton = refreshNpLikeButton;
  window.checkLikedVersion = checkLikedVersion;
  window.playResult = playResult;
  window.syncModalScrollLock = syncModalScrollLock;
  window.clearUiAfterPlaybackReset = clearUiAfterPlaybackReset;
  window.doClearAll = doClearAll;
  window.updateUrlBar = updateUrlBar;
})();
