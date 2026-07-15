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

/* ---- Lazy (windowed) queue rendering ----
   Shared by the desktop queue list and the mobile queue modal. Only a window
   of rows is materialized; a 1px sentinel after the last row pages in the next
   chunk when scrolled near. Each container keeps its own state in
   el._lazyQueue = { queue, currentIndex } and its own observer. */

function _renderedQueueRows(container) {
  return container.querySelectorAll(':scope > .queue-swipe-wrapper');
}

// Builds one queue row (wrapper + item + listeners). thumbsById, when given,
// maps video_id -> already-loaded <img> to transplant so the browser never
// re-fetches/re-decodes it (the re-fetch flash on every track change was the
// visible flicker here). Rows capture their index by closure, so callers must
// rebuild rows whose index changed (reorders) rather than reuse them.
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
      Like
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
      <div class="queue-artist">${window.artistLinksHtml(item.artist, item.channelId)}</div>
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

  // Artist name clicks: stop propagation to prevent parent row's play action
  window.wireArtistLinks(el);

  // Tap on the item Ã¢â€ â€™ play from queue. Mark it active immediately so the
  // "you tapped this" feedback shows right away instead of only after the
  // server round-trip completes and playFromQueue's own re-render lands.
  attachQueueItemTap(el, () => {
    for (const other of container.querySelectorAll('.queue-item.active')) other.classList.remove('active');
    el.classList.add('active');
    playFromQueue(item, i);
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
  renderMobileInlineQueue(queue, currentIndex);
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
    renderedArr.forEach(function(w, i) { w.classList.toggle('active', i === currentIndex); w.classList.toggle('playing', i === currentIndex && state.isPlaying); });
    _syncQueueSentinel(list);
    return;
  }
  var newChildren = [];
  for (var i = 0; i < renderLimit; i++) {
    newChildren.push(_buildQueueRow(list, queue[i], i, currentIndex, existingThumbsById));
  }
  list.replaceChildren.apply(list, newChildren);
  _syncQueueSentinel(list);
  var active = list.querySelector('.active');
  if (active) requestAnimationFrame(function() { active.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); });
}
window.renderNpQueue = renderNpQueue;

function renderMobileInlineQueue(queue, currentIndex) {
  var list = document.getElementById('mobile-inline-queue');
  if (!list) return;
  if (!Array.isArray(queue) || !queue.length) {
    list.replaceChildren();
    list._mobileInlineQueueIndex = null;
    return;
  }
  var start = Math.max(0, Number(currentIndex) || 0);
  var previous = list._mobileInlineQueueIndex;
  var transitionPrimed = list._mobileInlineQueueTransitionPrimed;
  var isMobile = window.matchMedia('(max-width: 899px)').matches;
  var existingRows = Array.from(list.querySelectorAll(':scope > .queue-swipe-wrapper'));
  var sameQueue = existingRows.length === queue.length - start &&
    existingRows.every(function(row, index) {
      return row.dataset.videoId === (queue[start + index].video_id || '');
    });
  if (list._mobileInlineQueueIndex === start && sameQueue) {
    existingRows.forEach(function(row, index) {
      var item = row.querySelector('.queue-item');
      var queueIndex = start + index;
      if (item) {
        item.classList.toggle('active', queueIndex === start);
        item.classList.toggle('playing', queueIndex === start && state.isPlaying);
      }
    });
    return;
  }
  var direction = isMobile && !transitionPrimed && Number.isFinite(previous) && start !== previous
    ? (start > previous ? 'next' : 'previous')
    : '';
  var end = queue.length;
  var rows = [];
  for (var i = start; i < end; i++) {
    var row = _buildQueueRow(list, queue[i], i, start, new Map());
    if (direction) row.classList.add('mobile-queue-shift-' + direction);
    rows.push(row);
  }
  list.replaceChildren.apply(list, rows);
  list._mobileInlineQueueIndex = start;
  list._mobileInlineQueueTransitionPrimed = false;
}

