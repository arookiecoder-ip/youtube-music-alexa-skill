const toastEl = document.getElementById('toast');
const deviceEl = document.getElementById('device');
const volumeEl = document.getElementById('volume');
volumeEl.disabled = false;

let isPlaying = false;
let toastTimer;
let lastToastMsg = '';
let lastToastKind = '';
let lastActionAt = 0;           // timestamp of last user button press
const GRACE_MS = 8000;          // don't let SSE override state for 8s after a user action
let _currentVideoId = '';       // video_id of the currently playing track
let volumeUserActive = false;
let volumeGraceUntil = 0;       // ignore server volume pushes until this time
const VOLUME_GRACE_MS = 4000;   // covers the debounce + command round-trip
let lastVolumeRefreshAt = 0;
let _hasTrack = false;          // a track (even optimistic) is loaded
let _resultsOpen = false;       // search results panel is showing
let _loggedIn = false;

/* ---- UI mode sync ----
   Decides, from (_loggedIn, _hasTrack, _resultsOpen):
   - idle: nothing playing -> hide the player, center the search bar
   - results-open: results panel showing -> full player/queue give way to the
     bottom mini player
   All transitions are CSS-driven (reveal classes), so changes are smooth. */
function syncUiState() {
  const mainEl = document.querySelector('main');
  const player = document.querySelector('.player-section');
  const mini = document.getElementById('mini-player');
  // Clear only makes sense when there is something to clear: a loaded track
  // or open search results. Just having text in the search box is not enough.
  document.getElementById('clear-all-btn').hidden = !(_hasTrack || _resultsOpen);
  document.body.classList.toggle('results-open', _resultsOpen);
  mini.classList.toggle('visible', _resultsOpen && _hasTrack);
  mainEl.classList.toggle('idle', _loggedIn && !_hasTrack && !_resultsOpen);
  // Recommendations are visible by default, so the user can continue browsing
  // while a track is playing, preventing a massive empty screen void.
  const recsSection = document.getElementById('recs-section');
  if (recsSection) {
    const shouldShow = _loggedIn && !_resultsOpen && !_hasTrack;
    if (shouldShow && !_recsLoaded) loadRecommendations();
    else recsSection.hidden = !shouldShow || !_recsLoaded;
  }
  if (_hasTrack) {
    clearTimeout(player._hideTimer);
    player.classList.remove('is-collapsed');
    requestAnimationFrame(() => player.classList.add('is-visible'));
  } else {
    player.classList.remove('is-visible');
    clearTimeout(player._hideTimer);
    player._hideTimer = setTimeout(() => player.classList.add('is-collapsed'), 300);
  }
}

function animatePlaySectionLayout(applyState) {
  const playSection = document.querySelector('.play-section');
  if (!playSection || playSection.hidden || playSection.offsetParent === null) {
    applyState();
    return;
  }

  const first = playSection.getBoundingClientRect();
  document.body.classList.add('layout-snap');
  let last;
  try {
    applyState();
    last = playSection.getBoundingClientRect();
  } finally {
    document.body.classList.remove('layout-snap');
  }

  if (!first.width || !first.height || !last.width || !last.height) return;

  const dx = first.left - last.left;
  const dy = first.top - last.top;
  const sx = first.width / last.width;
  const sy = first.height / last.height;
  if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5 && Math.abs(sx - 1) < 0.01 && Math.abs(sy - 1) < 0.01) return;

  clearTimeout(playSection._layoutAnimTimer);
  playSection.style.transformOrigin = 'top left';
  playSection.style.transition = 'none';
  playSection.style.transform = `translate(${dx}px, ${dy}px) scale(${sx}, ${sy})`;
  playSection.getBoundingClientRect();
  requestAnimationFrame(() => {
    playSection.style.transition = 'transform .36s cubic-bezier(.22, 1, .36, 1)';
    playSection.style.transform = '';
    playSection._layoutAnimTimer = setTimeout(() => {
      playSection.style.transformOrigin = '';
      playSection.style.transition = '';
      playSection.style.transform = '';
    }, 390);
  });
}

/* ---- toast ---- */
function toast(msg, kind) {
  if (!msg) {
    toastEl.classList.remove('show');
    lastToastMsg = '';
    lastToastKind = '';
    return;
  }
  kind = kind || 'info';
  if (toastEl.classList.contains('show') && msg === lastToastMsg && kind === lastToastKind) return;
  lastToastMsg = msg;
  lastToastKind = kind;
  toastEl.textContent = msg;
  toastEl.className = 'toast show ' + kind;
  clearTimeout(toastTimer);
  const delay = kind === 'error' ? 5000 : kind === 'ok' ? 2500 : 3500;
  toastTimer = setTimeout(() => {
    toastEl.classList.remove('show');
    lastToastMsg = '';
    lastToastKind = '';
  }, delay);
}

/* ---- play/pause state ---- */
function syncPlayPause() {
  for (const btn of [document.getElementById('pp-btn'), document.getElementById('mini-pp'), document.getElementById('mp-pp-btn')]) {
    btn.querySelector('.icon-play').style.display = isPlaying ? 'none' : '';
    btn.querySelector('.icon-pause').style.display = isPlaying ? '' : 'none';
    btn.title = isPlaying ? 'Pause' : 'Play';
  }
}

/* ---- now-playing display (single element, no dual placeholder bug) ---- */
// Last-rendered track fingerprint — used to skip redundant DOM writes.
let _lastNpFingerprint = '';

