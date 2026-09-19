(function () {
  'use strict';
  const state = window.__appState = window.__appState || {};
  if (state.isPlaying === undefined) state.isPlaying = false;
  if (state.lastActionAt === undefined) state.lastActionAt = 0;
  if (state.lastActionIntent === undefined) state.lastActionIntent = null;
  if (state._lastPlayAttemptVideoId === undefined) state._lastPlayAttemptVideoId = '';
  if (state._lastQueueJson === undefined) state._lastQueueJson = '';
  if (state._lastQueueIndex === undefined) state._lastQueueIndex = -1;
  // Rendering hundreds of interactive rows while the now-playing page slides
  // in blocks the animation. Start with a viewport-sized window; the sentinel
  // appends later chunks as the user scrolls.
  if (state._queueRenderLimit === undefined) state._queueRenderLimit = 30;
  if (state._historyCache === undefined) state._historyCache = [];
  if (state._hasTrack === undefined) state._hasTrack = false;
  if (state._resultsOpen === undefined) state._resultsOpen = false;
  // Set by reorderQueue for a short window after a drag-drop: the optimistic
  // re-render plus the confirming poll snapshot must not yank the viewport to
  // the active row. The user just placed a song deliberately — keep the
  // viewport where they dropped it.
  if (state._suppressQueueScrollUntil === undefined) state._suppressQueueScrollUntil = 0;
  // Floating queue panel is retired — queue is embedded in the #now-playing page.
  state._queueOpen = false;
  try { localStorage.removeItem('queuePanelOpen'); } catch (_) {}

const QUEUE_RENDER_CHUNK = 30;
let _addToQueueBusy = false;

async function addToQueue(item, position, silent) {
  if (_addToQueueBusy) return;
  // Nothing playing? Just play the song directly instead of silently queuing.
  if (!state._hasTrack) {
    playResult(item);
    return;
  }
  const serial = selectedSerial();
  if (!serial) return;
  _addToQueueBusy = true;
  const label = position === 'next' ? 'Playing next' : 'Adding to queue';
  if (!silent) toast(label + '\u2026');
  try {
    await api('/alexa/queue_add/', {
      serial,
      video_id: item.video_id,
      title: item.title,
      artist: item.artist,
      artists: item.artists || [],
      artist_id: item.artist_id || item.artistId || item.channelId || item.channel_id || '',
      thumbnail: item.thumbnail,
      duration_ms: item.duration_ms,
      position,
    });
    if (!silent) {
      if (position === 'next') {
        toast('\u201c' + item.title + '\u201d will play next', 'ok');
      } else {
        toast('Added \u201c' + item.title + '\u201d to queue', 'ok');
      }
    }
    // Re-adding a song right after deleting it: just poll, next SSE confirms.
    // Don't blank state._lastQueueJson here: that forces the next SSE snapshot to be
    // treated as "changed" even when it matches what's already on screen,
    // triggering a full rebuild (visible flicker) for no reason. Just poll;
    // the normal qJson !== state._lastQueueJson check in updateNowPlaying will only
    // re-render if the confirmed queue actually differs.
    schedulePollNowPlaying(500);
  } catch (e) {
    if (!silent) toast(e.message, 'error');
    else throw e;
  } finally {
    _addToQueueBusy = false;
  }
}

/* ---- Shared row swipe gesture (mobile) ----
   Right swipe = play next; left swipe = append to queue. The helper is shared
   by search, album, playlist, and artist rows so every surface keeps the same
   gesture thresholds, scroll lock, and click suppression behavior. */
const RESULT_SWIPE_UNDERLAY_HTML = `
  <div class="result-swipe-underlay underlay-play-next">
    <svg viewBox="0 0 24 24" fill="currentColor"><path d="M4 5v14l11-7L4 5zm13 0v14h3V5h-3z"/></svg>
    Play next
  </div>
  <div class="result-swipe-underlay underlay-add-queue">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
    Add to queue
  </div>
`;

function _ensureResultSwipeUnderlay(wrapper) {
  if (!wrapper || wrapper.querySelector('.result-swipe-underlay')) return;
  wrapper.insertAdjacentHTML('afterbegin', RESULT_SWIPE_UNDERLAY_HTML);
}

function _attachSwipeGesture(wrapper, inner, item) {
  _ensureResultSwipeUnderlay(wrapper);
  const SWIPE_THRESHOLD = 60;
  const LOCK_DISTANCE = 8;
  const AXIS_BIAS = 1.2;
  let startX = 0, startY = 0, currentX = 0, gesture = 'pending';

  inner.addEventListener('touchstart', (e) => {
    if (e.target.closest && e.target.closest('button, a, input, textarea, select')) return;
    if (e.touches.length !== 1) return;
    inner._swipeSuppressClick = false;
    inner._swipeAllowClick = false;
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
    wrapper.classList.remove('swiping-left', 'swiping-right');

    // Only a committed action swipe cancels the browser's follow-up click.
    // A short horizontal wobble should still behave like a normal row tap.
    if (currentX > SWIPE_THRESHOLD) {
      inner._swipeSuppressClick = true;
      inner._swipeAllowClick = false;
      addToQueue(item, 'next');
    } else if (currentX < -SWIPE_THRESHOLD) {
      inner._swipeSuppressClick = true;
      inner._swipeAllowClick = false;
      addToQueue(item, 'last');
    } else {
      // Keep a short horizontal wobble as a normal row tap. This overrides
      // attachQueueItemTap's generic drag guard for touch gestures that did
      // axis-lock horizontally but never reached the action threshold.
      inner._swipeAllowClick = true;
    }
    gesture = 'idle';
    currentX = 0;
  }, { passive: true });

  inner.addEventListener('touchcancel', () => {
    inner.style.transition = '';
    inner.style.transform = '';
    wrapper.classList.remove('swiping-left', 'swiping-right');
    inner._swipeSuppressClick = false;
    inner._swipeAllowClick = false;
    gesture = 'idle';
    currentX = 0;
  }, { passive: true });

  // Collection and artist rows use their own click handlers instead of
  // attachQueueItemTap. Capture the synthetic click after a committed swipe
  // so it cannot start playback on any surface.
  inner.addEventListener('click', (e) => {
    if (!inner._swipeSuppressClick) return;
    inner._swipeSuppressClick = false;
    inner._swipeAllowClick = false;
    e.preventDefault();
    e.stopImmediatePropagation();
  }, true);
}


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
      el._swipeAllowClick = false;
      return;
    }
    if (el._swipeAllowClick) {
      // A sub-threshold horizontal queue gesture is still a normal tap,
      // even though pointermove may have exceeded the generic drag guard.
      el._swipeAllowClick = false;
      onTap();
      return;
    }
    if (!dragged) onTap();
  });
}

/* ---- Lazy (windowed) queue rendering ----
   Shared by the desktop queue list and the mobile queue modal. Only a window
   of rows is materialized; a 1px sentinel after the last row pages in the next
   chunk when scrolled near. Each container keeps its own state in
   el._lazyQueue = { queue, currentIndex } and its own observer. */

function _renderedQueueRows(container) {
  return container.querySelectorAll(':scope > .queue-swipe-wrapper');
}

// Keeps a queue surface (#np-queue-list, #queue-modal-body) scrolled to the
// currently-playing row whenever the highlighted track changes — natural
// playback advance, Next/Previous, a voice command, or the page/sheet being
// opened while mid-queue. Guarded by the row's own last-known index so
// repeated calls with an unchanged index (frequent SSE polling) never yank
// a user's manual scroll position back to the active row.
//
// `force` marks a full render (the player just opened, or the queue contents
// themselves changed) as opposed to a lightweight highlight shift.
//
// IMPORTANT: the scroll must target ONLY this list's own scrollTop. Using
// `el.scrollIntoView({ block: 'start' })` scrolls every scrollable ancestor so
// the target lands at the top of the *viewport* — and when the list isn't its
// own scroll area (mobile embeds the queue in the scrolling now-playing page,
// where #np-queue-list is overflow:visible) that scrolls the ENTIRE now-playing
// section up, shoving the artwork/header off the top. Scrolling the container
// directly keeps the surrounding page absolutely stationary.
function _scrollQueueRowIntoView(container, currentIndex, force) {
  if (!container) return;
  const key = String(currentIndex);
  if (!force && container.dataset.lastActiveIndex === key) return;
  container.dataset.lastActiveIndex = key;
  // A drag-drop reorder holds the viewport at the drop position through its
  // optimistic re-render and the confirming poll snapshot. Record the index
  // above (so no delayed scroll queues up) but do not move the viewport.
  if (Date.now() < (state._suppressQueueScrollUntil || 0)) return;
  // While the user is actively scrolling the mobile sheet, never fight the
  // gesture with a smooth auto-scroll — that yank reads as flicker/jank
  // when scrolling up away from the active row.
  if (!force && container.id === 'queue-modal-body' &&
      Date.now() - (container._lastUserScrollAt || 0) < 1500) return;
  const row = container.querySelector('.queue-item.active');
  if (!row) return;
  const wrapper = row.closest('.queue-swipe-wrapper') || row;
  requestAnimationFrame(function () {
    if (typeof container.scrollTo !== 'function') return;
    const cRect = container.getBoundingClientRect();
    const rRect = wrapper.getBoundingClientRect();
    // Row fully within the list's viewport (incl. sitting below the top).
    const fullyVisible = rRect.top >= cRect.top && rRect.bottom <= cRect.bottom;
    if (fullyVisible) return;
    let delta;
    if (force) {
      // Player opened: align the row's top with the list's top — but only if
      // it actually fell out of view. A row already visible is left alone.
      delta = rRect.top - cRect.top;
    } else {
      // Highlight shift: minimal 'nearest' adjustment so the row is visible
      // without ever overriding manual scrolling.
      delta = rRect.top < cRect.top
        ? rRect.top - cRect.top
        : rRect.bottom - cRect.bottom;
    }
    if (!delta) return;
    container.scrollTo({ top: container.scrollTop + delta, behavior: 'smooth' });
  });
}

// Builds one queue row (wrapper + item + listeners). thumbsById, when given,
// maps video_id -> already-loaded <img> to transplant so the browser never
// re-fetches/re-decodes it (the re-fetch flash on every track change was the
// visible flicker here). Rows capture their index by closure, so callers must
// rebuild rows whose index changed (reorders) rather than reuse them.
function _refreshQueueSwipeLikeLabel(wrapper, item) {
  if (!wrapper || !item) return;
  const label = wrapper.querySelector('.queue-swipe-like-label');
  if (!label) return;
  const liked = window._playlistsData &&
    Array.isArray(window._playlistsData.liked_songs) &&
    window._playlistsData.liked_songs.includes(item.video_id);
  label.textContent = liked ? 'Unlike' : 'Like';
}

