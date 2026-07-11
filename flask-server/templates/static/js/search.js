(function () {
  'use strict';
  const state = window.__appState = window.__appState || {};
  if (state._resultsOpen === undefined) state._resultsOpen = false;
  if (state._currentVideoId === undefined) state._currentVideoId = '';
  if (state._hasTrack === undefined) state._hasTrack = false;
  if (state._searchCategorized === undefined) state._searchCategorized = {};
  if (state._activeCategory === undefined) state._activeCategory = 'songs';
  if (state._resultsPage === undefined) state._resultsPage = {};
  if (state._searchSeq === undefined) state._searchSeq = 0;

const RESULTS_PER_PAGE = 10;

async function runSearch(query) {
  const mySeq = ++state._searchSeq;
  toast('Searching \u201c' + query + '\u201d\u2026');
  try {
    const data = await api('/alexa/search/?q=' + encodeURIComponent(query));
    if (mySeq !== state._searchSeq) return;   // a newer search won
    state._searchCategorized = data || {};
    const totalItems = (data.songs?.length || 0) + (data.artists?.length || 0) + (data.albums?.length || 0) + (data.playlists?.length || 0);
    if (!totalItems) { toast('No results found.', 'error'); return; }
    state._resultsPage = { songs: 0, artists: 0, albums: 0, playlists: 0 };
    state._activeCategory = 'songs';
    document.querySelectorAll('.results-tab').forEach(t => t.classList.toggle('active', t.dataset.category === 'songs'));
    renderResults();
    openResults();
    toast(totalItems + ' results', 'ok');
  } catch (e) {
    if (mySeq === state._searchSeq) toast(e.message, 'error');
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
  // Views swap, they don't stack (YT Music): searching from the artist page
  // must hide it and drop the #artist/ hash. replaceState avoids firing
  // hashchange, which would re-run the home route and hide these results.
  const artistSection = document.getElementById('artist-section');
  if (artistSection) artistSection.hidden = true;
  if ((location.hash || '').indexOf('#artist/') === 0) {
    history.replaceState(null, '', '#home');
  }
  // Same for the playlist views: they sit above the content area (z-210),
  // so results rendered behind them would be invisible until manually closed.
  for (const id of ['playlists-modal-overlay', 'playlist-detail-modal-overlay']) {
    const ov = document.getElementById(id);
    if (ov) ov.classList.remove('open');
  }
  animatePlaySectionLayout(() => {
    state._resultsOpen = true;
    mainEl.classList.remove('has-queue');
    queueSection.classList.remove('is-visible');
    queueSection.hidden = true;
    section.hidden = false;
    syncUiState();
  });
  section._showTimer = setTimeout(() => {
    requestAnimationFrame(() => {
      if (state._resultsOpen && !section.hidden) section.classList.add('is-visible');
    });
  }, 120);
}

function closeResults() {
  if (!state._resultsOpen) return;
  const section = document.getElementById('results-section');
  // Fade the results panel out smoothly, then collapse it and show the queue.
  section.classList.remove('is-visible');
  clearTimeout(section._showTimer);
  clearTimeout(section._hideTimer);

  // If a queue will be restored, pre-add has-queue BEFORE removing results-open
  // so the grid columns stay at 1fr 1fr (no shrink-then-expand bounce).
  let queue = [];
  try { queue = JSON.parse(state._lastQueueJson || '[]'); } catch (_) {}
  const willShowQueue = queue.length > 1;
  if (willShowQueue) {
    document.querySelector('main').classList.add('has-queue');
  }

  // Wait for the CSS opacity/transform transition (~280ms) before hiding.
  section._hideTimer = setTimeout(() => {
    animatePlaySectionLayout(() => {
      state._resultsOpen = false;
      section.hidden = true;
      syncUiState();
      // Replay the player's reveal animation so enlarging from the mini player
      // slides the full player in instead of popping it.
      if (state._hasTrack) {
        const player = document.querySelector('.player-section');
        player.classList.remove('is-collapsed');
        player.classList.remove('is-visible');
        void player.offsetHeight;
        player.classList.add('is-visible');
      }
      // Bring the queue panel back after results have faded out.
      try { showQueue(queue, state._lastQueueIndex); } catch (_) {}
    });
  }, 300);
}

function _categoryTitle(item, cat) {
  if (cat === 'artists') return item.name || '';
  if (cat === 'albums') return item.title || '';
  if (cat === 'playlists') return item.title || '';
  return '';
}

function _categorySubtitle(item, cat) {
  if (cat === 'artists') return 'Artist';
  if (cat === 'albums') return (item.artist || '') + (item.year ? ' - ' + item.year : '');
  if (cat === 'playlists') return (item.track_count || '?') + ' tracks - ' + (item.owner || '');
  return '';
}

function renderResults() {
  const list = document.getElementById('results-list');
  const category = state._activeCategory;
  const items = state._searchCategorized[category] || [];
  const totalPages = Math.max(1, Math.ceil(items.length / RESULTS_PER_PAGE));
  let page = state._resultsPage[category] || 0;
  page = Math.min(Math.max(0, page), totalPages - 1);
  state._resultsPage[category] = page;
  const start = page * RESULTS_PER_PAGE;
  const pageItems = items.slice(start, start + RESULTS_PER_PAGE);
  // Transplant already-loaded thumbnails for videos that also appear on the
  // new page (e.g. paging back and forth) so their <img> doesn't re-fetch
  // and flash blank/reload.
  const existingThumbsById = new Map();
  for (const w of list.children) {
    const id = w.dataset.videoId || '';
    const img = w.querySelector('img.result-thumb.loaded');
    if (id && img && !existingThumbsById.has(id)) existingThumbsById.set(id, img);
  }
  // Close any open more-menu when re-rendering
  _closeAllMoreMenus();
  const newChildren = [];
  if (category === 'songs') {
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
    const isCurrent = item.video_id === state._currentVideoId;
    const inner = document.createElement('div');
    inner.className = 'result-item-inner' + (isCurrent ? ' active' : '');

    const reusableImg = item.thumbnail && existingThumbsById.get(item.video_id);
    const sameUrl = reusableImg && reusableImg.src === item.thumbnail;
    const thumbHtml = sameUrl
      ? `<div class="result-thumb-slot"></div>`
      : item.thumbnail
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
      <button class="result-queue-btn" type="button" title="Add to queue" ${isCurrent ? 'hidden' : ''}>${queueAddSvg}</button>
      <button class="result-more-btn" type="button" title="More options">${moreSvg}</button>
      <div class="result-more-menu">
        <div class="result-menu-option" data-action="play-next" ${isCurrent ? 'hidden' : ''}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
          Play next
        </div>
        <div class="result-menu-option" data-action="add-to-queue" ${isCurrent ? 'hidden' : ''}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
          Add to queue
        </div>
        <div class="result-menu-option" data-action="play-radio">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4.93 19.07A10 10 0 1 1 19.07 4.93 10 10 0 0 1 4.93 19.07z"/><circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="M2 12h2"/><path d="M20 12h2"/></svg>
          Play Radio
        </div>
        <div class="result-menu-option" data-action="save-playlist">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path><line x1="12" y1="11" x2="12" y2="17"></line><line x1="9" y1="14" x2="15" y2="14"></line></svg>
          Save to Playlist
        </div>
      </div>
    `;

    if (sameUrl) inner.querySelector('.result-thumb-slot').replaceWith(reusableImg);

    wrapper.appendChild(inner);

    // Tap on the main area → play the result. Highlight immediately so the
    // tap feedback doesn't wait on the server round-trip in playResult.
    attachQueueItemTap(inner, () => {
      for (const other of list.querySelectorAll('.result-item-inner.active')) other.classList.remove('active');
      inner.classList.add('active');
      playResult(item);
    });

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
    moreMenu.querySelector('[data-action="play-radio"]').addEventListener('click', (e) => {
      e.stopPropagation();
      _closeAllMoreMenus();
      // force_radio=true: build a fresh queue seeded from this track instead
      // of whatever queue currently exists (same as the queue's own Play
      // Radio option).
      playResult(item, false, true);
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

    newChildren.push(wrapper);
  });
  } else {
    pageItems.forEach((item) => {
      const wrapper = document.createElement('div');
      wrapper.className = 'result-swipe-wrapper';
      const inner = document.createElement('div');
      inner.className = 'result-item-inner';
      const thumbUrl = item.thumbnail || '';
      const roundThumb = category === 'artists';
      inner.innerHTML = `
        ${thumbUrl ? `<img class="result-thumb${roundThumb ? ' result-thumb-round' : ''}" src="${escHtml(thumbUrl)}" alt="" loading="lazy" onload="this.classList.add('loaded')">` : '<div class="result-thumb"></div>'}
        <div class="result-info">
          <div class="result-title">${escHtml(_categoryTitle(item, category))}</div>
          <div class="result-artist">${escHtml(_categorySubtitle(item, category))}</div>
        </div>
      `;
      // Artist rows open the artist page (nothing happened on click before).
      if (category === 'artists' && item.browse_id) {
        inner.classList.add('result-item-link');
        inner.addEventListener('click', () => {
          location.hash = '#artist/' + encodeURIComponent(item.browse_id);
        });
      }
      wrapper.appendChild(inner);
      newChildren.push(wrapper);
    });
  }
  list.replaceChildren(...newChildren);
  updateCountLabel();
  document.getElementById('results-page-label').textContent =
    'Page ' + (page + 1) + ' of ' + totalPages;
  document.getElementById('results-prev').disabled = page <= 0;
  document.getElementById('results-next').disabled = page >= totalPages - 1;
}

function updateCountLabel() {
  const cat = state._activeCategory;
  const items = state._searchCategorized[cat] || [];
  const allTotal = (state._searchCategorized.songs?.length || 0) +
                   (state._searchCategorized.artists?.length || 0) +
                   (state._searchCategorized.albums?.length || 0) +
                   (state._searchCategorized.playlists?.length || 0);
  const el = document.getElementById('results-count');
  if (el) el.textContent = 'Showing ' + items.length + ' of ' + allTotal + ' results';
}

// Close all open more-menus
function _closeAllMoreMenus() {
  for (const m of document.querySelectorAll('.result-more-menu.open')) {
    m.classList.remove('open');
    // See _closeAllQueueMenus: defer the position reset/reparent until the
    // fade-out finishes so it doesn't jump to (0,0) mid-transition.
    setTimeout(() => {
      if (m.classList.contains('open')) return;
      m.style.top = '';
      m.style.bottom = '';
      m.style.left = '';
      m.style.right = '';
      if (m._home && m.parentElement !== m._home) m._home.appendChild(m);
    }, 150);
  }
  for (const b of document.querySelectorAll('.result-more-btn.open')) b.classList.remove('open');
  for (const w of document.querySelectorAll('.result-swipe-wrapper.menu-open')) w.classList.remove('menu-open');
}
document.addEventListener('click', _closeAllMoreMenus);
// Same staleness issue as the queue menu: the open menu is fixed-positioned
// at its row's coordinates at open time, then portaled to <body>. Scrolling
// the results list afterward moves the row but not the menu, so close it
// instead of trying to keep it live-repositioned.
(function () {
  const list = document.getElementById('results-list');
  if (list) list.addEventListener('scroll', () => _closeAllMoreMenus(), { passive: true });
})();

// Highlight the currently playing track in the visible results page.
function updateResultsActive() {
  for (const el of document.querySelectorAll('#results-list .result-item-inner')) {
    el.classList.toggle('active', !!state._currentVideoId && el.closest('.result-swipe-wrapper')?.dataset.videoId === state._currentVideoId);
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
  state._resultsPage[state._activeCategory]--;
  renderResults();
  scrollResultsToTop();
});
document.getElementById('results-next').addEventListener('click', () => {
  state._resultsPage[state._activeCategory]++;
  renderResults();
  scrollResultsToTop();
});
document.querySelectorAll('.results-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    const category = tab.dataset.category;
    if (category === state._activeCategory) return;
    state._activeCategory = category;
    document.querySelectorAll('.results-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    renderResults();
    updateCountLabel();
    scrollResultsToTop();
  });
});
document.getElementById('results-close').addEventListener('click', closeResults);

/* ---- Add to queue ---- */
let _addToQueueBusy = false;

/* ---- search suggestions ---- */
(function () {
  const input = document.getElementById('query');
  const listEl = document.getElementById('suggest-list');
  const clearBtn = document.getElementById('query-clear');
  let items = [];        // current suggestion strings
  let activeIdx = -1;    // highlighted item (-1 = none)
  let debounceTimer = null;
  let seq = 0;           // request sequencer, drops stale responses
  let showingHistory = false; // list currently shows recent searches, not live suggestions

  const searchSvg =
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="2" stroke-linecap="round">' +
    '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>';
  const clockSvg =
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="2" stroke-linecap="round">' +
    '<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15.5 14"/></svg>';

  /* Recent searches, newest first. Shown (max 7) when the empty search bar is
     focused; every submitted text search is recorded. */
  const HISTORY_KEY = 'searchHistory';
  const HISTORY_MAX_SHOWN = 7;
  const HISTORY_MAX_STORED = 25;

  function getHistory() {
    try {
      const a = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
      return Array.isArray(a) ? a.filter(h => typeof h === 'string') : [];
    } catch (_) { return []; }
  }

  function recordSearch(q) {
    q = (q || '').trim();
    if (!q || isYoutubeLinkLike(q)) return;
    // De-dupe case-insensitively so re-searching moves the entry to the top.
    const hist = getHistory().filter(h => h.toLowerCase() !== q.toLowerCase());
    hist.unshift(q);
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(hist.slice(0, HISTORY_MAX_STORED))); } catch (_) {}
  }
  // The GO button handler lives outside this closure; let it record searches.
  window._recordSearchHistory = recordSearch;

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
    showingHistory = false;
    input.setAttribute('aria-expanded', 'false');
  }

  function render() {
    if (!items.length) { closeList(); return; }
    listEl.innerHTML = '';
    items.forEach((text, i) => {
      const li = document.createElement('li');
      li.className = 'suggest-item' + (i === activeIdx ? ' active' : '');
      li.setAttribute('role', 'option');
      li.innerHTML = (showingHistory ? clockSvg : searchSvg) + '<span></span>';
      li.querySelector('span').textContent = text;
      // mousedown (not click) so it fires before the input's blur
      li.addEventListener('mousedown', e => { e.preventDefault(); choose(i); });
      listEl.appendChild(li);
    });
    if (showingHistory) {
      const clearLi = document.createElement('li');
      clearLi.className = 'suggest-clear-history';
      clearLi.textContent = 'Clear search history';
      clearLi.addEventListener('mousedown', e => {
        e.preventDefault();
        try { localStorage.removeItem(HISTORY_KEY); } catch (_) {}
        closeList();
        input.focus();
      });
      listEl.appendChild(clearLi);
    }
    listEl.hidden = false;
    input.setAttribute('aria-expanded', 'true');
  }

  function showHistory() {
    const hist = getHistory().slice(0, HISTORY_MAX_SHOWN);
    if (!hist.length) { closeList(); return; }
    // Invalidate any in-flight suggestion fetch so it can't overwrite history.
    clearTimeout(debounceTimer);
    seq++;
    items = hist;
    activeIdx = -1;
    showingHistory = true;
    render();
  }

  window._suggestHistory = showHistory;

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
    syncClearBtn();
    closeResults();   // also dismiss the results panel for this search
    input.focus();
    showHistory();    // cleared + focused: offer recent searches
  });
  // Mobile: touchend fires instead of mousedown; mirror the same behaviour.
  clearBtn.addEventListener('touchend', e => {
    e.preventDefault();
    input.value = '';
    syncClearBtn();
    closeResults();
    input.focus();
    showHistory();
  });
  syncClearBtn();

  async function fetchSuggestions(q) {
    const mySeq = ++seq;
    try {
      const data = await api('/alexa/suggest/?q=' + encodeURIComponent(q));
      if (mySeq !== seq) return;            // a newer keystroke won
      items = (data.suggestions || []).slice(0, 8);
      activeIdx = -1;
      showingHistory = false;
      render();
    } catch (_) {
      // Suggestions are best-effort; stay silent on failure.
    }
  }

  window.fetchSuggestions = fetchSuggestions;

  input.addEventListener('input', () => {
    const q = input.value.trim();
    syncClearBtn();
    clearTimeout(debounceTimer);
    // Empty box: fall back to recent searches; links get no suggestions.
    // Only when the box is actually focused — synthetic input events (e.g.
    // clearUiAfterPlaybackReset emptying the box after "Clear") must not
    // pop the history dropdown open on an unfocused input.
    if (!q) {
      if (document.activeElement === input) showHistory();
      else closeList();
      return;
    }
    if (isYoutubeLinkLike(q)) { closeList(); return; }
    debounceTimer = setTimeout(() => fetchSuggestions(q), 180);
  });

  // Clicking/tabbing into the empty search bar surfaces recent searches.
  input.addEventListener('focus', () => {
    if (!input.value.trim()) showHistory();
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

  window.runSearch = runSearch;
  window.openResults = openResults;
  window.closeResults = closeResults;
  window.renderResults = renderResults;
  window.updateCountLabel = updateCountLabel;
  window.updateResultsActive = updateResultsActive;
  window.scrollResultsToTop = scrollResultsToTop;
  window._closeAllMoreMenus = _closeAllMoreMenus;
})();