function showNowPlaying(info) {
  const np = document.getElementById('now-playing');
  const miniArt = document.getElementById('mini-art');
  if (!info || !info.title) {
    // Only update if we had a track before.
    if (_hasTrack || _lastNpFingerprint) {
      np.classList.remove('visible');
      document.getElementById('mini-title').textContent = '';
      miniArt.style.backgroundImage = '';
      miniArt.classList.remove('has-thumb');
      // Clear popup now-playing too
      document.getElementById('mp-np-title').textContent = '';
      document.getElementById('mp-np-artist').textContent = '';
      const mpArt = document.getElementById('mp-np-art');
      mpArt.style.backgroundImage = '';
      mpArt.classList.remove('has-thumb');
      _hasTrack = false;
      _currentVideoId = '';
      _lastNpFingerprint = '';
      updateUrlBar();
      syncUiState();
      updateResultsActive();
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
    document.getElementById('np-artist').textContent = info.artist || '';
    document.getElementById('mini-title').textContent = info.title;
    // Sync mini popup
    document.getElementById('mp-np-title').textContent = info.title;
    document.getElementById('mp-np-artist').textContent = info.artist || '';
    const art = document.getElementById('np-art');
    const mpArt = document.getElementById('mp-np-art');
    if (info.thumbnail) {
      const url = 'url(' + info.thumbnail + ')';
      const img = new Image();
      img.onload = () => {
        if (_lastNpFingerprint === fp) {
          art.style.backgroundImage = url;
          art.classList.add('has-thumb');
          miniArt.style.backgroundImage = url;
          miniArt.classList.add('has-thumb');
          mpArt.style.backgroundImage = url;
          mpArt.classList.add('has-thumb');
        }
      };
      img.src = info.thumbnail;
    } else {
      art.style.backgroundImage = '';
      art.classList.remove('has-thumb');
      miniArt.style.backgroundImage = '';
      miniArt.classList.remove('has-thumb');
      mpArt.style.backgroundImage = '';
      mpArt.classList.remove('has-thumb');
    }
    // Track video_id for the URL button. Clear it when the new track's id is
    // unknown (optimistic plain-text play) so the "Open on YouTube Music"
    // link never keeps pointing at the previous song.
    _currentVideoId = info.video_id || '';
    updateUrlBar();
  }

  const likeBtn = document.getElementById('np-like-btn');
  if (likeBtn && _currentVideoId && typeof _playlistsData !== 'undefined' && _playlistsData.liked_songs) {
    const isLiked = _playlistsData.liked_songs.includes(_currentVideoId);
    likeBtn.classList.toggle('liked', isLiked);
    likeBtn.innerHTML = isLiked 
      ? `<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>`
      : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>`;
  }

  const wasTrack = _hasTrack;
  _hasTrack = true;
  if (changed || !wasTrack) {
    syncUiState();
    updateResultsActive();
  }
}

/* ---- Progress bar ----
   The server sends an anchor (position_ms at started_at, plus duration_ms); we
   tick locally for a smooth bar and only talk to the server when the user drags
   to seek. Opening the app partway through a song still shows the right spot
   because started_at is server truth, not a local guess. */
const progress = (function () {
  const wrap = document.getElementById('progress');
  const track = document.getElementById('progress-track');
  const fill = document.getElementById('progress-fill');
  const handle = document.getElementById('progress-handle');
  const elapsedEl = document.getElementById('progress-elapsed');
  const totalEl = document.getElementById('progress-total');

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
    rafId = requestAnimationFrame(loop);
  }
  // Only animate while actually playing (or mid-drag): a paused/idle bar is
  // static, so a 60fps repaint loop would just waste battery. update() repaints
  // once on every state change, so the frozen position stays correct.
  function syncLoop() {
    // document.hidden guards against standalone/PWA windows where Chrome does
    // not reliably throttle requestAnimationFrame for a backgrounded window the
    // way it does for a backgrounded tab — without this the 60fps repaint loop
    // keeps running (and the device keeps heating) while the app is minimized.
    const shouldRun = (playing || dragging) && !awaitingStart && !wrap.hidden && !document.hidden;
    if (shouldRun && rafId == null) rafId = requestAnimationFrame(loop);
    else if (!shouldRun && rafId != null) { cancelAnimationFrame(rafId); rafId = null; }
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
      playing = np.playing && !!np.playback_confirmed;
    }
    syncLoop();
    paint();
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
  function beginDrag(e) {
    if (awaitingStart) return;
    // Track which progress-track was touched
    _activeTrack = e.currentTarget;
    dragging = true;
    _activeTrack.classList.add('dragging');
    dragMs = posFromEvent(e) * seekLimitMs();
    paint();
    e.preventDefault();
  }
  function moveDrag(e) {
    if (!dragging) return;
    dragMs = posFromEvent(e) * seekLimitMs();
    paint();
    e.preventDefault();
  }
  async function endDrag(e) {
    if (!dragging) return;
    dragging = false;
    _activeTrack.classList.remove('dragging');
    const target = Math.round(dragMs);
    // Optimistically anchor locally so the bar stays put during the round-trip.
    positionMs = target;
    anchorClientMs = Date.now();
    syncLoop();   // resume ticking from the new anchor if playing
    paint();
    const serial = deviceEl.value;
    if (!serial) { toast('Pick a device first.', 'error'); return; }
    lastActionAt = Date.now();
    localSeekUntil = Date.now() + 8000;
    toast('Seeking to ' + fmt(target) + '\u2026');
    try {
      await api('/alexa/seek/', { serial, position_ms: target });
      toast('Seeked to ' + fmt(target), 'ok');
    } catch (err) {
      // Seek failed: drop the hold so the next server push restores truth.
      localSeekUntil = 0;
      toast(err.message, 'error');
    }
  }

  track.addEventListener('mousedown', beginDrag);
  track.addEventListener('touchstart', beginDrag, { passive: false });
  // Also bind the mini popup progress track
  const mpTrack = document.getElementById('mp-progress-track');
  if (mpTrack) {
    mpTrack.addEventListener('mousedown', beginDrag);
    mpTrack.addEventListener('touchstart', beginDrag, { passive: false });
  }
  // Global move/end handlers work for both tracks
  window.addEventListener('mousemove', moveDrag);
  window.addEventListener('mouseup', endDrag);
  window.addEventListener('touchmove', moveDrag, { passive: false });
  window.addEventListener('touchend', endDrag);
  // Keyboard: arrow keys nudge +/- 5s. If duration is unknown, use a temporary
  // seek window so the scrubber still works while metadata catches up.
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

  return { update, resetPending, livePosition, getDuration: () => durationMs, syncLoop };
})();

/* ---- API ---- */
async function api(path, body) {
  const opts = body === undefined
    ? { credentials: 'same-origin' }
    : { method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body) };
  let res;
  try {
    res = await fetch(path, opts);
  } catch (_) {
    // Network-level failure: server unreachable, dropped connection, no internet.
    throw new Error('Can’t reach the server. Check your connection and try again.');
  }
  if (res.status === 401) { location.href = '/login/'; throw new Error('Session expired'); }
  const json = await res.json().catch(() => ({}));
  if (!res.ok) {
    // 502/503 mean the Echo (or Amazon) didn't take the command — say so
    // instead of a bare "HTTP 502".
    if (res.status === 502 || res.status === 503) {
      throw new Error(json.error || 'Device is offline or unreachable.');
    }
    throw new Error(json.error || ('HTTP ' + res.status));
  }
  return json;
}

/* ---- Sign-out confirmation ---- */
function doSignOut() {
  stopSSE();
  api('/logout/', {}).catch(() => {});
  location.href = '/login/';
}

(function () {
  const overlay = document.getElementById('confirm-signout');
  const cancelBtn = document.getElementById('confirm-signout-cancel');
  const yesBtn = document.getElementById('confirm-signout-yes');

  function showConfirm() { overlay.classList.add('open'); }
  function hideConfirm() { overlay.classList.remove('open'); }

  document.getElementById('logout').onclick = () => showConfirm();

  cancelBtn.addEventListener('click', hideConfirm);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) hideConfirm(); });
  yesBtn.addEventListener('click', () => { hideConfirm(); doSignOut(); });

  // Expose for sidebar
  window._showSignOutConfirm = showConfirm;
})();

function selectedSerial() {
  if (!deviceEl.value) { toast('Pick a device first.', 'error'); return null; }
  return deviceEl.value;
}

function isYoutubeLinkLike(value) {
  return /^(https?:\/\/)?(www\.|m\.|music\.)?(youtube\.com\/|youtu\.be\/)/i.test((value || '').trim());
}

function syncVolume(value, force) {
  const volume = Number(value);
  if (!Number.isFinite(volume)) return;
  volumeEl.disabled = false;
  const v = String(Math.max(0, Math.min(100, Math.round(volume))));
  // Server-pushed volume must not fight the user. The drag flag alone isn't
  // enough: it's cleared the instant the finger lifts, while the debounced
  // volume command is still in flight — a push generated before the command
  // landed still carries the OLD volume and would snap the slider back before
  // it jumps forward again. Hold server updates for a short grace window.
  if (!force && (volumeUserActive || Date.now() < volumeGraceUntil)) return;
  volumeEl.value = v;
  const mpVol = document.getElementById('mp-volume');
  if (mpVol) mpVol.value = v;
}

async function refreshVolume(force) {
  const serial = deviceEl.value;
  if (!serial) {
    return;
  }
  const now = Date.now();
  if (!force && now - lastVolumeRefreshAt < 15000) return;
  lastVolumeRefreshAt = now;
  try {
    const data = await api('/alexa/volume/?serial=' + encodeURIComponent(serial));
    if (data.available === false || data.volume === undefined || data.volume === null) {
      return;
    }
    syncVolume(data.volume);
  } catch (_) {
    // Volume reads are best-effort. The slider still sends volume commands.
  }
}

/* ---- now-playing via SSE (Server-Sent Events) ---- */
let _lastQueueJson = '';
let _lastQueueIndex = -1;
let _evtSource = null;

// The device list tags each option with data-online. When the selected Echo
// is offline it cannot be playing anything, whatever the server state says.
function selectedDeviceOnline() {
  const opt = deviceEl.selectedOptions && deviceEl.selectedOptions[0];
  return !opt || opt.dataset.online !== '0';
}

/* Removals the user has committed locally but the server may not have
   processed yet. Any queue snapshot that still contains one of these songs
   (an SSE push generated before the removal landed, or a poll response that
   raced it) is filtered before rendering, so deleted rows can't flash back
   and renumber the list mid-delete. Entries carry a count (duplicate songs:
   one delete hides one copy) and expire on their own so a failed request
   can never hide a song forever. */
const _pendingRemovals = new Map();   // video_id -> { count, expires }
const PENDING_REMOVAL_TTL = 8000;

function _markPendingRemoval(videoId) {
  if (!videoId) return;
  const e = _pendingRemovals.get(videoId);
  _pendingRemovals.set(videoId, {
    count: (e ? e.count : 0) + 1,
    expires: Date.now() + PENDING_REMOVAL_TTL,
  });
}

function _unmarkPendingRemoval(videoId) {
  if (!videoId) return;
  const e = _pendingRemovals.get(videoId);
  if (!e) return;
  if (e.count > 1) { e.count--; }
  else _pendingRemovals.delete(videoId);
}

function _filterPendingRemovals(queue, queueIndex) {
  if (!_pendingRemovals.size) return { queue, queueIndex };
  const now = Date.now();
  for (const [id, e] of _pendingRemovals) {
    if (e.expires < now) _pendingRemovals.delete(id);
  }
  if (!_pendingRemovals.size) return { queue, queueIndex };
  const consumed = new Map();
  const out = [];
  let idx = queueIndex;
  queue.forEach((item, i) => {
    const e = item && _pendingRemovals.get(item.video_id);
    const used = consumed.get(item && item.video_id) || 0;
    // Never hide the playing row — the server refuses to remove it anyway.
    if (e && used < e.count && i !== queueIndex) {
      consumed.set(item.video_id, used + 1);
      if (i < queueIndex) idx--;
      return;
    }
    out.push(item);
  });
  return { queue: out, queueIndex: idx };
}

let _lastHistoryVideoId = null;

function handleNpUpdate(np) {
  // A real track change means the server just recorded a listen (on the
  // skill's 'started' webhook). Reload history after a short delay so the
  // JSON write — and the async metadata backfill — have time to land.
  const npVideoId = (np && np.video_id) || null;
  if (npVideoId && npVideoId !== _lastHistoryVideoId) {
    _lastHistoryVideoId = npVideoId;
    setTimeout(loadHistory, 1500);
    // NOTE: recommendations are intentionally NOT reset here — they stay cached
    // for the whole session (same set until the user refreshes the page), so
    // returning to the blank state re-shows them instantly instead of re-
    // fetching and reloading every thumbnail.
  }
  if (np.playing && !selectedDeviceOnline()) {
    // Offline device: show the last known track but never as live playback —
    // otherwise the progress bar ticks along for music that isn't playing.
    np = Object.assign({}, np, { playing: false });
  }
  if (np.volume !== undefined && np.volume !== null) syncVolume(np.volume);
  if (np.playback_error) {
    // Server gave up retrying a dropped play command (see
    // _watch_playback_confirmation) — stop showing "loading" and tell the user.
    toast(np.playback_error, 'error');
    isPlaying = false;
    syncPlayPause();
  }
  if (np.title) {
    showNowPlaying(np);
    if (np.playing !== undefined) {
      const inGrace = (Date.now() - lastActionAt) < GRACE_MS;
      if (np.playing || !inGrace) {
        isPlaying = np.playing;
        syncPlayPause();
      }
    }
  } else {
    // Server has no track (e.g. cleared from another tab): reset the display.
    showNowPlaying(null);
    if (np.playing !== undefined) {
      const inGrace = (Date.now() - lastActionAt) < GRACE_MS;
      if (!inGrace) {
        isPlaying = np.playing;
        syncPlayPause();
      }
    }
  }
  // Drive the progress bar from the same state update.
  progress.update(np);
  // Update queue. Re-render only when the list changes; if just the active
  // index changes, move the highlight without rebuilding the whole panel.
  // Snapshots that raced a local delete still contain the removed song —
  // filter those out so the row doesn't flash back (see _pendingRemovals).
  const filtered = _filterPendingRemovals(np.queue || [], np.queue_index ?? -1);
  _lastQueueIndex = filtered.queueIndex;
  const qJson = JSON.stringify(filtered.queue);
  if (qJson !== _lastQueueJson) {
    _lastQueueJson = qJson;
    showQueue(filtered.queue, _lastQueueIndex);
    // The mobile queue modal renders its own copy of the list; without this
    // it keeps showing the stale order after a reorder/remove until reopened.
    refreshQueueModalIfOpen();
  } else {
    updateQueueActive(_lastQueueIndex);
  }
}

function refreshQueueModalIfOpen() {
  const overlay = document.getElementById('queue-modal-overlay');
  if (overlay && overlay.classList.contains('open') && window._renderQueueModal) {
    window._renderQueueModal();
  }
}

function connectSSE() {
  stopSSE();
  const serial = deviceEl.value;
  if (!serial) return;
  _evtSource = new EventSource('/alexa/now_playing/stream?serial=' + encodeURIComponent(serial));
  _evtSource.onmessage = (e) => {
    try { handleNpUpdate(JSON.parse(e.data)); } catch (_) {}
  };
  _evtSource.onerror = () => {
    // SSE will auto-reconnect; no action needed
  };
}

function stopSSE() {
  if (_evtSource) { _evtSource.close(); _evtSource = null; }
}

// One-shot fetch of the current now-playing state. SSE already pushes updates,
// but after an action (play / next / prev) we nudge a refresh a few seconds
// later to reliably catch the track transition.
async function pollNowPlaying() {
  const serial = deviceEl.value;
  if (!serial) return;
  try {
    const np = await api('/alexa/now_playing/?serial=' + encodeURIComponent(serial));
    handleNpUpdate(np);
    refreshVolume(false);
  } catch (_) {
    // Best-effort; SSE remains the primary update path.
  }
}

document.addEventListener('visibilitychange', () => {
  progress.syncLoop();
  if (document.hidden) stopSSE();
  else if (deviceEl.value) {
    connectSSE();
    refreshVolume(true);
  }
});
window.addEventListener('focus', () => refreshVolume(false));

deviceEl.addEventListener('change', () => {
  if (deviceEl.value) {
    localStorage.setItem('selectedSerial', deviceEl.value);
    if (!selectedDeviceOnline()) toast('That device is offline. Commands may not reach it.', 'error');
    connectSSE();
    refreshVolume(true);
  }
});

/* ---- devices ---- */
async function loadDevices(refresh) {
  const showStatusToast = !!refresh;
  if (showStatusToast) toast('Loading devices\u2026');
  // Remember the current selection so a refresh doesn't silently jump the
  // remote to whatever device happens to be first in the rebuilt list.
  const prevSerial = deviceEl.value || localStorage.getItem('selectedSerial') || '';
  deviceEl.innerHTML = '<option value="">Loading\u2026</option>';
  try {
    const data = await api('/alexa/devices/' + (refresh ? '?refresh=1' : ''));
    const ok = _applyDevices(data.devices || [], prevSerial);
    if (!ok) {
      toast('No compatible devices found.', 'error');
      return;
    }
    if (showStatusToast) toast(data.devices.length + ' device' + (data.devices.length > 1 ? 's' : '') + ' found', 'ok');
    connectSSE();
    refreshVolume(true);
  } catch (e) {
    deviceEl.innerHTML = '<option value="">Unavailable</option>';
    toast(e.message, 'error');
  }
}

document.getElementById('refresh').onclick = () => loadDevices(true);

/* ---- Custom dropdown sync ---- */
(function () {
  const wrapper = document.getElementById('device-wrapper');
  const trigger = document.getElementById('device-trigger');
  const menu = document.getElementById('device-menu');
  const triggerLabel = trigger.querySelector('span');

  function syncCustomDropdown() {
    // Rebuild menu from the hidden select's options
    menu.innerHTML = '';
    for (const opt of deviceEl.options) {
      const item = document.createElement('div');
      item.className = 'custom-select-option' + (opt.selected ? ' selected' : '');
      item.dataset.value = opt.value;
      const isOnline = opt.dataset.online !== '0';
      item.innerHTML = (opt.value
        ? '<span class="cs-dot ' + (isOnline ? 'online' : 'offline') + '"></span>'
        : '') + escHtml(opt.textContent);
      item.addEventListener('click', () => {
        deviceEl.value = opt.value;
        deviceEl.dispatchEvent(new Event('change'));
        closeDropdown();
        updateTriggerLabel();
      });
      menu.appendChild(item);
    }
    updateTriggerLabel();
  }

  function updateTriggerLabel() {
    const sel = deviceEl.selectedOptions[0];
    triggerLabel.textContent = sel ? sel.textContent : 'Select device';
    // Update selected state
    for (const item of menu.children) {
      item.classList.toggle('selected', item.dataset.value === deviceEl.value);
    }
  }

  function closeDropdown() { wrapper.classList.remove('open'); }

  trigger.addEventListener('click', (e) => {
    e.stopPropagation();
    wrapper.classList.toggle('open');
  });

  document.addEventListener('click', (e) => {
    if (!wrapper.contains(e.target)) closeDropdown();
  });

  // Watch for select changes (from loadDevices, sidebar sync, etc.)
  new MutationObserver(() => syncCustomDropdown()).observe(deviceEl, { childList: true });
  deviceEl.addEventListener('change', updateTriggerLabel);

  // Initial sync
  syncCustomDropdown();
})();

/* ---- play a song / search ----
   YouTube links still play directly (there is one exact video to play).
   Plain-text queries open the search-results panel instead; the user picks
   the exact track from there. */
async function playDirectLink(query) {
  const serial = selectedSerial();
  if (!serial) return;
  lastActionAt = Date.now();
  toast('Resolving link\u2026');
  try {
    const data = await api('/alexa/play/', { serial, query });
    const npInfo = data.now_playing || { title: query, artist: '', thumbnail: '' };
    showNowPlaying(npInfo);
    // Hold the bar at 0:00 for the new track; it starts ticking only once the
    // server confirms playback (real anchor + duration via SSE/poll). For
    // direct link plays the video_id is already known, so only *that* track's
    // confirmation can end the wait.
    progress.resetPending(npInfo.video_id);
    isPlaying = true;
    syncPlayPause();
    toast('Playing', 'ok');
    // Force queue refresh
    _lastQueueJson = '';
    setTimeout(pollNowPlaying, 3000);
  } catch (e) {
    toast(e.message, 'error');
  }
}

document.getElementById('play-query').onclick = () => {
  const query = document.getElementById('query').value.trim();
  if (!query) { toast('Type something', 'error'); return; }
  document.getElementById('query').blur();
  
  if (isYoutubeLinkLike(query)) {
    if (query.includes('list=')) {
      if (confirm('This looks like a playlist. Do you want to save it to your Playlists?')) {
        const name = prompt("Enter a name for this playlist:", "Imported Playlist");
        if (name) {
          api('/api/playlists/', { name: name, source_url: query }).then(res => {
            toast('Playlist saved. Syncing...', 'ok');
            if (typeof syncPlaylist === 'function') syncPlaylist(res.id);
          }).catch(() => toast('Failed to save playlist', 'error'));
          return;
        }
      }
    }
    playDirectLink(query);
  } else {
    runSearch(query);
  }
};

/* ---- search results panel ---- */
const RESULTS_PER_PAGE = 10;
let _searchResults = [];
let _resultsPage = 0;
let _searchSeq = 0;   // drops stale search responses

async function runSearch(query) {
  const mySeq = ++_searchSeq;
  toast('Searching \u201c' + query + '\u201d\u2026');
  try {
    const data = await api('/alexa/search/?q=' + encodeURIComponent(query));
    if (mySeq !== _searchSeq) return;   // a newer search won
    _searchResults = data.results || [];
    if (!_searchResults.length) { toast('No results found.', 'error'); return; }
    _resultsPage = 0;
    renderResults();
    openResults();
    toast(_searchResults.length + ' results', 'ok');
  } catch (e) {
    if (mySeq === _searchSeq) toast(e.message, 'error');
  }
}

function openResults() {
  const section = document.getElementById('results-section');
  // The queue column collapses while results are showing; the mini player
  // takes over at the bottom.
  const mainEl = document.querySelector('main');
  const queueSection = document.getElementById('queue-section');
  clearTimeout(section._hideTimer);
  clearTimeout(section._showTimer);
  animatePlaySectionLayout(() => {
    _resultsOpen = true;
    mainEl.classList.remove('has-queue');
    queueSection.classList.remove('is-visible');
    queueSection.hidden = true;
    section.hidden = false;
    syncUiState();
  });
  section._showTimer = setTimeout(() => {
    requestAnimationFrame(() => {
      if (_resultsOpen && !section.hidden) section.classList.add('is-visible');
    });
  }, 120);
}

function closeResults() {
  if (!_resultsOpen) return;
  const section = document.getElementById('results-section');
  // Fade the results panel out smoothly, then collapse it and show the queue.
  section.classList.remove('is-visible');
  clearTimeout(section._showTimer);
  clearTimeout(section._hideTimer);

  // If a queue will be restored, pre-add has-queue BEFORE removing results-open
  // so the grid columns stay at 1fr 1fr (no shrink-then-expand bounce).
  let queue = [];
  try { queue = JSON.parse(_lastQueueJson || '[]'); } catch (_) {}
  const willShowQueue = queue.length > 1;
  if (willShowQueue) {
    document.querySelector('main').classList.add('has-queue');
  }

  // Wait for the CSS opacity/transform transition (~280ms) before hiding.
  section._hideTimer = setTimeout(() => {
    animatePlaySectionLayout(() => {
      _resultsOpen = false;
      section.hidden = true;
      syncUiState();
      // Replay the player's reveal animation so enlarging from the mini player
      // slides the full player in instead of popping it.
      if (_hasTrack) {
        const player = document.querySelector('.player-section');
        player.classList.remove('is-collapsed');
        player.classList.remove('is-visible');
        void player.offsetHeight;
        player.classList.add('is-visible');
      }
      // Bring the queue panel back after results have faded out.
      try { showQueue(queue, _lastQueueIndex); } catch (_) {}
    });
  }, 300);
}

function renderResults() {
  const list = document.getElementById('results-list');
  const totalPages = Math.max(1, Math.ceil(_searchResults.length / RESULTS_PER_PAGE));
  _resultsPage = Math.min(Math.max(0, _resultsPage), totalPages - 1);
  const start = _resultsPage * RESULTS_PER_PAGE;
  const pageItems = _searchResults.slice(start, start + RESULTS_PER_PAGE);
  list.innerHTML = '';
  // Close any open more-menu when re-rendering
  _closeAllMoreMenus();
  pageItems.forEach((item) => {
    const wrapper = document.createElement('div');
    wrapper.className = 'result-swipe-wrapper';
    wrapper.dataset.videoId = item.video_id;

    // Swipe underlays (mobile only, hidden via CSS on desktop)
    wrapper.innerHTML = `
      <div class="result-swipe-underlay underlay-play-next">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
        Play next
      </div>
      <div class="result-swipe-underlay underlay-add-queue">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
        Add to queue
      </div>
    `;

    // Inner content
    const inner = document.createElement('div');
    inner.className = 'result-item-inner' + (item.video_id === _currentVideoId ? ' active' : '');

    const thumbHtml = item.thumbnail
      ? `<img class="result-thumb" src="${escHtml(item.thumbnail)}" alt="" loading="lazy" onload="this.classList.add('loaded')">`
      : `<div class="result-thumb"></div>`;

    // SVG icons for buttons (inline to avoid extra network requests)
    const queueAddSvg = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>`;
    const moreSvg = `<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>`;

    // Check if liked
    const isLiked = typeof _playlistsData !== 'undefined' && _playlistsData.liked_songs && _playlistsData.liked_songs.includes(item.video_id);
    const heartSvg = isLiked 
      ? `<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>`
      : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>`;

    inner.innerHTML = `
      ${thumbHtml}
      <div class="result-info">
        <div class="result-title">${escHtml(item.title)}</div>
        <div class="result-artist">${escHtml(item.artist)}</div>
      </div>
      <button class="result-like-btn ${isLiked ? 'liked' : ''}" type="button" title="Like" data-vid="${escHtml(item.video_id)}">${heartSvg}</button>
      <button class="result-queue-btn" type="button" title="Add to queue">${queueAddSvg}</button>
      <button class="result-more-btn" type="button" title="More options">${moreSvg}</button>
      <div class="result-more-menu">
        <div class="result-menu-option" data-action="play-next">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
          Play next
        </div>
        <div class="result-menu-option" data-action="add-to-queue">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
          Add to queue
        </div>
        <div class="result-menu-option" data-action="save-playlist">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
          Save to Playlist
        </div>
      </div>
    `;

    wrapper.appendChild(inner);

    // Tap on the main area → play the result
    attachQueueItemTap(inner, () => playResult(item));

    // Mobile: queue-add icon tap → add to queue (last)
    const qBtn = inner.querySelector('.result-queue-btn');
    qBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      addToQueue(item, 'last');
    });

    // Desktop: more-options button
    const moreBtn = inner.querySelector('.result-more-btn');
    const moreMenu = inner.querySelector('.result-more-menu');
    // Prevent document click handler from closing menu when clicking inside it
    moreMenu.addEventListener('click', (e) => e.stopPropagation());
    moreBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const wasOpen = moreMenu.classList.contains('open');
      _closeAllMoreMenus();
      if (!wasOpen) {
        moreBtn.classList.add('open');
        // Position the menu using fixed coords so it escapes the swipe row's clipping
        const rect = moreBtn.getBoundingClientRect();
        const menuHeight = 88; // approximate height of two option rows
        const spaceBelow = window.innerHeight - rect.bottom;
        const openAbove = spaceBelow < menuHeight + 8;
        moreMenu.style.left = '';
        moreMenu.style.top = '';
        moreMenu.style.bottom = '';
        moreMenu.style.right = '';
        if (openAbove) {
          moreMenu.classList.add('above');
          moreMenu.style.bottom = (window.innerHeight - rect.top + 4) + 'px';
        } else {
          moreMenu.classList.remove('above');
          moreMenu.style.top = (rect.bottom + 4) + 'px';
        }
        // Align right edge of menu with right edge of button
        const menuWidth = 180;
        let left = rect.right - menuWidth;
        if (left < 8) left = 8;
        moreMenu.style.left = left + 'px';
        moreMenu.classList.add('open');
        // Portal the menu to <body> while open (see queue menu note: fixed
        // elements inside overflow-hidden rows in a scrollable list aren't
        // reliably clickable in Chromium). _closeAllMoreMenus returns it.
        moreMenu._home = inner;
        document.body.appendChild(moreMenu);
      }
    });
    moreMenu.querySelector('[data-action="play-next"]').addEventListener('click', (e) => {
      e.stopPropagation();
      _closeAllMoreMenus();
      addToQueue(item, 'next');
    });
    moreMenu.querySelector('[data-action="add-to-queue"]').addEventListener('click', (e) => {
      e.stopPropagation();
      _closeAllMoreMenus();
      addToQueue(item, 'last');
    });
    const saveOpt = moreMenu.querySelector('[data-action="save-playlist"]');
    if (saveOpt) {
      saveOpt.addEventListener('click', (e) => {
        e.stopPropagation();
        _closeAllMoreMenus();
        openAddToPlaylistModal(item);
      });
    }

    const likeBtn = inner.querySelector('.result-like-btn');
    if (likeBtn) {
      likeBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleLike(item, likeBtn);
      });
    }


    // Mobile: attach swipe gesture
    _attachSwipeGesture(wrapper, inner, item);

    list.appendChild(wrapper);
  });
  document.getElementById('results-page-label').textContent =
    'Page ' + (_resultsPage + 1) + ' of ' + totalPages;
  document.getElementById('results-prev').disabled = _resultsPage <= 0;
  document.getElementById('results-next').disabled = _resultsPage >= totalPages - 1;
}

// Close all open more-menus
function _closeAllMoreMenus() {
  for (const m of document.querySelectorAll('.result-more-menu.open')) {
    m.classList.remove('open');
    m.style.top = '';
    m.style.bottom = '';
    m.style.left = '';
    m.style.right = '';
    // Return a body-portaled menu to its row (see the open handler).
    if (m._home && m.parentElement !== m._home) m._home.appendChild(m);
  }
  for (const b of document.querySelectorAll('.result-more-btn.open')) b.classList.remove('open');
  for (const w of document.querySelectorAll('.result-swipe-wrapper.menu-open')) w.classList.remove('menu-open');
}
document.addEventListener('click', _closeAllMoreMenus);

// Highlight the currently playing track in the visible results page.
function updateResultsActive() {
  for (const el of document.querySelectorAll('#results-list .result-item-inner')) {
    el.classList.toggle('active', !!_currentVideoId && el.closest('.result-swipe-wrapper')?.dataset.videoId === _currentVideoId);
  }
}

/* After paging, jump back to the top of the results. On desktop the list
   scrolls inside .results-list — scrolling the document there would shove the
   whole (barely-overflowing) page upward, so only the inner container moves.
   On mobile the document is the scroll container, so scrollIntoView is right. */
function scrollResultsToTop() {
  document.getElementById('results-list').scrollTop = 0;
  if (window.matchMedia('(max-width: 899px)').matches) {
    document.getElementById('results-section').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}
document.getElementById('results-prev').addEventListener('click', () => {
  _resultsPage--;
  renderResults();
  scrollResultsToTop();
});
document.getElementById('results-next').addEventListener('click', () => {
  _resultsPage++;
  renderResults();
  scrollResultsToTop();
});
document.getElementById('results-close').addEventListener('click', closeResults);

/* ---- Add to queue ---- */
let _addToQueueBusy = false;
async function addToQueue(item, position) {
  if (_addToQueueBusy) return;
  // Nothing playing? Just play the song directly instead of silently queuing.
  if (!_hasTrack) {
    playResult(item);
    return;
  }
  const serial = selectedSerial();
  if (!serial) return;
  _addToQueueBusy = true;
  const label = position === 'next' ? 'Playing next' : 'Adding to queue';
  toast(label + '\u2026');
  try {
    await api('/alexa/queue_add/', {
      serial,
      video_id: item.video_id,
      title: item.title,
      artist: item.artist,
      thumbnail: item.thumbnail,
      duration_ms: item.duration_ms,
      position,
    });
    if (position === 'next') {
      toast('\u201c' + item.title + '\u201d will play next', 'ok');
    } else {
      toast('Added \u201c' + item.title + '\u201d to queue', 'ok');
    }
    // Re-adding a song right after deleting it must show up again \u2014 drop any
    // pending-removal entry that would otherwise filter it out.
    _pendingRemovals.delete(item.video_id);
    // Force queue refresh
    _lastQueueJson = '';
    setTimeout(pollNowPlaying, 500);
  } catch (e) {
    toast(e.message, 'error');
  } finally {
    _addToQueueBusy = false;
  }
}

/* ---- Swipe gesture for result items (mobile) ---- */
function _attachSwipeGesture(wrapper, inner, item) {
  const SWIPE_THRESHOLD = 60;
  const LOCK_DISTANCE = 8;
  const AXIS_BIAS = 1.2;
  let startX = 0, startY = 0, currentX = 0, gesture = 'pending';

  inner.addEventListener('touchstart', (e) => {
    if (e.touches.length !== 1) return;
    startX = e.touches[0].clientX;
    startY = e.touches[0].clientY;
    currentX = 0;
    gesture = 'pending';
    wrapper.classList.remove('swiping-left', 'swiping-right');
    inner.style.transition = 'none';
  }, { passive: true });

  inner.addEventListener('touchmove', (e) => {
    if (e.touches.length !== 1) return;
    const dx = e.touches[0].clientX - startX;
    const dy = e.touches[0].clientY - startY;
    const absX = Math.abs(dx);
    const absY = Math.abs(dy);

    if (gesture === 'pending') {
      if (Math.max(absX, absY) < LOCK_DISTANCE) return;
      if (absY > absX * AXIS_BIAS) {
        gesture = 'scroll';
        wrapper.classList.remove('swiping-left', 'swiping-right');
        inner.style.transition = '';
        inner.style.transform = '';
        return;
      }
      if (absX > absY * AXIS_BIAS) gesture = 'swipe';
      else return;
    }

    if (gesture !== 'swipe') return;
    e.preventDefault();
    currentX = dx;
    wrapper.classList.toggle('swiping-right', currentX > 0);
    wrapper.classList.toggle('swiping-left', currentX < 0);
    inner.style.transform = 'translateX(' + currentX + 'px)';
  }, { passive: false });

  inner.addEventListener('touchend', () => {
    if (gesture !== 'swipe') {
      inner.style.transition = '';
      inner.style.transform = '';
      wrapper.classList.remove('swiping-left', 'swiping-right');
      gesture = 'idle';
      return;
    }
    inner.style.transition = 'transform .25s cubic-bezier(.22,1,.36,1)';
    inner.style.transform = '';
    inner._swipeSuppressClick = true;
    wrapper.classList.remove('swiping-left', 'swiping-right');

    if (currentX > SWIPE_THRESHOLD) {
      addToQueue(item, 'next');
    } else if (currentX < -SWIPE_THRESHOLD) {
      addToQueue(item, 'last');
    }
    gesture = 'idle';
    currentX = 0;
  }, { passive: true });

  inner.addEventListener('touchcancel', () => {
    inner.style.transition = '';
    inner.style.transform = '';
    wrapper.classList.remove('swiping-left', 'swiping-right');
    gesture = 'idle';
    currentX = 0;
  }, { passive: true });
}

async function playResult(item) {
  const serial = selectedSerial();
  if (!serial) return;
  lastActionAt = Date.now();
  toast('Playing \u201c' + item.title + '\u201d\u2026');
  try {
    await api('/alexa/play_queue/', {
      serial,
      video_id: item.video_id,
      title: item.title,
      artist: item.artist,
      thumbnail: item.thumbnail,
      duration_ms: item.duration_ms,
    });
    showNowPlaying(item);
    progress.resetPending(item.video_id);
    isPlaying = true;
    syncPlayPause();
    toast('Playing', 'ok');
    _lastQueueJson = '';
    setTimeout(pollNowPlaying, 3000);
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

  function openMiniPopup() {
    if (_miniPopupOpen) return;
    _miniPopupOpen = true;
    // Sync volume from main slider
    mpVolume.value = volumeEl.value;
    // Sync shuffle state
    const mainShuffle = document.getElementById('shuffle-btn');
    mpShuffleBtn.classList.toggle('shuffle-active', mainShuffle.classList.contains('shuffle-active'));
    overlay.classList.add('open');
    syncModalScrollLock();
  }

  function closeMiniPopup() {
    if (!_miniPopupOpen) return;
    _miniPopupOpen = false;
    overlay.classList.remove('open');
    syncModalScrollLock();
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
  mpVolume.addEventListener('pointerdown', () => { volumeUserActive = true; });
  mpVolume.addEventListener('pointerup', () => { volumeUserActive = false; });
  mpVolume.addEventListener('touchend', () => { volumeUserActive = false; });
  mpVolume.addEventListener('change', () => { volumeUserActive = false; });
  mpVolume.oninput = (e) => {
    volumeUserActive = true;
    volumeGraceUntil = Date.now() + VOLUME_GRACE_MS;
    // Sync main slider visually
    volumeEl.value = e.target.value;
    clearTimeout(mpVolTimer);
    mpVolTimer = setTimeout(() => {
      const serial = selectedSerial();
      if (!serial) { volumeUserActive = false; volumeGraceUntil = 0; return; }
      const value = +e.target.value;
      volumeGraceUntil = Date.now() + VOLUME_GRACE_MS;
      toast('Volume ' + value + '\u2026');
      api('/alexa/command/', { serial, action: 'volume', value })
        .then(() => {
          volumeUserActive = false;
          volumeGraceUntil = Date.now() + VOLUME_GRACE_MS;
          syncVolume(value, true);
          toast('Volume ' + value, 'ok');
        })
        .catch(err => {
          volumeUserActive = false;
          volumeGraceUntil = 0;    // let server truth restore the slider
          refreshVolume(true);
          toast(err.message, 'error');
        });
    }, 300);
  };

  // Queue button: open the existing queue modal
  queueBtn.addEventListener('click', () => {
    if (window._openQueueModal) window._openQueueModal();
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
  popup.querySelector('.mini-popup-header').addEventListener('touchstart', onDragStart, { passive: true });

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
})();

function clearUiAfterPlaybackReset() {
  const mainEl = document.querySelector('main');
  const resultsSection = document.getElementById('results-section');
  const queueSection = document.getElementById('queue-section');
  const input = document.getElementById('query');
  const wasShowingResults = _resultsOpen && !resultsSection.hidden;
  const wasShowingQueue = mainEl.classList.contains('has-queue') && !queueSection.hidden;
  const shouldStageExit = wasShowingResults || wasShowingQueue;

  input.value = '';
  input.dispatchEvent(new Event('input'));  // hides the X, closes suggestions
  _searchSeq++;
  _searchResults = [];
  _lastQueueJson = '';
  _lastQueueIndex = -1;

  clearTimeout(resultsSection._hideTimer);
  clearTimeout(resultsSection._showTimer);
  clearTimeout(queueSection._hideTimer);
  resultsSection.classList.remove('is-visible');
  queueSection.classList.remove('is-visible');

  const finish = () => animatePlaySectionLayout(() => {
    _resultsOpen = false;
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
    isPlaying = false;
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
  const overlay = document.getElementById('confirm-clear');
  const cancelBtn = document.getElementById('confirm-clear-cancel');
  const yesBtn = document.getElementById('confirm-clear-yes');
  document.getElementById('clear-all-btn').addEventListener('click', () => overlay.classList.add('open'));
  cancelBtn.addEventListener('click', () => overlay.classList.remove('open'));
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.classList.remove('open'); });
  yesBtn.addEventListener('click', () => { overlay.classList.remove('open'); doClearAll(); });
})();
/* ---- search suggestions ---- */
(function () {
  const input = document.getElementById('query');
  const listEl = document.getElementById('suggest-list');
  const clearBtn = document.getElementById('query-clear');
  let items = [];        // current suggestion strings
  let activeIdx = -1;    // highlighted item (-1 = none)
  let debounceTimer = null;
  let seq = 0;           // request sequencer, drops stale responses

  const searchSvg =
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="2" stroke-linecap="round">' +
    '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>';

  function closeList() {
    // Cancel any pending debounce AND invalidate any in-flight request, so a
    // late suggestion response can't reopen the list after we've closed it
    // (e.g. right after Enter submits the query).
    clearTimeout(debounceTimer);
    seq++;
    listEl.hidden = true;
    listEl.innerHTML = '';
    items = [];
    activeIdx = -1;
    input.setAttribute('aria-expanded', 'false');
  }

  function render() {
    if (!items.length) { closeList(); return; }
    listEl.innerHTML = '';
    items.forEach((text, i) => {
      const li = document.createElement('li');
      li.className = 'suggest-item' + (i === activeIdx ? ' active' : '');
      li.setAttribute('role', 'option');
      li.innerHTML = searchSvg + '<span></span>';
      li.querySelector('span').textContent = text;
      // mousedown (not click) so it fires before the input's blur
      li.addEventListener('mousedown', e => { e.preventDefault(); choose(i); });
      listEl.appendChild(li);
    });
    listEl.hidden = false;
    input.setAttribute('aria-expanded', 'true');
  }

  function choose(i) {
    if (i < 0 || i >= items.length) return;
    input.value = items[i];
    syncClearBtn();
    closeList();
    document.getElementById('play-query').click();
  }

  function syncClearBtn() { clearBtn.hidden = !input.value; syncUiState(); }

  // mousedown (not click) so it fires before the input's blur; keep focus in
  // the box so the user can type a new query right away.
  clearBtn.addEventListener('mousedown', e => {
    e.preventDefault();
    input.value = '';
    closeList();
    syncClearBtn();
    closeResults();   // also dismiss the results panel for this search
    input.focus();
  });
  // Mobile: touchend fires instead of mousedown; mirror the same behaviour.
  clearBtn.addEventListener('touchend', e => {
    e.preventDefault();
    input.value = '';
    closeList();
    syncClearBtn();
    closeResults();
    input.focus();
  });
  syncClearBtn();

  async function fetchSuggestions(q) {
    const mySeq = ++seq;
    try {
      const data = await api('/alexa/suggest/?q=' + encodeURIComponent(q));
      if (mySeq !== seq) return;            // a newer keystroke won
      items = (data.suggestions || []).slice(0, 8);
      activeIdx = -1;
      render();
    } catch (_) {
      // Suggestions are best-effort; stay silent on failure.
    }
  }

  input.addEventListener('input', () => {
    const q = input.value.trim();
    syncClearBtn();
    clearTimeout(debounceTimer);
    // Don't suggest for links or empty input
    if (!q || isYoutubeLinkLike(q)) { closeList(); return; }
    debounceTimer = setTimeout(() => fetchSuggestions(q), 180);
  });

  input.addEventListener('keydown', e => {
    const open = !listEl.hidden && items.length;
    if (e.key === 'ArrowDown' && open) {
      e.preventDefault();
      activeIdx = (activeIdx + 1) % items.length;
      render();
    } else if (e.key === 'ArrowUp' && open) {
      e.preventDefault();
      activeIdx = (activeIdx - 1 + items.length) % items.length;
      render();
    } else if (e.key === 'Enter') {
      if (open && activeIdx >= 0) { e.preventDefault(); choose(activeIdx); }
      else { closeList(); document.getElementById('play-query').click(); }
    } else if (e.key === 'Escape') {
      closeList();
    }
  });

  input.addEventListener('blur', () => setTimeout(closeList, 120));
})();

/* ---- play/pause toggle ---- */
document.getElementById('pp-btn').onclick = () => {
  const serial = selectedSerial();
  if (!serial) return;
  lastActionAt = Date.now();
  const action = isPlaying ? 'pause' : 'play';
  toast((action === 'pause' ? 'Pausing' : 'Resuming') + '\u2026');
  api('/alexa/command/', { serial, action })
    .then(() => {
      isPlaying = action === 'play';
      syncPlayPause();
      toast(action === 'pause' ? 'Paused' : 'Resumed', 'ok');
    })
    .catch(e => toast(e.message, 'error'));
};

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
    // Guard the optimistic isPlaying=true below from the server's own
    // playing:false push (set synchronously by /alexa/command/ while the new
    // track is still loading) \u2014 without this, that SSE message arrives before
    // playback is confirmed and immediately flips the UI back to "paused".
    lastActionAt = Date.now();
    api('/alexa/command/', { serial, action: btn.dataset.action })
      .then((data) => {
        if (data.now_playing) showNowPlaying(data.now_playing);
        isPlaying = true;
        syncPlayPause();
        // New track incoming: hold the bar at 0:00 until PlaybackStarted
        // confirms *this* video_id (not a stale push for the track we just left).
        progress.resetPending(data.now_playing && data.now_playing.video_id);
        // Poll multiple times to catch the track transition
        _lastQueueJson = '';
        setTimeout(pollNowPlaying, 2000);
        setTimeout(pollNowPlaying, 5000);
        setTimeout(pollNowPlaying, 8000);
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
let volTimer;
volumeEl.addEventListener('pointerdown', () => { volumeUserActive = true; });
volumeEl.addEventListener('pointerup', () => { volumeUserActive = false; });
volumeEl.addEventListener('touchend', () => { volumeUserActive = false; });
volumeEl.addEventListener('change', () => { volumeUserActive = false; });
volumeEl.oninput = e => {
  volumeUserActive = true;
  volumeGraceUntil = Date.now() + VOLUME_GRACE_MS;
  clearTimeout(volTimer);
  volTimer = setTimeout(() => {
    const serial = selectedSerial();
    if (!serial) {
      volumeUserActive = false;
      volumeGraceUntil = 0;
      return;
    }
    const value = +e.target.value;
    volumeGraceUntil = Date.now() + VOLUME_GRACE_MS;
    toast('Volume ' + e.target.value + '\u2026');
    api('/alexa/command/', { serial, action: 'volume', value })
      .then(() => {
        volumeUserActive = false;
        volumeGraceUntil = Date.now() + VOLUME_GRACE_MS;
        syncVolume(value, true);
        toast('Volume ' + value, 'ok');
      })
      .catch(err => {
        volumeUserActive = false;
        volumeGraceUntil = 0;      // let server truth restore the slider
        refreshVolume(true);
        toast(err.message, 'error');
      });
  }, 220);
};

/* ---- login gating ---- */
const loginSection = document.getElementById('login-section');
const loginBtn = document.getElementById('login-btn');

let _firstShow = true;
function playStartupReveal() {
  document.body.classList.remove('startup-reveal');
  void document.body.offsetWidth;
  document.body.classList.add('startup-reveal');
  clearTimeout(playStartupReveal._timer);
  playStartupReveal._timer = setTimeout(() => {
    document.body.classList.remove('startup-reveal');
  }, 520);
}

function showControls(loggedIn) {
  loginSection.hidden = loggedIn;
  for (const el of document.querySelectorAll('.needs-login')) el.hidden = !loggedIn;
  _loggedIn = !!loggedIn;
  if (!loggedIn) closeResults();
  // showControls unhides every .needs-login element; the header history button
  // must stay hidden until loadHistory() confirms there's something to show.
  syncHistoryTriggerVisibility();
  if (loggedIn) loadHistory();
  else document.getElementById('recs-section').hidden = true;
  syncUiState();
  
  if (_firstShow && loggedIn) {
    _firstShow = false;
    // Allow the browser to calculate the final layout without transitions,
    // then remove the preload class to enable transitions going forward.
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        document.body.classList.remove('preload');
      });
    });
  }
}

// Populate the device <select> from an array of device objects.
// Shared by initPage() and loadDevices() so both paths produce identical UI.
function _applyDevices(devices, preferSerial) {
  deviceEl.innerHTML = '';
  if (!devices.length) {
    deviceEl.innerHTML = '<option value="">No devices found</option>';
    return false;
  }
  for (const d of devices) {
    const o = document.createElement('option');
    o.value = d.serial;
    o.textContent = d.name + (d.online ? '' : ' (offline)');
    o.dataset.online = d.online ? '1' : '0';
    deviceEl.appendChild(o);
  }
  // Restore previously selected device if still present
  if (preferSerial && [...deviceEl.options].some(o => o.value === preferSerial)) {
    deviceEl.value = preferSerial;
  }
  if (deviceEl.value && !selectedDeviceOnline()) {
    toast('Selected device is offline.', 'error');
  }
  return true;
}

async function refreshAuth() {
  try {
    const s = await api('/alexa/status/');
    if (!s.configured) {
      showControls(false);
      loginBtn.disabled = true;
      toast('Server missing PUBLIC_BASE_URL config.', 'error');
      return false;
    }
    showControls(!!s.logged_in);
    if (s.logged_in) loadDevices(false);
    else toast('Not connected to Amazon.', 'error');
    return !!s.logged_in;
  } catch (e) {
    toast(e.message, 'error');
    return false;
  }
}

// Fast startup: one request returns status + devices + now-playing so the
// UI renders immediately on page load instead of after 3 sequential round-trips.
async function initPage() {
  // Restore last-used serial from localStorage so we can ask the server for
  // that device's now-playing state right away (avoids an extra SSE wait).
  const savedSerial = localStorage.getItem('selectedSerial') || '';
  try {
    const data = await api('/alexa/init/' + (savedSerial ? '?serial=' + encodeURIComponent(savedSerial) : ''));
    const s = data.status || {};
    if (!s.configured) {
      showControls(false);
      loginBtn.disabled = true;
      toast('Server missing PUBLIC_BASE_URL config.', 'error');
      return;
    }
    showControls(!!s.logged_in);
    if (!s.logged_in) { toast('Not connected to Amazon.', 'error'); return; }

    // Populate devices
    const ok = _applyDevices(data.devices || [], savedSerial || data.serial);
    if (!ok) { toast('No compatible devices found.', 'error'); return; }

    // Immediately render now-playing if we got it — no waiting for SSE
    if (data.now_playing) {
      handleNpUpdate(data.now_playing);
      requestAnimationFrame(playStartupReveal);
    }

    // Open SSE stream (will push live updates going forward)
    connectSSE();
    refreshVolume(true);
  } catch (e) {
    // Fall back to the original sequential approach on any error
    toast(e.message, 'error');
    refreshAuth();
  }
}


let loginPoll;
async function startProxyLogin(email, password, force) {
  const { login_url } = await api('/alexa/proxy_login/', { email, password, force: !!force });
  document.getElementById('login-password').value = '';
  window.open(login_url, '_blank', 'noopener');
  toast('Complete login in the new tab\u2026');
  clearInterval(loginPoll);
  loginPoll = setInterval(async () => {
    try {
      const c = await api('/alexa/proxy_check/');
      if (c.logged_in) {
        clearInterval(loginPoll);
        toast('Connected', 'ok');
        loginBtn.disabled = false;
        showControls(true);
        loadDevices(false);
      }
    } catch (_) {}
  }, 2500);
}

loginBtn.onclick = async () => {
  const email = document.getElementById('login-email').value.trim();
  const password = document.getElementById('login-password').value;
  if (!email || !password) { toast('Enter email and password.', 'error'); return; }
  loginBtn.disabled = true;
  toast('Opening Amazon login\u2026');
  try {
    await startProxyLogin(email, password, false);
  } catch (e) {
    // Server refuses to swap the one shared Amazon session without
    // confirmation (see alexa_proxy_login's 409). Ask before overwriting it.
    if (e.message.includes('force') && confirm(e.message + '\n\nContinue and replace the current session?')) {
      try {
        await startProxyLogin(email, password, true);
      } catch (e2) {
        toast(e2.message, 'error');
        loginBtn.disabled = false;
      }
      return;
    }
    toast(e.message, 'error');
    loginBtn.disabled = false;
  }
};

initPage();

/* ---- queue display ---- */
// Treats a press as a tap only if the pointer barely moved; otherwise the
// gesture was a scroll drag and must not trigger playback (fixes taps
// firing on queue items when scrolling starts right on top of one).
function attachQueueItemTap(el, onTap) {
  const DRAG_THRESHOLD = 10;
  let startX = 0, startY = 0, dragged = false;
  el.addEventListener('pointerdown', (e) => {
    startX = e.clientX;
    startY = e.clientY;
    dragged = false;
  });
  el.addEventListener('pointermove', (e) => {
    if (Math.abs(e.clientX - startX) > DRAG_THRESHOLD || Math.abs(e.clientY - startY) > DRAG_THRESHOLD) {
      dragged = true;
    }
  });
  el.addEventListener('click', () => {
    if (el._swipeSuppressClick) {
      el._swipeSuppressClick = false;
      return;
    }
    if (!dragged) onTap();
  });
}

function showQueue(queue, currentIndex) {
  const section = document.getElementById('queue-section');
  const list = document.getElementById('queue-list');
  const mainEl = document.querySelector('main');
  if (_resultsOpen) {
    section.classList.remove('is-visible');
    section.hidden = true;
    mainEl.classList.remove('has-queue');
    return;
  }
  if (!queue || queue.length <= 1) {
    section.classList.remove('is-visible');
    clearTimeout(section._hideTimer);
    section._hideTimer = setTimeout(() => {
      section.hidden = true;
      mainEl.classList.remove('has-queue');
    }, 300);
    return;
  }
  clearTimeout(section._hideTimer);
  section.hidden = false;
  mainEl.classList.add('has-queue');
  requestAnimationFrame(() => section.classList.add('is-visible'));
  list.innerHTML = '';
  _closeAllQueueMenus();
  queue.forEach((item, i) => {
    const wrapper = document.createElement('div');
    wrapper.className = 'queue-swipe-wrapper';
    wrapper.dataset.index = String(i);

    // Swipe-to-delete underlay (mobile, hidden on desktop via CSS)
    wrapper.innerHTML = `
      <div class="queue-delete-underlay">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <path d="M3 6h18"/><path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/>
          <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
        </svg>
        Remove
      </div>
      <div class="queue-like-underlay">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>
        Like
      </div>
    `;

    const el = document.createElement('div');
    el.className = 'queue-item' + (i === currentIndex ? ' active' : '');
    el.dataset.index = String(i);

    const thumbUrl = item.thumbnail || '';
    const thumbHtml = thumbUrl
      ? `<img class="queue-thumb" src="${escHtml(thumbUrl)}" alt="" loading="lazy" onload="this.classList.add('loaded')">`
      : `<div class="queue-thumb"></div>`;

    const dragSvg = `<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="9" cy="5" r="1.5"/><circle cx="15" cy="5" r="1.5"/><circle cx="9" cy="10" r="1.5"/><circle cx="15" cy="10" r="1.5"/><circle cx="9" cy="15" r="1.5"/><circle cx="15" cy="15" r="1.5"/><circle cx="9" cy="20" r="1.5"/><circle cx="15" cy="20" r="1.5"/></svg>`;
    const moreSvg = `<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>`;

    const isLiked = typeof _playlistsData !== 'undefined' && _playlistsData.liked_songs && _playlistsData.liked_songs.includes(item.video_id);
    const likeSvg = isLiked
      ? '<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>'
      : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>';
    const likeText = isLiked ? "Dislike" : "Like";
    const likeClass = isLiked ? "queue-menu-option liked" : "queue-menu-option";

    el.innerHTML = `
      <div class="queue-drag-handle" title="Drag to reorder">${dragSvg}</div>
      <span class="queue-num">${i + 1}</span>
      ${thumbHtml}
      <div class="queue-info">
        <div class="queue-title">${escHtml(item.title)}</div>
        <div class="queue-artist">${escHtml(item.artist)}</div>
      </div>
      <button class="queue-more-btn" type="button" title="More options">${moreSvg}</button>
      <div class="queue-more-menu">
        <div class="${likeClass}" data-action="like">
          ${likeSvg}
          ${likeText}
        </div>
        <div class="queue-menu-option danger" data-action="remove">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <path d="M3 6h18"/><path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/>
            <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
          </svg>
          Remove from queue
        </div>
      </div>
    `;

    wrapper.appendChild(el);

    // Tap on the item → play from queue
    attachQueueItemTap(el, () => playFromQueue(item));

    // Desktop: 3-dot more-menu
    const moreBtn = el.querySelector('.queue-more-btn');
    const moreMenu = el.querySelector('.queue-more-menu');
    // Prevent document click handler from closing menu when clicking inside it
    moreMenu.addEventListener('click', (e) => e.stopPropagation());
    moreBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const wasOpen = moreMenu.classList.contains('open');
      _closeAllQueueMenus();
      if (!wasOpen) {
        moreBtn.classList.add('open');
        // Position the menu using fixed coords so it escapes any scrollable parent
        const rect = moreBtn.getBoundingClientRect();
        const menuHeight = 48; // approximate height of one option row
        const spaceBelow = window.innerHeight - rect.bottom;
        const openAbove = spaceBelow < menuHeight + 8;
        moreMenu.style.left = '';
        moreMenu.style.top = '';
        moreMenu.style.bottom = '';
        moreMenu.style.right = '';
        if (openAbove) {
          moreMenu.classList.add('above');
          moreMenu.style.bottom = (window.innerHeight - rect.top + 4) + 'px';
        } else {
          moreMenu.classList.remove('above');
          moreMenu.style.top = (rect.bottom + 4) + 'px';
        }
        // Align right edge of menu with right edge of button
        const menuWidth = 170;
        let left = rect.right - menuWidth;
        if (left < 8) left = 8; // don't go off screen left
        moreMenu.style.left = left + 'px';
        moreMenu.classList.add('open');
        // Portal the menu to <body> while open. Inside the row it sits under
        // an overflow-hidden wrapper within a scrollable list, and Chromium's
        // input hit-testing clips fixed elements there — the menu is visible
        // but clicks land on the row below it. _closeAllQueueMenus returns it.
        moreMenu._home = el;
        document.body.appendChild(moreMenu);
      }
    });
    moreMenu.querySelector('[data-action="remove"]').addEventListener('click', (e) => {
      e.stopPropagation();
      _closeAllQueueMenus();
      removeFromQueue(i, item.title, item.video_id);
    });
    const likeBtn = moreMenu.querySelector('[data-action="like"]');
    likeBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      _closeAllQueueMenus();
      if (typeof toggleLike === 'function') {
        await toggleLike(item);
        const isLikedNow = typeof _playlistsData !== 'undefined' && _playlistsData.liked_songs && _playlistsData.liked_songs.includes(item.video_id);
        const likeSvgNow = isLikedNow 
          ? '<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>'
          : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>';
        likeBtn.innerHTML = `\n          ${likeSvgNow}\n          ${isLikedNow ? "Dislike" : "Like"}\n        `;
        if (isLikedNow) likeBtn.classList.add('liked');
        else likeBtn.classList.remove('liked');
      }
    });

    // Mobile: swipe gestures (like/delete)
    _attachQueueSwipeGestures(wrapper, el, i, item, currentIndex);

    // Drag-to-reorder (both mobile + desktop via the handle)
    _attachQueueDragReorder(el, list, i);

    list.appendChild(wrapper);
  });
  const active = list.querySelector('.active');
  if (active) active.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

function _closeAllQueueMenus() {
  for (const m of document.querySelectorAll('.queue-more-menu.open')) {
    m.classList.remove('open');
    m.style.top = '';
    m.style.bottom = '';
    m.style.left = '';
    m.style.right = '';
    // Return a body-portaled menu to its row (see the open handler).
    if (m._home && m.parentElement !== m._home) m._home.appendChild(m);
  }
  for (const b of document.querySelectorAll('.queue-more-btn.open')) b.classList.remove('open');
  for (const w of document.querySelectorAll('.queue-swipe-wrapper.menu-open')) w.classList.remove('menu-open');
}
document.addEventListener('click', _closeAllQueueMenus);

function updateQueueActive(currentIndex) {
  const list = document.getElementById('queue-list');
  for (const el of list.querySelectorAll('.queue-item')) {
    el.classList.toggle('active', Number(el.dataset.index) === currentIndex);
  }
}

/* ---- Remove from queue ---- */
// Resolve a queue item's index in the CURRENT queue snapshot. Rows capture
// their index at render time, but the queue may have shifted since (another
// removal, a reorder, an SSE re-render) \u2014 deleting by the stale index would
// remove whatever song *now* sits in that slot. Prefers the remembered index
// when it still matches (handles duplicate songs), otherwise searches by id.
// Returns -1 when the item is gone.
function _liveQueueIndexOf(item, guessIndex) {
  try {
    const queue = JSON.parse(_lastQueueJson || '[]');
    if (queue[guessIndex] && queue[guessIndex].video_id === item.video_id) return guessIndex;
    return queue.findIndex((q) => q && q.video_id === item.video_id);
  } catch (_) { return guessIndex; }
}

async function removeFromQueue(index, title, videoId) {
  // Re-verify by identity right before acting: the index may have gone stale
  // between the gesture and this call (e.g. during the swipe-out animation).
  try {
    const queue = JSON.parse(_lastQueueJson || '[]');
    if (videoId && (!queue[index] || queue[index].video_id !== videoId)) {
      index = queue.findIndex((q) => q && q.video_id === videoId);
      if (index === -1) {
        // Already gone (removed from another view/tab): just resync.
        _lastQueueJson = '';
        setTimeout(pollNowPlaying, 300);
        return;
      }
    }
    // Optimistically drop the row locally so it vanishes right away instead
    // of reappearing until the server confirms. On error the poll below
    // restores the true queue. Mark the id pending so a stale SSE snapshot
    // (generated before the server processed this removal) can't resurrect
    // the row and shift the numbering of a follow-up delete.
    if (index >= 0 && index < queue.length) {
      _markPendingRemoval(videoId || (queue[index] && queue[index].video_id));
      queue.splice(index, 1);
      let currentIdx = _lastQueueIndex;
      if (currentIdx > index) currentIdx--;
      _lastQueueJson = JSON.stringify(queue);
      _lastQueueIndex = currentIdx;
      showQueue(queue, currentIdx);
      refreshQueueModalIfOpen();
    }
  } catch (_) {}
  try {
    // Send the video_id too so the server can refuse if its queue has moved
    // on and the index no longer points at this song.
    await api('/alexa/queue_remove/', videoId ? { index, video_id: videoId } : { index });
    toast('Removed \u201c' + (title || 'track') + '\u201d from queue', 'ok');
    // Keep the optimistic snapshot as-is: blanking _lastQueueJson here made
    // a rapid follow-up delete resolve its index against an empty queue and
    // silently no-op. The next SSE push / poll confirms the true state (with
    // the pending filter suppressing any stale copy of this song).
    setTimeout(pollNowPlaying, 300);
  } catch (e) {
    toast(e.message, 'error');
    // Revert the optimistic removal: let the song show again and force a
    // refresh from the server.
    _unmarkPendingRemoval(videoId);
    _lastQueueJson = '';
    setTimeout(pollNowPlaying, 300);
  }
}

/* ---- Reorder queue (drag complete) ---- */
async function reorderQueue(fromIndex, toIndex) {
  if (fromIndex === toIndex) return;
  // Optimistically reorder local queue data so the UI doesn't snap back
  // to the old position while waiting for the server to confirm.
  try {
    const queue = JSON.parse(_lastQueueJson || '[]');
    let currentIdx = _lastQueueIndex;
    if (fromIndex >= 0 && fromIndex < queue.length) {
      const [moved] = queue.splice(fromIndex, 1);
      queue.splice(toIndex, 0, moved);
      // Adjust the active index to follow the reorder
      if (currentIdx === fromIndex) {
        currentIdx = toIndex;
      } else {
        if (fromIndex < currentIdx && toIndex >= currentIdx) currentIdx--;
        else if (fromIndex > currentIdx && toIndex <= currentIdx) currentIdx++;
      }
      _lastQueueJson = JSON.stringify(queue);
      _lastQueueIndex = currentIdx;
      // Re-render immediately with the optimistic order
      showQueue(queue, currentIdx);
      refreshQueueModalIfOpen();
    }
  } catch (_) {}
  try {
    await api('/alexa/queue_reorder/', { from_index: fromIndex, to_index: toIndex });
    // Confirm with server data after a short delay
    _lastQueueJson = '';
    setTimeout(pollNowPlaying, 500);
  } catch (e) {
    // Revert on error: force refresh from server
    _lastQueueJson = '';
    setTimeout(pollNowPlaying, 300);
    toast(e.message, 'error');
  }
}

/* ---- Queue swipe gestures (mobile) ---- */
function _attachQueueSwipeGestures(wrapper, el, index, item, currentIndex) {
  const SWIPE_THRESHOLD = 80;
  const LOCK_DISTANCE = 8;
  const AXIS_BIAS = 1.2;
  const HOLD_DURATION = 50;
  let startX = 0, startY = 0, currentX = 0, gesture = 'pending';
  let holdTimer = null, holdReady = false;

  function resetSwipeState() {
    // Never undo the slide-out/collapse of a row whose removal is committed.
    if (wrapper._removing) return;
    clearTimeout(holdTimer);
    el.classList.remove('hold-selected');
    wrapper.classList.remove('swiping-left', 'swiping-right');
    el.style.transition = '';
    el.style.transform = '';
    wrapper.style.transition = '';
    wrapper.style.height = '';
    wrapper.style.opacity = '';
    gesture = 'idle';
    holdReady = false;
    currentX = 0;
  }

  el.addEventListener('touchstart', (e) => {
    if (wrapper._removing) return;
    if (e.target.closest('.queue-drag-handle') || e.touches.length !== 1) return;
    startX = e.touches[0].clientX;
    startY = e.touches[0].clientY;
    currentX = 0;
    gesture = 'pending';
    holdReady = false;
    clearTimeout(holdTimer);
    holdTimer = setTimeout(() => {
      if (gesture === 'scroll') return;
      holdReady = true;
      el.style.transition = 'none';
    }, HOLD_DURATION);
  }, { passive: true });

  el.addEventListener('touchmove', (e) => {
    if (e.target.closest('.queue-drag-handle') || e.touches.length !== 1) return;
    const dx = e.touches[0].clientX - startX;
    const dy = e.touches[0].clientY - startY;
    const absX = Math.abs(dx);
    const absY = Math.abs(dy);

    if (gesture === 'pending' && Math.max(absX, absY) >= LOCK_DISTANCE) {
      if (absY > absX * AXIS_BIAS) {
        clearTimeout(holdTimer);
        gesture = 'scroll';
        return;
      }
      if (absX > absY * AXIS_BIAS) gesture = 'swipe';
    }

    if (gesture !== 'swipe' || !holdReady) return;
    e.preventDefault();
    currentX = dx;
    if (currentX > 0) {
      wrapper.classList.add('swiping-right');
      wrapper.classList.remove('swiping-left');
    } else {
      wrapper.classList.add('swiping-left');
      wrapper.classList.remove('swiping-right');
    }
    el.style.transform = 'translateX(' + currentX + 'px)';
  }, { passive: false });

  el.addEventListener('touchend', () => {
    clearTimeout(holdTimer);
    if (gesture !== 'swipe' || !holdReady) {
      resetSwipeState();
      return;
    }
    el._swipeSuppressClick = true;
    const liveIdx = _liveQueueIndexOf(item, index);
    
    // Left swipe = delete
    const committedDelete = currentX < -SWIPE_THRESHOLD && !wrapper._removing;
    // Right swipe = like
    const committedLike = currentX > SWIPE_THRESHOLD && !wrapper._removing;

    if (committedDelete && liveIdx !== -1 && liveIdx !== _lastQueueIndex) {
      wrapper._removing = true;
      el.style.transition = 'transform .15s ease-out';
      el.style.transform = 'translateX(-105%)';
      wrapper.style.height = wrapper.offsetHeight + 'px';
      void wrapper.offsetHeight; 
      wrapper.style.transition = 'height .18s ease .12s, opacity .18s ease .12s';
      wrapper.style.height = '0px';
      wrapper.style.opacity = '0';
      setTimeout(() => removeFromQueue(liveIdx, item.title, item.video_id), 320);
    } else {
      if (committedDelete) {
        if (liveIdx === -1) {
          _lastQueueJson = '';
          setTimeout(pollNowPlaying, 300);
        } else {
          toast('Can’t remove the playing track', 'error');
        }
      } else if (committedLike) {
        if (typeof toggleLike === 'function') toggleLike(item);
      }
      
      el.style.transition = 'transform .25s cubic-bezier(.22,1,.36,1)';
      el.style.transform = '';
      setTimeout(() => wrapper.classList.remove('swiping-left', 'swiping-right'), 260);
    }
    gesture = 'idle';
    holdReady = false;
    currentX = 0;
  }, { passive: true });

  el.addEventListener('touchcancel', resetSwipeState, { passive: true });
}

/* ---- Queue drag-to-reorder ---- */
function _attachQueueDragReorder(el, listEl, originalIndex) {
  const handle = el.querySelector('.queue-drag-handle');
  if (!handle) return;

  let dragging = false, startY = 0, initialTop = 0, cloneEl = null, placeholder = null;
  let currentOver = -1, fromIdx = originalIndex;

  // Auto-scroll state
  let _scrollRafId = null;
  let _scrollSpeed = 0;
  let _scrollContainer = null;
  const EDGE_ZONE = 50;   // px from container edge to trigger scroll
  const MAX_SPEED = 12;   // px per frame at the very edge

  function getItemElements() {
    return Array.from(listEl.querySelectorAll('.queue-swipe-wrapper'));
  }

  // Find the nearest scrollable ancestor of the list
  function findScrollContainer() {
    let node = listEl;
    while (node && node !== document.body) {
      const style = getComputedStyle(node);
      if ((style.overflowY === 'auto' || style.overflowY === 'scroll') &&
          node.scrollHeight > node.clientHeight) {
        return node;
      }
      node = node.parentElement;
    }
    // If the listEl itself is scrollable
    if (listEl.scrollHeight > listEl.clientHeight) return listEl;
    return null;
  }

  function startAutoScroll() {
    if (_scrollRafId) return;
    function tick() {
      if (!dragging || !_scrollContainer || _scrollSpeed === 0) {
        _scrollRafId = null;
        return;
      }
      _scrollContainer.scrollTop += _scrollSpeed;
      _scrollRafId = requestAnimationFrame(tick);
    }
    _scrollRafId = requestAnimationFrame(tick);
  }

  function stopAutoScroll() {
    if (_scrollRafId) {
      cancelAnimationFrame(_scrollRafId);
      _scrollRafId = null;
    }
    _scrollSpeed = 0;
  }

  function updateAutoScroll(clientY) {
    if (!_scrollContainer) return;
    const rect = _scrollContainer.getBoundingClientRect();
    const distFromTop = clientY - rect.top;
    const distFromBottom = rect.bottom - clientY;

    if (distFromTop < EDGE_ZONE && _scrollContainer.scrollTop > 0) {
      // Scroll up — speed increases as pointer gets closer to edge
      const ratio = 1 - (distFromTop / EDGE_ZONE);
      _scrollSpeed = -(MAX_SPEED * Math.max(0, Math.min(1, ratio)));
      startAutoScroll();
    } else if (distFromBottom < EDGE_ZONE &&
               _scrollContainer.scrollTop < _scrollContainer.scrollHeight - _scrollContainer.clientHeight) {
      // Scroll down
      const ratio = 1 - (distFromBottom / EDGE_ZONE);
      _scrollSpeed = MAX_SPEED * Math.max(0, Math.min(1, ratio));
      startAutoScroll();
    } else {
      _scrollSpeed = 0;
    }
  }

  function beginDrag(clientY) {
    dragging = true;
    document.body.classList.add('drag-lock');
    startY = clientY;
    fromIdx = originalIndex;
    const rect = el.getBoundingClientRect();
    initialTop = rect.top;

    // Create a clone to show as the dragged element
    cloneEl = el.cloneNode(true);
    cloneEl.style.position = 'fixed';
    cloneEl.style.left = rect.left + 'px';
    cloneEl.style.top = rect.top + 'px';
    cloneEl.style.width = rect.width + 'px';
    cloneEl.style.zIndex = '1000';
    cloneEl.style.pointerEvents = 'none';
    cloneEl.style.opacity = '.85';
    cloneEl.style.boxShadow = '0 8px 32px rgba(0,0,0,.5)';
    cloneEl.style.background = 'var(--surface)';
    document.body.appendChild(cloneEl);

    el.classList.add('dragging');
    currentOver = fromIdx;
    _scrollContainer = findScrollContainer();
  }

  function moveDrag(clientY) {
    if (!dragging || !cloneEl) return;
    const dy = clientY - startY;
    cloneEl.style.top = (initialTop + dy) + 'px';

    // Auto-scroll when near the edges of the scrollable container
    updateAutoScroll(clientY);

    // Find which item we're over
    const items = getItemElements();
    let targetIdx = fromIdx;
    for (let i = 0; i < items.length; i++) {
      const r = items[i].getBoundingClientRect();
      const mid = r.top + r.height / 2;
      if (clientY < mid) { targetIdx = i; break; }
      targetIdx = i + 1;
    }
    targetIdx = Math.min(targetIdx, items.length - 1);
    if (targetIdx !== currentOver) {
      // Remove old placeholder
      const old = listEl.querySelector('.queue-drop-placeholder');
      if (old) old.remove();
      // Insert placeholder
      placeholder = document.createElement('div');
      placeholder.className = 'queue-drop-placeholder';
      if (targetIdx < items.length) {
        listEl.insertBefore(placeholder, items[targetIdx]);
      } else {
        listEl.appendChild(placeholder);
      }
      currentOver = targetIdx;
    }
  }

  function endDrag() {
    if (!dragging) return;
    dragging = false;
    document.body.classList.remove('drag-lock');
    stopAutoScroll();
    _scrollContainer = null;
    el.classList.remove('dragging');
    if (cloneEl) { cloneEl.remove(); cloneEl = null; }
    if (placeholder) { placeholder.remove(); placeholder = null; }

    // currentOver is an insertion index computed with the dragged item still
    // occupying its old slot. The server (and the optimistic splice) remove
    // the item first, shifting everything below it up by one — so a downward
    // move must drop the index by 1 or the item lands one slot too low.
    let toIdx = currentOver;
    if (toIdx > fromIdx) toIdx -= 1;
    toIdx = Math.max(0, Math.min(toIdx, getItemElements().length - 1));
    if (toIdx !== fromIdx) {
      reorderQueue(fromIdx, toIdx);
    }
    currentOver = -1;
  }

  // Pointer events for both mouse and touch (via the drag handle)
  handle.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    e.stopPropagation();
    handle.setPointerCapture(e.pointerId);
    beginDrag(e.clientY);
  });
  handle.addEventListener('pointermove', (e) => {
    if (dragging) {
      e.preventDefault();
      e.stopPropagation();
      moveDrag(e.clientY);
    }
  });
  handle.addEventListener('pointerup', (e) => {
    if (dragging) {
      e.preventDefault();
      e.stopPropagation();
      try { handle.releasePointerCapture(e.pointerId); } catch (_) {}
      endDrag();
    }
  });
  handle.addEventListener('pointercancel', () => {
    if (dragging) endDrag();
  });
  handle.addEventListener('lostpointercapture', () => {
    if (dragging) endDrag();
  });
  window.addEventListener('pointerup', () => {
    if (dragging) endDrag();
  });
}

function escHtml(s) {
  // Also escape quotes: this helper is used inside attribute values
  // (e.g. src="${escHtml(item.thumbnail)}"), where innerHTML-based escaping
  // would let a stray double quote break out of the attribute.
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

async function playFromQueue(item) {
  const serial = selectedSerial();
  if (!serial) return;
  lastActionAt = Date.now();
  if (!item.video_id) { toast('That recommendation cannot be played.', 'error'); return; }
  toast('Playing \u201c' + item.title + '\u201d\u2026');
  try {
    // Pass along metadata so a song that isn't in the server's queue yet (e.g.
    // a recommendation tile) plays from the supplied title/artist/thumbnail
    // instead of a metadata lookup that can fail ("no longer available").
    const data = await api('/alexa/play_queue/', {
      serial,
      video_id: item.video_id,
      title: item.title || '',
      artist: item.artist || '',
      thumbnail: (item.thumbnail && item.thumbnail.url) || item.thumbnail || '',
      duration_ms: item.duration_ms || 0,
    });
    const npInfo = { video_id: item.video_id, title: item.title, artist: item.artist, thumbnail: item.thumbnail };
    showNowPlaying(npInfo);
    progress.resetPending(item.video_id);
    isPlaying = true;
    syncPlayPause();
    toast('Playing', 'ok');
    setTimeout(pollNowPlaying, 3000);
    // Optimistically prepend this song to history right away so "Recently
    // Played" shows it immediately without waiting for the server webhook.
    const optimisticEntry = {
      video_id: item.video_id,
      title: item.title || '',
      artist: item.artist || '',
      thumbnail_url: (item.thumbnail && item.thumbnail.url) || item.thumbnail || '',
    };
    _historyCache = [optimisticEntry, ..._historyCache.filter(e => e.video_id !== item.video_id)].slice(0, 20);
    syncHistoryTriggerVisibility();
    // If the modal is open, prepend the new row with a slide-in animation.
    const histOverlay = document.getElementById('history-modal-overlay');
    if (histOverlay.classList.contains('open')) {
      const list = histOverlay.querySelector('.history-list');
      if (list) {
        // Remove stale entry for same song if present
        list.querySelectorAll('.history-item').forEach(el => {
          if (el.dataset.videoId === item.video_id) el.remove();
        });
        const row = _buildHistoryRow(optimisticEntry);
        row.classList.add('history-item-new');
        row.dataset.videoId = item.video_id;
        list.prepend(row);
        // Add divider below new item if there's a sibling
        const next = row.nextElementSibling;
        if (next) next.style.borderTop = '1px solid var(--border)';
      } else {
        renderHistoryModalList(_historyCache);
      }
    }
    // Still schedule server refreshes to pick up proper metadata / dedup
    scheduleHistoryRefresh();
  } catch (e) {
    toast(e.message, 'error');
  }
}

// The 'started' webhook that records a listen can lag a few seconds behind the
// play dispatch, so poll history a few times rather than once.
function scheduleHistoryRefresh() {
  [2500, 5000, 9000].forEach(ms => setTimeout(loadHistory, ms));
}

/* ---- Recently listened ---- */
// Server-side history, recorded when the skill confirms a real playback start.
// It's pre-fetched on load and cached here, and refreshed after each song, so
// the popup (opened from the small header button) shows instantly from cache
// rather than fetching on click.
let _historyCache = [];

async function apiDelete(path) {
  // DELETE with a JSON content-type: the server's CSRF guard rejects any
  // mutating session-cookie request that isn't application/json.
  let res;
  try {
    res = await fetch(path, {
      method: 'DELETE', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: '{}',
    });
  } catch (_) {
    throw new Error('Can’t reach the server. Check your connection and try again.');
  }
  if (res.status === 401) { location.href = '/login/'; throw new Error('Session expired'); }
  const json = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(json.error || ('HTTP ' + res.status));
  return json;
}

async function loadHistory() {
  if (!_loggedIn) return;
  try {
    const history = await api('/history/?limit=20');
    const fresh = Array.isArray(history) ? history.filter(e => e && e.video_id) : [];
    const prevTopId = _historyCache.length ? _historyCache[0].video_id : null;
    const newTopId  = fresh.length ? fresh[0].video_id : null;
    _historyCache = fresh;
    syncHistoryTriggerVisibility();
    // Keep the modal current if it's open.
    const overlay = document.getElementById('history-modal-overlay');
    if (overlay.classList.contains('open')) {
      const isNewTop = newTopId && newTopId !== prevTopId;
      const list = overlay.querySelector('.history-list');
      if (isNewTop && list) {
        // A genuinely new song appeared at the top — prepend it animated and
        // remove any existing row for the same id (avoids duplicates from the
        // optimistic insert above).
        list.querySelectorAll('.history-item').forEach(el => {
          if (el.dataset.videoId === newTopId) el.remove();
        });
        const row = _buildHistoryRow(fresh[0]);
        row.classList.add('history-item-new');
        row.dataset.videoId = newTopId;
        list.prepend(row);
      } else if (!list) {
        // Modal open but no list yet — full render.
        renderHistoryModalList(_historyCache);
      }
      // If only metadata changed (same top), leave the list as-is.
    }
  } catch (e) {
    console.warn('Failed to load history', e);
  }
}

function syncHistoryTriggerVisibility() {
  const show = _loggedIn && _historyCache.length > 0;
  document.getElementById('history-modal-btn').hidden = !show;
}

function _buildHistoryRow(entry) {
  const el = document.createElement('div');
  el.className = 'history-item';

  const thumbHtml = entry.thumbnail_url
    ? `<img class="queue-thumb" src="${escHtml(entry.thumbnail_url)}" alt="" loading="lazy" onload="this.classList.add('loaded')">`
    : `<div class="queue-thumb history-thumb-placeholder">
         <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
           <path d="M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z"/>
         </svg>
       </div>`;

  el.innerHTML = `
    ${thumbHtml}
    <div class="queue-info">
      <div class="queue-title">${escHtml(entry.title || 'Unknown title')}</div>
      <div class="queue-artist">${escHtml(entry.artist)}</div>
    </div>
  `;

  // No per-row remove control by design — Clear (with confirmation) is the
  // only way to modify history from this popup.
  el.addEventListener('click', () => {
    window._closeHistoryModal();
    playFromQueue({
      video_id: entry.video_id,
      title: entry.title || '',
      artist: entry.artist || '',
      thumbnail: entry.thumbnail_url || '',
    });
  });
  return el;
}

function renderHistoryModalList(history) {
  const body = document.getElementById('history-modal-body');
  const clearBtn = document.getElementById('clear-history-btn');
  const items = Array.isArray(history) ? history.filter(e => e && e.video_id) : [];

  clearBtn.hidden = items.length === 0;
  if (items.length === 0) {
    body.innerHTML = '<div class="history-modal-empty">No listening history yet</div>';
    return;
  }
  const list = document.createElement('div');
  list.className = 'history-list';
  for (const entry of items) list.appendChild(_buildHistoryRow(entry));
  body.innerHTML = '';
  body.appendChild(list);
}

async function doClearHistory() {
  try {
    await apiDelete('/history/');
    _historyCache = [];
    renderHistoryModalList([]);
    syncHistoryTriggerVisibility();
    window._closeHistoryModal();
    toast('History cleared', 'ok');
  } catch (e) {
    toast(e.message, 'error');
  }
}

(function () {
  const overlay = document.getElementById('history-modal-overlay');
  const closeBtn = document.getElementById('history-modal-close');
  const openBtn = document.getElementById('history-modal-btn');

  function openHistoryModal() {
    // Render immediately from the pre-fetched cache — no fetch-on-click wait.
    renderHistoryModalList(_historyCache);
    overlay.classList.add('open');
  }

  function closeHistoryModal() {
    overlay.classList.remove('open');
  }

  openBtn.addEventListener('click', openHistoryModal);
  closeBtn.addEventListener('click', closeHistoryModal);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) closeHistoryModal(); });

  window._closeHistoryModal = closeHistoryModal;
})();

(function () {
  const overlay = document.getElementById('confirm-clear-history');
  const cancelBtn = document.getElementById('confirm-clear-history-cancel');
  const yesBtn = document.getElementById('confirm-clear-history-yes');
  document.getElementById('clear-history-btn').addEventListener('click', () => {
    overlay.classList.add('open');
  });
  cancelBtn.addEventListener('click', () => overlay.classList.remove('open'));
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.classList.remove('open'); });
  yesBtn.addEventListener('click', () => { overlay.classList.remove('open'); doClearHistory(); });
})();

/* ---- Recommendations (blank-state, mixed history + discovery) ---- */
let _recsLoaded = false;
let _recsLoading = false;   // guards re-entrancy: syncUiState can fire several
                             // times in quick succession (SSE, np updates)
                             // before the first fetch resolves.
let _recsItems = null;      // cached for the session so returning to the blank
                             // state re-shows the same set instantly.

function showRecsSkeleton(show) {
  const skeleton = document.getElementById('recs-skeleton');
  if (show) {
    const cols = recsColumns();
    const rows = recsRows();
    const tile = `<div class="recs-skeleton-tile"><div class="skeleton-block"></div><div class="skeleton-line skeleton-line-title"></div><div class="skeleton-line skeleton-line-artist"></div></div>`;
    skeleton.innerHTML = tile.repeat(cols * rows);
  }
  skeleton.hidden = !show;
  document.getElementById('recs-list').hidden = show;
}

async function loadRecommendations() {
  if (!_loggedIn || _recsLoaded || _recsLoading) return;
  _recsLoading = true;
  const section = document.getElementById('recs-section');
  section.hidden = !(!_hasTrack && !_resultsOpen);
  showRecsSkeleton(true);
  try {
    // refresh=1 so each visit rebuilds from current history (a fresh mix) and
    // never serves a stale cached list from an earlier fallback.
    const items = await api('/recommendations/?refresh=1');
    _recsLoaded = true;
    _recsItems = Array.isArray(items) ? items : [];
    // renderRecommendations keeps the skeleton up until the thumbnails have
    // loaded, then swaps to the real grid — so no empty flash in between.
    renderRecommendations(_recsItems);
  } catch (e) {
    console.warn('Failed to load recommendations', e);
    section.hidden = true;
    showRecsSkeleton(false);
  } finally {
    _recsLoading = false;
  }
}

// Mobile: always 3 columns. Desktop: fill width with tiles ≥132px.
function recsColumns() {
  const wide = window.matchMedia('(min-width: 900px)').matches;
  if (!wide) return 3;
  const gap = 18, pad = 80;
  const avail = Math.max(0, window.innerWidth - pad);
  return Math.max(2, Math.floor((avail + gap) / (132 + gap)));
}

// How many rows fit in the visible area (including footer) without scrolling.
function recsRows() {
  const wide = window.matchMedia('(min-width: 900px)').matches;
  const cols = recsColumns();
  const hGap = wide ? 18 : 10;
  const pad  = wide ? 80 : 24;
  const vGap = wide ? 22 : 10;
  const tileW = Math.floor((window.innerWidth - pad - hGap * (cols - 1)) / cols);
  // tile height = square art + title + artist + internal gap (~40px desktop, ~34px mobile)
  const tileH = tileW + (wide ? 40 : 34);
  // overhead: header + idle-hero + search bar + section label + footer
  const overhead = wide ? 320 : 280;
  const avail = Math.max(tileH, window.innerHeight - overhead);
  return Math.max(2, Math.floor((avail + vGap) / (tileH + vGap)));
}

function renderRecommendations(items) {
  const section = document.getElementById('recs-section');
  const list = document.getElementById('recs-list');
  const shouldShow = !_hasTrack && !_resultsOpen && Array.isArray(items) && items.length > 0;
  section.hidden = !shouldShow;
  if (!shouldShow) { list.innerHTML = ''; return; }
  list.innerHTML = '';
  const cols = recsColumns();
  const rows = recsRows();
  _recsShownCols = cols;
  list.style.setProperty('--recs-cols', cols);
  const shown = items.filter(it => it && it.video_id).slice(0, cols * rows);
  const tiles = [];
  for (const item of shown) {
    if (!item || !item.video_id) continue;
    const thumbUrl = (item.thumbnail && item.thumbnail.url) || item.thumbnail || '';
    const el = document.createElement('div');
    // Each tile starts hidden and reveals only once its own thumbnail has
    // finished loading (or failed), so no tile ever flashes as an empty/
    // half-loaded box. .is-ready is added per-tile below.
    el.className = 'recs-tile';
    const thumbHtml = thumbUrl
      ? `<img src="${escHtml(thumbUrl)}" alt="" loading="eager" onload="this.classList.add('loaded')">`
      : `<svg width="28" height="28" viewBox="0 0 24 24" fill="currentColor">
           <path d="M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z"/>
         </svg>`;
    el.innerHTML = `
      <div class="recs-tile-art">${thumbHtml}</div>
      <div class="recs-tile-title">${escHtml(item.title || '')}</div>
      <div class="recs-tile-artist">${escHtml(item.artist || '')}</div>
    `;
    el.addEventListener('click', () => playFromQueue({
      video_id: item.video_id,
      title: item.title || '',
      artist: item.artist || '',
      thumbnail: thumbUrl,
    }));
    list.appendChild(el);
    tiles.push({ el, thumbUrl });
  }

  // Make the (still-empty-looking, tiles hidden) grid live so its <img>s start
  // fetching; the skeleton stays on top until enough thumbnails are ready.
  document.getElementById('recs-skeleton').hidden = false;
  list.hidden = false;

  let readyCount = 0;
  let skeletonHidden = false;
  const hideSkeletonOnce = () => {
    if (skeletonHidden) return;
    skeletonHidden = true;
    document.getElementById('recs-skeleton').hidden = true;
  };

  // Reveal the art once its thumbnail is decoded. Text is already visible.
  const revealTile = (t, i) => {
    if (t.el.dataset.ready) return;
    t.el.dataset.ready = '1';
    const art = t.el.querySelector('.recs-tile-art');
    if (art) art.style.transitionDelay = Math.min(i * 25, 400) + 'ms';
    t.el.classList.add('is-ready');
    readyCount++;
    if (readyCount >= Math.max(1, tiles.length - 2)) hideSkeletonOnce();
  };
  tiles.forEach((t, i) => {
    const img = t.el.querySelector('img');
    if (!img) { revealTile(t, i); return; }   // placeholder tile, no image
    const done = () => revealTile(t, i);
    if (img.complete && img.naturalWidth > 0) { done(); return; }  // cached
    img.addEventListener('load', done, { once: true });
    img.addEventListener('error', done, { once: true });
  });
  // Safety nets: never leave the skeleton up forever, and reveal any tiles
  // still waiting on slow/hung images.
  setTimeout(hideSkeletonOnce, 2500);
  setTimeout(() => { tiles.forEach((t, i) => revealTile(t, i)); hideSkeletonOnce(); }, 5000);
}

// Re-flow the 2-row grid when the column count changes on resize (e.g. window
// resized, orientation change) so it always fills the width in exactly 2 rows.
let _recsResizeTimer = null;
let _recsShownCols = 0;
window.addEventListener('resize', () => {
  clearTimeout(_recsResizeTimer);
  _recsResizeTimer = setTimeout(() => {
    if (!_recsLoaded || !_recsItems) return;
    if (document.getElementById('recs-section').hidden) return;
    if (recsColumns() === _recsShownCols) return;   // no column change
    renderRecommendations(_recsItems);
  }, 200);
});

/* ---- Open on YouTube Music ---- */
function updateUrlBar() {
  const ytmBtn = document.getElementById('np-url-toggle');
  const mpYtmBtn = document.getElementById('mp-url-toggle');
  if (_currentVideoId) {
    const url = 'https://music.youtube.com/watch?v=' + encodeURIComponent(_currentVideoId);
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
    if (!_currentVideoId) {
      e.preventDefault();
      toast('No song playing.', 'error');
    }
  };
  if (ytmBtn) ytmBtn.addEventListener('click', onClick);
  if (mpYtmBtn) mpYtmBtn.addEventListener('click', onClick);
})();

/* ---- Mobile sidebar ---- */
(function () {
  const hamburger = document.getElementById('hamburger-btn');
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('sidebar-overlay');
  const closeBtn = document.getElementById('sidebar-close');
  const deviceSidebar = document.getElementById('device-sidebar');
  const refreshSidebar = document.getElementById('refresh-sidebar');
  const logoutSidebar = document.getElementById('logout-sidebar');

  // Sidebar custom dropdown elements
  const sbWrapper = document.getElementById('device-sidebar-wrapper');
  const sbTrigger = document.getElementById('device-sidebar-trigger');
  const sbMenu = document.getElementById('device-sidebar-menu');
  const sbLabel = sbTrigger.querySelector('span');

  function syncSidebarDropdown() {
    // Copy options from the main hidden select into sidebar hidden select
    deviceSidebar.innerHTML = deviceEl.innerHTML;
    deviceSidebar.value = deviceEl.value;
    // Rebuild the custom menu
    sbMenu.innerHTML = '';
    for (const opt of deviceSidebar.options) {
      const item = document.createElement('div');
      item.className = 'custom-select-option' + (opt.selected ? ' selected' : '');
      item.dataset.value = opt.value;
      const isOnline = opt.dataset.online !== '0';
      item.innerHTML = (opt.value
        ? '<span class="cs-dot ' + (isOnline ? 'online' : 'offline') + '"></span>'
        : '') + escHtml(opt.textContent);
      item.addEventListener('click', () => {
        deviceSidebar.value = opt.value;
        deviceEl.value = opt.value;
        deviceEl.dispatchEvent(new Event('change'));
        sbWrapper.classList.remove('open');
        updateSbLabel();
      });
      sbMenu.appendChild(item);
    }
    updateSbLabel();
  }

  function updateSbLabel() {
    const sel = deviceSidebar.selectedOptions[0];
    sbLabel.textContent = sel ? sel.textContent : 'Select device';
    for (const item of sbMenu.children) {
      item.classList.toggle('selected', item.dataset.value === deviceSidebar.value);
    }
  }

  sbTrigger.addEventListener('click', (e) => {
    e.stopPropagation();
    sbWrapper.classList.toggle('open');
  });
  document.addEventListener('click', (e) => {
    if (!sbWrapper.contains(e.target)) sbWrapper.classList.remove('open');
  });

  function openSidebar() {
    overlay.style.display = 'block';
    requestAnimationFrame(() => {
      sidebar.classList.add('open');
      overlay.classList.add('open');
    });
    syncSidebarDropdown();
  }

  function closeSidebar() {
    sidebar.classList.remove('open');
    overlay.classList.remove('open');
    sbWrapper.classList.remove('open');
    setTimeout(() => { overlay.style.display = 'none'; }, 300);
  }

  hamburger.addEventListener('click', openSidebar);
  closeBtn.addEventListener('click', closeSidebar);
  overlay.addEventListener('click', closeSidebar);

  // When main device list loads, sync sidebar
  new MutationObserver(() => syncSidebarDropdown()).observe(deviceEl, { childList: true });

  refreshSidebar.addEventListener('click', () => {
    loadDevices(true);
  });

  logoutSidebar.addEventListener('click', () => {
    closeSidebar();
    window._showSignOutConfirm();
  });

  // Exposed so playing/removing a history row from inside the sidebar (mobile)
  // can close it, matching the sign-out button's behavior.
  window._closeSidebar = closeSidebar;
})();

/* ---- Queue modal (mobile) ---- */
(function () {
  const modalOverlay = document.getElementById('queue-modal-overlay');
  const modalBody = document.getElementById('queue-modal-body');
  const modalCloseBtn = document.getElementById('queue-modal-close');
  const openBtn = document.getElementById('queue-modal-btn');

  function openQueueModal() {
    renderQueueModal();
    modalOverlay.classList.add('open');
    syncModalScrollLock();
  }

  function closeQueueModal() {
    modalOverlay.classList.remove('open');
    syncModalScrollLock();
  }

  openBtn.addEventListener('click', openQueueModal);
  modalCloseBtn.addEventListener('click', closeQueueModal);
  modalOverlay.addEventListener('click', (e) => {
    if (e.target === modalOverlay) closeQueueModal();
  });

  window._renderQueueModal = renderQueueModal;
  window._closeQueueModal = closeQueueModal;
  window._openQueueModal = openQueueModal;

  function renderQueueModal() {
    let queue, currentIndex;
    try {
      queue = JSON.parse(_lastQueueJson || '[]');
      currentIndex = _lastQueueIndex ?? -1;
    } catch (_) { queue = []; currentIndex = -1; }
    if (!queue.length) {
      modalBody.innerHTML = '<div class="queue-modal-empty">No songs in queue</div>';
      return;
    }
    modalBody.innerHTML = '';
    queue.forEach((item, i) => {
      const wrapper = document.createElement('div');
      wrapper.className = 'queue-swipe-wrapper';
      wrapper.dataset.index = String(i);
      wrapper.innerHTML = `
        <div class="queue-delete-underlay">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <path d="M3 6h18"/><path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/>
            <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
          </svg>
          Remove
        </div>
      `;
      const el = document.createElement('div');
      el.className = 'queue-item' + (i === currentIndex ? ' active' : '');
      el.dataset.index = String(i);
      const thumbUrl = item.thumbnail || '';
      const thumbHtml = thumbUrl
        ? `<img class="queue-thumb" src="${escHtml(thumbUrl)}" alt="" loading="lazy" onload="this.classList.add('loaded')">`
        : `<div class="queue-thumb"></div>`;
      const dragSvg = `<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="9" cy="5" r="1.5"/><circle cx="15" cy="5" r="1.5"/><circle cx="9" cy="10" r="1.5"/><circle cx="15" cy="10" r="1.5"/><circle cx="9" cy="15" r="1.5"/><circle cx="15" cy="15" r="1.5"/><circle cx="9" cy="20" r="1.5"/><circle cx="15" cy="20" r="1.5"/></svg>`;
      el.innerHTML = `
        <div class="queue-drag-handle" title="Drag to reorder">${dragSvg}</div>
        <span class="queue-num">${i + 1}</span>
        ${thumbHtml}
        <div class="queue-info">
          <div class="queue-title">${escHtml(item.title)}</div>
          <div class="queue-artist">${escHtml(item.artist)}</div>
        </div>
      `;
      wrapper.appendChild(el);
      attachQueueItemTap(el, () => {
        closeQueueModal();
        playFromQueue(item);
      });
      _attachQueueSwipeDelete(wrapper, el, i, item, currentIndex);
      _attachQueueDragReorder(el, modalBody, i);
      modalBody.appendChild(wrapper);
    });
    const active = modalBody.querySelector('.active');
    if (active) setTimeout(() => active.scrollIntoView({ block: 'nearest', behavior: 'smooth' }), 100);
  }
})();

/* ---- Like Button ---- */
(function () {
  const likeBtn = document.getElementById('np-like-btn');
  if (likeBtn) {
    likeBtn.addEventListener('click', () => {
      if (!_currentVideoId) return;
      const title = document.getElementById('np-title').textContent || '';
      const artist = document.getElementById('np-artist').textContent || '';
      // We don't have the thumbnail here easily, but the backend doesn't strictly need it for liked songs
      const item = { video_id: _currentVideoId, title, artist };
      if (typeof toggleLike === 'function') toggleLike(item, likeBtn);
    });
  }
})();

/* ---- Shuffle ---- */
let _shuffleEnabled = false;
(function () {
  const shuffleBtn = document.getElementById('shuffle-btn');

  shuffleBtn.addEventListener('click', async () => {
    _shuffleEnabled = !_shuffleEnabled;
    shuffleBtn.classList.toggle('shuffle-active', _shuffleEnabled);
    if (_shuffleEnabled) {
      toast('Shuffling queue\u2026');
      try {
        await api('/alexa/shuffle_queue/', {});
        toast('Queue shuffled', 'ok');
        // Force queue UI refresh on next SSE update
        _lastQueueJson = '';
        setTimeout(pollNowPlaying, 500);
      } catch (e) {
        toast(e.message, 'error');
        _shuffleEnabled = false;
        shuffleBtn.classList.remove('shuffle-active');
      }
    } else {
      toast('Shuffle off', 'ok');
    }
  });
})();

/* ---- Queue modal drag-to-dismiss ---- */
(function () {
  const overlay = document.getElementById('queue-modal-overlay');
  const modal = document.getElementById('queue-modal');
  const dragArea = document.getElementById('queue-modal-drag');
  const headerArea = modal.querySelector('.queue-modal-header');
  let startY = 0, currentY = 0, dragging = false;

  function onStart(e) {
    // Don't start drag from the close button
    if (e.target.closest('.queue-modal-close')) return;
    dragging = true;
    startY = e.touches[0].clientY;
    currentY = 0;
    modal.style.transition = 'none';
  }

  dragArea.addEventListener('touchstart', onStart, { passive: true });
  headerArea.addEventListener('touchstart', onStart, { passive: true });

  window.addEventListener('touchmove', (e) => {
    if (!dragging) return;
    currentY = e.touches[0].clientY - startY;
    if (currentY < 0) currentY = 0; // only drag down
    modal.style.transform = 'translateY(' + currentY + 'px)';
  }, { passive: true });

  window.addEventListener('touchend', () => {
    if (!dragging) return;
    dragging = false;
    modal.style.transition = '';
    if (currentY > 100) {
      // Go through the real close so the body scroll lock is released too.
      if (window._closeQueueModal) window._closeQueueModal();
      else overlay.classList.remove('open');
    }
    modal.style.transform = '';
  });
})();

/* ---- Disable pull-to-refresh (mobile) ---- */
(function () {
  let touchStartY = 0;
  document.addEventListener('touchstart', (e) => {
    touchStartY = e.touches[0].clientY;
  }, { passive: true });

  document.addEventListener('touchmove', (e) => {
    const dy = e.touches[0].clientY - touchStartY;
    // Pulling down while at the top of the page
    if (dy > 0 && window.scrollY <= 0) {
      // Allow scrollable child containers to scroll
      let el = e.target;
      while (el && el !== document.body && el !== document.documentElement) {
        const style = window.getComputedStyle(el);
        const overflowY = style.overflowY;
        if ((overflowY === 'auto' || overflowY === 'scroll') && el.scrollTop > 0) return;
        el = el.parentElement;
      }
      e.preventDefault();
    }
  }, { passive: false });
})();

/* ---- PWA: register the service worker so the app is installable ---- */
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/service-worker.js').catch(() => {});
  });
}