function _buildQueueRow(container, item, i, currentIndex, thumbsById) {
  const id = item.video_id || '';
  const wrapper = document.createElement('div');
  wrapper.className = 'queue-swipe-wrapper';
  wrapper.dataset.index = String(i);
  wrapper.dataset.videoId = id;
  wrapper._songContextTrack = item;

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
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>
      <span class="queue-swipe-like-label">${typeof _playlistsData !== 'undefined' &&
        Array.isArray(_playlistsData.liked_songs) &&
        _playlistsData.liked_songs.includes(item.video_id) ? 'Unlike' : 'Like'}</span>
    </div>
  `;

  const el = document.createElement('div');
  el.className = 'queue-item' + (i === currentIndex ? ' active' : '') + (i === currentIndex && state.isPlaying ? ' playing' : '');
  el.dataset.index = String(i);
  // On mobile the queue row is one play target: artist links use the same
  // row action, while the separate more button keeps its own menu behavior.
  el.dataset.mobileRowPlay = 'true';

  const thumbUrl = item.thumbnail || '';
  const duration = window.formatTrackDuration ? window.formatTrackDuration(item) : '';
  const reusableImg = thumbsById && thumbUrl ? thumbsById.get(id) : null;
  const sameUrl = reusableImg && reusableImg.src === thumbUrl;
  // A placeholder marker <div> stands in for the thumb during innerHTML
  // parsing; a transplantable already-loaded <img> replaces it right after.
  const thumbHtml = sameUrl
    ? `<div class="queue-thumb-slot"></div>`
    : thumbUrl
      ? `<img class="queue-thumb" src="${escHtml(thumbUrl)}" alt="" loading="lazy" onload="this.classList.add('loaded')">`
      : `<div class="queue-thumb"></div>`;

  const dragSvg = `<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="9" cy="5" r="1.5"/><circle cx="15" cy="5" r="1.5"/><circle cx="9" cy="10" r="1.5"/><circle cx="15" cy="10" r="1.5"/><circle cx="9" cy="15" r="1.5"/><circle cx="15" cy="15" r="1.5"/><circle cx="9" cy="20" r="1.5"/><circle cx="15" cy="20" r="1.5"/></svg>`;

  el.innerHTML = `
    <div class="queue-drag-handle" title="Drag to reorder">${dragSvg}</div>
    <span class="queue-num">${i + 1}</span>
    <div class="queue-thumb-wrap">
      ${thumbHtml}
      <span class="music-bars queue-playing-bars" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i></span>
    </div>
    <div class="queue-info">
      <div class="queue-title">${escHtml(item.title)}</div>
      <div class="queue-artist">${window.artistLinksHtml(
        item.artist,
        Array.isArray(item.artists) && item.artists.length
          ? item.artists.map((artist) => typeof artist === 'string' ? '' :
              (artist.id || artist.browseId || artist.channelId || artist.channel_id || ''))
      : (item.channelId || item.channel_id || item.artistId || item.artist_id || ''),
    item.video_id || item.videoId || ''
  )}</div>
    </div>
    ${duration ? `<span class="track-duration">${escHtml(duration)}</span>` : ''}
    ${_queueMoreMenuHtml(item)}
  `;
  if (sameUrl) el.querySelector('.queue-thumb-slot').replaceWith(reusableImg);

  wrapper.appendChild(el);

  // Removing the active track would stop the current playback.
  if (i === currentIndex) {
    const removeOption = el.querySelector('.queue-more-menu [data-action="remove"]');
    if (removeOption) removeOption.hidden = true;
  }

  // Artist name clicks: navigate on desktop (stopping the row's play
  // action); on mobile let the tap fall through so it still plays the row.
  window.wireArtistLinks(el);

  // Tap on the item Ã¢â€ â€™ play from queue. Mark it active immediately so the
  // "you tapped this" feedback shows right away instead of only after the
  // server round-trip completes and playFromQueue's own re-render lands.
  attachQueueItemTap(el, () => {
    for (const other of container.querySelectorAll('.queue-item.active')) other.classList.remove('active');
    el.classList.add('active');
    // Live position, not the build-time index: DOM-move reorders renumber
    // rows without rebuilding them.
    const _liveRow = Number(el.dataset.index);
    playFromQueue(item, Number.isInteger(_liveRow) ? _liveRow : i);
  });

  _wireQueueMoreMenu(el, item, i);

  // Mobile: swipe gestures (like/delete)
  _attachQueueSwipeGestures(wrapper, el, i, item, currentIndex);

  // Drag-to-reorder (both mobile + desktop via the handle)
  _attachQueueDragReorder(el, container, i);

  return wrapper;
}

function _appendLazyQueueRows(container, targetCount) {
  const st = container._lazyQueue;
  if (!st) return;
  const start = _renderedQueueRows(container).length;
  const end = Math.min(st.queue.length, Math.max(targetCount, start));
  if (end > start) {
    const frag = document.createDocumentFragment();
    for (let i = start; i < end; i++) {
      frag.appendChild(_buildQueueRow(container, st.queue[i], i, st.currentIndex, null));
    }
    const sentinel = container.querySelector(':scope > .queue-lazy-sentinel');
    if (sentinel) container.insertBefore(frag, sentinel);
    else container.appendChild(frag);
    if (container.id === 'queue-list' && end > state._queueRenderLimit) state._queueRenderLimit = end;
  }
  _syncQueueSentinel(container);
}

function _syncQueueSentinel(container) {
  const st = container._lazyQueue;
  const total = st ? st.queue.length : 0;
  let sentinel = container.querySelector(':scope > .queue-lazy-sentinel');
  if (_renderedQueueRows(container).length >= total) {
    if (sentinel) sentinel.remove();
    return;
  }
  if (!sentinel) {
    sentinel = document.createElement('div');
    sentinel.className = 'queue-lazy-sentinel';
    sentinel.style.height = '1px';
  }
  container.appendChild(sentinel); // (re)position after the last row
  if (!container._lazyQueueObserver) {
    container._lazyQueueObserver = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        _appendLazyQueueRows(container, _renderedQueueRows(container).length + QUEUE_RENDER_CHUNK);
      }
    }, { root: container, rootMargin: '600px' });
  }
  container._lazyQueueObserver.disconnect();
  container._lazyQueueObserver.observe(sentinel);
}

function showQueue(queue, currentIndex) {
  // The floating #queue-section panel is retired.
  // All queue display now lives in #np-queue-list inside #now-playing-section.
  // Always hide the panel so it never overlaps homepage / search content.
  const section = document.getElementById('queue-section');
  const mainEl = document.querySelector('main');
  if (section) { section.classList.remove('is-visible'); section.hidden = true; }
  if (mainEl) mainEl.classList.remove('has-queue');

  // Keep the now-playing page's inline queue in sync when visible.
  const npSection = document.getElementById('now-playing-section');
  if (npSection && !npSection.hidden && queue && queue.length > 0 && window.renderNpQueue) {
    window.renderNpQueue(queue, currentIndex);
  }
}

window.showQueue = showQueue;

// Render queue into the now-playing page's #np-queue-list.
// Called by the router when navigating to #now-playing and by SSE updates.
function renderNpQueue(queue, currentIndex) {
  var list = document.getElementById('np-queue-list');
  if (!list) return;
  if (!queue || queue.length === 0) {
    list.innerHTML = '<div style="padding:24px;color:var(--muted);font-size:.88rem">No queue</div>';
    return;
  }
  var renderLimit = Math.min(queue.length, Math.max(state._queueRenderLimit || QUEUE_RENDER_CHUNK, currentIndex + 11));
  list._lazyQueue = { queue: queue, currentIndex: currentIndex };
  var existingThumbsById = new Map();
  var existingRows = _renderedQueueRows(list);
  existingRows.forEach(function(w) {
    var id = w.dataset.videoId || '';
    var img = w.querySelector('img.queue-thumb.loaded');
    if (id && img && !existingThumbsById.has(id)) existingThumbsById.set(id, img);
  });
  var renderedArr = Array.from(existingRows);
  var existingIds = renderedArr.map(function(w) { return w.dataset.videoId || ''; });
  var incomingIds = queue.map(function(item) { return item.video_id || ''; });
  var samePrefix = existingIds.length > 0
    && existingIds.length <= incomingIds.length
    && existingIds.every(function(id, i) { return id === incomingIds[i]; });
  if (samePrefix) {
    // The queue itself is unchanged, but Alexa may have advanced several
    // tracks. Ensure that row exists before updating the highlight; otherwise
    // a lazy queue can remain stuck on the last rendered item.
    if (currentIndex >= renderedArr.length) {
      _appendLazyQueueRows(list, currentIndex + 11);
      renderedArr = Array.from(_renderedQueueRows(list));
    }
    renderedArr.forEach(function(w) {
      // CSS and click handlers use .queue-item.active. Toggling the wrapper
      // leaves the previously-active visible row highlighted indefinitely.
      var item = w.querySelector('.queue-item');
      if (!item) return;
      var isCurrent = Number(item.dataset.index) === Number(currentIndex);
      item.classList.toggle('active', isCurrent);
      item.classList.toggle('playing', isCurrent && state.isPlaying);
    });
    _syncQueueSentinel(list);
    _scrollQueueRowIntoView(list, currentIndex, false);
    return;
  }
  var newChildren = [];
  // Never shrink the rendered window on a rebuild: rendering fewer rows than
  // are already on screen collapses the list while scrolled deep and the
  // browser clamps scrollTop (a violent jump the drop then gets blamed for).
  renderLimit = Math.min(queue.length, Math.max(renderLimit, renderedArr.length));
  for (var i = 0; i < renderLimit; i++) {
    newChildren.push(_buildQueueRow(list, queue[i], i, currentIndex, existingThumbsById));
  }
  list.replaceChildren.apply(list, newChildren);
  _syncQueueSentinel(list);
  // A full rebuild happens on first render (page just opened) or when the
  // queue contents themselves changed (reorder, add/remove, new radio batch).
  // Either way the active row should be brought into view.
  _scrollQueueRowIntoView(list, currentIndex, true);
}
window.renderNpQueue = renderNpQueue;

// Called when the player opens/reopens so the currently-playing row sits at
// the top of the queue. The inline queue's DOM survives a close, so a reopen
// with unchanged content flows through renderNpQueue's `samePrefix` branch,
// which deliberately only does a minimal 'nearest' scroll. This forces the
// row back to the top of the visible area when it fell out of view.
window.scrollQueueToCurrent = function (container) {
  var list = container || document.getElementById('np-queue-list');
  if (!list) return;
  // Opening the player is an explicit "show me what's playing" gesture, so it
  // opts out of any reorder scroll-suppression window still in effect.
  state._suppressQueueScrollUntil = 0;
  var currentIndex = (window.__appState && window.__appState._lastQueueIndex != null
    ? window.__appState._lastQueueIndex
    : (typeof window._lastQueueIndex === 'number' ? window._lastQueueIndex : 0));
  _scrollQueueRowIntoView(list, currentIndex, true);
};

// Builds the "3-dot" more-options button + dropdown for a queue row (used by
// both the desktop inline queue and the mobile queue popup, which otherwise
// only offered swipe gestures with no menu equivalent).
function _queueMoreMenuHtml(item) {
  const moreSvg = `<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>`;
  const isLiked = window._playlistsData && window._playlistsData.liked_songs && window._playlistsData.liked_songs.includes(item.video_id);
  const likeSvg = isLiked
    ? '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M2 10h4v12H2zM8 22V10l3.5-7.5c.3-.7 1.1-1.1 1.8-.8l.2.1c1.1.5 1.6 1.7 1.3 2.8L14 10h6.2c1.3 0 2.3 1.2 2 2.5l-1.5 7.5c-.2 1.2-1.2 2-2.4 2H8z"/></svg>'
    : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>';
  const likeText = isLiked ? "Unlike" : "Like";
  const likeClass = isLiked ? "queue-menu-option liked" : "queue-menu-option";
  return `
      <button class="queue-more-btn" type="button" title="More options">${moreSvg}</button>
      <div class="queue-more-menu">
        <div class="queue-menu-option" data-action="play-radio">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4.93 19.07A10 10 0 1 1 19.07 4.93 10 10 0 0 1 4.93 19.07z"/><circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="M2 12h2"/><path d="M20 12h2"/></svg>
          Play Radio
        </div>
        <div class="${likeClass}" data-action="like">
          ${likeSvg}
          ${likeText}
        </div>
        <div class="queue-menu-option" data-action="save-playlist">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path><line x1="12" y1="11" x2="12" y2="17"></line><line x1="9" y1="14" x2="15" y2="14"></line></svg>
          Add to Playlist
        </div>
        <div class="queue-menu-option" data-action="open-album">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r="1" fill="currentColor"/></svg>
          Go to album
        </div>
        <div class="queue-menu-option" data-action="open-artist">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-7 8-7s8 3 8 7"/></svg>
          Go to artist
        </div>
        <div class="queue-menu-option danger" data-action="remove">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
          Remove from queue
        </div>
      </div>
  `;
}

// Wires up a queue row's 3-dot menu (rendered via _queueMoreMenuHtml above).
// `el` must contain a .queue-more-btn + .queue-more-menu pair; `index` is the
// row's position at render time (removeFromQueue re-verifies by video_id
// itself, so a stale index from before a reorder/removal is still safe).
function _wireQueueMoreMenu(el, item, index) {
  const moreBtn = el.querySelector('.queue-more-btn');
  const moreMenu = el.querySelector('.queue-more-menu');
  // Prevent document click handler from closing menu when clicking inside it
  moreMenu.addEventListener('click', (e) => e.stopPropagation());
  moreBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    // Use the same menu as right-click and search result 3-dot controls.
    if (window.openSongContextMenu) {
      // Live position: DOM-move reorders renumber rows without rebuilding.
      const _liveIdx = Number(el.dataset.index);
      window.openSongContextMenu(e, Object.assign({}, item, {
        _queueIndex: Number.isInteger(_liveIdx) ? _liveIdx : index,
        _queueIsActive: el.classList.contains('active')
      }));
      return;
    }
    const wasOpen = moreMenu.classList.contains('open');
    _closeAllQueueMenus();
    if (!wasOpen) {
      moreBtn.classList.add('open');
      const rect = moreBtn.getBoundingClientRect();
      const menuHeight = 6 * 48; // approximate height of the six option rows
      const menuWidth = 170;
      
      let x = e && e.clientX ? e.clientX : rect.right - menuWidth;
      let y = e && e.clientY ? e.clientY : rect.bottom;
      
      const spaceBelow = window.innerHeight - y;
      const spaceRight = window.innerWidth - x;
      const openAbove = spaceBelow < menuHeight + 8;
      
      if (spaceRight < menuWidth + 8) {
         moreMenu.style.left = 'auto';
         moreMenu.style.right = (window.innerWidth - x) + 'px';
      } else {
         moreMenu.style.left = x + 'px';
         moreMenu.style.right = 'auto';
      }
      
      if (openAbove) {
         moreMenu.style.top = 'auto';
         moreMenu.style.bottom = (window.innerHeight - y + 4) + 'px';
      } else {
         moreMenu.style.top = (y + 4) + 'px';
         moreMenu.style.bottom = 'auto';
      }
      
      moreMenu.classList.add('open');
      // Portal the menu to <body> while open. Inside the row it sits under
      // an overflow-hidden wrapper within a scrollable list, and Chromium's
      // input hit-testing clips fixed elements there Ã¢â‚¬â€  the menu is visible
      // but clicks land on the row below it. _closeAllQueueMenus returns it.
      moreMenu._home = el;
      document.body.appendChild(moreMenu);
    }
  });
  // Right-click anywhere on the row opens the same more-options menu
  el.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    e.stopPropagation();
    // Simulate a mouse click event with current cursor position
    moreBtn.dispatchEvent(new MouseEvent('click', {
      clientX: e.clientX,
      clientY: e.clientY,
      bubbles: true,
      cancelable: true
    }));
  });
  moreMenu.querySelector('[data-action="remove"]').addEventListener('click', (e) => {
    e.stopPropagation();
    _closeAllQueueMenus();
    // Live position (see above); removeFromQueue re-verifies by video_id too.
    const _liveRm = Number(el.dataset.index);
    removeFromQueue(Number.isInteger(_liveRm) ? _liveRm : index, item.title, item.video_id);
  });
  moreMenu.querySelector('[data-action="save-playlist"]').addEventListener('click', (e) => {
    e.stopPropagation();
    _closeAllQueueMenus();
    openAddToPlaylistModal(item);
  });
  moreMenu.querySelector('[data-action="play-radio"]').addEventListener('click', (e) => {
    e.stopPropagation();
    _closeAllQueueMenus();
    // force_radio=true: this track is already sitting in the current queue
    // (that's exactly why the menu item is here), so the normal "already in
    // queue, just play it" path would leave the existing queue untouched.
    // Force a fresh queue seeded from just this track instead.
    playResult(item, false, true);
  });
  moreMenu.querySelector('[data-action="open-album"]').addEventListener('click', (e) => {
    e.stopPropagation();
    _closeAllQueueMenus();
    const albumId = item.album_id || item.albumId || item.album_browse_id || '';
    if (!albumId) return;
    if (window.preloadNavigateAlbum) window.preloadNavigateAlbum(albumId);
    else if (window.navigateTo) window.navigateTo('#album/' + encodeURIComponent(albumId));
  });
  moreMenu.querySelector('[data-action="open-artist"]').addEventListener('click', (e) => {
    e.stopPropagation();
    _closeAllQueueMenus();
    const artistId = item.artist_id || item.channelId || item.channel_id || item.artistId || '';
    if (!artistId) return;
    if (window.preloadNavigateArtist) window.preloadNavigateArtist(artistId);
    else if (window.navigateTo) window.navigateTo('#artist/' + encodeURIComponent(artistId));
  });
  const likeBtn = moreMenu.querySelector('[data-action="like"]');
  likeBtn.addEventListener('click', async (e) => {
    e.stopPropagation();
    _closeAllQueueMenus();
    if (typeof toggleLike === 'function') {
      await toggleLike(item);
      const isLikedNow = window._playlistsData && window._playlistsData.liked_songs && window._playlistsData.liked_songs.includes(item.video_id);
      const likeSvgNow = isLikedNow
        ? '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M2 10h4v12H2zM8 22V10l3.5-7.5c.3-.7 1.1-1.1 1.8-.8l.2.1c1.1.5 1.6 1.7 1.3 2.8L14 10h6.2c1.3 0 2.3 1.2 2 2.5l-1.5 7.5c-.2 1.2-1.2 2-2.4 2H8z"/></svg>'
        : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>';
      likeBtn.innerHTML = `\n          ${likeSvgNow}\n          ${isLikedNow ? "Unlike" : "Like"}\n        `;
      if (isLikedNow) likeBtn.classList.add('liked');
      else likeBtn.classList.remove('liked');
    }
  });
}

// The open menu is fixed-positioned and portaled to <body> (see the open
// handler above) at the coordinates of its row *at open time*. If the list
// scrolls afterward, the row moves but the menu doesn't follow it -- it's
// simplest and safest to just close it, rather than keep it live-repositioned
// during scroll.
(function () {
  const list = document.getElementById('queue-list');
  if (list) list.addEventListener('scroll', () => _closeAllQueueMenus(), { passive: true });
  const modalBody = document.getElementById('queue-modal-body');
  if (modalBody) modalBody.addEventListener('scroll', () => _closeAllQueueMenus(), { passive: true });
})();

function _closeAllQueueMenus() {
  for (const m of document.querySelectorAll('.queue-more-menu.open')) {
    m.classList.remove('open');
    // Wait for the fade-out transition to finish before resetting position
    // and reparenting back to its row -- doing it immediately would yank the
    // menu to a default (0,0) position mid-fade, flashing it in the wrong
    // spot for a frame instead of just fading out in place.
    setTimeout(() => {
      if (m.classList.contains('open')) return; // reopened before the timeout fired
      m.style.top = '';
      m.style.bottom = '';
      m.style.left = '';
      m.style.right = '';
      if (m._home && m.parentElement !== m._home) m._home.appendChild(m);
    }, 150);
  }
  for (const b of document.querySelectorAll('.queue-more-btn.open')) b.classList.remove('open');
  for (const w of document.querySelectorAll('.queue-swipe-wrapper.menu-open')) w.classList.remove('menu-open');
}
document.addEventListener('click', function () {
  // Tap-and-hold release guard (same signal song-context-menu.js honors): the
  // finger's release click after a hold-open must not close the just-opened
  // sheet. Never arms on desktop.
  if (typeof window._contextSheetSwallowArmed === 'function' &&
      window._contextSheetSwallowArmed()) return;
  _closeAllQueueMenus();
});

function updateQueueActive(currentIndex) {
  // #queue-list is the retired/hidden panel. The desktop now-playing queue,
  // mobile inline queue, and legacy panel may coexist in the DOM, so update
  // every rendered surface when a slim SSE payload changes only the index.
  const targetIndex = Number(currentIndex);
  for (const id of ['np-queue-list', 'queue-list']) {
    const list = document.getElementById(id);
    if (!list) continue;
    for (const el of list.querySelectorAll('.queue-item')) {
      const isCurrent = Number(el.dataset.index) === targetIndex;
      el.classList.toggle('active', isCurrent);
      el.classList.toggle('playing', isCurrent && state.isPlaying);
    }
    _scrollQueueRowIntoView(list, targetIndex, false);
  }
}

function updateQueuePlaying(isPlaying) {
  state.isPlaying = !!isPlaying;
  const active = document.querySelectorAll('.queue-item.active');
  active.forEach(el => el.classList.toggle('playing', state.isPlaying));
}

// Same highlight sync for the mobile queue modal Ã¢â‚¬â€ used when an SSE push
// carries only a queue_index change (queue itself omitted as unchanged).
function updateQueueModalActive(currentIndex) {
  const modalBody = document.getElementById('queue-modal-body');
  if (!modalBody) return;
  for (const el of modalBody.querySelectorAll('.queue-item')) {
    el.classList.toggle('active', Number(el.dataset.index) === currentIndex);
    el.classList.toggle('playing', Number(el.dataset.index) === currentIndex && state.isPlaying);
  }
  // Only auto-scroll while the sheet is actually open — otherwise this would
  // silently move its scroll position while hidden, surprising the user the
  // next time they open it on an unrelated row.
  const overlay = document.getElementById('queue-modal-overlay');
  if (overlay && overlay.classList.contains('open')) {
    _scrollQueueRowIntoView(modalBody, currentIndex, false);
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
    const queue = JSON.parse(state._lastQueueJson || '[]');
    if (queue[guessIndex] && queue[guessIndex].video_id === item.video_id) return guessIndex;
    return queue.findIndex((q) => q && q.video_id === item.video_id);
  } catch (_) { return guessIndex; }
}

async function removeFromQueue(index, title, videoId) {
  // Re-verify by identity right before acting: the index may have gone stale
  // between the gesture and this call (e.g. during the swipe-out animation).
  try {
    const queue = JSON.parse(state._lastQueueJson || '[]');
    if (videoId && (!queue[index] || queue[index].video_id !== videoId)) {
      index = queue.findIndex((q) => q && q.video_id === videoId);
      if (index === -1) {
        // Already gone (removed from another view/tab): just resync.
        state._lastQueueJson = '';
        schedulePollNowPlaying(300);
        return;
      }
    }
    // Optimistically drop the row locally so it vanishes right away instead
    // of reappearing until the server confirms. On error the poll below
    // restores the true queue.
    if (index >= 0 && index < queue.length) {
      queue.splice(index, 1);
      let currentIdx = state._lastQueueIndex;
      if (currentIdx > index) currentIdx--;
      state._lastQueueJson = JSON.stringify(queue);
      state._lastQueueIndex = currentIdx;
      showQueue(queue, currentIdx);
      refreshQueueModalIfOpen();
    }
  } catch (_) {}
  try {
    // Send the video_id too so the server can refuse if its queue has moved
    // on and the index no longer points at this song.
    await api('/alexa/queue_remove/', videoId ? { index, video_id: videoId } : { index });
    toast('Removed \u201c' + (title || 'track') + '\u201d from queue', 'ok');
    // Keep the optimistic snapshot as-is: blanking state._lastQueueJson here made
    // a rapid follow-up delete resolve its index against an empty queue and
    // silently no-op. The next SSE push / poll confirms the true state (with
    // the pending filter suppressing any stale copy of this song).
    schedulePollNowPlaying(300);
  } catch (e) {
    toast(e.message, 'error');
    // Revert the optimistic removal: force a refresh from the server.
    state._lastQueueJson = '';
    schedulePollNowPlaying(300);
  }
}

/* ---- Reorder scroll helpers (drop render) ----
   A same-scrollTop rebuild already displays the new order correctly: rows the
   move displaced sit one slot over, which is the reorder itself, not an
   error — so there is deliberately NO scroll compensation here (an earlier
   anchor-glue version fought the browser's own scroll anchoring and doubled
   the very shove it tried to remove). The FLIP glide below is what makes the
   legitimate one-row shift read as smooth motion instead of a snap. */

// Commit an optimistic reorder by moving the existing DOM row into place
// instead of rebuilding the list. Replacing dozens of rows (images, layout,
// scroll anchoring) on every drop is what made releases jump; a single node
// move keeps every pixel that didn't logically move exactly where it was.
// Returns true when it handled the render (caller falls back to showQueue).
function _moveQueueRowDom(container, queue, fromIdx, toIdx, currentIdx) {
  try {
    if (!container) return false;
    const rows = Array.from(container.querySelectorAll(':scope > .queue-swipe-wrapper'));
    if (!rows.length) return false;
    const moving = rows.find((w) => Number(w.dataset && w.dataset.index) === fromIdx);
    if (!moving) return false;
    const others = rows.filter((w) => w !== moving);
    if (toIdx < 0 || toIdx > others.length) return false;
    // Never append past the lazy sentinel: it must stay the last child so
    // further chunks keep paging in below.
    const sentinel = container.querySelector(':scope > .queue-lazy-sentinel');
    const ref = others[toIdx] || null;
    if (ref) container.insertBefore(moving, ref);
    else if (sentinel) container.insertBefore(moving, sentinel);
    else container.appendChild(moving);
    container._lazyQueue = { queue: queue, currentIndex: currentIdx };
    // Renumber in place (positions ARE the new indices) and sync highlight.
    const now = Array.from(container.querySelectorAll(':scope > .queue-swipe-wrapper'));
    now.forEach((w, i) => {
      w.dataset.index = String(i);
      const item = w.querySelector('.queue-item');
      if (item) {
        item.dataset.index = String(i);
        item.classList.toggle('active', i === currentIdx);
        item.classList.toggle('playing', i === currentIdx && state.isPlaying);
      }
      const num = w.querySelector('.queue-num');
      if (num) num.textContent = String(i + 1);
      const rm = w.querySelector('.queue-more-menu [data-action="remove"]');
      if (rm) rm.hidden = (i === currentIdx);
    });
    try { container.dataset.lastActiveIndex = String(currentIdx); } catch (_) {}
    return true;
  } catch (_) { return false; }
}

// FLIP-glide the optimistic reorder so the drop reads as rows sliding into
// place instead of the list teleporting (the jerky "shifting scroll" on
// release). Callers capture First tops before the re-render and play the
// inversion after scroll has settled (restore + anchor glue first).
function _captureRowTops(container) {
  const tops = new Map();
  try {
    if (!container || !container.clientHeight) return tops;
    const rows = container.querySelectorAll(':scope > .queue-swipe-wrapper');
    for (const w of rows) {
      const item = w.querySelector('.queue-item');
      const raw = (w.dataset && w.dataset.index) || (item && item.dataset && item.dataset.index);
      const idx = Number(raw);
      if (!Number.isInteger(idx)) continue;
      tops.set(idx, w.getBoundingClientRect().top);
    }
  } catch (_) {}
  return tops;
}

function _flipReorder(container, firstTops, fromIdx, toIdx) {
  try {
    if (!container || !firstTops || !firstTops.size) return;
    if (typeof window !== 'undefined' && window.matchMedia &&
        window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const movers = [];
    const rows = container.querySelectorAll(':scope > .queue-swipe-wrapper');
    for (const w of rows) {
      const j = Number(w.dataset && w.dataset.index);
      if (!Number.isInteger(j)) continue;
      // Inverse map: which pre-move index now sits at post-move index j.
      let o;
      if (j === toIdx) o = fromIdx;
      else if (fromIdx < toIdx) o = (j >= fromIdx && j < toIdx) ? j + 1 : j;
      else o = (j > toIdx && j <= fromIdx) ? j - 1 : j;
      const first = firstTops.get(o);
      if (first === undefined) continue;
      const dy = first - w.getBoundingClientRect().top;
      if (!dy) continue;
      w.style.transition = 'none';
      w.style.transform = 'translateY(' + dy + 'px)';
      movers.push(w);
    }
    if (!movers.length) return;
    void container.offsetHeight; // commit the inversion before gliding home
    const raf = (typeof requestAnimationFrame === 'function') ? requestAnimationFrame : (fn) => fn();
    raf(() => {
      for (const w of movers) {
        if (!w.isConnected && w.isConnected !== undefined) continue;
        w.style.transition = 'transform .28s cubic-bezier(.22,1,.36,1)';
        w.style.transform = '';
      }
      setTimeout(() => {
        for (const w of movers) {
          try {
            if (w.isConnected === false) continue;
            w.style.transition = '';
            w.style.transform = '';
          } catch (_) {}
        }
      }, 350);
    });
  } catch (_) {}
}

/* ---- Reorder queue (drag complete) ---- */
async function reorderQueue(fromIndex, toIndex) {
  if (fromIndex === toIndex) return;
  // The user placed this song deliberately: hold the viewport at the drop
  // position through the optimistic re-render below and the confirming poll
  // snapshot (~500ms later), both of which otherwise force-scroll the list
  // back to the active row. _scrollQueueRowIntoView honors this window.
  state._suppressQueueScrollUntil = Date.now() + 2500;
  // Snapshot the lists' scroll positions so the full rebuild below restores
  // the drop viewport instead of jumping. (Same-scrollTop + same rows shows
  // the new order correctly; no extra compensation — see the note above.)
  const _reorderScrollTops = new Map();
  const _reorderFirstTops = new Map();
  for (const _id of ['np-queue-list', 'queue-list', 'queue-modal-body']) {
    try {
      const _c = document.getElementById(_id);
      if (!_c) continue;
      _reorderScrollTops.set(_c, _c.scrollTop);
      const _t = _captureRowTops(_c);
      if (_t.size) _reorderFirstTops.set(_c, _t);
    } catch (_) {}
  }
  // Optimistically reorder local queue data so the UI doesn't snap back
  // to the old position while waiting for the server to confirm.
  try {
    const queue = JSON.parse(state._lastQueueJson || '[]');
    let currentIdx = state._lastQueueIndex;
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
      state._lastQueueJson = JSON.stringify(queue);
      state._lastQueueIndex = currentIdx;
      // Keep the SSE/poll mirror in lockstep: it prefers window._lastQueueJson,
      // so leaving it stale would make the confirming snapshot look "changed"
      // and trigger a second full rebuild (plus a stale queue-modal render).
      try {
        window._lastQueueJson = state._lastQueueJson;
        window._lastQueueIndex = currentIdx;
      } catch (_) {}
      // Commit the new order to the live DOM by moving the existing row into
      // the reserved gap (no rebuild: no image/layout churn, no anchoring
      // storm, nothing for the eye to catch). Falls back to a full rebuild
      // only when the row isn't rendered (shouldn't happen — the lifted row
      // is always on screen — but never break the reorder over rendering).
      let _npHandled = false, _modalHandled = false;
      try {
        const _npList = document.getElementById('np-queue-list');
        const _npSection = document.getElementById('now-playing-section');
        if (_npList && _npSection && !_npSection.hidden) {
          _npHandled = _moveQueueRowDom(_npList, queue, fromIdx, toIdx, currentIdx);
        }
      } catch (_) {}
      if (!_npHandled) showQueue(queue, currentIdx);
      try {
        const _ov = document.getElementById('queue-modal-overlay');
        const _mb = document.getElementById('queue-modal-body');
        if (_ov && _ov.classList.contains('open') && _mb) {
          _modalHandled = _moveQueueRowDom(_mb, queue, fromIdx, toIdx, currentIdx);
        } else {
          _modalHandled = true; // sheet closed: renders fresh on next open
        }
      } catch (_) {}
      if (!_modalHandled) { try { refreshQueueModalIfOpen(); } catch (_) {} }
      for (const [_c, _top] of _reorderScrollTops) {
        try { _c.scrollTop = _top; } catch (_) {}
      }
      // Glide the rows into their new spots (FLIP) instead of teleporting:
      // the drop reads as a smooth slide, not a shifting snap.
      for (const [_c, _t] of _reorderFirstTops) {
        _flipReorder(_c, _t, fromIndex, toIndex);
      }
    }
  } catch (_) {}
  try {
    await api('/alexa/queue_reorder/', { from_index: fromIndex, to_index: toIndex });
    // Confirm with server data after a short delay. Don't blank state._lastQueueJson
    // here Ã¢â‚¬â€ the optimistic reorder above already matches what the server will
    // report, and invalidating the cache forces a needless full rebuild (the
    // visible flicker) as soon as the confirming snapshot arrives.
    schedulePollNowPlaying(500);
  } catch (e) {
    // Revert on error: force refresh from server
    state._lastQueueJson = '';
    schedulePollNowPlaying(300);
    toast(e.message, 'error');
  }
}

/* ---- Queue swipe gestures (mobile) ---- */
function _attachQueueSwipeGestures(wrapper, el, index, item, currentIndex) {
  const SWIPE_THRESHOLD = 80;
  const LOCK_DISTANCE = 8;
  const AXIS_BIAS = 1.2;
  let startX = 0, startY = 0, currentX = 0, gesture = 'pending';

  function resetSwipeState() {
    // Never undo the slide-out/collapse of a row whose removal is committed.
    if (wrapper._removing) return;
    wrapper.classList.remove('swiping-left', 'swiping-right');
    el.style.transition = '';
    el.style.transform = '';
    wrapper.style.transition = '';
    wrapper.style.height = '';
    wrapper.style.opacity = '';
    gesture = 'idle';
    currentX = 0;
  }

  el.addEventListener('touchstart', (e) => {
    if (wrapper._removing) return;
    if (e.target.closest('.queue-drag-handle') || e.touches.length !== 1) return;
    el._swipeSuppressClick = false;
    el._swipeAllowClick = false;
    _refreshQueueSwipeLikeLabel(wrapper, item);
    startX = e.touches[0].clientX;
    startY = e.touches[0].clientY;
    currentX = 0;
    gesture = 'pending';
    el.style.transition = 'none';
  }, { passive: true });

  el.addEventListener('touchmove', (e) => {
    if (e.target.closest('.queue-drag-handle') || e.touches.length !== 1) return;
    const dx = e.touches[0].clientX - startX;
    const dy = e.touches[0].clientY - startY;
    const absX = Math.abs(dx);
    const absY = Math.abs(dy);

    if (gesture === 'pending' && Math.max(absX, absY) >= LOCK_DISTANCE) {
      if (absY > absX * AXIS_BIAS) {
        gesture = 'scroll';
        return;
      }
      if (absX > absY * AXIS_BIAS) gesture = 'swipe';
    }

    if (gesture !== 'swipe') return;
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
    if (gesture !== 'swipe') {
      resetSwipeState();
      return;
    }
    const liveIdx = _liveQueueIndexOf(item, index);
    
    // Left swipe = delete
    const committedDelete = currentX < -SWIPE_THRESHOLD && !wrapper._removing;
    // Right swipe = like
    const committedLike = currentX > SWIPE_THRESHOLD && !wrapper._removing;
    // Only committed actions cancel the synthetic click that follows a touch.
    // A short horizontal wobble should still activate the row normally.
    if (committedDelete || committedLike) {
      el._swipeSuppressClick = true;
      el._swipeAllowClick = false;
    } else {
      el._swipeAllowClick = true;
    }

    if (committedDelete && liveIdx !== -1 && liveIdx !== state._lastQueueIndex) {
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
          state._lastQueueJson = '';
          schedulePollNowPlaying(300);
        } else {
          toast('CanÃ¢â‚¬â„¢t remove the playing track', 'error');
        }
      } else if (committedLike) {
        if (typeof toggleLike === 'function') {
          Promise.resolve(toggleLike(item)).then(() => {
            _refreshQueueSwipeLikeLabel(wrapper, item);
          });
        }
      }
      
      el.style.transition = 'transform .25s cubic-bezier(.22,1,.36,1)';
      el.style.transform = '';
      setTimeout(() => wrapper.classList.remove('swiping-left', 'swiping-right'), 260);
    }
    gesture = 'idle';
    currentX = 0;
  }, { passive: true });

  el.addEventListener('touchcancel', () => {
    el._swipeSuppressClick = false;
    el._swipeAllowClick = false;
    resetSwipeState();
  }, { passive: true });
}

/* ---- Queue drag-to-reorder ---- */
function _attachQueueDragReorder(el, listEl, originalIndex) {
  const handle = el.querySelector('.queue-drag-handle');
  if (!handle) return;

  let dragging = false, startY = 0, initialTop = 0, cloneEl = null, placeholder = null;
  let currentOver = -1, fromIdx = originalIndex;
  let sourceWrapper = null, draggedHeight = 0;

  // Auto-scroll state. Deliberately conservative: the zone is narrow, the
  // speed is low, and the pointer must dwell at the edge briefly before any
  // scrolling starts — otherwise just ferrying a song a few rows near the
  // visible edge sends the whole list drifting (and the rows sliding under a
  // stationary finger move the drop indicator, which reads as the queue
  // "shifting" on its own).
  let _scrollRafId = null;
  let _scrollSpeed = 0;
  let _scrollContainer = null;
  let _lastDragClientY = 0;
  let _edgeDir = 0;       // -1 (top), 1 (bottom), 0 (not at an edge)
  let _edgeSince = 0;
  const EDGE_ZONE = 24;   // px from container edge to trigger scroll
  const MAX_SPEED = 6;    // px per frame at the very edge
  const EDGE_DWELL_MS = 180; // continuous ms at the edge before scrolling

  function getItemElements() {
    // The lift source is collapsed (zero height) so the list already shows
    // the post-removal order; exclude it so drop indices come out directly
    // in post-removal coordinates with no +/-1 adjustment at release.
    return Array.from(listEl.querySelectorAll('.queue-swipe-wrapper:not(.drag-source)'));
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
      if (!dragging || !_scrollContainer) {
        _scrollRafId = null;
        return;
      }
      // Re-evaluate every frame from the last known pointer position: the
      // list moves under a stationary finger while scrolling, and the dwell
      // timer must be able to expire without waiting for another pointermove.
      _updateScrollSpeed(_lastDragClientY);
      if (_scrollSpeed !== 0) _scrollContainer.scrollTop += _scrollSpeed;
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
    _edgeDir = 0;
  }

  function _updateScrollSpeed(clientY) {
    if (!_scrollContainer) {
      _scrollSpeed = 0;
      _edgeDir = 0;
      return;
    }
    const rect = _scrollContainer.getBoundingClientRect();
    const distFromTop = clientY - rect.top;
    const distFromBottom = rect.bottom - clientY;
    const canUp = _scrollContainer.scrollTop > 0;
    const canDown = _scrollContainer.scrollTop <
      _scrollContainer.scrollHeight - _scrollContainer.clientHeight;

    let dir = 0;
    if (distFromTop < EDGE_ZONE && canUp) dir = -1;
    else if (distFromBottom < EDGE_ZONE && canDown) dir = 1;

    const now = (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
    if (dir === 0) {
      _edgeDir = 0;
      _scrollSpeed = 0;
      return;
    }
    if (dir !== _edgeDir) {
      // Freshly entered the edge (or switched edges): wait out the dwell so
      // a quick pass through the zone never jerks the list.
      _edgeDir = dir;
      _edgeSince = now;
    }
    if (now - _edgeSince < EDGE_DWELL_MS) {
      _scrollSpeed = 0;
      return;
    }
    // Scroll up — speed increases as pointer gets closer to edge
    const dist = dir === -1 ? distFromTop : distFromBottom;
    const ratio = 1 - (dist / EDGE_ZONE);
    const next = dir * (MAX_SPEED * Math.max(0, Math.min(1, ratio)));
    _scrollSpeed = next;
  }

  function updateAutoScroll(clientY) {
    _lastDragClientY = clientY;
    _updateScrollSpeed(clientY);
    // Keep the frame loop alive while dragging so the dwell timer can expire
    // (and the speed can track a moving container) even when the finger is
    // momentarily stationary.
    if (dragging && _scrollContainer && !_scrollRafId) startAutoScroll();
  }

  function beginDrag(clientY) {
    dragging = true;
    document.body.classList.add('drag-lock');
    startY = clientY;
    // Read the live position: a previous drop may have moved this row via a
    // DOM move (no rebuild), leaving the build-time originalIndex stale.
    const _srcWrap = (typeof el.closest === 'function') ? el.closest('.queue-swipe-wrapper') : null;
    const _liveFrom = _srcWrap ? Number(_srcWrap.dataset.index) : NaN;
    fromIdx = Number.isInteger(_liveFrom) ? _liveFrom : originalIndex;
    const rect = el.getBoundingClientRect();
    initialTop = rect.top;
    draggedHeight = Math.max(1, Math.round(rect.height || el.offsetHeight || 0));

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
    cloneEl.style.display = '';
    document.body.appendChild(cloneEl);

    // Reserve the space up front (the user's suggestion): collapse the source
    // slot and open a full-row gap at the target, so the list *during* the
    // drag already shows the final order. The release re-render then swaps a
    // same-size gap for the song instead of teleporting a full row across
    // the list — no down/up shove on place.
    sourceWrapper = el.closest('.queue-swipe-wrapper');
    if (sourceWrapper) sourceWrapper.classList.add('drag-source');
    el.classList.add('dragging');
    el.style.display = 'none';
    currentOver = fromIdx;
    _scrollContainer = findScrollContainer();
  }

  function moveDrag(clientY) {
    if (!dragging || !cloneEl) return;
    const dy = clientY - startY;
    cloneEl.style.top = (initialTop + dy) + 'px';

    // Auto-scroll when near the edges of the scrollable container
    updateAutoScroll(clientY);

    // Find which item we're over (source excluded: it is collapsed, so this
    // index is already in post-removal coordinates — the same coordinates
    // the server pop/insert and the optimistic splice use).
    const items = getItemElements();
    let targetIdx = fromIdx;
    for (let i = 0; i < items.length; i++) {
      const r = items[i].getBoundingClientRect();
      const mid = r.top + r.height / 2;
      if (clientY < mid) { targetIdx = i; break; }
      targetIdx = i + 1;
    }
    targetIdx = Math.min(targetIdx, items.length);
    if (targetIdx !== currentOver) {
      // Remove old placeholder
      const old = listEl.querySelector('.queue-drop-placeholder');
      if (old) old.remove();
      // Insert a full-row gap (not a thin line): the list then shows the
      // final order live, and the release render changes ~nothing.
      placeholder = document.createElement('div');
      placeholder.className = 'queue-drop-placeholder';
      if (draggedHeight > 0) placeholder.style.height = draggedHeight + 'px';
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

    // currentOver counts non-source rows before the gap, i.e. the insertion
    // index after removal — exactly what reorderQueue/the server expect, so
    // no -1 fiddling (and the very end of the list is reachable now).
    // getItemElements still excludes the collapsed source here.
    const liveCount = getItemElements().length; // N-1 while source is marked
    let toIdx = Math.max(0, Math.min(currentOver, liveCount));
    // Restore the source row before the re-render (the reorder rebuild
    // replaces it anyway; the no-op path below needs it back).
    if (sourceWrapper) sourceWrapper.classList.remove('drag-source');
    sourceWrapper = null;
    el.style.display = '';
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

async function playFromQueue(item, queueIndex, openPlaybackPage) {
  const serial = selectedSerial();
  if (!serial) return;
  if (!item.video_id) { toast('That recommendation cannot be played.', 'error'); return; }
  // Claim the intent and paint the row + banner *before* the await. Rapid
  // clicks down the queue previously each rendered after their own response,
  // so out-of-order responses made the selected-row highlight jump around.
  const mySeq = window.beginPlayIntent(item.video_id);
  state._lastPlayAttemptVideoId = item.video_id;
  if (window.preloadNowPlayingArtwork) window.preloadNowPlayingArtwork(item);
  const npInfo = {
    video_id: item.video_id,
    title: item.title,
    artist: item.artist,
    artists: item.artists || [],
    channelId: item.channelId || item.channel_id || item.artistId || item.artist_id || '',
    thumbnail: item.thumbnail
  };
  showNowPlaying(npInfo);
  progress.resetPending(item.video_id);
  state.isPlaying = true;
  state.lastActionIntent = true;
  syncPlayPause();
  if (typeof queueIndex === 'number' && queueIndex >= 0) {
    // Move the highlight immediately to the row that was actually clicked.
    state._lastQueueIndex = queueIndex;
    window._lastQueueIndex = queueIndex;
    updateQueueActive(queueIndex);
    if (window.updateQueueModalActive) window.updateQueueModalActive(queueIndex);
  }
  toast('Playing \u201c' + item.title + '\u201d\u2026');
  try {
    // Pass along metadata so a song that isn't in the server's queue yet (e.g.
    // a recommendation tile) plays from the supplied title/artist/thumbnail
    // instead of a metadata lookup that can fail ("no longer available").
    // queue_index disambiguates the same song appearing more than once in the
    // queue -- without it the server matches by video_id alone and always
    // jumps to that song's *first* occurrence, even when a later duplicate
    // was the one actually clicked.
    const data = await api('/alexa/play_queue/', {
      serial,
      video_id: item.video_id,
      title: item.title || '',
      artist: item.artist || '',
      artists: item.artists || [],
      artist_id: item.artist_id || item.artistId || item.channelId || item.channel_id || '',
      thumbnail: (item.thumbnail && item.thumbnail.url) || item.thumbnail || '',
      duration_ms: item.duration_ms || 0,
      queue_index: typeof queueIndex === 'number' ? queueIndex : undefined,
      // Lets the server drop this play if a later click supersedes it.
      intent_seq: mySeq,
    });
    // Superseded by a later click: that click owns the UI now.
    if (!window.isCurrentPlayIntent(mySeq)) return;
    toast('Playing', 'ok');
    if (openPlaybackPage && window.matchMedia('(min-width: 900px)').matches) window.navigateTo('#now-playing');
    schedulePollNowPlaying(3000);
    // Optimistically prepend this song to history right away so "Recently
    // Played" shows it immediately without waiting for the server webhook.
    const optimisticEntry = {
      video_id: item.video_id,
      title: item.title || '',
      artist: item.artist || '',
      thumbnail_url: (item.thumbnail && item.thumbnail.url) || item.thumbnail || '',
      play_count: 1,
    };
    state._historyCache = [optimisticEntry, ...state._historyCache.filter(e => e.video_id !== item.video_id)].slice(0, 100);
    syncHistoryTriggerVisibility();
    // If the modal is open, prepend the new row with a slide-in animation.
    const historyPage = document.getElementById('history-page');
    if (historyPage && !historyPage.hidden) {
      const list = historyPage.querySelector('.history-list');
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
        renderHistoryModalList(state._historyCache);
      }
    }
    // Still schedule server refreshes to pick up proper metadata / dedup
    scheduleHistoryRefresh();
  } catch (e) {
    if (!window.isCurrentPlayIntent(mySeq)) return;
    // Painting before the await means a failed play has already rendered as
    // playing. Undo the optimistic state and let server state take over.
    window.settlePlayIntent(item.video_id);
    if (window.progress && window.progress.cancelPending) window.progress.cancelPending();
    state.isPlaying = false;
    state.lastActionIntent = false;
    syncPlayPause();
    schedulePollNowPlaying(0);
    toast(e.message, 'error');
  }
}

// Start a playlist/album as one queue operation.  Collection buttons must not
// fall through to playFromQueue/playResult with only their first track: that is
// a single-song play and intentionally grows a radio recommendation queue.
async function playCollection(items, options) {
  options = options || {};
  const serial = selectedSerial();
  if (!serial) return;
  const playlistId = String(options.playlistId || '').replace(/^VL/, '');
  const startVideoId = String(options.startVideoId || '').trim();
  const requestedStartIndex = Number.isInteger(options.startIndex) ? options.startIndex : 0;
  const queueItems = (items || []).map(function (item) {
    const artists = Array.isArray(item.artists)
      ? item.artists.map(function (artist) {
          return typeof artist === 'string' ? artist : artist && artist.name;
        }).filter(Boolean).join(', ')
      : '';
    const thumbnails = item.thumbnails;
    const thumbnail = (item.thumbnail && item.thumbnail.url) || item.thumbnail ||
      (Array.isArray(thumbnails) && thumbnails.length ? thumbnails[thumbnails.length - 1].url : '') || '';
    return {
      video_id: item.video_id || item.videoId || '',
      title: item.title || '',
      artist: item.artist || artists,
      artists: Array.isArray(item.artists) ? item.artists : [],
      artist_id: item.artist_id || item.artistId || item.channelId || item.channel_id || '',
      thumbnail: thumbnail,
      duration_ms: item.duration_ms || 0,
    };
  }).filter(function (item) { return item.video_id; });

  if (!playlistId && !queueItems.length) {
    toast('This collection has no playable songs.', 'error');
    return;
  }

  // The first track is only known after the server resolves the collection,
  // so this path claims the intent without a video_id and pins it once the
  // response arrives. beginPlayIntent still bumps the sequence, so a later
  // single-song click supersedes this collection play.
  const mySeq = window.beginPlayIntent('');
  if (window.progress && window.progress.resetPending) window.progress.resetPending();
  toast(options.shuffle ? 'Shuffling collection…' : 'Playing collection…');
  try {
    // Use the queue endpoint for both kinds of collection. Besides retaining
    // the whole collection in Up Next, it lets a track-row click start at the
    // exact song the user selected instead of falling back to a radio queue.
    const data = await api('/alexa/play_queue/', {
      serial: serial,
      playlist_id: playlistId || undefined,
      queue_items: playlistId ? undefined : queueItems,
      target_video_id: startVideoId || undefined,
      start_index: requestedStartIndex,
      shuffle: !!options.shuffle,
      // Lets the server drop this play if a later click supersedes it.
      intent_seq: mySeq,
    });
    // Superseded by a later click: that click owns the UI now.
    if (!window.isCurrentPlayIntent(mySeq)) return data;
    const first = data && data.now_playing;
    if (first && first.video_id) {
      state._lastPlayAttemptVideoId = first.video_id;
      state._playIntentVideoId = first.video_id;
      state._playIntentAt = Date.now();
      showNowPlaying(first);
      progress.resetPending(first.video_id);
    }
    state.isPlaying = true;
    state.lastActionIntent = true;
    syncPlayPause();
    toast(options.shuffle ? 'Shuffle started' : 'Playing', 'ok');
    if (options.openPlaybackPage && window.matchMedia('(min-width: 900px)').matches) {
      window.navigateTo('#now-playing');
    }
    schedulePollNowPlaying(1000);
    return data;
  } catch (error) {
    if (!window.isCurrentPlayIntent(mySeq)) return null;
    window.settlePlayIntent(state._playIntentVideoId);
    if (window.progress && window.progress.cancelPending) window.progress.cancelPending();
    toast((error && error.message) || 'Could not play collection', 'error');
    return null;
  }
}

async function addCollectionToQueue(items, options) {
  options = options || {};
  const serial = selectedSerial();
  if (!serial) return null;
  const playlistId = String(options.playlistId || '').replace(/^VL/, '');
  const queueItems = (items || []).map(function (item) {
    const artists = Array.isArray(item.artists)
      ? item.artists.map(function (artist) {
          return typeof artist === 'string' ? artist : artist && artist.name;
        }).filter(Boolean).join(', ')
      : '';
    const thumbnails = item.thumbnails;
    return {
      video_id: item.video_id || item.videoId || '',
      title: item.title || '',
      artist: item.artist || artists,
      thumbnail: (item.thumbnail && item.thumbnail.url) || item.thumbnail ||
        (Array.isArray(thumbnails) && thumbnails.length ? thumbnails[thumbnails.length - 1].url : '') || '',
      duration_ms: item.duration_ms || 0,
    };
  }).filter(function (item) { return item.video_id; });

  if (!playlistId && !queueItems.length) {
    toast('This collection has no playable songs.', 'error');
    return null;
  }
  // An empty player has no meaningful queue tail. In that case, installing the
  // collection as the active queue gives the button a useful result.
  if (!state._hasTrack) return playCollection(queueItems, { playlistId: playlistId });

  toast('Adding collection to queue…');
  try {
    const data = await api('/alexa/queue_add/', {
      serial: serial,
      position: 'last',
      playlist_id: playlistId || undefined,
      queue_items: playlistId ? undefined : queueItems,
    });
    const count = Number(data && data.added_count) || queueItems.length;
    const skipped = Number(data && data.skipped_count) || 0;
    const message = count + (count === 1 ? ' song added to queue' : ' songs added to queue') +
      (skipped ? ' (' + skipped + ' unavailable skipped)' : '');
    toast(message, 'ok');
    schedulePollNowPlaying(500);
    return data;
  } catch (error) {
    toast((error && error.message) || 'Could not add collection to queue', 'error');
    return null;
  }
}

// The 'started' webhook that records a listen can lag a few seconds behind the
// play dispatch, so poll history a few times rather than once.
function scheduleHistoryRefresh() {
  [2500, 5000, 9000].forEach(ms => setTimeout(loadHistory, ms));
}

/* ---- Recently listened ---- */
// Server-side history, recorded when the skill confirms a real playback start.

/* ---- Queue panel toggle (playbar button, desktop) ---- */
/* The queue button navigates to the #now-playing page where the queue
   is embedded as the right column. The old floating panel is gone. */
(function () {
  const btn = document.getElementById('queue-toggle-btn');
  if (!btn) return;
  btn.addEventListener('click', () => {
    // Navigate to the now-playing page — queue is built in as the right column.
    if (state._hasTrack) {
      window.navigateTo('#now-playing');
    }
  });
  // Never highlight as "active" since floating panel no longer exists
  btn.classList.remove('active');
})();

/* ---- Queue bottom-sheet (mobile) ---- */
// Renders into #queue-modal-body reusing the panel's row builder. Opened from
// the expanded now-playing sheet; sse.js calls _renderQueueModal to keep it
// fresh while open.
(function () {
  const overlay = document.getElementById('queue-modal-overlay');
  const body = document.getElementById('queue-modal-body');
  if (!overlay || !body) return;
  const modal = document.getElementById('queue-modal');

  // Full modal rebuilds (replaceChildren) mid-scroll flash thumbnails and
  // fight the gesture. Track the last user scroll so renders can defer until
  // the scroll settles, and stamp the time on every scroll event.
  let pendingModalRender = false;
  let modalScrollIdleTimer = 0;
  body.addEventListener('scroll', () => {
    body._lastUserScrollAt = Date.now();
    if (pendingModalRender) {
      clearTimeout(modalScrollIdleTimer);
      modalScrollIdleTimer = setTimeout(() => {
        pendingModalRender = false;
        renderQueueModal();
      }, 350);
    }
  }, { passive: true });

  function _queueSnapshot() {
    // SSE writes window._lastQueueJson; optimistic edits write the appState
    // copy. Prefer whichever is non-empty, matching what the user last saw.
    try { return JSON.parse(window._lastQueueJson || state._lastQueueJson || '[]'); }
    catch (_) { return []; }
  }

  function renderQueueModal() {
    const preserveOpenScroll = overlay.classList.contains('open');
    const previousScrollTop = body.scrollTop;
    const queue = _queueSnapshot();
    const idx = (typeof window._lastQueueIndex === 'number' && window._lastQueueIndex >= 0)
      ? window._lastQueueIndex : state._lastQueueIndex;
    // The sheet is refreshed on every SSE push while open. If the user is
    // actively scrolling (e.g. flicking up through the list), defer the full
    // rebuild until the scroll settles — destroying/recreating all rows
    // mid-gesture flashes thumbnails and visibly flickers. Keep the highlight
    // fresh on the existing rows in the meantime (cheap class toggles, no
    // image reload), then do the real rebuild once idle.
    if (preserveOpenScroll && Date.now() - (body._lastUserScrollAt || 0) < 400) {
      for (const el of body.querySelectorAll('.queue-item')) {
        const isCurrent = Number(el.dataset.index) === Number(idx);
        el.classList.toggle('active', isCurrent);
        el.classList.toggle('playing', isCurrent && state.isPlaying);
      }
      if (!pendingModalRender) {
        pendingModalRender = true;
        clearTimeout(modalScrollIdleTimer);
        modalScrollIdleTimer = setTimeout(() => {
          pendingModalRender = false;
          renderQueueModal();
        }, 350);
      }
      return;
    }
    if (!queue.length) {
      const empty = document.createElement('div');
      empty.className = 'queue-modal-empty';
      empty.textContent = 'No songs in queue';
      body.replaceChildren(empty);
      return;
    }
    const limit = Math.min(queue.length, Math.max(QUEUE_RENDER_CHUNK, idx + 11,
      // Never shrink the rendered window while open: fewer rows collapse the
      // sheet mid-scroll and the browser clamps scrollTop (same violent jump
      // as the main list's rebuild truncation).
      body.querySelectorAll(':scope > .queue-swipe-wrapper').length));
    body._lazyQueue = { queue, currentIndex: idx };
    // Transplant already-loaded <img> elements (same as renderNpQueue) so a
    // rebuild while open doesn't re-fetch/re-decode every cover — that
    // opacity-0 → loaded fade on all visible rows is the flicker.
    const thumbsById = new Map();
    body.querySelectorAll(':scope > .queue-swipe-wrapper').forEach((w) => {
      const id = w.dataset.videoId || '';
      const img = w.querySelector('img.queue-thumb.loaded');
      if (id && img && !thumbsById.has(id)) thumbsById.set(id, img);
    });
    const rows = [];
    for (let i = 0; i < limit; i++) rows.push(_buildQueueRow(body, queue[i], i, idx, thumbsById));
    body.replaceChildren(...rows);
    _syncQueueSentinel(body);
    if (preserveOpenScroll) {
      body.scrollTop = previousScrollTop;
      // The sheet is refreshed on every SSE push while open (full rebuild,
      // no diffing). If the active track changed since the last refresh,
      // follow it; otherwise leave the user's manual scroll position alone.
      _scrollQueueRowIntoView(body, idx, false);
    }
  }

  function scrollPlayingQueueRowToTop() {
    const activeItem = body.querySelector('.queue-item.active');
    const activeRow = activeItem?.closest('.queue-swipe-wrapper') || activeItem;
    if (!activeRow) return;
    const bodyRect = body.getBoundingClientRect();
    const rowRect = activeRow.getBoundingClientRect();
    body.scrollTop = Math.max(0, body.scrollTop + rowRect.top - bodyRect.top);
    // Record the index we just scrolled to so the next SSE-driven
    // updateQueueModalActive() call doesn't immediately re-scroll on top of
    // this deliberate open-time placement.
    if (activeItem) body.dataset.lastActiveIndex = activeItem.dataset.index || '';
  }

  let inlineMorph = null;
  let inlineMorphProgress = 0;
  let inlineMorphFrame = 0;

  const clamp01 = (value) => Math.max(0, Math.min(1, Number(value) || 0));
  const fadeBetween = (value, start, end) => {
    const t = clamp01((value - start) / (end - start));
    return t * t * (3 - 2 * t);
  };

  function resetInlineMorphStyles() {
    cancelAnimationFrame(inlineMorphFrame);
    inlineMorphFrame = 0;
    if (inlineMorph) {
      [inlineMorph.sourceLabel, inlineMorph.sourceList, inlineMorph.targetTitle, inlineMorph.targetBody]
        .filter(Boolean)
        .forEach((element) => {
          element.style.translate = '';
          element.style.opacity = '';
          element.style.visibility = '';
          element.style.willChange = '';
        });
      inlineMorph.targetRows.forEach((row) => {
        row.style.filter = '';
        row.style.opacity = '';
        row.style.willChange = '';
      });
    }
    modal.style.clipPath = '';
    modal.style.maskImage = '';
    modal.style.webkitMaskImage = '';
    modal.style.opacity = '';
    modal.style.willChange = '';
    overlay.style.removeProperty('--queue-drag-progress');
    overlay.style.removeProperty('--queue-detail-progress');
    overlay.style.removeProperty('--queue-background-progress');
    overlay.classList.remove('queue-origin-expanded', 'queue-origin-closing', 'queue-origin-open');
    inlineMorph = null;
    inlineMorphProgress = 0;
  }

  function prepareInlineMorph(sourceRects) {
    const sourceLabel = document.querySelector('#mobile-queue-handle .mobile-queue-label');
    const sourceList = document.getElementById('mobile-inline-queue');
    const targetTitle = modal.querySelector('.queue-modal-header h3');
    const targetBody = body;
    if (!sourceLabel || !sourceList || !targetTitle) return false;

    const modalRect = modal.getBoundingClientRect();
    const targetTitleRect = targetTitle.getBoundingClientRect();
    const sourceRow = sourceList.querySelector('.queue-swipe-wrapper, .queue-item');
    const activeItem = body.querySelector('.queue-item.active');
    const targetRow = activeItem?.closest('.queue-swipe-wrapper') ||
      activeItem || body.querySelector('.queue-swipe-wrapper, .queue-item');
    const allTargetRows = Array.from(body.children);
    const activeRowPosition = Math.max(0, allTargetRows.indexOf(targetRow));
    const targetRows = allTargetRows.slice(activeRowPosition, activeRowPosition + 8);
    const sourceRowRect = sourceRow ? sourceRow.getBoundingClientRect() : sourceRects.list;
    const targetRowRect = targetRow ? targetRow.getBoundingClientRect() : targetBody.getBoundingClientRect();
    const revealTop = Math.max(0, Math.min(modalRect.height, sourceRects.label.top - modalRect.top));
    const revealBottom = Math.max(revealTop + 1, Math.min(
      modalRect.height,
      sourceRects.list.bottom - modalRect.top
    ));

    inlineMorph = {
      sourceLabel,
      sourceList,
      targetTitle,
      targetBody,
      targetRows,
      labelDx: targetTitleRect.left - sourceRects.label.left,
      labelDy: targetTitleRect.top - sourceRects.label.top,
      listDx: targetRowRect.left - sourceRowRect.left,
      listDy: targetRowRect.top - sourceRowRect.top,
      revealTop,
      revealBottom,
      modalHeight: modalRect.height
    };
    sourceLabel.style.visibility = 'hidden';
    sourceList.style.visibility = 'hidden';
    targetTitle.style.opacity = '1';
    targetBody.style.opacity = '1';
    targetTitle.style.willChange = 'translate';
    targetBody.style.willChange = 'translate';
    targetRows.forEach((row) => { row.style.willChange = 'opacity'; });
    modal.style.maskImage = 'none';
    modal.style.webkitMaskImage = 'none';
    modal.style.opacity = '1';
    modal.style.willChange = 'clip-path';
    return true;
  }

  function setInlineQueueProgress(progress) {
    if (!overlay.classList.contains('queue-origin-open') || !modal || !inlineMorph) return;
    const value = clamp01(progress);
    const remaining = 1 - value;
    const m = inlineMorph;

    inlineMorphProgress = value;
    overlay.style.setProperty('--queue-drag-progress', value.toFixed(4));
    overlay.style.setProperty('--queue-detail-progress', fadeBetween(value, .76, .96).toFixed(4));
    overlay.style.setProperty('--queue-background-progress', fadeBetween(value, 0, 1).toFixed(4));
    const revealTop = m.revealTop * remaining;
    const revealBottom = m.revealBottom + (m.modalHeight - m.revealBottom) * value;
    const clipBottom = Math.max(0, m.modalHeight - revealBottom);
    modal.style.clipPath = `inset(${revealTop.toFixed(1)}px 0 ${clipBottom.toFixed(1)}px 0)`;

    m.targetTitle.style.translate = `${(-m.labelDx * remaining).toFixed(2)}px ${(-m.labelDy * remaining).toFixed(2)}px`;
    m.targetBody.style.translate = `${(-m.listDx * remaining).toFixed(2)}px ${(-m.listDy * remaining).toFixed(2)}px`;
    m.targetRows.forEach((row, index) => {
      const startOpacity = [0.92, 0.72, 0.42, 0.22][index] ?? 0.08;
      row.style.opacity = String(startOpacity + (1 - startOpacity) * value);
    });
  }

  function completeInlineMorphClose() {
    // The morph has already reached its inline start state. Hide the overlay
    // without its normal fade before removing morph variables; otherwise the
    // base full-width sheet can appear for one transition frame.
    overlay.style.transition = 'none';
    overlay.classList.remove('open');
    overlay.dataset.queueClosing = '0';
    resetInlineMorphStyles();
    void overlay.offsetWidth;
    requestAnimationFrame(() => { overlay.style.transition = ''; });
    if (window.syncModalScrollLock) syncModalScrollLock();
  }

  function animateInlineMorph(target, onComplete) {
    cancelAnimationFrame(inlineMorphFrame);
    const from = inlineMorphProgress;
    const to = clamp01(target);
    const distance = Math.abs(to - from);
    if (distance < .001) {
      setInlineQueueProgress(to);
      onComplete?.();
      return;
    }
    const started = performance.now();
    const duration = Math.max(120, 300 * distance);
    const tick = (now) => {
      const elapsed = clamp01((now - started) / duration);
      const eased = 1 - Math.pow(1 - elapsed, 3);
      setInlineQueueProgress(from + (to - from) * eased);
      if (elapsed < 1) inlineMorphFrame = requestAnimationFrame(tick);
      else {
        inlineMorphFrame = 0;
        onComplete?.();
      }
    };
    inlineMorphFrame = requestAnimationFrame(tick);
  }

  let queueHistoryEntry = false;
  // Single-flight for the history-backed close: one tap fires the backdrop's
  // pointerdown + touchstart (+ click), and without this each of them would
  // dispatch history.back(). The extra pop eats a real page entry, whose
  // popstate falls through to the router's now-playing branch and minimizes
  // the player along with the queue.
  let queueHistoryBackPending = false;

  function openQueueModal(options) {
    renderQueueModal();
    queueHistoryBackPending = false;
    if (window.matchMedia('(max-width: 899px)').matches && !queueHistoryEntry && window.history && window.history.pushState) {
      window.history.pushState({ queueModal: true }, '', window.location.href);
      queueHistoryEntry = true;
    }
    const expandFromInline = options && options.fromInline && window.matchMedia('(max-width: 899px)').matches;
    const interactive = expandFromInline && options && options.interactive;
    if (expandFromInline) {
      const sourceLabel = document.querySelector('#mobile-queue-handle .mobile-queue-label');
      const sourceList = document.getElementById('mobile-inline-queue');
      if (sourceLabel && sourceList) {
        const sourceRects = {
          label: sourceLabel.getBoundingClientRect(),
          list: sourceList.getBoundingClientRect()
        };
        overlay.classList.add('queue-origin-open', 'open');
        scrollPlayingQueueRowToTop();
        if (prepareInlineMorph(sourceRects)) {
          setInlineQueueProgress(0);
          if (!interactive) finishInlineQueueProgress(true);
        } else {
          resetInlineMorphStyles();
          overlay.classList.add('open');
        }
      } else {
        overlay.classList.add('open');
        scrollPlayingQueueRowToTop();
      }
    } else {
      overlay.style.removeProperty('--queue-drag-progress');
      overlay.classList.add('open');
      if (window.matchMedia('(max-width: 899px)').matches) scrollPlayingQueueRowToTop();
    }
    if (window.syncModalScrollLock) syncModalScrollLock();
  }

  function finishInlineQueueProgress(shouldOpen) {
    if (!overlay.classList.contains('queue-origin-open') || !inlineMorph) return;
    if (shouldOpen) {
      overlay.classList.remove('queue-origin-closing');
      animateInlineMorph(1, () => overlay.classList.add('queue-origin-expanded'));
    } else {
      overlay.classList.add('queue-origin-closing');
      animateInlineMorph(0, completeInlineMorphClose);
    }
  }

  function closeQueueModal(fromHistory) {
    // A second backdrop event from the same tap must not dispatch another
    // history.back() while the first pop is still in flight (see above).
    if (!fromHistory && queueHistoryBackPending) return;
    if (overlay.classList.contains('queue-origin-open') && inlineMorph) {
      if (!fromHistory && queueHistoryEntry && window.history && window.history.back) {
        queueHistoryBackPending = true;
        window.history.back();
        return;
      }
      if (overlay.classList.contains('queue-origin-closing')) return;
      finishInlineQueueProgress(false);
      return;
    }
    // Already closed (e.g. pointerdown already dismissed the sheet and the
    // trailing touchstart/click from the same tap arrived before popstate):
    // never dispatch another history.back() for it.
    if (!fromHistory && !overlay.classList.contains('open')) return;
    overlay.classList.remove('open');
    overlay.style.removeProperty('--queue-drag-progress');
    if (!fromHistory && queueHistoryEntry && window.history && window.history.back) {
      queueHistoryBackPending = true;
      window.history.back();
      return;
    }
    queueHistoryEntry = false;
    if (modal) {
      modal.style.transition = '';
      modal.style.transform = '';
    }
    if (window.syncModalScrollLock) syncModalScrollLock();
  }

  const closeBtn = document.getElementById('queue-modal-close');
  if (closeBtn) closeBtn.addEventListener('click', closeQueueModal);
  const closeFromBackdrop = (event) => {
    if (event.target === overlay) {
      event.preventDefault();
      closeQueueModal();
    }
  };
  overlay.addEventListener('pointerdown', closeFromBackdrop);
  overlay.addEventListener('touchstart', closeFromBackdrop, { passive: false });
  overlay.addEventListener('click', closeFromBackdrop);
  const openBtn = document.getElementById('queue-modal-btn');
  if (openBtn) openBtn.addEventListener('click', openQueueModal);

  // Drag the queue sheet from its header to close it. Keep the queue body as
  // a native scroll surface so upward/downward scrolling cannot start the
  // sheet close animation and nudge the content.
  const dragHandle = document.getElementById('queue-modal-drag');
  const dragHeader = document.querySelector('.queue-modal-header');
    const dragSurfaces = [
      { element: dragHeader || dragHandle, fromBody: false }
    ].filter(({ element }) => element);
  if (dragSurfaces.length && modal) {
    let startY = 0;
    let lastY = 0;
    let startX = 0;
    let startTime = 0;
    let dragging = false;
    let morphDragging = false;
    let bodyDragging = false;
    // The close (cross) button lives inside the header drag surface. Drags
    // starting on it must still move the sheet, while plain taps on it must
    // still click through to the button's own close handler.
    let startedOnControl = false;
    let activePointerId = null;
    let captureSurface = null;
    const CONTROL_SEL = '.queue-modal-close, button, a, input, select, textarea, [contenteditable="true"]';
    const isOnControl = (event) => {
      const t = event && event.target;
      return !!(t && t.closest && t.closest(CONTROL_SEL));
    };

    const beginDrag = (clientY, fromBody, clientX = 0) => {
      if (fromBody && body.scrollTop > 1) return false;
      startY = lastY = clientY;
      startX = clientX;
      startTime = performance.now();
      dragging = true;
      bodyDragging = fromBody;
      morphDragging = overlay.classList.contains('queue-origin-open') && !!inlineMorph;
      cancelAnimationFrame(inlineMorphFrame);
      inlineMorphFrame = 0;
      if (!morphDragging) modal.style.transition = 'none';
      return true;
    };
    const moveDrag = (clientY, event, clientX = startX) => {
      if (!dragging) return;
      // Tap slop for gestures that started on the close button: a plain tap
      // stays a click (sheet untouched), a real move becomes a sheet drag.
      if (startedOnControl &&
          Math.abs(clientY - startY) <= 8 && Math.abs(clientX - startX) <= 8) return;
      // Late capture: the finger left tap slop, so this is a sheet drag now —
      // capture to keep receiving moves even off the button.
      if (startedOnControl && captureSurface && activePointerId != null) {
        try { captureSurface.setPointerCapture?.(activePointerId); } catch (_) {}
        startedOnControl = false;
        captureSurface = null;
      }
      lastY = clientY;
      const rawDistance = clientY - startY;
      if (bodyDragging && (
        body.scrollTop > 1 ||
        rawDistance <= 4 ||
        Math.abs(clientX - startX) > rawDistance
      )) return;
      if (morphDragging) {
        const distance = Math.max(0, rawDistance);
        const closeDistance = Math.max(240, Math.min(360, window.innerHeight * .34));
        setInlineQueueProgress(1 - distance / closeDistance);
        event?.preventDefault?.();
        return;
      }
      // The sheet only closes downward: ignore upward drags entirely so the
      // modal can never be pushed above its resting position.
      const dy = clientY - startY;
      if (dy <= 0) {
        modal.style.transform = '';
        return;
      }
      const offset = Math.min(window.innerHeight * 0.9, dy);
      modal.style.transform = `translateY(${offset}px)`;
      event?.preventDefault?.();
    };
    const endDrag = () => {
      if (!dragging) return;
      const distance = lastY - startY;
      const elapsed = Math.max(1, performance.now() - startTime);
      const velocity = distance / elapsed;
      dragging = false;
      bodyDragging = false;
      const wasTapOnControl = startedOnControl;
      startedOnControl = false;
      activePointerId = null;
      captureSurface = null;
      if (wasTapOnControl && Math.abs(distance) <= 8 && Math.abs(velocity) < 0.3) {
        // Plain tap on the close button: leave the sheet alone and let the
        // button's own click handler close it.
        modal.style.transition = '';
        modal.style.transform = '';
        return;
      }
      if (morphDragging) {
        morphDragging = false;
        finishInlineQueueProgress(inlineMorphProgress >= .72 && velocity < .55);
        return;
      }
      modal.style.transition = '';
      if (distance > 70 || velocity > 0.55) {
        closeQueueModal();
      } else {
        modal.style.transform = '';
      }
    };

    dragSurfaces.forEach(({ element: surface, fromBody }) => {
      surface.addEventListener('pointerdown', (event) => {
        startedOnControl = isOnControl(event);
        activePointerId = event.pointerId;
        captureSurface = surface;
        if (!beginDrag(event.clientY, fromBody, event.clientX)) {
          startedOnControl = false;
          activePointerId = null;
          captureSurface = null;
          return;
        }
        // Defer capture when starting on the close button so a plain tap
        // still clicks the button; capture happens on first real move.
        if (!startedOnControl && !fromBody) surface.setPointerCapture?.(event.pointerId);
      });
      surface.addEventListener('pointermove', (event) => moveDrag(event.clientY, event, event.clientX));
      surface.addEventListener('pointerup', endDrag);
      surface.addEventListener('pointercancel', endDrag);

      surface.addEventListener('touchstart', (event) => {
        if (!event.touches.length) return;
        startedOnControl = isOnControl(event);
        activePointerId = null;
        captureSurface = null;
        if (!beginDrag(event.touches[0].clientY, fromBody, event.touches[0].clientX)) {
          startedOnControl = false;
        }
      }, { passive: true });
      surface.addEventListener('touchmove', (event) => {
        if (event.touches.length) {
          moveDrag(event.touches[0].clientY, event, event.touches[0].clientX);
        }
      }, { passive: false });
      surface.addEventListener('touchend', endDrag, { passive: true });
      surface.addEventListener('touchcancel', endDrag, { passive: true });
    });
  }

  window._renderQueueModal = renderQueueModal;
  window._openQueueModal = openQueueModal;
  window._closeQueueModal = closeQueueModal;
  window._closeQueueModalFromHistory = function () {
    queueHistoryEntry = false;
    queueHistoryBackPending = false;
    closeQueueModal(true);
  };
  window._queueModalHistoryOpen = function () { return queueHistoryEntry; };
  window._setInlineQueueProgress = setInlineQueueProgress;
  window._finishInlineQueueProgress = finishInlineQueueProgress;
})();

/* ---- Mobile Queue drag-up panel ---- */
(function () {
  const handle = document.getElementById('mobile-queue-handle');
  if (!handle) return;

  let startY = 0;
  let lastY = 0;
  let pointerId = null;
  let dragging = false;
  let modalStarted = false;
  let progress = 0;
  const dragDistance = () => Math.max(240, Math.min(360, window.innerHeight * .34));
  const updateProgress = (clientY, event) => {
    lastY = clientY;
    const distance = startY - clientY;
    if (!modalStarted && distance < 8) return;
    if (!modalStarted) {
      if (window._openQueueModal) {
        window._openQueueModal({ fromInline: true, interactive: true });
        modalStarted = true;
      } else {
        return;
      }
    }
    progress = Math.max(0, Math.min(1, distance / dragDistance()));
    handle.style.setProperty('--queue-drag-fill', `${progress * 100}%`);
    handle.classList.add('queue-dragging');
    window._setInlineQueueProgress?.(progress);
    if (event?.cancelable) event.preventDefault();
  };
  const finishDrag = () => {
    if (!dragging) return;
    dragging = false;
    pointerId = null;
    handle.classList.remove('queue-dragging');
    handle.style.removeProperty('--queue-drag-fill');
    if (modalStarted) window._finishInlineQueueProgress?.(progress >= 0.5);
    modalStarted = false;
    progress = 0;
  };

  handle.addEventListener('pointerdown', (event) => {
    if (window.matchMedia('(min-width: 900px)').matches) return;
    pointerId = event.pointerId;
    startY = event.clientY;
    lastY = startY;
    dragging = true;
    handle.setPointerCapture?.(pointerId);
  });

  handle.addEventListener('pointermove', (event) => {
    if (!dragging || event.pointerId !== pointerId) return;
    updateProgress(event.clientY, event);
  });

  const endDrag = (event) => {
    if (event.pointerId !== pointerId) return;
    finishDrag();
  };
  handle.addEventListener('pointerup', endDrag);
  handle.addEventListener('pointercancel', endDrag);

  // Fallback for mobile browsers that do not deliver a complete pointer
  // gesture for a vertically draggable element.
  handle.addEventListener('touchstart', (event) => {
    if (window.matchMedia('(min-width: 900px)').matches || !event.touches.length) return;
    startY = event.touches[0].clientY;
    lastY = startY;
    dragging = true;
  }, { passive: true });
  handle.addEventListener('touchmove', (event) => {
    if (!dragging || !event.touches.length) return;
    updateProgress(event.touches[0].clientY, event);
  }, { passive: false });
  handle.addEventListener('touchend', () => {
    finishDrag();
  }, { passive: true });
  handle.addEventListener('touchcancel', finishDrag, { passive: true });
})();

  window.addToQueue = addToQueue;
  window._attachSwipeGesture = _attachSwipeGesture;
  window.attachResultSwipeGesture = _attachSwipeGesture;
  window.attachQueueItemTap = attachQueueItemTap;
  window._renderedQueueRows = _renderedQueueRows;
  window._buildQueueRow = _buildQueueRow;
  window._appendLazyQueueRows = _appendLazyQueueRows;
  window._syncQueueSentinel = _syncQueueSentinel;
  window.showQueue = showQueue;
window.renderNpQueue = renderNpQueue;
  window._queueMoreMenuHtml = _queueMoreMenuHtml;
  window._wireQueueMoreMenu = _wireQueueMoreMenu;
  window._closeAllQueueMenus = _closeAllQueueMenus;
  window.updateQueueActive = updateQueueActive;
  window.updateQueuePlaying = updateQueuePlaying;
  window.updateQueueModalActive = updateQueueModalActive;
  window._liveQueueIndexOf = _liveQueueIndexOf;
  window.removeFromQueue = removeFromQueue;
  window.reorderQueue = reorderQueue;
  window._attachQueueSwipeGestures = _attachQueueSwipeGestures;
  window._refreshQueueSwipeLikeLabel = _refreshQueueSwipeLikeLabel;
  window._attachQueueDragReorder = _attachQueueDragReorder;
  window.playFromQueue = playFromQueue;
  window.playCollection = playCollection;
  window.addCollectionToQueue = addCollectionToQueue;
  window.scheduleHistoryRefresh = scheduleHistoryRefresh;
})();