function animateMobileInlineQueue(direction) {
  var list = document.getElementById('mobile-inline-queue');
  if (!list || !window.matchMedia('(max-width: 899px)').matches) return;
  if (direction !== 'next' && direction !== 'previous') return;
  var className = 'mobile-queue-shift-' + direction;
  list._mobileInlineQueueTransitionPrimed = true;
  list.querySelectorAll(':scope > .queue-swipe-wrapper').forEach(function(row) {
    row.classList.remove('mobile-queue-shift-next', 'mobile-queue-shift-previous');
    void row.offsetWidth;
    row.classList.add(className);
  });
  clearTimeout(list._mobileInlineQueueTransitionTimer);
  list._mobileInlineQueueTransitionTimer = setTimeout(function() {
    list._mobileInlineQueueTransitionPrimed = false;
  }, 1200);
}
window.animateMobileInlineQueue = animateMobileInlineQueue;

function optimisticallyAdvanceMobileInlineQueue(direction) {
  if (!window.matchMedia('(max-width: 899px)').matches) return false;
  var list = document.getElementById('mobile-inline-queue');
  if (!list) return false;
  var queue;
  try { queue = JSON.parse(window._lastQueueJson || '[]'); } catch (_) { queue = []; }
  if (!Array.isArray(queue) || !queue.length) return false;
  var current = Number(window._lastQueueIndex);
  if (!Number.isFinite(current) || current < 0) current = Number(list._mobileInlineQueueIndex);
  if (!Number.isFinite(current) || current < 0) return false;
  var target = current + (direction === 'next' ? 1 : -1);
  if (target < 0 || target >= queue.length) return false;
  window._lastQueueIndex = target;
  renderMobileInlineQueue(queue, target);
  return true;
}
window.optimisticallyAdvanceMobileInlineQueue = optimisticallyAdvanceMobileInlineQueue;

