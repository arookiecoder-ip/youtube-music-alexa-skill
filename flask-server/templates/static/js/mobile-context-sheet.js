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
})();