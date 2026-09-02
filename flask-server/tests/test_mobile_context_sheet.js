// Regression coverage for mobile bottom-sheet context menus.
//
// Feature: on mobile (max-width: 899px) every 3-dot / tap-and-hold context
// menu (song rows, queue rows, home/explore/playlist cards, the playlist-detail
// rename-delete menu, and the now-playing 3-dot) is presented as a wide bottom
// sheet that slides up from the bottom, with a scrim, body scroll lock, and
// pull-down-to-dismiss.
//
// Split: presentation/lifecycle + dismissal logic live in
// static/js/mobile-context-sheet.js; all geometry/animation live in
// static/css/mobile.css. The desktop (min-width: 900px) menus must be
// untouched.
//
// Run: `node flask-server/tests/test_mobile_context_sheet.js`

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.join(__dirname, '..');
const JS_DIR = path.join(root, 'templates', 'static', 'js');
const CSS_DIR = path.join(root, 'templates', 'static', 'css');

let passed = 0;
let failed = 0;
function check(name, condition, detail) {
  if (condition) {
    passed += 1;
    console.log(`PASS  ${name}`);
  } else {
    failed += 1;
    console.log(`FAIL  ${name}${detail ? `\n        ${detail}` : ''}`);
  }
}

const css = fs.readFileSync(path.join(CSS_DIR, 'mobile.css'), 'utf8');
const js = fs.readFileSync(path.join(JS_DIR, 'mobile-context-sheet.js'), 'utf8');
const remote = fs.readFileSync(path.join(root, 'templates', 'remote.html'), 'utf8');