// Builds the "3-dot" more-options button + dropdown for a queue row (used by
// both the desktop inline queue and the mobile queue popup, which otherwise
// only offered swipe gestures with no menu equivalent).
function _queueMoreMenuHtml(item) {
  const moreSvg = `<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>`;
  const isLiked = typeof _playlistsData !== 'undefined' && _playlistsData.liked_songs && _playlistsData.liked_songs.includes(item.video_id);
  const likeSvg = isLiked
    ? '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M2 10h4v12H2zM8 22V10l3.5-7.5c.3-.7 1.1-1.1 1.8-.8l.2.1c1.1.5 1.6 1.7 1.3 2.8L14 10h6.2c1.3 0 2.3 1.2 2 2.5l-1.5 7.5c-.2 1.2-1.2 2-2.4 2H8z"/></svg>'
    : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>';
  const likeText = isLiked ? "Dislike" : "Like";
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
          <svg viewBox="0 0 24 24" fill="currentColor">
            <path d="M3 6h18"/><path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/>
            <path d="M5 7h14l-1 14H6L5 7zm3-4h8l1 4H7l1-4zM3 6h18v2H3z"/>
          </svg>
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
      window.openSongContextMenu(e, Object.assign({}, item, {
        _queueIndex: index,
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
    removeFromQueue(index, item.title, item.video_id);
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
      const isLikedNow = typeof _playlistsData !== 'undefined' && _playlistsData.liked_songs && _playlistsData.liked_songs.includes(item.video_id);
      const likeSvgNow = isLikedNow
        ? '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M2 10h4v12H2zM8 22V10l3.5-7.5c.3-.7 1.1-1.1 1.8-.8l.2.1c1.1.5 1.6 1.7 1.3 2.8L14 10h6.2c1.3 0 2.3 1.2 2 2.5l-1.5 7.5c-.2 1.2-1.2 2-2.4 2H8z"/></svg>'
        : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>';
      likeBtn.innerHTML = `\n          ${likeSvgNow}\n          ${isLikedNow ? "Dislike" : "Like"}\n        `;
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
document.addEventListener('click', _closeAllQueueMenus);

function updateQueueActive(currentIndex) {
  const list = document.getElementById('queue-list');
  if (!list) return;
  for (const el of list.querySelectorAll('.queue-item')) {
    el.classList.toggle('active', Number(el.dataset.index) === currentIndex);
    el.classList.toggle('playing', Number(el.dataset.index) === currentIndex && state.isPlaying);
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

/* ---- Reorder queue (drag complete) ---- */
async function reorderQueue(fromIndex, toIndex) {
  if (fromIndex === toIndex) return;
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
      // Re-render immediately with the optimistic order
      showQueue(queue, currentIdx);
      refreshQueueModalIfOpen();
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
      // Scroll up Ã¢â‚¬â€ speed increases as pointer gets closer to edge
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
    // the item first, shifting everything below it up by one Ã¢â‚¬â€ so a downward
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

async function playFromQueue(item, queueIndex, openPlaybackPage) {
  const serial = selectedSerial();
  if (!serial) return;
  state.lastActionAt = Date.now();
  if (!item.video_id) { toast('That recommendation cannot be played.', 'error'); return; }
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
      thumbnail: (item.thumbnail && item.thumbnail.url) || item.thumbnail || '',
      duration_ms: item.duration_ms || 0,
      queue_index: typeof queueIndex === 'number' ? queueIndex : undefined,
    });
    state._lastPlayAttemptVideoId = item.video_id;
    const npInfo = { video_id: item.video_id, title: item.title, artist: item.artist, thumbnail: item.thumbnail };
    showNowPlaying(npInfo);
    progress.resetPending(item.video_id);
    state.isPlaying = true;
    state.lastActionIntent = true;
    syncPlayPause();
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
    if (!queue.length) {
      const empty = document.createElement('div');
      empty.className = 'queue-modal-empty';
      empty.textContent = 'No songs in queue';
      body.replaceChildren(empty);
      return;
    }
    const limit = Math.min(queue.length, Math.max(QUEUE_RENDER_CHUNK, idx + 11));
    body._lazyQueue = { queue, currentIndex: idx };
    const rows = [];
    for (let i = 0; i < limit; i++) rows.push(_buildQueueRow(body, queue[i], i, idx, new Map()));
    body.replaceChildren(...rows);
    _syncQueueSentinel(body);
    if (preserveOpenScroll) body.scrollTop = previousScrollTop;
  }

  function scrollPlayingQueueRowToTop() {
    const activeItem = body.querySelector('.queue-item.active');
    const activeRow = activeItem?.closest('.queue-swipe-wrapper') || activeItem;
    if (!activeRow) return;
    const bodyRect = body.getBoundingClientRect();
    const rowRect = activeRow.getBoundingClientRect();
    body.scrollTop = Math.max(0, body.scrollTop + rowRect.top - bodyRect.top);
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

  function openQueueModal(options) {
    renderQueueModal();
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

  function closeQueueModal() {
    if (overlay.classList.contains('queue-origin-open') && inlineMorph) {
      if (overlay.classList.contains('queue-origin-closing')) return;
      finishInlineQueueProgress(false);
      return;
    }
    overlay.classList.remove('open');
    overlay.style.removeProperty('--queue-drag-progress');
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
      const offset = Math.max(-window.innerHeight * 0.25, Math.min(window.innerHeight * 0.9, clientY - startY));
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
        if (event.target.closest('.queue-modal-close, button, a, input, select, textarea, [contenteditable="true"]')) return;
        if (!beginDrag(event.clientY, fromBody, event.clientX)) return;
        if (!fromBody) surface.setPointerCapture?.(event.pointerId);
      });
      surface.addEventListener('pointermove', (event) => moveDrag(event.clientY, event, event.clientX));
      surface.addEventListener('pointerup', endDrag);
      surface.addEventListener('pointercancel', endDrag);

      surface.addEventListener('touchstart', (event) => {
        if (event.target.closest('.queue-modal-close, button, a, input, select, textarea, [contenteditable="true"]')) return;
        if (event.touches.length) beginDrag(event.touches[0].clientY, fromBody, event.touches[0].clientX);
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
  window._attachQueueDragReorder = _attachQueueDragReorder;
  window.playFromQueue = playFromQueue;
  window.scheduleHistoryRefresh = scheduleHistoryRefresh;
})();
