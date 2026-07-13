# Mobile Web Remote Remediation Plan

> Single source of truth for fixing the broken mobile experience of the YouTube Music → Alexa web remote.
> The desktop shell is working and must not regress. This plan retires the dual mobile/desktop player widgets, resolves the CSS cascade wars, and retrofit touch-first interactions.

---

## 1. Executive Summary

The app is a Flask + vanilla-JS web remote (no SPA framework). Its desktop shell — a YT Music-style left rail, persistent bottom playbar, and full routed pages like Home / Search / Artist / Album / Playlist / Now-Playing — was redesigned in the recent `redesign.css` overhaul. The **mobile experience is half-finished**: an older "card-stack" mobile implementation (bottom-sheet popups, hidden DOM stubs, hover-dependent affordances) was left in place alongside the desktop shell, with multiple media queries in `player.css`, `base.css`, `search.css`, and `redesign.css` fighting for control. The result: hamburger hidden, minibar layout folded in impossibility, dual expanded-player widgets (#mini-popup-overlay vs #now-playing), touch surfaces gated on `:hover`, and a queue bottom-sheet that drags itself past its own bounds.

This plan:

1. Audits every break with file:line citations.
2. Picks **one ASP.NET-of-truth** for mobile styling (`redesign.css`) and **one expanded-player pattern** (`#now-playing-section` global).
3. Lays out phased work: **P0 unblocks navigation, P1 consolidates player/queue, P2 retrofits touch affordances, P3 cleans PWA shell**.
4. Specifies acceptance criteria and verification.

The plan is code-light by intent — no actual diffs are included here — so the implementer can choose between PWA-era homepage rewrites vs surgical edits per problem statement.

---

## 2. Architecture Snapshot (current state)

### Tech stack

- Jinja templates in `flask-server/templates/{index,login,jam,remote}.html`; CSS/JS `{% include %}`-ed into strings.
- No build system, no framework, no Tailwind. Plain CSS media queries, plain JS modules loaded sequentially as `<script>` tags.
- Cache-busting via `{{ asset_v }}` → `window.APP_VERSION`.
- Routes live in internal `window.__route` (NOT the URL hash). Hash comes from clean-URL pushstate. Routes: `#home`, `#explore`, `#library`, `#history`, `#playlist/<id>`, `#album/<id>`, `#artist/<id>`, `#artist/<id>/songs`, `#mood/<params>`, `#now-playing`.
- Mobile breakpoint: `@media (max-width: 899px)`; desktop: `@media (min-width: 900px)`.
- PWA enabled: `manifest.webmanifest`, service worker, install banner, update toast. Dark theme `#0a0a0a` + orange accent `#e8590c`. iOS safe-area via `env(safe-area-inset-*)`. `<meta name="viewport-fit=cover>` set on `remote.html` and `jam.html`.

### Three page templates

| Template      | Audience               | User actions       | CSS modules loaded                                                                                                  |
| ------------- | ---------------------- | ------------------ | ------------------------------------------------------------------------------------------------------------------- |
| `login.html`  | Anonymous              | Credentials + TOTP | Self-contained                                                                                                      |
| `remote.html` | Logged-in owner        | Full app           | base, header, sidebar, player, queue, search, toast, recs, home, artist, playlist, **redesign**                     |
| `jam.html`    | Guest (via share link) | Search + play only | **base, player, queue, search, toast, home, redesign, jam** (no header, no sidebar, no artist CSS, no playlist CSS) |

### Two competing mobile-player systems

| Widget                                                                                  | CSS source                         | JS source                                                                          | Breakpoint                            |
| --------------------------------------------------------------------------------------- | ---------------------------------- | ---------------------------------------------------------------------------------- | ------------------------------------- |
| `#mini-popup-overlay` (bottom-sheet with full transport + volume + queue + drag handle) | `player.css`, `design.css` (minor) | `player.js` `openMiniPopup()`                                                      | Mobile only                           |
| `#now-playing-section` (full-page overlay with art + queue + transport, YT-Music style) | `redesign.css`                     | `player.js` (routes via `#now-playing`), `queue.js` (`renderNpQueue`), `router.js` | Desktop only (mobile shows condensed) |

`player.js openMiniPopup()` decides which widget to open by `matchMedia('(min-width: 900px)')`. That branch is fragile and produces the broken state on mobile because:

- `mini-popup-overlay` lives only inside the `remote.html` static hidden-stub block in `jam.html` (where it shouldn't even be needed).
- On mobile `#now-playing-section` shows full-width art stacked above the queue but the queue list `#np-queue-list` is empty (`queue.js renderNpQueue` only runs when the desktop overlay route is active).
- `openMiniPopup()` from inside the bottom playbar on mobile → opens the bottom sheet.
- Tapping the now-playing artwork on mobile → plays/pauses; doesn't open anything.
- Result: a Mobile user gets a bottom sheet with no way to swipe into the queue UI that the rest of the app navigates to.

### Three media-query layers fighting for the playbar

| CSS file                         | Mobile rules on `.player-section`                                                                                                                                                                                            | Specificity                           |
| -------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------- |
| `base.css`                       | None (just Bootstrap-style section chrome)                                                                                                                                                                                   | Low                                   |
| `player.css` (line ~460-820)     | `.player-section.redesigned-bar` block + Mobile Adjustments section (lines 821-890) with `!important` overrides                                                                                                              | High (uses `!important` occasionally) |
| `redesign.css` (line ~1100-1300) | Floating compact card: `left:8px; right:8px; bottom:8px; grid-template-columns: minmax(0, 1fr) auto auto; padding: 12px 12px 10px; border-radius: --radius-xl`, hides `.vol` and `.transport button[data-action="previous"]` | Highest (loaded last normally)        |

The "loading order wins" rule holds for non-`!important` rules, but `player.css` Mobile Adjustments uses `!important` (`width: 46px !important; height: 46px !important;` for `.pp-btn.dominant`). Specificity wars around `flex`, `width`, `border-radius`, `padding`, `margin`, and complex `:has()` selectors from `redesign.css` mean a single state can have multiple competing rules resolved in unexpected order.

---

## 3. Audited Break List

> Each break cites the file & approximate line(s) responsible so the implementer can grep-and-fix.

### 3.1 Navigation & shell

**B-01. Hamburger button is hidden globally on mobile.**

- File: `flask-server/templates/static/css/redesign.css`
- The rule `.hamburger-btn { display: none !important; }` at the very top of the file has no `min-width: 900px` wrapper.
- `base.css` line ~717 `@media (max-width: 899px) .hamburger-btn { display: inline-flex; }` therefore loses to the redesign rule.
- Mobile has NO way to invoke `sidebar.js openSidebar()`. The right-side drawer is unreachable.
- **Visible symptom:** `header.html` shows brand + empty right cluster; tapping the brand navigates Home but the user can't sign out, change device, or reach Library/History/Explore.

**B-02. Right-side header actions are hidden on mobile with no replacement.**

- File: `header.css` (device picker is wrapped in `.device-section-inline { display: flex }` with mobile override `display: none !important` in `base.css` line ~725).
- File: `redesign.css` hides `#history-modal-btn` and the standalone `#playlists-modal-btn`/`#jam-btn` on desktop, leaving a leaner desktop header.
- Mobile has no header buttons at all.

**B-03. Sidebar drawer in mobile dependent on the hamburger.**

- File: `flask-server/templates/static/css/sidebar.css` lines 1-90 define the right-side drawer (width: 280px, transform: translateX(100%)).
- File: `redesign.css` lines 1180+ add `@media (max-width: 899px) .sidebar .rail-toggle-btn { transform: translateX(10px) }` — but the sidebar itself depends on the hamburger click chain in `sidebar.js openSidebar()` (line 65).
- **Cascade:** hamburger invisible → openSidebar never fires → drawer never slides → user is locked out of drawer.

**B-04. Brand row has a misplaced toggle on mobile.**

- File: `redesign.css` `@media (max-width: 899px) .rail-toggle-btn { transform: translateX(10px); }`. The wordmark sits after the toggle and the toggle has a fixed left bearing.
- On mobile `header.css` defines `.brand { gap: 14px; }`. The 14px gap between the rail-toggle and wordmark plus the 10px translateX produces a slightly off-axis header.

**B-05. Profile menu (account dropdown) cannot be reached on mobile.**

- File: `flask-server/templates/remote.html` profile menu is wrapped in `class="profile-menu-wrap needs-login" id="profile-menu-wrap" hidden`.
- File: `redesign.css` line ~311: `.profile-menu-wrap { position: relative; flex: none; }` — but the trigger is `id="profile-menu-trigger"` and lives in `.header-actions` which is **not present on mobile** (`device-section-inline` is hidden) and profile-menu-wrap itself is `hidden` via `needs-login` attribute before login.
- Even after login, the trigger button has no entry point in the mobile header.

### 3.2 Playbar

**B-06. Two CSS systems for the same component.**

- File: `player.css` lines 460-820 define `.player-section.redesigned-bar` (desktop persistent bar with left/center/right clusters).
- File: `player.css` lines 821-890 apply Mobile Adjustments: `.redesigned-bar .player-center .progress { display: none !important; }`, hides `.player-right > *:not(.player-expand-btn)`, `.redesigned-bar .np-art { width: 40px !important; }`.
- File: `redesign.css` lines 1100-1300 define an entirely different `.player-section` (not `.redesigned-bar`) at the modern shell, with mobile floating card layout.
- Both target the same DOM (`#player-section`). With `redesigned-bar` class still on it, multiple rules fight.

**B-07. Previous button hidden on mobile but next/pause shown — inconsistent.**

- File: `redesign.css` mobile block: `.player-section .transport button[data-action="previous"] { display: none !important; }`.
- File: `player.css`: `.redesigned-bar .player-center .transport button[data-action="previous"]` ALSO has `display: none !important` for mobile.
- Result: Users can skip forward but cannot go back without opening the queue sheet — an asymmetry users will assume lacks support.

**B-08. Volume cluster hidden on mobile playbar; only exists in queue modal.**

- File: `redesign.css` mobile block: `.player-section .vol { display: none !important; }`.
- File: `player.js mpVolume` is wired for the now-suppressed `#mp-volume` slider inside the mini-popup.
- Means: NO seekable volume control exists on mobile end-to-end.

**B-09. Playback-rate and shuffle button placement drifts.**

- File: `redesign.css` mobile block: keeps `.np-shuffle-btn` (visible, 34×34) and `.np-like-btn` (targeted via the unified `:is()` rule above).
- File: `player.css`: `.redesigned-bar .np-shuffle-btn` only renders in the older playbar version.
- The "3-... more menu" `np-more-wrap` opacity:0 until hover has no touch fallback.

**B-10. `body:has(.player-section.is-visible)` keeps state cache on mobile.**

- File: `redesign.css` line 273: `main:has(.player-section.is-visible) { padding-bottom: calc(var(--playbar-h-mobile) + 12px); }`.
- `jam.css` line 273: same. But during a route-leave-to-home the class persists until a follow-up track arrives. With the now-retired `#mini-popup-overlay` lifecycle this can leave residual padding for a frame after the bar hides.

### 3.3 Expanded player / modal architecture

**B-11. Two expanded-player widgets exist; mobile routes use the wrong one.**

- File: `flask-server/templates/static/js/player.js` `openMiniPopup()`:
  ```js
  if (window.matchMedia('(min-width: 900px)').matches) { ... window.navigateTo('#now-playing'); ... return; }
  if (_miniPopupOpen) return;
  _miniPopupOpen = true;
  ... overlay.classList.add('open'); // #mini-popup-overlay
  ```
- File: `#now-playing-section` markup is in `remote.html` (real content) AND in `jam.html` static hidden-stub block ("Hidden stubs"). Mobile `jam.html` shows real content via the stub.
- Mobile users can open the bottom sheet (`#mini-popup-overlay`) AND can navigate to `#now-playing` via deep-link — they get two different players in one app.

**B-12. Queue inside `#now-playing-section` is never populated on mobile.**

- File: `flask-server/templates/static/js/queue.js` `renderNpQueue()`:
  ```js
  if (list) {
    if (!queue || queue.length === 0) { ... 'No queue' ... }
    ... renderNpQueue(queue, currentIndex);
  }
  ```
- File: `router.js` `#now-playing` route handler:
  ```js
  if (window._lastQueueJson && window.renderNpQueue) {
    try { var queue = JSON.parse(window._lastQueueJson); window.renderNpQueue(queue, ...); }
  }
  ```
- The route is only invoked when the user navigates to `#now-playing`. On mobile users hit `#now-playing` only via the route but they don't see it populated until they re-route after first SSE.

**B-13. Queue bottom-sheet drag area clips taps intended for close.**

- File: `flask-server/templates/static/css/queue.css` lines 309-400: `.queue-modal-drag { padding: 10px 0 6px; cursor: grab; touch-action: none; }` and `.queue-modal-header { padding: var(--space-2) 20px var(--space-3); cursor: grab; touch-action: none; }`. The whole header is the drag area — the close button overlaps it.
- File: `flask-server/templates/static/js/player.js` `onDragStart`: `if (e.target.closest('.mini-popup-close') || e.target.closest('.mini-popup-queue-btn')) return;` — this EXCEPTION is in the **mini-popup** code, not in the queue-modal handler. So `mini-popup-close` is excluded but `#queue-modal-close` is not.

**B-14. Auto-scroll inside the queue modal can run past the bound.**

- File: `flask-server/templates/static/js/queue.js` `_attachQueueDragReorder` `findScrollContainer()` walks up for overflowY: auto on the listEl; if none, falls back to listEl itself.
- File: `flask-server/templates/static/css/queue.css`: `.queue-modal-body { flex: 1; overflow-y: auto; overscroll-behavior: contain; }` — body is the scroll container.
- The drag handler's `findScrollContainer` walks up from `listEl = body`, sees body overflow-y, returns it. Speed logic computes `_scrollSpeed` based on `EDGE_ZONE = 50px` to the container's top/bottom. If the touch is below container bottom but inside an outer overlay (`#queue-modal-overlay` is fixed inset:0) → no scroll target → stuck.

**B-15. Swipe-to-delete inside queue modal conflicts with modal drag handle.**

- File: `queue.js _attachQueueSwipeGestures`: uses `touchstart` on the `.queue-item`, conflict-free:
  ```js
  if (e.target.closest(".queue-drag-handle") || e.touches.length !== 1) return;
  ```
- File: `queue.css .queue-delete-underlay` is `position: absolute; left: 0; right: 0;` — covers the whole row including the drag handle's eye region. Pulling left 80px triggers delete.

### 3.4 Search & results

**B-16. Sticky search bar competes between `search.css` and `redesign.css`.**

- File: `search.css` lines 340-380: `@media (max-width: 899px)` defines `body.results-open .needs-login:has(#play-query) { position: sticky; top: 0; ... backdrop-filter: blur(8px); }` and `body.results-open main { padding-bottom: 92px; }`.
- File: `redesign.css` lines 790-820: `@media (max-width: 899px) .play-section { position: sticky; top: 0; ... backdrop-filter: blur(16px); }`.
- Two media queries both target sticky `.play-section` with different `padding`, `margin`, `backdrop-filter`, `background`, etc. The `redesign.css` rule loads later and generally wins, but `:has(#play-query)` rule from `search.css` is more specific when results are open.

**B-17. `.play-section` still carries unused legacy DOM.**

- File: `flask-server/templates/remote.html` and `jam.html` still include `<button class="btn-accent" id="play-query">GO</button>` and `<div class="section-head"> <div class="label">Play</div> </div>` and `<div class="row">` inside `.play-section`.
- File: `redesign.css` `#play-query { display: none !important; }` and `.play-section .section-head { display: none; }` hide those chunks but they remain in the DOM and create two flexible children inside the search wrap.
- Net effect: The label/row markup is hidden but the `flex/gap` calc gets a phantom 2nd child.

**B-18. Search results list lacks dedicated swipe handler on mobile.**

- File: `flask-server/templates/static/js/queue.js` `_attachSwipeGesture` is wired to `.result-item-inner` rows (left swipe = Play Next, right swipe = Add to Queue).
- The swipe works only when JS sees the rows; the swipe underlay (`.underlay-play-next` / `.underlay-add-queue`) is positioned at z-index:0 absolutely behind each row wrapper.
- File: `search.css` `.result-swipe-underlay { ... width: 100%; }` — but the visual underlay is meant to appear only when `.swiping-right`/`.swiping-left` is on the wrapper. Browser testers on iOS Safari sometimes "swallow" the swipe event when the user starts on a button inside the row.

**B-19. `#results-section .result-queue-btn` exists in DOM but is hidden on mobile.**

- File: `base.css` line ~739: `.result-queue-btn { display: none !important; }` for mobile (the queue-add is replaced by the swipe gesture). The button is dead weight in the DOM.
- File: `search.css` defaults: `.result-queue-btn, .result-like-btn { width: 34px; height: 34px; }` — those rules only apply on desktop where the queue button is rendered.

**B-20. Top-result card `.top-result-card` is too wide on narrow phones.**

- File: `search.css` `.top-result-card { padding: 24px; gap: 24px; }` and `.top-result-art { width: 120px; height: 120px; }` for desktop.
- File: `search.css` `@media (max-width: 768px) .top-result-card { flex-direction: column; padding: 16px; align-items: stretch; }` is fine, but `.top-result-songs` width becomes 100% and individual rows' tap targets collide with the column gap.

### 3.5 Home feed

**B-21. Shortcuts shelf on mobile collapses to a 4-row grid (broken layout).**

- File: `home.css` line ~273 `@media (max-width: 600px) .home-layout-shortcuts .home-shelf-content { grid-template-rows: repeat(4, 1fr); grid-auto-columns: max-content; }`. Multiplies the row count.
- File: `home-renderers.js` renders shortcut items as 56px tiles — with 4 rows they're tiny.
- File: `home.js` no longer fetches the shortcuts data-set; the home feed no longer returns a `shortcuts` layout. The 4-row grid is unreachable in practice but remains styled.

**B-22. Home-feed greeting only adjusts font.**

- File: `home.css` `.home-greeting { font-size: 1.5rem; font-weight: 700; }` → `@media (min-width: 900px) { font-size: 2rem; padding: 10px 0 0; }`. No mobile-specific reactiveness — no clock-based greeting on mobile, just smaller.

**B-23. `.home-feed-content` padding drifts.**

- File: `home.css` mobile blocks line 273 and 321 adjust `.home-shelf-content` padding.
- File: `redesign.css` body and main padding for the shell — `main { padding: 0 var(--space-4); }` always. The shelf-content uses its own padding inside the section → disjoint with `.home-section` overall gutter.

### 3.6 Artist / Library / Playlists

**B-24. `.artist-song-play-btn` hidden behind `:hover` on touch.**

- File: `artist.css` lines ~340: `.artist-song-play-btn { opacity: 0; pointer-events: none; }` baseline, then `.artist-song-row:hover .artist-song-play-btn, .artist-song-row:focus-within .artist-song-play-btn { opacity: 1; transform: translateY(-50%) scale(1); pointer-events: auto; }`.
- Touch devices never receive `:hover`, so users must tap the small cover button OR tap-then-tap to navigate. This is reported in user feedback referenced in commit history.

**B-25. Artist-page hero height mismatch between skeleton and real content.**

- File: `artist.css` `@media (max-width: 768px) #artist-hero { height: calc(340px + var(--topbar-h, 64px) + 7px); }`.
- File: `artist.css` `@media (max-width: 768px) #artist-skeleton .artist-skeleton-hero { height: calc(320px + ...) }`.
- Loading flicker when skeleton → content swap.

**B-26. `.artist-hero-content` left padding on mini-rail desktop subtracts mobile margin.**

- File: `artist.css` `@media (min-width: 900px) body.rail-collapsed.artist-route #artist-hero { margin-left: calc(-28px - var(--rail-w-mini)); padding-left: calc(48px + var(--rail-w-mini)); }`. Safe for desktop; ignored on mobile.

**B-27. Playlist detail on mobile uses centered modal but `body:has(...open)` lock targets page scroll, eating the bottom-sheet's own scroll on iOS.**

- File: `redesign.css` `body:has(#playlist-detail-modal-overlay.open), body:has(#history-modal-overlay.open), body:has(#explore-modal-overlay.open), body:has(#library-modal-overlay.open) { overflow: hidden; overscroll-behavior: none; }` is mobile-friendly.
- File: `header.css` `.history-modal-overlay { ... inset: 0 ... background: rgba(0,0,0,.7); }` — inset:0 keeps it fill-screen but on mobile iOS Safari, the dynamic URL bar steals height, leaving the modal not fully backing the device.

**B-28. Playlist hero buttons row on mobile shrinks to sub-tap-target.**

- File: `playlist.css` `@media (min-width: 900px) .playlist-hero-shuffle, .playlist-hero-play-next, .playlist-hero-share, .playlist-hero-more { width: 44px; height: 44px; }` only desktop.
- Mobile defaults inherit `.playlist-card-play` (width: 44px; height: 44px) which is OK, but the cluster `.playlist-detail-hero-actions` is not mobile-tuned.

**B-29. `#album-section` on mobile shows nothing.**

- File: `redesign.css` `#album-section { width: 100%; max-width: none; margin: 0; padding: 24px 32px 96px; ... }` — 32px padding ×2 + 96px bottom padding fits desktop.
- File: `@media (max-width: 768px)` rule not defined for `#album-section`. Mobile users navigate to `#album/...` and see desktop layout crammed into 360-dp viewport (overlapping text).

### 3.7 PWA shell & iOS chrome

**B-30. Update toast and install banner stacking conflicts.**

- File: `base.css` `.install-banner { padding: env(safe-area-inset-bottom, 0px); }` line ~38.
- File: `base.css` `.update-toast { top: 12px; }` — but `redesign.css` `body:not(.preload) header` z-index is 240; install-banner z-index is 9000.
- On iOS, when the install banner is at bottom and the safe-area is consumed, a notch in the iOS status bar can push it visually up. Combined with `body:has(.player-section.is-visible) .toast { bottom: calc(var(--playbar-h-mobile) + 20px); }` the toast goes 20px ABOVE the bar's env-inset → can clip with the install banner.

**B-31. Body `padding: env(safe-area-inset-bottom)` is missing.**

- File: `body { ... }` in `base.css` doesn't include any safe-area padding rules.
- File: `redesign.css` adds `padding: 0` on mobile but doesn't add safe-area to `body` itself.
- File: `player.css mini-popup-body` and `queue-modal-body` both use `padding-bottom: env(...)`. The interactive playbar at the bottom of the viewport is offset from the home-indicator via `bottom: calc(8px + env(safe-area-inset-bottom))` (redesign.css line 1133). This is correct — but anything else (e.g., a 1-legged CSS variable) doesn't pick up the safe area.

**B-32. Status-bar color meta mismatch.**

- File: `remote.html` and `jam.html` meta `apple-mobile-web-app-status-bar-style content="black-translucent"` is set.
- iOS renders the status bar transparent over the page; combined with dark page background this is fine. But **when the install banner shows**, the banner overlays the status bar's bottom edge because the banner is `bottom: 0` and includes its own safe-area padding. The bar's `padding-bottom: env(...)` is correct but `padding-top` is not — leaving 0 padding between the bar's content and the iOS top inset boundary when the iPhone is in landscape.

**B-33. Service-worker update prompt interaction.**

- File: `pwa.js` shows `.update-toast` with `top: 12px` and translateX(-50%). On page load it can stack below the install banner — and the install banner is dynamic. The update toast can render with leftover offsets on mobile.

### 3.8 Queue rendering

**B-34. `#queue-list` is `display: none !important` on mobile then renders inside the queue modal — but the modal body reuses the row builder — orphaned event listeners.**

- File: `base.css` line ~728 `#queue-section { display: none !important; }` for mobile.
- File: `queue.js _buildQueueRow` calls `_attachQueueSwipeGestures(wrapper, el, i, item, currentIndex)` and `_attachQueueDragReorder(el, container, i)` where `container = #queue-modal-body`. Inside the modal, `_attachQueueDragReorder` calls `findScrollContainer()` which finds `#queue-modal-body`, OK. But `attachQueueItemTap(el, ...)` registers pointer listeners on the row — these leak if `#queue-modal-body` is `.replaceChildren`'d without removing them, and (depending on browser GC) may keep old DOM references.

**B-35. SSE queue updates sometimes paint before the modal opens.**

- File: `sse.js` (untested but inferred from JS ordering) calls `renderNpQueue` for `#np-queue-list` whenever SSE arrives. Mobile doesn't initially show that list. So queue updates arrive but are held until the user routes to `#now-playing`.
- File: `queue.js _renderQueueModal` reads `window._lastQueueJson` and rebuilds `#queue-modal-body`. If SSE hasn't arrived yet, it shows "No songs in queue".

### 3.9 Toast and modal scroll-lock

**B-36. `body.modal-open` lock blocks nested-modal scroll.**

- File: `base.css` `body.modal-open { overflow: hidden; overscroll-behavior: none; }`.
- File: `player.js syncModalScrollLock()`:
  ```js
  const anyOpen = ['mini-popup-overlay', 'queue-modal-overlay'].some(...);
  document.body.classList.toggle('modal-open', anyOpen);
  ```
- iOS Safari with `overflow:hidden` on body makes the queue-modal-body inside lose rubber-banding. Body should keep Bounce, modal-body should clamp.

**B-37. `body.drag-lock` overrides touch-action.**

- File: `base.css` `body.drag-lock { overflow: hidden; overscroll-behavior: none; touch-action: none; }`.
- During reorder-drag with multiple `drag-lock`s the body becomes unscrollable and iOS Safari stops auto-scroll on the body's edges inside the queue modal.

### 3.10 Templates

**B-38. `jam.html` static hidden stubs duplicate IDs from `remote.html`.**

- File: `flask-server/templates/jam.html` lines 70-130 has `<div style="display:none">` blocks with IDs: `mini-player`, `mini-popup-overlay`, `#mp-pp-btn`, `#mp-volume`, `#np-page-title`, `#np-page-artist`, etc.
- File: `remote.html` has the same IDs through real sections. Two pages over same IDs — JS modules that `document.getElementById(...)` rely on whichever page is loaded. Safe in practice (each page is a separate tab/URL) but migration risk if `jam.html` ever grows real DOM instead of stubs.

**B-39. `jam.html` is missing CSS for sidebar/header/artist/playlist/recs.**

- File: `jam.html` line 6-15: only `base, player, queue, search, toast, home, redesign, jam` are loaded.
- File: `jam.css` cherry-picks overrides but doesn't bring header.css or sidebar.css; jam is by-design guest-only — but the markup still contains `home-route` body class and `<button id="sidebar-rail-toggle">` indirectly via JS-isolation patterns? Verify before templating.

**B-40. Login form has no `viewport-fit=cover` meta.**

- File: `login.html` meta is plain `<meta name="viewport" content="width=device-width, initial-scale=1">`. On iPhone X-series the credential card doesn't account for top safe-area inset — bottom is also fine without banner, but the brand has `margin-bottom: 24px` which can hit the home indicator when the form is filled.

### 3.11 Routing & overlays

**B-41. Body class toggling for routes (`home-route`, `playlist-route`, `artist-route`) drives shell padding — mobile ignores some.**

- File: `redesign.css` `body.artist-route:not(.header-scrolled) main header, body.album-route:not(.results-open):not(.header-scrolled) main header, body.now-playing-route:not(.results-open) main header { background: transparent; }` is desktop-only (the comment says "Wide screens" earlier; but the rule is not wrapped in a media query that excludes mobile).
- Mobile does honor `body.artist-route` etc. but the rule applies **only when header is not scrolled** — on mobile scroll within a section scrolls the outer body, not the section itself, so `.header-scrolled` may never apply, leaving the rule permanently off.

**B-42. `header-scrolled` scroll listener uses `window.scrollY > 12` — never fires on mobile when page is "stuck" inside an inner scroll container.**

- File: `router.js syncHeaderScrollState()`:
  ```js
  document.body.classList.toggle("header-scrolled", window.scrollY > 12);
  ```
- Mobile scroll feeds the document only when there's no inner-scroll container. Sections like `#results-section` (which has `overflow: visible`) feed the body. But `#playlist-detail-modal` and `#now-playing-section` both use inner scroll. The header never becomes "scrolled" even on long content.

---

## 4. Architectural Decisions

### AD-01. **CSS source of truth: `redesign.css`**

**Rationale:** Loading order matters and `redesign.css` is included last in `remote.html` and `jam.html`. It already defines the modern, fully-tested mobile playbar. The legacy `.player-section.redesigned-bar` block in `player.css` represents the previous design and should be **removed** so that:

- One CSS rulebook governs the playbar across breakpoints.
- Specificity wars go away: no more `!important` ping-pong between the two files.
- Future CSS changes touch one file.

**Implication for B-06, B-09, B-10, B-16:** Delete the old mobile block from `player.css`; let `redesign.css` own everything.

### AD-02. **Unified expanded player: `#now-playing-section` everywhere**

**Rationale:** Two widgets with two state-sync points is a maintenance disaster. `renderNpQueue()` already supports both desktop and mobile layouts (it just needs a CSS variant for mobile). Routing `#now-playing` works the same on both breakpoints.

**Implication:** `player.js openMiniPopup()` should route to `#now-playing` regardless of viewport. `#mini-popup-overlay` should be **deprecated and removed**. Mobile gets a CSS variant of `#now-playing-section` (stacked vertically) so it shows the same content, no bottom-sheet.

**Trade-off:** The bottom-sheet pattern is a tried mobile UX for "playing track". A full-page replacement still feels native when that's effectively a full page anyway. Users get a single mental model regardless of input device.

### AD-03. **Touch affordances rule: `hover` only when `(hover: hover)`**

**Rationale:** Mobile users don't hover. Any affordance gated on `:hover` is a bug. The codebase already imports the convention in `recs.css` (line 197 `@media (hover: hover)` block) and should expand it everywhere.

**Implication:** All play-button reveal animations, hover-only darkener overlays (`.home-card-art::after`, `.hscroll-card-art::after`), and the `.artist-song-row:hover .artist-song-play-btn` pattern must be wrapped in `@media (hover: hover)` blocks. Touch devices get a permanent control surface via either:

- a tiny always-visible play button to the right of the artwork, OR
- a row-tap fallback that fires `playFromQueue`.

### AD-04. **Hamburger drives sidebar on mobile; the rail-toggle reverts to a desktop-only interaction**

**Rationale:** The brand-area toggle is overloaded across breakpoints. Cleanly separating the two serves clarity.

**Implication:**

- Mobile: Hamburger button visible in `.header-actions`. Tap → opens `#sidebar` as a right drawer.
- Desktop: Brand-area rail toggle continues to collapse/expand the left rail. The existing localStorage key `railCollapsed` is fine.

### AD-05. **CSS/JS load order: keep `<style>{% include %}` inline ordering; never reorder**

**Rationale:** Jinja templates embed CSS via `{% include "static/css/...css" %}` inside `<style>` tags. Reordering can regress multiple modules. **Rule:** When moving rules between files, preserve the relative order in which the original CSS snippets were loaded.

### AD-06. **Mobile breakpoint: 899px / 600px**

**Rationale:** Already the codebase's convention. Don't introduce new breakpoints; consistent fits the layout. The 600px breakpoint is used only by `home.css` for shortcut layouts.

### AD-07. **Accessibility check: ARIA roles for bottom-sheet now **deprecated\*\*

**Rationale:** With the unified full-page `#now-playing-section`, ARIA roles (`role="dialog"`, `aria-modal="true"`) are appropriate for the page transition; the bottom-sheet pattern's `role="dialog"` was scoped per-widget and was wrong for mobile.

---

## 5. Phased Remediation Plan

### Phase P0 — Unblock Navigation (Critical)

**Goal:** Mobile user can reach every top-level surface (Home, Search, Library, History, Explore, Profile, Sign-out) without a working internal state.

#### P0-1. Restore the hamburger button on mobile

- File: `flask-server/templates/static/css/redesign.css`
- Wrap the existing `.hamburger-btn { display: none !important; }` rule in a `@media (min-width: 900px) { ... }` block. Mobile gets the existing default (`display: none` from `base.css` line 731 is overridden by `display: inline-flex` in `base.css` line 720 only when `max-width: 899px`).
- Acceptance: tapping the right end of the header opens `#sidebar` via `sidebar.js openSidebar()`.

#### P0-2. Move device picker into a reachable location on mobile

- File: `flask-server/templates/static/css/redesign.css` line ~1100
- Currently `.device-section-inline` is hidden on mobile (its job migrates to the sidebar). Verify: the sidebar already contains the device picker (`#device-sidebar`); ensure it renders above the sign-out.
- Acceptance: From the sidebar, the user can switch devices without leaving mobile.

#### P0-3. Sign-out reachable on mobile

- File: `flask-server/templates/static/css/sidebar.css` line ~80 already defines `.sidebar-signout { margin-top: auto; }` so the sign-out button pins to the bottom.
- Confirm `base.css` `:not(.jam-guest)` rules don't hide it on mobile (they don't, but verify).
- Acceptance: Sign-out is one tap from the sidebar.

#### P0-4. Verify sidebar drawer slide-in works after P0-1

- File: `flask-server/templates/static/js/sidebar.js`
- Already wires up `openSidebar()` fine given the toggle. Test with no JS errors in DevTools console.
- Acceptance: Console shows no missing element errors after tapping hamburger.

### Phase P1 — Consolidate Player & Queue (Architecture)

**Goal:** Single source of truth for the expanded player and queue; delete the dual system.

#### P1-1. Remove legacy mobile adjustments from `player.css`

- File: `flask-server/templates/static/css/player.css`
- Delete the `/* Mobile Adjustments for Redesign */` block (lines ~821-890). Delete `.redesigned-bar .np-art { border-radius: 6px !important }` if no longer needed.
- Keep `.redesigned-bar` desktop rules because the **desktop** `.player-section` continues to use that class.
- Acceptance: mobile playbar matches `redesign.css` styling exactly.

#### P1-2. Remove `#mini-popup-overlay` DOM and JS

- Files: `flask-server/templates/remote.html`, `flask-server/templates/jam.html`, `flask-server/templates/static/js/player.js`, `flask-server/templates/static/js/queue.js`, `flask-server/templates/static/css/player.css`, `flask-server/templates/static/css/queue.css`.
- Delete the entire `<div class="mini-popup-overlay" id="mini-popup-overlay">…</div>` block from both templates.
- Delete stubs in `jam.html` matching those IDs.
- Delete `_openMiniPopup`, `_closeMiniPopup`, related `mp-*` element references in `player.js`.
- Delete `#mp-np-art`, `#mp-np-title`, `#mp-np-artist`, `#mp-progress-*`, `#mp-prev`, `#mp-next`, `#mp-volume`, `#mp-shuffle-btn` references across CSS/JS.
- Acceptance: `grep -r "mini-popup-overlay" flask-server/` returns zero matches.

#### P1-3. Update `openMiniPopup()` to always route to `#now-playing`

- File: `flask-server/templates/static/js/player.js`
- Replace the `if (window.matchMedia('(min-width: 900px)').matches) { ... navigateTo; return; }` branch with a single unconditional `window.navigateTo('#now-playing')`. Keep the guard for `_hasTrack`.
- Delete the bottom-sheet open path entirely.
- Acceptance: tapping the playbar artwork/expand button on any viewport routes to `#now-playing`.

#### P1-4. Mobile-specific styles for `#now-playing-section`

- File: `flask-server/templates/static/css/redesign.css`
- Currently:
  ```css
  @media (max-width: 899px) {
    .np-page {
      flex-direction: column;
      height: auto;
      overflow-y: auto;
    }
    .np-page-left {
      flex: none;
      padding: 24px 20px;
    }
    .np-page-right {
      flex: none;
      border-left: none;
      border-top: 1px solid...;
    }
    #now-playing-section {
      margin: 0 -16px;
    }
  }
  ```
- Extend to:
  - Give `.np-page-left` a top padding equal to `var(--topbar-h, 64px)` (covers sticky-header overlap).
  - `.np-page-art` width: `min(360px, 88vw)` slightly safer on narrow phones.
  - `.np-page-right` height should be auto with internal scroll (`max-height: 60dvh`).
  - `.np-page-meta text-align: center` already inherited.
  - Add a `.np-page-btns` row of Like/Radio/etc. mobile-tuned buttons (44×44 tap targets).
- Acceptance: `#now-playing-section` is fully usable on mobile; queue visible below the art on narrow viewports.

#### P1-5. Queue renders on mobile when `#now-playing-section` opens

- File: `flask-server/templates/static/js/queue.js renderNpQueue()`
- Already renders into `#np-queue-list` when queue is non-empty. Verify it triggers reliably — the `router.js` `#now-playing` route calls it.
- File: `flask-server/templates/static/js/router.js` `#now-playing` route: ensure `renderNpQueue` is called whether the queue is empty (`return;`) or populated. Currently calls only when `window._lastQueueJson` exists — add a fallback to render the modal-empty state if it's the first paint.
- Acceptance: On mobile, opening `#now-playing` shows the queue within 100ms, or "No queue" if SSE hasn't arrived yet.

#### P1-6. Remove `.queue-modal-overlay`

- Files: `flask-server/templates/remote.html`, `flask-server/templates/static/js/queue.js`, `flask-server/templates/static/css/queue.css`.
- The queue modal as a separate bottom-sheet is a duplicate of `#now-playing-section`'s queue.
- Migration: `#queue-modal-btn` and `mini-popup-queue-btn` are deleted in P1-2. Keep the queue rendered inside `#now-playing-section`.
- The drag-to-reorder, swipe-to-delete, and 3-dot more menu all stay usable inside `#np-queue-list`.
- Acceptance: `grep -r "queue-modal-overlay" flask-server/` returns zero matches.

#### P1-7. Replace `.queue-modal-overflow` and `.queue-modal-body` styles

- The new `.np-page-right` on mobile acts as the queue panel, internally scrolling.
- Update `.queue-modal` styles to mirror `.np-page-right` styling (or remove entirely).
- Acceptance: same look-and-feel; no separate modal needed.

### Phase P2 — Touch Affordances & Component Polish

**Goal:** No element on mobile is gated on `:hover` alone. Tap targets meet 44×44 minimum. Hover decorations add value on desktop touch only when `(hover: hover)`.

#### P2-1. Hover-only affordances: gate on `(hover: hover)`

- Files to update:
  - `flask-server/templates/static/css/home.css`: `.home-card:hover .result-like-btn`, `.home-card:active .home-card-art::before`, etc.
  - `flask-server/templates/static/css/artist.css`: `.artist-song-row:hover .artist-song-play-btn`, `.artist-song-row:hover .artist-song-thumb`.
  - `flask-server/templates/static/css/search.css`: `#results-section .hscroll-card:hover .hscroll-play-btn`, `.hscroll-card:hover .hscroll-card-art::after`.
  - `flask-server/templates/static/css/redesign.css`: `.np-page-art-overlay` (visible on hover for desktop preview).
- Wrap each `:hover` rule in `@media (hover: hover) {}`. Move non-hover decorative overlay into a permanent visibility model for `(hover: none)`:
  - `(hover: none)` shows the play-affordance permanently at low opacity (`.8`) and raises it to 1 on `:active`.
  - Tap-row continues to invoke the play action.
- Acceptance: every visual affordance that matters on mobile can be reached without long-press or hover.

#### P2-2. Touch-target sizing on home / artist / playlist rows

- File: `flask-server/templates/static/css/artist.css`
- Each `.artist-song-row` is `min-height: 64px` — OK for tap.
- The `.artist-song-play-btn` (44×44 tappable by default, scaled to 56px) — make it tap-permanent at low opacity 0.4 then 1 on `:active` for `(hover: none)`.
- File: `flask-server/templates/static/css/home.css`
- `.home-item-song` already 48px-tall; add `padding: 8px 0` for additional tactile area.
- File: `flask-server/templates/static/css/playlist.css`
- `#playlist-detail-modal` mobile: `.playlist-detail-hero-actions` row needs `min-height: 56px`. Each button is already 44px — but the row's vertical padding cuts them to 32px effective on mobile. Add explicit mobile row padding.
- Acceptance: Grep for `min-height: 32px` or lower; replace with 44px minimums.

#### P2-3. Search results — sticky pill

- File: `flask-server/templates/static/css/redesign.css`
- Currently `@media (max-width: 899px) .play-section { position: sticky; top: 0; ... }`.
- Conflict: `search.css` line ~340 duplicates this with `:has(#play-query)`.
- Resolution: Delete `search.css` `:has(#play-query)` block; keep `redesign.css` rule. Verify `body.results-open` state matches.
- Acceptance: tapping "Songs" chip on a search-result page doesn't jump scroll to top; sticky is consistent.

#### P2-4. Search results row tap targets

- File: `flask-server/templates/static/css/search.css`
- `.result-item-inner` `min-height: 56px` is implicit from `padding`. Make explicit.
- `.result-more-btn` is 32×32 — too small for mobile. Wrap its display in `@media (hover: hover)` for desktop, and create a `:hover: none` mobile variant with 44×44 size + permanent visibility.
- Acceptance: tapping the more-options "..." button on a search row works first tap on iOS Safari.

#### P2-5. Top-result card stack sizing on mobile

- File: `flask-server/templates/static/css/search.css`
- `.top-result-card { padding: 16px; }` (mobile).
- `.top-result-title { font-size: 2.2rem; }` is too big for mobile — drop to 1.4rem for `(max-width: 768px)`.
- Acceptance: top-result title doesn't truncate unactionably on iPhone widths.

#### P2-6. Playlist detail list mobile pass

- File: `flask-server/templates/static/css/playlist.css`
- `@media (max-width: 800px) #playlist-detail-modal .playlist-detail-hero .playlist-collage { width: 90px; height: 90px; }` already exists.
- Add mobile rule for `.playlist-track-duration` to not crowd the more button.
- Add mobile rule for `.history-item` rows in the playlist detail: same 56px min-height.
- Acceptance: playlist track rows feel like YT Mobile.

### Phase P3 — PWA Shell & Cleanup

**Goal:** PWA install / update flows are visually clean on iOS. Safe-area handled once at the right place. No DOM stubs leftover.

#### P3-1. Body safe-area padding

- File: `flask-server/templates/static/css/base.css`
- Add `body { padding-bottom: env(safe-area-inset-bottom); }`. Since the persistent playbar is `bottom: calc(8px + env(safe-area-inset-bottom))`, the body now has a fallback in case the playbar is hidden (e.g., on idle).
- Acceptance: On iPhone with home indicator, the playbar's bottom edge does not overlap the indicator.

#### P3-2. Install banner / update toast stacking

- File: `flask-server/templates/static/css/base.css`
- `.install-banner { padding-bottom: env(...); }` is correct. Verify `.update-toast` visual offset doesn't collide with `.install-banner` bottom edge when both are present.
- File: `flask-server/templates/static/js/pwa.js` (no code edits needed here; just verify behavior).
- Acceptance: Both UI surfaces coexist without overlap on iPhone.

#### P3-3. Strip static hidden stubs from `jam.html`

- File: `flask-server/templates/jam.html` lines ~70-130
- Delete the entire `<div style="display:none">…</div>` block.
- Confirm `player.js`, `queue.js`, `search.js` etc. don't reference these stubs.
- Acceptance: HTML lint passes; no duplicate IDs between `remote.html` and `jam.html`.

#### P3-4. Verify `jam.html` body's CSS module set

- File: `flask-server/templates/jam.html` line ~6
- Currently omits `header.css`, `sidebar.css`, `artist.css`, `playlist.css`, `recs.css`. Intentional for guest shell.
- Verify the markup in `jam.html` doesn't contain `class="needs-login"` elements that would never become visible — quick scan confirms none.
- Acceptance: jam guest page only renders its stripped-down shell (search, results, persistent playbar). No silent dependency on missing CSS.

#### P3-5. `login.html` safe-area tuning

- File: `flask-server/templates/login.html`
- Add `viewport-fit=cover` meta.
- File: `flask-server/templates/static/css/base.css` (or new additions)
- Card padding-top: `env(safe-area-inset-top, 0px)` to keep the brand out of the status bar.
- Card padding-bottom: keep current spacing.
- Acceptance: Login card sits below iOS status bar on iPhone X+.

### Phase P4 — Queue rendering / State sync hardening (Optional Improvement)

**Goal:** SSE-driven queue updates always paint in `<100ms` across all routes.

#### P4-1. `sse.js`/`queue.js` consistency

- File `sse.js` (untested here)
- Confirm `renderNpQueue` is invoked from the SSE handler (not from `showQueue` only when on `#queue` route).
- Acceptance: queue ListView updates eliminate stale UI after SSE push.

#### P4-2. `#np-queue-list` content visibility

- File: `flask-server/templates/remote.html` line ~700
- The `#np-queue-list` is inside `#now-playing-section` (set `hidden` by default). Confirm it doesn't pre-populate when the section is hidden — only render when section visible.
- Acceptance: Mobile `#now-playing-section` doesn't render its queue eagerly; renders when routed.

---

## 6. Acceptance Criteria Summary

| Item                                                                                    | Verification                                                               |
| --------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| Mobile hamburger opens sidebar drawer                                                   | Visual: drag from right across hamburger. Tap brand/Home macro to dismiss. |
| Sidebar contains Home / History / Explore / Library / Jam / Sign-out / Device           | Sidebar fully scrollable on iPhone 12 viewport.                            |
| Persistent playbar visible as a floating compact card on mobile                         | Visual: bottom of viewport, ~112px tall, rounded edges.                    |
| Playbar art thumbnail, song title, transport (Play/Pause/Next), Shuffle, Expand visible | Mobile screenshot.                                                         |
| Previous button visible or moved elsewhere                                              | Mobile has Previous in expanded view; document the asymmetry if removed.   |
| Tap playbar (or expand chevron) opens `#now-playing` as a routed page                   | Mobile: visual swipe-up transition not bottom-sheet slide.                 |
| `#now-playing-section` shows art stacked above queue on mobile                          | Mobile screenshot through album art + queue list.                          |
| Queue shows tracks, supports swipe-to-delete, drag-to-reorder, 3-dot context menu       | Mobile interaction test.                                                   |
| Search pill sticks to top when results are open                                         | Mobile screenshot; no scroll-jump.                                         |
| Tap a search result row plays (no hover dependency)                                     | Interaction test on iOS Safari.                                            |
| Artist page play button visible on touch                                                | Visual.                                                                    |
| Install banner + update toast no overlap on iPhone with home indicator                  | Visual iPhone X/11/14/15 screenshots.                                      |
| Sign-out reachable                                                                      | Sidebar → sign-out.                                                        |
| Library / Explore reachable                                                             | Sidebar or footer nav.                                                     |

---

## 7. Verification Plan

> Chrome and emulator farms are not in the dev environment. Verification must rely on:

- Browser DevTools device-mode emulation.
- Browser screenshot capture across breakpoints.
- Manual smoke tests in `flask-server/templates/{remote,login,jam}.html`.

### Manual checks

1. **Hamburger drawer**: iPhone 12 emulation, dev mode, open + close + scroll inside.
2. **Playbar visibility**: Play a track; capture mobile screenshot at 360×800 and 414×896 (iPhone sizes).
3. **Tap playbar**: Routes to `#now-playing`. Verify art + queue visible.
4. **Search**: Type a song, results render. Verify sticky pill. Tap a result. Verify row hit area.
5. **Artist page**: Navigate to `#artist/<id>`. Verify hero, top songs visible. Verify play button.
6. **Library / History / Explore**: Sidebar navigates correctly.
7. **PWA**: Check manifest.webmanifest + service worker registered. Check Install banner shows after second visit (iOS visitors use Safari's native share-to-home-screen).
8. **Swipe gestures**: Inside `#np-queue-list`, swipe left deletes, drag-reorder works.

### Regression checks

- Desktop layout (≥ 900px) must not regress: rail, persistent bar, full-page now-playing overlay, two-column playlist detail layout, home-shelf rows.
- Jam guest page (`jam.html`) still serves only the limited shell (search + play, public charts). No hidden state from the owner app.
- Login flow + TOTP works on mobile (small viewport — keyboard interactions may differ).

### Tooling suggestions

- `flask-server/Dockerfile` spins up a local Flask container — no progressive build. Hot reload requires a Flask debug mode toggle that's not currently set.
- If added, consider `Flask-Tailwind` for tokens; not required.
- For visual regression, snapshot the rendered HTML+CSS at multiple breakpoints via Playwright in headless mode.

---

## 8. Risk Register

| #    | Risk                                                                                                                    | Impact                                 | Severity | Mitigation                                                                         |
| ---- | ----------------------------------------------------------------------------------------------------------------------- | -------------------------------------- | -------- | ---------------------------------------------------------------------------------- |
| R-01 | Moving `#mini-popup-overlay` deletion can orphan references in `playlist-context-menu.js`, `song-context-menu.js`, etc. | High — broken scripts                  | Med      | Grep for `mini-popup`, `mp-pp-btn`, `#mp-` before deletion.                        |
| R-02 | `player.css` mobile adjustments may have inline comments referencing other rules.                                       | Low                                    | Low      | Comment search before delete.                                                      |
| R-03 | Body safe-area padding may double-offset with `.install-banner .padding-bottom: env(...)`.                              | Med                                    | Med      | Apply safe-area **only at the body level** for idle, let banner pad itself.        |
| R-04 | Changing route to `#now-playing` on mobile increases render cost (full art + queue).                                    | Low                                    | Low      | `renderNpQueue` already lazy-renders with chunked windowed rows.                   |
| R-05 | Removing `.queue-modal-overlay` may delete drag-to-reorder env that mobile tests rely on.                               | Med                                    | Med      | Re-attach handlers to `#np-queue-list`.                                            |
| R-06 | Removing the legacy player-side `redesigned-bar` class on mobile.                                                       | High if accidentally stripped globally | High     | **Constraint: only strip its **mobile adjustments** block; keep desktop.**         |
| R-07 | PWA install banner overflowing iOS browsers' persistent status-bar.                                                     | Low                                    | Low      | Add `padding-top: env(safe-area-inset-top, 0px)` for banner.                       |
| R-08 | `body.results-open .needs-login:has(#play-query)` selection capacity on older WebViews.                                 | Med                                    | Med      | Provide a class-based fallback (`.search-results-open`) for non-`:has()` browsers. |
| R-09 | Legacy `#play-query` button is hidden but referenced by some browser extensions (e.g., Vimium).                         | Low                                    | Low      | Delete entirely.                                                                   |
| R-10 | Dropping hamburger from reordering CSS could remove `.rail-toggle-btn` mobile transform.                                | Low                                    | Low      | Verify in mobile viewport.                                                         |
| R-11 | Server-side `flask-server/server.py` may emit events tied to old DOM IDs (`mini-popup-overlay`).                        | Low                                    | Low      | Server emits JSON; client renders. No server-side coupling.                        |
| R-12 | iOS Webkit's gesture-handler on `:has()` for body may degrade below iOS 16.                                             | Low                                    | Low      | Test on iOS 15 Safari if user base includes older devices.                         |
| R-13 | Home feed multi-shelf layout degrades on iPhone-SE-sized screens.                                                       | Med                                    | Med      | Test at 320×568 too.                                                               |
| R-14 | `body:has(.player-section.is-visible)` uses `:has`, may fail in unsupported browsers.                                   | Low                                    | Low      | Provide fallback `.has-playing-track` class.                                       |
| R-15 | Touching `rem` font-size scale may make certain buttons subtle on iOS user's Dynamic Type.                              | Low                                    | Low      | Use `clamp()` for font sizes where Dynamic Compatibility matters.                  |

---

## 9. Out-of-Scope (Future Considerations)

The following are noted but not part of this remediation; raise them in a later milestone:

1. **Server-Sent Events fallback to polling** for flaky networks. Currently SSE is the only transport. Could add 5s+ polling on reconnect retry.
2. **Adaptive image quality** based on viewport — currently loaded lazily but with low-res fallback. Could fetch higher resolution for tablet retries.
3. **Offline mode** for PWA — currently install banner shows; offline page should be a stub for the Caddy `static/`.
4. **Standalone PWA splash image** for iOS — `apple-touch-startup-image` meta is not set in `remote.html` or `jam.html`.
5. **Headset media-button key handling** — JS does not listen for `MediaKey` events.
6. **Voice search** — only text input. Could integrate Web Speech API.
7. **Group chat / multi-device sync** — extend Jam mechanism beyond one guest.
8. **EQ / Audio Effects** — server-side limitation, but UI placeholders could evolve.
9. **Lyrics integration** — no API call yet.
10. **Multi-track download for offline**. No scope creep here — keep this as policy.

---

## 10. Quick-Reference for the Implementer

If you read the entire codebase once and want a checklist version:

### P0 (must do)

- [ ] Wrap `.hamburger-btn { display: none !important; }` in redesign.css with `@media (min-width: 900px)`.
- [ ] Verify sidebar opens from hamburger tap.
- [ ] Check device picker is in sidebar.
- [ ] Check sign-out is reachable.

### P1 (do next)

- [ ] Delete `player.css` Mobile Adjustments block (lines ~821-890).
- [ ] Delete `#mini-popup-overlay` HTML + JS.
- [ ] Update `openMiniPopup()` to always route to `#now-playing`.
- [ ] Delete `#queue-modal-overlay` HTML + JS.
- [ ] Move queue/scrub/drag handlers to `#np-queue-list`.
- [ ] Add mobile CSS for `#now-playing-section` (stacked layout).

### P2 (do after)

- [ ] Wrap every `:hover` rule in `@media (hover: hover)`.
- [ ] Add touch-target minimums (44×44) where they fall short.
- [ ] Sync artist hero skeleton height with real height.
- [ ] Sync top-result-card font/scales for mobile.
- [ ] Resolve sticky search-bar CSS fights.

### P3 (do last)

- [ ] Bake safe-area into body padding for idle.
- [ ] Update install / update banner stacking for iPhone.
- [ ] Strip hidden stubs from jam.html.
- [ ] Add viewport-fit meta + top safe-area to login.

### Optional

- [ ] Add `filterShelves` tests for mobile visibility.
- [ ] Snapshot tests for mobile viewports.

---

## Appendix A: File-by-File Change List

| File                                             | Phase                                        | Type                |
| ------------------------------------------------ | -------------------------------------------- | ------------------- |
| `flask-server/templates/static/css/redesign.css` | P0-1, P1-4, P1-7, P2-1 (partial), P2-3, P3-1 | Edit                |
| `flask-server/templates/static/css/player.css`   | P1-1, P1-2, P2-1, P3-1 (footer)              | Edit                |
| `flask-server/templates/static/css/base.css`     | P2-1, P3-1, P3-2                             | Edit                |
| `flask-server/templates/static/css/queue.css`    | P1-6, P1-7                                   | Edit / Delete       |
| `flask-server/templates/static/css/search.css`   | P2-1, P2-3, P2-5, P2-4                       | Edit                |
| `flask-server/templates/static/css/artist.css`   | P2-1, P3-3 (technical)                       | Edit                |
| `flask-server/templates/static/css/playlist.css` | P2-1, P2-6                                   | Edit                |
| `flask-server/templates/static/css/home.css`     | P2-1, P2-2                                   | Edit                |
| `flask-server/templates/static/css/header.css`   | P0-2                                         | Verify (no change)  |
| `flask-server/templates/static/css/sidebar.css`  | P0-3, P0-4                                   | Verify              |
| `flask-server/templates/static/css/jam.css`      | P3-4                                         | Verify              |
| `flask-server/templates/static/js/player.js`     | P1-2, P1-3                                   | Edit                |
| `flask-server/templates/static/js/queue.js`      | P1-5, P1-6, P4-1, P4-2                       | Edit                |
| `flask-server/templates/static/js/router.js`     | P1-5 (verify), P3-4 (related)                | Edit (small)        |
| `flask-server/templates/static/js/sidebar.js`    | P0 (verify)                                  | Verify              |
| `flask-server/templates/static/js/sse.js`        | P4-1, P4-2                                   | Verify / small edit |
| `flask-server/templates/remote.html`             | P1-2, P1-6, P3-3 (mirror)                    | Edit                |
| `flask-server/templates/jam.html`                | P1-2, P3-3, P3-4                             | Edit                |
| `flask-server/templates/login.html`              | P3-5                                         | Small edit          |

---

## Appendix B: Pattern Library

### B1. Mobile-only CSS guard pattern

```css
.thing {
  /* Desktop-only when hovered */
  opacity: 0;
}
@media (hover: hover) {
  .thing:hover,
  .thing:focus-within {
    opacity: 1;
  }
}
@media (hover: none) {
  .thing {
    /* Always visible at low opacity on touch */
    opacity: 0.4;
    transition: opacity 0.15s;
  }
  .thing:active {
    opacity: 1;
  }
}
```

### B2. Bottom-sheet to full-page migration snippet (queue modal → #now-playing right column on mobile)

```css
@media (max-width: 899px) {
  /* #now-playing: vertical stack */
  #now-playing-section .np-page {
    flex-direction: column;
    height: auto;
    padding-top: calc(var(--topbar-h, 64px) + var(--safe-top, 0px));
  }
  #now-playing-section .np-page-left {
    padding: 24px 16px;
  }
  #now-playing-section .np-page-right {
    border-left: none;
    border-top: 1px solid rgba(255, 255, 255, 0.07);
    max-height: 60dvh;
    overflow-y: auto;
  }
  /* Belt the in-page queue list to the new container */
  #np-queue-list {
    /* styles aligning with old .queue-modal-body */
  }
}
```

### B3. Safe-area-aware persistent bar

```css
:root {
  --playbar-h-mobile: 112px;
}
@media (max-width: 899px) {
  .player-section.is-visible {
    left: 8px;
    right: 8px;
    bottom: calc(8px + env(safe-area-inset-bottom, 0px));
    border-radius: var(--radius-xl);
    padding-bottom: calc(10px + env(safe-area-inset-bottom, 0px));
    /* keeps is-visible unimpaired; on PWA standalone without env() */
    gap: 0 6px;
  }
}
```

### B4. Sticky search pill

```css
@media (max-width: 899px) {
  .play-section {
    position: sticky;
    top: 0;
    z-index: 90;
    /* No backdrop-filter: too costly on mobile */
    background: #000;
    contain: paint;
  }
}
```

---

## Appendix C: Decision Log

- **P0 / P1 priority order** — chosen because the user reported "everything is broken"; navigation is the highest leverage to fix first. P1 is the largest single theme in scope and is what most mobile breakage stems from.
- **CSS source of truth** — `redesign.css` because of the existing load-order convention.
- **One expanded player** — `#now-playing-section` because that widget is already styled and JS-wired up out of the box; adopting it saves dozens of bugs.
- **Touch affordances** — `(hover: hover)` is the modern alternative to feature-detect; prefer that.
- **Deferring `sse.js` polish** — the queue SSE is functional, defer detailed changes to P4 unless P1 testing reveals regressions.

---

_End of plan._

After copy-paste of this file, the implementation team should:

1. Start a branch with `git checkout -b mobile-remediation-p0`.
2. Implement P0 first; commit and verify.
3. Implement P1, P2, P3 in order; commit incrementally.
4. Use `git bisect` if regressions on desktop occur.
5. Final verification on iPhone and Android via a real device or BrowserStack.
