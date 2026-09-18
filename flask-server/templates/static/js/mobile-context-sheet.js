(function () {
  'use strict';

  // Mobile-only bottom-sheet presenter for every context menu surface.
  //
  // The app has many distinct 3-dot / tap-and-hold menus (song rows, queue
  // rows, home / explore / playlist cards, the playlist-detail rename-delete
  // menu, and the now-playing menu). They all share two contracts:
  //   1. they are "open" while they carry `.open` (or, for the now-playing
  //      3-dot, `.mobile-open`, which player.js adds alongside
  //      `.mobile-now-playing-menu`),
  //   2. they already close through the module's own document / scroll keys
  //      and per-menu close helpers (`_closeAllMoreMenus`,
  //      `_closeAllQueueMenus`, `_closeNpMoreMenu`).
  //
  // So instead of threading this behavior through a dozen open() functions,
  // we observe the document for any menu gaining its open class and, on
  // mobile only, present it as a bottom sheet:
  //   - show a scrim + lock page scroll,
  //   - attach pull-down-to-dismiss, and
  //   - tear the scrim down the moment the menu closes by any means.
  //
  // All geometry and animation live in mobile.css; this file owns the
  // presentation lifecycle and the drag gesture.

  const MEDIA = window.matchMedia('(max-width: 899px)');
  // Menus that count as "currently open".
  const OPEN_SELECTOR = [
    '.result-more-menu.open',
    '.queue-more-menu.open',
    '.playlist-more-menu.open',
    '#np-more-menu.mobile-open',
    '.mobile-now-playing-menu.mobile-open'
  ].join(', ');

  let scrim = null;
  let activeSheet = null;
  let drag = null;      // the active drag gesture (if any)
  let swallowClick = false; // swallow exactly one by-product click
  let sheetHistoryEntry = false;
  // Set right before history.back() is dispatched and kept until the popstate
  // that back triggers is consumed by the router (via
  // _closeContextSheetsFromHistory). Guards against a second closeAllSheets()
  // landing before that popstate and dispatching another back() that would
  // navigate a real page away.
  let historyBackPending = false;

  function isMobile() {
    return !!window.matchMedia && MEDIA.matches;
  }

  function currentSheet() {
    return document.querySelector(OPEN_SELECTOR);
  }

  function ensureScrim() {
    if (scrim && scrim.parentNode) return scrim;
    scrim = document.createElement('div');
    scrim.className = 'context-sheet-scrim';
    // Tapping the scrim is an explicit dismissal (equivalent to the app's own
    // "tap outside" close). Clicks still bubble to document, so any existing
    // outside-click closer also fires — harmless because both just close.
    scrim.addEventListener('click', closeAllSheets);
    document.body.appendChild(scrim);
    return scrim;
  }

  function showScrim() {
    ensureScrim();
    document.body.classList.add('context-sheet-open');
    if (!sheetHistoryEntry && window.history && window.history.pushState) {
      window.history.pushState({ contextSheet: true }, '', window.location.href);
      sheetHistoryEntry = true;
    }
  }
  function hideScrim() {
    // Drop the open class first so the scrim fades out over its own opacity
    // transition (not removed instantly), then detach the element after that
    // fade so a quick close still shows the closing animation.
    document.body.classList.remove('context-sheet-open');
    const el = scrim;
    if (el && el.parentNode) {
      window.setTimeout(function () {
        if (el && el.parentNode) el.remove();
      }, 260);
    }
  }

  // The canonical way this app closes its menus. Calling all three helpers is
  // idempotent — each only closes menus it owns. The playlist-detail menu is
  // not covered by any shared helper, so we close it directly.
  function closeAllSheets(fromHistory) {
    if (fromHistory) {
      // The router consumed our pushed history entry via popstate.
      sheetHistoryEntry = false;
      historyBackPending = false;
    } else if (sheetHistoryEntry && !historyBackPending && window.history && window.history.back) {
      // Leave sheetHistoryEntry armed: the popstate this back() triggers is
      // how the router knows this close was history-driven (its mobile
      // popstate guard checks _contextSheetHistoryOpen). Clearing it here
      // would make that popstate fall through to the generic handler, which
      // re-runs the current route — visibly refreshing the page underneath.
      historyBackPending = true;
      window.history.back();
    }
    if (typeof window._closeAllMoreMenus === 'function') window._closeAllMoreMenus();
    if (typeof window._closeAllQueueMenus === 'function') window._closeAllQueueMenus();
    const np = document.getElementById('np-more-menu');
    if (np && np.classList.contains('mobile-open') && typeof window._closeNpMoreMenu === 'function') {
      window._closeNpMoreMenu();
    }
    const detail = document.getElementById('playlist-detail-more-menu');
    if (detail && detail.classList.contains('open')) detail.classList.remove('open');
    reconcile();
  }

  // ---- drag-to-dismiss + click-through suppression ------------------------
  //
  // Tap-and-hold opens the sheet while the finger is *still down on the row*.
  // That release is dangerous in three ways, all of which we handle here:
  //   1. The finger may lift over a sheet option — that release click would
  //      activate an option the user never meant to touch. We remember the
  //      press whose release opened the sheet and swallow that one click.
  //   2. Because there was no fresh pointerdown on the sheet, a classic
  //      "drag the sheet" handler never starts. We instead track the pointer
  //      continuously from the original pointerdown (wherever it was) and let
  //      a downward pull — once a sheet is open — drag the sheet.
  //   3. Tapping the scrim/empty area to dismiss must not also activate the
  //      row, card, or link underneath. We swallow the click that caused the
  //      dismiss.

  // A by-product swallow must never leak into the user's next gesture. If the
  // click it was waiting for never fires (pointercancel, or an untrusted
  // programmatic click we skip), the flag would stay armed and block the next
  // legit tap. Clear it on every new press AND after a short grace so it can
  // only ever apply to the click that belongs to the gesture that armed it.
  let swallowTimer = null;
  function armSwallowClick() {
    swallowClick = true;
    if (swallowTimer) window.clearTimeout(swallowTimer);
    swallowTimer = window.setTimeout(function () {
      swallowClick = false;
      swallowTimer = null;
    }, 600);
  }
  function clearSwallowClick() {
    swallowClick = false;
    if (swallowTimer) { window.clearTimeout(swallowTimer); swallowTimer = null; }
  }

  function onPointerDown(e) {
    if (!isMobile()) return;
    // A tap outside an open sheet must be consumed entirely. Some playlist,
    // album, and card handlers act during pointerdown (before the later click
    // suppression can run), so close and stop the dismissal at capture time.
    const openSheet = currentSheet();
    const insideSheet = e.target === openSheet ||
      !!(e.target && e.target.closest && e.target.closest(OPEN_SELECTOR));
    // Any press outside the sheet is a dismissal gesture, even when the
    // background contains a song/card/link. Consume both pointerdown and the
    // synthetic click so the underlying element cannot be activated after the
    // sheet closes. Only descendants of the sheet itself are exempt.
    if (openSheet && e.target !== scrim && !insideSheet) {
      armSwallowClick();
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
      closeAllSheets();
      return;
    }
    // Track the press from its very start, even when it begins on a row/card
    // and only later reveals the sheet via long-press. Also clear any stale
    // by-product swallow from a *previous* gesture so this new press is never
    // wrongly blocked.
    clearSwallowClick();
    dragReset();
    drag = {
      sheet: null,
      pointerId: e.pointerId,
      startY: e.clientY,
      startX: e.clientX,
      lastY: e.clientY,
      moved: false,
      suppressRelease: false,
      onScrim: !!scrim && e.target === scrim,
      lastT: e.timeStamp || performance.now(),
      velocity: 0
    };
    if (drag.onScrim) {
      // Tapping the scrim is a deliberate dismiss; never let that click reach
      // whatever sits underneath.
      armSwallowClick();
      closeAllSheets();
    }
  }

  function onPointerMove(e) {
    if (!isMobile() || !drag || e.pointerId !== drag.pointerId) return;
    const dy = e.clientY - drag.startY;
    const now = e.timeStamp || performance.now();
    const dt = Math.max(1, now - drag.lastT);
    drag.velocity = 0.75 * drag.velocity + 0.25 * ((dy - (drag.lastY - drag.startY)) / dt);
    drag.lastY = e.clientY;
    drag.lastT = now;

    if (drag.moved) {
      if (drag.sheet) {
        const clamped = Math.max(0, dy);
        drag.sheet.style.setProperty('--sheet-drag-y', clamped + 'px');
        e.preventDefault();
      }
      return;
    }

    // Not dragging yet: see if a sheet is open and the finger is pulling down.
    const sheet = currentSheet();
    if (!sheet) return;
    if (dy < 8) return;                       // require a clear downward pull
    const dx = e.clientX - drag.startX;
    if (Math.abs(dx) > Math.abs(dy)) return;  // horizontal-ish: pass through
    if (sheet.scrollTop > 0) return;          // sheet scrolled: scroll, don't drag
    drag.moved = true;
    drag.sheet = sheet;
    sheet.setAttribute('data-sheet-dragging', '');
    sheet.style.setProperty('--sheet-drag-y', Math.max(0, dy) + 'px');
    e.preventDefault();
  }

  function onPointerEnd(e) {
    if (!isMobile() || !drag || e.pointerId !== drag.pointerId) return;
    const { sheet, moved } = drag;
    const dy = (e.clientY || drag.lastY) - drag.startY;
    const height = sheet ? (sheet.offsetHeight || window.innerHeight) : window.innerHeight;
    const threshold = Math.min(120, height * 0.24);
    const dismiss = moved && sheet && (dy > threshold || (drag.velocity > 0.55 && dy > 40));

    if (moved && sheet) {
      if (dismiss) {
        // Let the sheet slide fully off before actual close.
        sheet.removeAttribute('data-sheet-dragging');
        sheet.style.setProperty('--sheet-drag-y', (height + 80) + 'px');
        window.setTimeout(function () {
          sheet.style.setProperty('--sheet-drag-y', '0px');
          closeAllSheets();
        }, 220);
        armSwallowClick(); // releasing after a dismiss must not click anything
      } else {
        // Not far enough (or an upward flick): spring back to the open perch.
        sheet.removeAttribute('data-sheet-dragging');
        sheet.style.setProperty('--sheet-drag-y', '0px');
      }
    } else if (drag.suppressRelease) {
      // The press whose release opened the sheet (hold-to-open) — the finger
      // lifted over an option. Swallow that by-product click.
      armSwallowClick();
    }
    dragReset();
  }

  // Swallow exactly one by-product click (trusted only, so programmatic
  // long-press opens are never blocked) in the capture phase, before any
  // section handler underneath can see it.
  function onSwallowClick(e) {
    if (!swallowClick) return;
    if (!e.isTrusted) return;
    swallowClick = false;
    e.preventDefault();
    e.stopPropagation();
    e.stopImmediatePropagation();
  }

  function dragReset() {
    if (drag) {
      drag = null;
    }
  }

  function markOpenByHeldPress() {
    // reconcile() calls this right after presenting a sheet. If a pointer is
    // currently held down (the long-press is still in progress), its upcoming
    // release would click whatever is under the finger — remember to swallow it.
    if (drag) drag.suppressRelease = true;
  }

  // ---- YouTube-Music sheet dressing (mobile only) ---------------------------
  //
  // The sheets above are plain option lists, so on phones they read as a
  // boring stack of identical rows. enhanceSheet() dresses the open sheet,
  // YouTube-Music style, without touching any menu's own open/close logic:
  //   - a track header (artwork + title + artist) so the user sees WHAT the
  //     actions apply to, and
  //   - a quick-action row (Like / Play next / Add to queue / Radio) of big
  //     round buttons for the most common actions; the remaining actions
  //     stay as list rows below.
  //
  // Quick buttons are proxies: tapping one just clicks the real option it
  // mirrors, so every existing handler (likes, queue, radio, playlists,
  // navigation) runs unchanged. The mirrored list rows are then hidden on
  // mobile via CSS ([data-sheet-quick]) to avoid showing them twice.
  //
  // Menus without a track (playlist rename/delete, playlist cards) get no
  // header and — with fewer than 4 options — no quick row either; they keep
  // the polished list styling from mobile.css. Everything here is defensive
  // (try/catch + capability checks) so a strange menu can never break the
  // scrim/drag lifecycle above, and desktop is untouched (reconcile only
  // calls this on mobile).

  var SHEET_OPTION_SELECTOR = '.result-menu-option, .queue-menu-option';
  var SHEET_FALLBACK_ART =
    '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' +
    '<path d="M9 18V6l10-2v11"/>' +
    '<circle cx="7" cy="18" r="3"/><circle cx="17" cy="15" r="3"/></svg>';

  function ytmEsc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function ytmDocText(selector) {
    try {
      var el = document.querySelector(selector);
      if (el && el.textContent) return el.textContent.trim();
    } catch (_) { /* missing DOM — fall through */ }
    return '';
  }

  // Deep thumbnail pick, mirroring explore.js imageUrl(): plain strings,
  // arrays (highest-resolution last), {url}/{src} objects, and nested
  // {thumbnails}/{thumbnail} wrappers as returned by the catalog APIs.
  function ytmThumbValue(value, depth) {
    if (!value || depth > 4) return '';
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) {
      for (var i = value.length - 1; i >= 0; i--) {
        var found = ytmThumbValue(value[i], depth + 1);
        if (found) return found;
      }
      return '';
    }
    if (typeof value === 'object') {
      return ytmThumbValue(value.url || value.src, depth + 1) ||
        ytmThumbValue(value.thumbnails, depth + 1) ||
        ytmThumbValue(value.thumbnail, depth + 1);
    }
    return '';
  }

  function ytmThumbFromItem(item) {
    try {
      if (!item || typeof item !== 'object') return '';
      return ytmThumbValue(
        [item.thumbnail, item.thumbnails, item.image, item.images, item.artwork], 0);
    } catch (_) { /* fall through */ }
    return '';
  }

  // Resolve {title, subtitle, thumbnail} for a sheet, or null when the menu
  // has no track context (playlist rename/delete keeps a plain list).
  function ytmTrackFromSheet(sheet) {
    try {
      var t = sheet && sheet._track;
      if (t && (t.title || t.video_id)) {
        var artist = t.artist || '';
        if (!artist && Array.isArray(t.artists)) {
          artist = t.artists.map(function (a) {
            return typeof a === 'string' ? a : (a && (a.name || a.title)) || '';
          }).filter(Boolean).join(', ');
        }
        return {
          title: t.title || 'Unknown title',
          subtitle: artist || 'YouTube Music',
          thumbnail: t.thumbnail || t.artwork || ''
        };
      }
      var pl = sheet && sheet._playlist;
      if (pl && (pl.title || pl.id)) {
        return {
          title: pl.title || 'Playlist',
          subtitle: pl.subtitle || 'Playlist',
          thumbnail: pl.thumbnail || ytmThumbFromItem(pl)
        };
      }
      var item = sheet && sheet._item;
      if (item && (item.title || item.name)) {
        var sub = item.artist || item.author || item.subtitle || '';
        if (!sub && Array.isArray(item.artists)) {
          sub = item.artists.map(function (a) {
            return typeof a === 'string' ? a : (a && a.name) || '';
          }).filter(Boolean).join(', ');
        }
        return {
          title: item.title || item.name || 'Unknown title',
          subtitle: sub || 'YouTube Music',
          thumbnail: ytmThumbFromItem(item)
        };
      }
      // Now-playing 3-dot: mirror whatever is playing.
      var isNp = false;
      try {
        isNp = (sheet && sheet.id === 'np-more-menu') ||
          !!(sheet && sheet.classList && sheet.classList.contains('mobile-now-playing-menu'));
      } catch (_) { isNp = false; }
      if (isNp) {
        var st = window.__appState || {};
        var cur = st._currentTrack || {};
        var title = cur.title || ytmDocText('#np-title') || ytmDocText('#np-page-title');
        if (title && title !== 'Nothing is playing') {
          var npArtist = cur.artist ||
            ytmDocText('#np-artist') || ytmDocText('#np-page-artist');
          return {
            title: title,
            subtitle: npArtist || 'Now playing',
            thumbnail: cur.thumbnail || st._currentThumbnail || ''
          };
        }
        return null;
      }
      // Queue fallback rows (per-row menu, used only when the shared song
      // menu is unavailable) carry their row as _home — read it directly.
      try {
        var home = sheet && sheet._home;
        if (home && home.querySelector) {
          var qt = home.querySelector('.queue-title');
          var qa = home.querySelector('.queue-artist');
          var qi = home.querySelector('img');
          if (qt && qt.textContent && qt.textContent.trim()) {
            return {
              title: qt.textContent.trim(),
              subtitle: (qa && qa.textContent.trim()) || 'Queue',
              thumbnail: (qi && (qi.currentSrc || qi.src)) || ''
            };
          }
        }
      } catch (_) { /* fall through */ }
      // Home cards stash their card for repositioning — same deal.
      try {
        var card = sheet && sheet._triggerCard;
        if (card && card.querySelector) {
          var ht = card.querySelector('.home-item-title');
          var hs = card.querySelector('.home-item-subtitle');
          var hi = card.querySelector('img');
          if (ht && ht.textContent && ht.textContent.trim()) {
            return {
              title: ht.textContent.trim(),
              subtitle: (hs && hs.textContent.trim()) || 'YouTube Music',
              thumbnail: (hi && (hi.currentSrc || hi.src)) || ''
            };
          }
        }
      } catch (_) { /* fall through */ }
    } catch (_) { /* never break presentation */ }
    return null;
  }

  function ytmIsOptionVisible(opt) {
    try {
      if (!opt) return false;
      if (opt.hidden) return false;
      if (typeof opt.hasAttribute === 'function' && opt.hasAttribute('hidden')) return false;
      if (opt.style && opt.style.display === 'none') return false;
      if (opt.classList && opt.classList.contains('sheet-quick-btn')) return false;
    } catch (_) { return false; }
    return true;
  }

  function ytmVisibleOptions(sheet) {
    try {
      if (!sheet || typeof sheet.querySelectorAll !== 'function') return [];
      var nodes = sheet.querySelectorAll(SHEET_OPTION_SELECTOR);
      var out = [];
      for (var i = 0; i < nodes.length; i++) {
        if (ytmIsOptionVisible(nodes[i])) out.push(nodes[i]);
      }
      return out;
    } catch (_) { return []; }
  }

  function ytmOptionAction(opt) {
    try {
      if (opt && typeof opt.getAttribute === 'function') {
        return opt.getAttribute('data-action') || '';
      }
      if (opt && opt.dataset) return opt.dataset.action || '';
    } catch (_) { /* fall through */ }
    return '';
  }

  function ytmOptionSvg(opt) {
    try {
      var svg = opt && typeof opt.querySelector === 'function'
        ? opt.querySelector('svg') : null;
      if (svg && svg.outerHTML) return svg.outerHTML;
    } catch (_) { /* fall through */ }
    return SHEET_FALLBACK_ART;
  }

  function ytmOptionText(opt) {
    try {
      var span = opt && typeof opt.querySelector === 'function'
        ? opt.querySelector('span') : null;
      var raw = (span && span.textContent) || (opt && opt.textContent) || '';
      return String(raw).replace(/\s+/g, ' ').trim();
    } catch (_) { return ''; }
  }

  function ytmIsLiked(opt) {
    try {
      return !!(opt && opt.classList && opt.classList.contains('liked'));
    } catch (_) { return false; }
  }

  // Candidate quick slots, in priority order. `key` picks the short grid
  // label; `match` finds the real option to proxy-click.
  var SHEET_QUICK_DEFS = [
    { key: 'like', match: function (opt, action) {
        if (action === 'toggle-like' || action === 'like') return true;
        try { return opt && opt.id === 'np-menu-like'; } catch (_) { return false; }
      } },
    { key: 'next', match: function (opt, action) { return action === 'play-next'; } },
    { key: 'queue', match: function (opt, action) { return action === 'add-to-queue'; } },
    { key: 'radio', match: function (opt, action) {
        if (action === 'play-radio') return true;
        try { return opt && opt.id === 'np-menu-radio'; } catch (_) { return false; }
      } },
    { key: 'save', match: function (opt, action) {
        if (action === 'save-playlist') return true;
        try { return opt && opt.id === 'np-menu-playlist'; } catch (_) { return false; }
      } },
    { key: 'album', match: function (opt, action) {
        if (action === 'open-album') return true;
        try { return opt && opt.id === 'np-menu-album'; } catch (_) { return false; }
      } },
    { key: 'artist', match: function (opt, action) {
        if (action === 'open-artist') return true;
        try { return opt && opt.id === 'np-menu-artist'; } catch (_) { return false; }
      } },
    { key: 'play', match: function (opt, action) {
        return action === 'play' || action === 'play-home';
      } },
    { key: 'shuffle', match: function (opt, action) {
        return action === 'shuffle' || action === 'shuffle-play';
      } }
  ];
  var SHEET_QUICK_LABELS = {
    next: 'Next', queue: 'Queue', radio: 'Radio', save: 'Save',
    album: 'Album', artist: 'Artist', play: 'Play', shuffle: 'Shuffle'
  };

  function ytmEnsureHeader(sheet, track) {
    var header = null;
    try {
      header = sheet.querySelector('.sheet-track-header');
    } catch (_) { header = null; }
    if (!track || (!track.title && !track.subtitle)) {
      if (header && header.parentNode) header.parentNode.removeChild(header);
      return null;
    }
    if (!header) {
      try {
        header = document.createElement('div');
        header.className = 'sheet-track-header';
        header.setAttribute('aria-hidden', 'true');
        if (typeof sheet.insertBefore === 'function' && sheet.firstChild) {
          sheet.insertBefore(header, sheet.firstChild);
        } else if (typeof sheet.appendChild === 'function') {
          sheet.appendChild(header);
        } else {
          return null;
        }
      } catch (_) { return null; }
    } else if (header.parentNode === sheet &&
        typeof sheet.insertBefore === 'function' &&
        sheet.firstChild && sheet.firstChild !== header) {
      // The menu reuses one element across tracks — keep the header on top.
      try { sheet.insertBefore(header, sheet.firstChild); } catch (_) { /* keep order */ }
    }
    var art = track.thumbnail
      ? '<img src="' + ytmEsc(track.thumbnail) + '" alt="" loading="lazy" decoding="async">'
      : SHEET_FALLBACK_ART;
    try {
      header.innerHTML =
        '<div class="sheet-track-art">' + art + '</div>' +
        '<div class="sheet-track-meta">' +
          '<div class="sheet-track-title">' + ytmEsc(track.title) + '</div>' +
          '<div class="sheet-track-subtitle">' + ytmEsc(track.subtitle) + '</div>' +
        '</div>';
    } catch (_) { /* keep previous header */ }
    return header;
  }

  function ytmClearQuickRow(sheet) {
    try {
      var olds = sheet.querySelectorAll('.sheet-quick-row');
      for (var i = 0; i < olds.length; i++) {
        if (olds[i] && olds[i].parentNode) olds[i].parentNode.removeChild(olds[i]);
      }
      var proxied = sheet.querySelectorAll('[data-sheet-quick]');
      for (var j = 0; j < proxied.length; j++) {
        try { proxied[j].removeAttribute('data-sheet-quick'); } catch (_) { /* keep going */ }
      }
      var firsts = sheet.querySelectorAll('.sheet-first-row');
      for (var k = 0; k < firsts.length; k++) {
        try { if (firsts[k].classList) firsts[k].classList.remove('sheet-first-row'); } catch (_) { /* keep going */ }
      }
    } catch (_) { /* nothing to clear */ }
  }

  function ytmEnsureQuickRow(sheet, header, visible) {
    ytmClearQuickRow(sheet);
    if (!visible) visible = ytmVisibleOptions(sheet);
    // Only song-sized menus earn the quick grid; 2-option menus (playlist
    // Play/Shuffle, rename/delete, explore Open) stay a clean list.
    if (visible.length < 4) return;
    var picks = [];
    var used = [];
    for (var d = 0; d < SHEET_QUICK_DEFS.length && picks.length < 4; d++) {
      var def = SHEET_QUICK_DEFS[d];
      for (var v = 0; v < visible.length; v++) {
        if (used.indexOf(v) !== -1) continue;
        var action = ytmOptionAction(visible[v]);
        var ok = false;
        try { ok = def.match(visible[v], action); } catch (_) { ok = false; }
        if (ok) {
          picks.push({ def: def, opt: visible[v] });
          used.push(v);
          break;
        }
      }
    }
    if (picks.length < 3) {
      // Not enough primary actions (e.g. a degraded menu) — don't leave a
      // stubby 1-2 button grid; the list below already covers everything.
      return;
    }
    var row = null;
    try {
      row = document.createElement('div');
      row.className = 'sheet-quick-row';
      row.setAttribute('role', 'group');
      row.setAttribute('aria-label', 'Quick actions');
      for (var p = 0; p < picks.length; p++) {
        (function (pick) {
          var label = pick.def.key === 'like'
            ? (ytmOptionText(pick.opt) || 'Like')
            : (SHEET_QUICK_LABELS[pick.def.key] || ytmOptionText(pick.opt) || 'More');
          var btn = document.createElement('button');
          btn.className = 'sheet-quick-btn' + (ytmIsLiked(pick.opt) ? ' is-liked' : '');
          btn.type = 'button';
          btn.innerHTML =
            '<span class="sheet-quick-icon">' + ytmOptionSvg(pick.opt) + '</span>' +
            '<span class="sheet-quick-label">' + ytmEsc(label) + '</span>';
          btn.addEventListener('click', function (ev) {
            try { ev.preventDefault(); ev.stopPropagation(); } catch (_) { /* keep going */ }
            try {
              if (pick.opt && typeof pick.opt.click === 'function') pick.opt.click();
            } catch (_) { /* the real option owns error feedback */ }
          });
          row.appendChild(btn);
          try { pick.opt.setAttribute('data-sheet-quick', pick.def.key); } catch (_) { /* list stays */ }
        })(picks[p]);
      }
      var anchor = (header && header.parentNode === sheet) ? header.nextSibling : sheet.firstChild;
      if (typeof sheet.insertBefore === 'function' && anchor) {
        sheet.insertBefore(row, anchor);
      } else if (typeof sheet.insertBefore === 'function') {
        sheet.insertBefore(row, sheet.firstChild);
      } else if (typeof sheet.appendChild === 'function') {
        sheet.appendChild(row);
      }
    } catch (_) {
      try { ytmClearQuickRow(sheet); } catch (_) { /* keep the plain list */ }
    }
    // The grid owns the divider beneath it (mobile.css .sheet-quick-row
    // border-bottom). The first remaining list row would otherwise draw a
    // second, near-adjacent divider via the option `+` border-top rules
    // (its hidden, proxied siblings still count for `+`), so tag it to
    // drop that top border. Only when the grid actually landed.
    try {
      if (row && row.parentNode === sheet) {
        for (var f = 0; f < visible.length; f++) {
          if (used.indexOf(f) === -1) {
            if (visible[f].classList) visible[f].classList.add('sheet-first-row');
            break;
          }
        }
      }
    } catch (_) { /* doubled divider at worst */ }
  }

  // Signature of everything the dressing renders from. reconcile() runs on
  // every observed mutation while a sheet is open — and our own inserts ARE
  // observed mutations (subtree childList) — so without this guard each
  // reconcile would rewrite the header/quick row, scheduling another
  // reconcile: a perpetual rAF loop that also recreates the artwork <img>
  // every frame (visible flicker). With the guard, a settled sheet costs
  // one extra no-op reconcile and zero DOM writes.
  function ytmSheetSig(track, visible) {
    var parts = [track ? (track.title + '\n' + track.subtitle + '\n' + track.thumbnail) : '-'];
    for (var i = 0; i < visible.length; i++) {
      parts.push(ytmOptionAction(visible[i]) + '|' +
        ytmOptionText(visible[i]) + '|' +
        (ytmIsLiked(visible[i]) ? '1' : '0'));
    }
    return parts.join('\n');
  }

  // Dress one open sheet. Safe to call on every reconcile: the signature
  // gate makes repeat calls with unchanged menus write nothing (no
  // observer feedback loop), while a reused menu element with a new track
  // (shared song menu) or changed options (like toggled) re-renders. Still
  // a no-op when required DOM APIs are missing (jsdom-free unit tests).
  function ytmEnhanceSheet(sheet) {
    try {
      if (!sheet || typeof sheet.querySelector !== 'function' ||
          typeof sheet.querySelectorAll !== 'function') return;
      var track = ytmTrackFromSheet(sheet);
      var visible = ytmVisibleOptions(sheet);
      var sig = '';
      try {
        sig = ytmSheetSig(track, visible);
      } catch (_) { return; }
      if (sig && sheet._ytmSig === sig) return;
      var header = ytmEnsureHeader(sheet, track);
      ytmEnsureQuickRow(sheet, header, visible);
      try { sheet._ytmSig = sig; } catch (_) { /* redress next time */ }
    } catch (_) { /* plain list is always an acceptable fallback */ }
  }

  function ytmEnhanceAll() {
    try {
      var menus = [];
      if (document.querySelectorAll) {
        try { menus = document.querySelectorAll(OPEN_SELECTOR); } catch (_) { menus = []; }
      }
      var detail = null;
      try {
        detail = document.getElementById
          ? document.getElementById('playlist-detail-more-menu') : null;
      } catch (_) { detail = null; }
      var i;
      if (menus && typeof menus.length === 'number') {
        for (i = 0; i < menus.length; i++) ytmEnhanceSheet(menus[i]);
      }
      // The detail menu is not part of OPEN_SELECTOR (it never takes the
      // scrim), but it renders as a sheet via CSS — polish it too.
      try {
        if (detail && detail.classList && detail.classList.contains('open')) {
          ytmEnhanceSheet(detail);
        }
      } catch (_) { /* leave it plain */ }
    } catch (_) { /* never break presentation */ }
  }

  // ---- presentation lifecycle ---------------------------------------------

  function reconcile() {
    if (!isMobile()) {
      activeSheet = null;
      hideScrim();
      return;
    }
    const sheet = document.querySelector(OPEN_SELECTOR);
    if (sheet) {
      if (sheet !== activeSheet) {
        activeSheet = sheet;
        // Clear any leftover drag offset / state from a previous open.
        sheet.style.setProperty('--sheet-drag-y', '0px');
        markOpenByHeldPress();
      }
      try { ytmEnhanceAll(); } catch (_) { /* plain list fallback */ }
      showScrim();
    } else if (activeSheet) {
      activeSheet = null;
      hideScrim();
    }
  }

  // React to menus opening/closing and to menus being portal-ed to <body>
  // (queue / search / np-more menus reparent while opening).
  //
  // IMPORTANT: the SSE feeds re-render lists in bursts, so class/child
  // mutations pour in by the thousands. Running reconcile() once per mutation
  // (each a full querySelector + possible scrim/DOM writes) janks the main
  // thread and can even starve long-press.js's open timer — the menu never
  // appears. Always coalesce to a single reconcile per animation frame.
  let reconcileScheduled = false;
  function scheduleReconcile() {
    if (reconcileScheduled) return;
    reconcileScheduled = true;
    const run = () => { reconcileScheduled = false; try { reconcile(); } catch (_) { /* keep UI responsive */ } };
    if (window.requestAnimationFrame) window.requestAnimationFrame(run);
    else window.setTimeout(run, 16);
  }

  // Only observe child additions/removals (menus are portal-ed to <body>) and
  // class changes, but never do heavy work directly in the callback — always
  // funnel through scheduleReconcile.
  let mo = null;
  if (window.MutationObserver) {
    mo = new window.MutationObserver(function () { scheduleReconcile(); });
    mo.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['class'] });
  }

  document.addEventListener('pointerdown', onPointerDown, true);
  document.addEventListener('pointermove', onPointerMove, { passive: false, capture: true });
  document.addEventListener('pointerup', onPointerEnd, true);
  document.addEventListener('pointercancel', onPointerEnd, true);
  document.addEventListener('click', onSwallowClick, true);
  // Existing menus already close themselves on scroll/outside-click; when that
  // happens the observer below reconciles and tears the scrim down. We also
  // reconcile on resize / media changes so a phone rotate or a drag across the
  // 899px breakpoint can't strand a sheet.
  window.addEventListener('resize', scheduleReconcile);

  function onMediaChange() { scheduleReconcile(); }
  if (MEDIA.addEventListener) MEDIA.addEventListener('change', onMediaChange);
  else if (MEDIA.addListener) MEDIA.addListener(onMediaChange);

  // Test hooks.
  window._reconcileContextSheets = reconcile;
  window._closeContextSheets = closeAllSheets;
  window._closeContextSheetsFromHistory = function () { closeAllSheets(true); };
  window._contextSheetHistoryOpen = function () { return sheetHistoryEntry; };
  window._mobileContextSheetActive = function () { return activeSheet; };
  // Dismissal by-product signal. A tap that closes a sheet dismisses it on
  // pointerdown, which drops the scrim out from under the finger — Chromium
  // then dispatches the tap's click to the content now underneath (e.g. a
  // song title) instead of the scrim. Early document-capture activators
  // (player.js mobile title->play) run before the click swallows below and
  // must skip while this is armed. Read-only; safe on desktop (never arms
  // there — every arming path is mobile-gated above).
  window._contextSheetSwallowArmed = function () { return swallowClick; };
})();