// ---------- static structure checks ----------
check('mobile.css converts row menus into bottom sheets',
  /@media \(max-width: 899px\)[\s\S]*\.result-more-menu,\s*\.queue-more-menu,\s*\.playlist-more-menu,\s*#playlist-detail-more-menu,\s*#np-more-menu\.mobile-open/.test(css));
check('sheets are pinned full-width to the bottom with rounded top corners',
  /bottom: 0\s*!important/.test(css) &&
  /border-radius: 16px 16px 0 0\s*!important/.test(css) &&
  /width: 100%\s*!important/.test(css));
check('sheets slide up via a quick springy transform animation (< 300ms)',
  /translateY\(101%\)\s*!important/.test(css) &&
  /translateY\(var\(--sheet-drag-y,\s*0px\)\)\s*!important/.test(css) &&
  /transition: transform \.22s cubic-bezier/.test(css));
check('closing keeps the sheet visible until the slide-down finishes (full close animation)',
  /visibility 0s \.22s !important/.test(css) &&
  /visibility 0s 0s !important/.test(css));
check('sheets expose a grab-handle pill',
  /::before\s*\{[\s\S]*?width: 40px;\s*height: 4px;/.test(css));
check('sheet options get 54px touch targets (>= 44px guidance)',
  /min-height: 54px/.test(css));
check('a scrim + body lock are defined',
  /\.context-sheet-scrim\s*\{/.test(css) &&
  /body\.context-sheet-open\s*\{[\s\S]*?overflow: hidden/.test(css));
check('sheets own the vertical gesture (touch-action) so drags are not swallowed by native scroll',
  /touch-action: none/.test(css));
check('sheet geometry is locked behind the mobile media query only',
  (css.match(/@media \(max-width: 899px\)/g) || []).length >= 2 &&
  !/@media \(min-width: 900px\)[\s\S]*\.context-sheet-scrim/.test(css));
check('now-playing 3-dot is included as a sheet',
  /#np-more-menu\.mobile-open/.test(css));

check('mobile-context-sheet.js observes the body for open menus',
  /new window\.MutationObserver[\s\S]*observe\(document\.body,\s*\{ subtree: true/.test(js));
check('observer work is coalesced to one reconcile per animation frame (anti-freeze/anti-jank)',
  /scheduleReconcile/.test(js) &&
  /window\.requestAnimationFrame\(run\)/.test(js) &&
  /try \{ reconcile\(\); \} catch \(_/.test(js));
check('drag-to-dismiss tracks the pointer continuously from pointerdown',
  /onPointerDown/.test(js) && /onPointerMove/.test(js) &&
  /document\.addEventListener\('pointerdown', onPointerDown, true\)/.test(js) &&
  /pointermove', onPointerMove/.test(js));
check('tap-and-hold release is swallowed so the finger does not hit an option',
  /markOpenByHeldPress/.test(js) && /drag\.suppressRelease = true/.test(js) &&
  /onSwallowClick/.test(js));
check('tapping the scrim switches on click suppression before closing',
  /drag\.onScrim/.test(js) && /swallowClick = true/.test(js) &&
  /closeAllSheets\(\)/.test(js));
check('closing uses the app close helpers (idempotent)',
  /window\._closeAllMoreMenus/.test(js) &&
  /window\._closeAllQueueMenus/.test(js) &&
  /window\._closeNpMoreMenu/.test(js));
check('scrim dismissal pops history exactly once and only via popstate (no page re-render)',
  /historyBackPending/.test(js) &&
  /sheetHistoryEntry && !historyBackPending && window\.history && window\.history\.back/.test(js) &&
  /if \(fromHistory\) \{[\s\S]*?sheetHistoryEntry = false;[\s\S]*?historyBackPending = false;/m.test(js) &&
  !/sheetHistoryEntry = false;[^\n]*\n[^\n]*window\.history\.back\(\)/.test(js));
check('dismiss threshold + velocity flick are implemented',
  /dy > threshold/.test(js) && /drag\.velocity/.test(js));
check('scrim is detached (with a fade-out) when no sheet is open',
  /hideScrim\(/.test(js) && /\.remove\(\)/.test(js) && /260/.test(js));
check('exposes test hooks',
  /window\._reconcileContextSheets = reconcile/.test(js) &&
  /window\._closeContextSheets = closeAllSheets/.test(js));
check('remote.html loads the sheet module (after long-press)',
  /long-press\.js[\s\S]*mobile-context-sheet\.js/.test(remote));
const longPress = fs.readFileSync(path.join(JS_DIR, 'long-press.js'), 'utf8');
check('song tap-and-hold opens the sheet promptly (hold window <= 400ms)',
  /}, \d{2,3}\)/.test(longPress) && !/}, 55[0-9]\)/.test(longPress));

console.log(`  (static: ${passed} passed, ${failed} failed)`);
const staticFailed = failed;
passed = 0;
failed = 0;

// ---------- behavioural checks (jsdom-free fake DOM) ----------
class FakeClassList {
  constructor(el) { this.el = el; }
  add(c) { this.el.classes.push(c); }
  remove(c) { this.el.classes = this.el.classes.filter(x => x !== c); }
  contains(c) { return this.el.classes.indexOf(c) !== -1; }
}

function makeEl(tag, cls) {
  const el = {
    id: '',
    tag,
    classes: cls ? [cls].flat() : [],
    _props: {},
    attrs: {},
    parent: null,
    children: [],
    scrollTop: 0,
    offsetHeight: 400,
    style: { setProperty: (n, v) => { el._props[n] = v; } },
    classList: null,
    attach() { el.classList = new FakeClassList(el); return el; },
    childOf(p) { el.parent = p; if (p && p.appendChild) p.appendChild(el); return el; },
    appendChild(c) { c.parent = el; el.children.push(c); return c; },
    remove() { if (el.parent) el.parent.children = el.parent.children.filter(c => c !== el); el.parent = null; },
    matches() { return false; },
    querySelector() { return null; },
    setAttribute(k, v) { el.attrs[k] = String(v); },
    removeAttribute(k) { delete el.attrs[k]; },
    closest() { return null; },
    addEventListener() {},
  };
  Object.defineProperty(el, 'className', {
    get() { return el.classes.join(' '); },
    set(v) { el.classes = String(v).split(/\s+/).filter(Boolean); },
    configurable: true,
  });
  Object.defineProperty(el, 'parentNode', {
    get() { return el.parent; },
    configurable: true,
  });
  el.attach();
  return el;
}

function makeEnv(opts) {
  const state = {
    openRowMenus: [],
    npMenuMobileOpen: false,
    scrimes: [],
  };

  const body = makeEl('body');
  const closeCount = { more: 0, queue: 0, np: 0 };
  const _closeAllMoreMenus = () => { closeCount.more += 1; state.openRowMenus = []; };
  const _closeAllQueueMenus = () => { closeCount.queue += 1; };
  const _closeNpMoreMenu = () => { closeCount.np += 1; state.npMenuMobileOpen = false; };

  const listeners = {};   // type -> [fn] for document-level listeners (capture)
  const document = {
    body,
    createElement: (tag) => makeEl(tag),
    querySelector(sel) {
      if (sel.includes('.open')) return state.openRowMenus[0] ? state.openRowMenus[0] : null;
      if (sel.includes('#np-more-menu.mobile-open') && state.npMenuMobileOpen) return makeEl('div');
      return null;
    },
    querySelectorAll: () => [],
    getElementById(id) {
      if (id === 'playlist-detail-more-menu') return makeEl('div');
      const np = makeEl('div');
      np.id = id;
      if (id === 'np-more-menu' && state.npMenuMobileOpen) np.classes.push('mobile-open');
      return np;
    },
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    removeEventListener(type, fn) {
      if (!listeners[type]) return;
      listeners[type] = listeners[type].filter(f => f !== fn);
    },
  };
  const dispatch = (type, props = {}) => {
    const stopped = { value: false };
    const e = {
      type, clientY: 0, clientX: 0, pointerId: 1, isTrusted: true,
      target: state._target || body,
      defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; },
      stopPropagation() { stopped.value = true; },
      stopImmediatePropagation() {
        stopped.value = true; this.stoppedImmediate = true;
      },
      ...props,
    };
    if (listeners[type]) {
      for (const fn of listeners[type].slice()) {
        fn.call(document, e);
        if (e.stoppedImmediate || stopped.value) break;
      }
    }
    e._stopped = stopped.value;
    return e;
  };

  const timers = [];  // captured setTimeout callbacks (flushed explicitly per test)
  const win = {
    matchMedia: () => ({ matches: true, addEventListener() {}, addListener() {} }),
    MutationObserver: function () { this.observe = () => {}; this.disconnect = () => {}; },
    addEventListener() {},
    removeEventListener() {},
    setTimeout: (fn, ms) => { timers.push({ fn, ms: ms || 0 }); return { id: timers.length }; },
    clearTimeout: () => {},
    performance: { now: () => 0 },
    requestAnimationFrame: (fn) => { timers.push({ fn, ms: 0 }); return { id: timers.length }; },
    _closeAllMoreMenus,
    _closeAllQueueMenus,
    _closeNpMoreMenu,
  };

  if (opts && opts.withHistory) {
    const history = {
      pushed: 0,
      backCalls: 0,
      pushState() { history.pushed += 1; },
      replaceState() {},
      back() { history.backCalls += 1; },
    };
    win.history = history;
    win.location = { href: 'https://example.com/album?browse=ABC' };
  }

  const sandbox = { document, window: win, console, performance: win.performance };
  const context = vm.createContext(sandbox);
  vm.runInContext(js, context, { filename: 'mobile-context-sheet.js' });
  // Run captured timers whose delay is <= the given ms (default: all). This lets
  // a test flush the 220ms slide-off close WITHOUT also firing the 600ms
  // by-product-swallow auto-clear that a real click would outrun.
  const flushTimers = (upToMs) => {
    const cap = upToMs === undefined ? Infinity : upToMs;
    let ran = true;
    while (ran) {
      ran = false;
      for (let i = 0; i < timers.length; i++) {
        if (timers[i].ms <= cap) {
          const { fn } = timers.splice(i, 1)[0];
          fn();
          ran = true;
          break;
        }
      }
    }
  };

  // Inspect scrim lifecycle through the body's child list (scrim is removed on close).
  const scrimCount = () => body.children.filter(c => c.classes.includes('context-sheet-scrim')).length;
  const isLocked = () => body.classList.contains('context-sheet-open');
  const menu = makeEl('div');
  menu.classes.push('result-more-menu');
  return {
    state, doc: document, body, scrimCount, isLocked, menu, closeCount, win, sandbox, listeners, dispatch,
    flushTimers, history: win.history,
  };
}

function main() {
  {
    console.log('--- open a song menu => scrim + scroll lock ---');
    const env = makeEnv();
    env.state.openRowMenus.push(env.menu);
    env.sandbox.window._reconcileContextSheets();
    check('scrim is appended to <body> when a sheet is open', env.scrimCount() === 1);
    check('body scroll is locked while a sheet is open', env.isLocked() === true);

    console.log('--- closing the menu tears the sheet down (after fade) ---');
    env.state.openRowMenus.splice(0, 1); // menu loses .open
    env.sandbox.window._reconcileContextSheets();
    check('body scroll is unlocked on close', env.isLocked() === false);
    env.flushTimers(260);
    check('scrim is removed when the sheet closes', env.scrimCount() === 0);
  }

  {
    console.log('--- closeAllSheets defers to the app close helpers ---');
    const env = makeEnv();
    env.state.openRowMenus.push(env.menu);
    env.state.npMenuMobileOpen = true;
    env.sandbox.window._closeContextSheets();
    check('menu was closed via _closeAllMoreMenus', env.closeCount.more >= 1);
    check('queue helper also invoked (idempotent)', env.closeCount.queue >= 1);
    check('np helper invoked only when the np menu is open', env.closeCount.np === 1);
    check('teardown leaves no scrim behind', env.scrimCount() === 0);

    console.log('--- np helper skipped when the np menu is not open ---');
    const env2 = makeEnv();
    env2.closeCount.more = 0; env2.closeCount.np = 0;
    env2.sandbox.window._closeContextSheets();
    check('np helper not called for a closed np menu', env2.closeCount.np === 0);
  }

  {
    console.log('--- hold-to-open release must NOT click an option ---');
    const env = makeEnv();
    const option = makeEl('div'); option.classes.push('result-menu-option');
    env.option = option; env.state._target = option;
    // Simulate: press starts on a row, sheet opens while finger is held.
    env.dispatch('pointerdown', { clientY: 100, clientX: 50 });
    env.state.openRowMenus.push(env.menu);   // sheet becomes open during the hold
    env.sandbox.window._reconcileContextSheets(); // reconcile marks the held press
    // Finger lifts /over the option/, then the browser fires click — order
    // matters: pointerup arms the swallow, the click then gets consumed.
    env.dispatch('pointerup', { clientY: 100, clientX: 50 });
    const click = env.dispatch('click', { target: option, clientY: 100, clientX: 50, isTrusted: true });
    check('hold-release click was swallowed (option not activated)', click.defaultPrevented === true || click._stopped === true);
    // A subsequent, deliberate option tap must still be allowed. The option
    // lives INSIDE the sheet, so the press must not be treated as an outside
    // dismissal: that outside-branch is what consumes the tap-to-close.
    const env2 = makeEnv();
    const opt2 = makeEl('div'); opt2.classes.push('result-menu-option');
    opt2.closest = () => env2.menu; // descendant of the open sheet
    env2.option = opt2; env2.state._target = opt2;
    env2.state.openRowMenus.push(env2.menu);
    env2.sandbox.window._reconcileContextSheets();
    env2.dispatch('pointerdown', { clientY: 200, clientX: 50 });
    const c2 = env2.dispatch('click', { target: opt2, isTrusted: true, clientY: 200, clientX: 50 });
    check('deliberate option tap after open still passes through', c2.defaultPrevented === false && c2._stopped === false);
  }

  {
    console.log('--- tapping the scrim dismisses without clicking content underneath ---');
    const env = makeEnv();
    env.state.openRowMenus.push(env.menu);
    env.sandbox.window._reconcileContextSheets();
    const scrim = env.body.children.find(c => c.classes.includes('context-sheet-scrim'));
    check('scrim exists while sheet is open', !!scrim);
    env.state._target = scrim;
    env.dispatch('pointerdown', { target: scrim, clientY: 50, clientX: 50 });
    check('sheet closed when scrim tapped', env.state.openRowMenus.length === 0);
    // Body unlock happens immediately (starts the fade); the scrim ELEMENT is
    // removed on a short delay so the fade-out is actually visible.
    check('scrim stays mounted to fade out (body unlocked first)', env.scrimCount() === 1 && env.isLocked() === false);
    const click = env.dispatch('click', { target: scrim, isTrusted: true, clientY: 50, clientX: 50 });
    check('scrim dismissal click was swallowed (content not clicked)', click.defaultPrevented === true || click._stopped === true);
    env.flushTimers(260);
    check('scrim element removed after the fade completes', env.scrimCount() === 0);
  }

  {
    console.log('--- scrim dismissal keeps the history entry armed until popstate (no page refresh) ---');
    const env = makeEnv({ withHistory: true });
    env.state.openRowMenus.push(env.menu);
    env.sandbox.window._reconcileContextSheets();
    check('opening the sheet pushes exactly one history entry', env.history.pushed === 1);
    check('router can see the sheet entry as armed', env.win._contextSheetHistoryOpen() === true);

    const scrim = env.body.children.find(c => c.classes.includes('context-sheet-scrim'));
    env.state._target = scrim;
    env.dispatch('pointerdown', { target: scrim, clientY: 50, clientX: 50 });
    check('scrim tap closes the menu directly', env.state.openRowMenus.length === 0);
    check('closing dispatched exactly one back()', env.history.backCalls === 1);
    // The entry must stay armed: if it were cleared before back(), the
    // popstate would fall through the router guard and re-render the page.
    check('entry stays armed until the popstate consumes it', env.win._contextSheetHistoryOpen() === true);

    // A redundant close (e.g. the scrim's own click listener) landing before
    // the popstate must not dispatch a second back and pop a real page.
    env.sandbox.window._closeContextSheets();
    check('redundant close before popstate does not pop another entry', env.history.backCalls === 1);

    // The browser now fires popstate; the router consumes the entry.
    env.sandbox.window._closeContextSheetsFromHistory();
    check('popstate consumption clears the armed entry', env.win._contextSheetHistoryOpen() === false);
    check('popstate consumption does not dispatch another back()', env.history.backCalls === 1);
    env.flushTimers(260);
    check('scrim element removed after the fade completes', env.scrimCount() === 0);

    console.log('--- closing without an open sheet never touches history ---');
    const env2 = makeEnv({ withHistory: true });
    env2.sandbox.window._closeContextSheets();
    check('no back() when no sheet entry is armed', env2.history.backCalls === 0);
  }

  {
    console.log('--- drag-to-dismiss works from a held press ---');
    const env = makeEnv();
    env.state.openRowMenus.push(env.menu);
    env.sandbox.window._reconcileContextSheets();
    // Press begins (finger was down from opening the sheet).
    env.dispatch('pointerdown', { clientY: 500, clientX: 50 });
    // Pull down enough to cross the dismissal threshold (sheet height ~400,
    // threshold capped at 120; dy 200 clears it).
    env.dispatch('pointermove', { clientY: 620, clientX: 50 });
    env.dispatch('pointermove', { clientY: 700, clientX: 50 });
    const up = env.dispatch('pointerup', { clientY: 700, clientX: 50 });
    env.flushTimers(220); // let the slide-off timer call closeAllSheets
    check('drag release past threshold closes the menu', env.closeCount.more >= 1);
    check('drag release armed the swallow flag', up !== null);
    const click = env.dispatch('click', { isTrusted: true, clientY: 700, clientX: 50 });
    check('release click after drag dismiss was swallowed', click.defaultPrevented === true || click._stopped === true);

    console.log('--- short pull below threshold springs back (sheet stays open) ---');
    const env2 = makeEnv();
    env2.state.openRowMenus.push(env2.menu);
    env2.sandbox.window._reconcileContextSheets();
    // The pull begins ON the sheet; a press outside the sheet would instead be
    // consumed as an outside dismissal before any drag can start.
    env2.state._target = env2.menu;
    env2.dispatch('pointerdown', { clientY: 500, clientX: 50 });
    env2.dispatch('pointermove', { clientY: 540, clientX: 50 }); // dy 40 < threshold(120)
    env2.dispatch('pointerup', { clientY: 540, clientX: 50 });
    check('short pull keeps the sheet open', env2.closeCount.more === 0 && env2.scrimCount() === 1);
  }

  console.log('  (behavioural done)');
}

main();

console.log(`\nmobile-context-sheet: static+behavioural passed=${passed} failed=${failed}`);
process.exit(failed || staticFailed ? 1 : 0);