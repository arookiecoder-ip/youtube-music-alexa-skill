// Repro for: tap-and-hold opens the context menu, but the finger's release
// click instantly closes it again (mobile, across all song surfaces).
//
// Kill chain under test (real modules, shared fake DOM):
//   1. touch pointerdown on a song row -> long-press.js arms its 350ms timer,
//      mobile-context-sheet.js records the press as `drag`.
//   2. timer fires -> hidden more-button .click() -> REAL openSongContextMenu
//      adds .open to the shared song menu.
//   3. reconcile() presents the sheet and marks the still-held press
//      (drag.suppressRelease) so the release click is swallowed.
//   4. pointerup, then the trusted release click targets the ROW.
//   5. BUG: song-context-menu.js's document CAPTURE closer runs before the
//      hold-release suppressors and closes the just-opened menu.
//
// Run: `node flask-server/tests/test_hold_release_keeps_sheet.js`

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const JS_DIR = path.join(__dirname, '..', 'templates', 'static', 'js');
const load = (f) => fs.readFileSync(path.join(JS_DIR, f), 'utf8');

let passed = 0;
let failed = 0;
function check(name, condition, detail) {
  if (condition) { passed += 1; console.log(`PASS  ${name}`); }
  else { failed += 1; console.log(`FAIL  ${name}${detail ? `\n        ${detail}` : ''}`); }
}

// ---------- minimal selector engine (only what the modules query) ----------
function tokenMatches(el, token) {
  token = token.trim();
  if (!token) return false;
  if (token === '*') return true;
  const m = token.match(/^([a-zA-Z][a-zA-Z0-9]*)?((?:[.#][a-zA-Z0-9_-]+)*)((?:\[[^\]]+\])*)$/);
  if (!m) return false;
  const [, tag, clsIds, attrs] = m;
  if (tag && el.tag !== tag) return false;
  for (const part of (clsIds.match(/[.#][a-zA-Z0-9_-]+/g) || [])) {
    if (part[0] === '.' && !el.classes.includes(part.slice(1))) return false;
    if (part[0] === '#' && el.id !== part.slice(1)) return false;
  }
  for (const attr of (attrs.match(/\[[^\]]+\]/g) || [])) {
    const name = attr.slice(1, -1).split('=')[0];
    if (name === 'data-video-id' && !el.dataset.videoId) return false;
    if (name === 'data-album-id' && !el.dataset.albumId) return false;
    if (!(name in (el.dataset || {})) && name !== 'data-video-id' && name !== 'data-album-id') {
      const camel = name.replace(/^data-/, '').replace(/-([a-z])/g, (_, c) => c.toUpperCase());
      if (!el.dataset || !(camel in el.dataset)) return false;
    }
  }
  return true;
}
function makeEl(tag, classes, id) {
  const el = {
    tag, id: id || '',
    classes: (classes || []).slice(),
    dataset: {},
    parent: null, children: [],
    style: { setProperty() {}, removeProperty() {} },
    textContent: '',
    get className() { return el.classes.join(' '); },
    set className(v) { el.classes = String(v).split(/\s+/).filter(Boolean); },
    classList: {
      add(c) { if (!el.classes.includes(c)) el.classes.push(c); },
      remove(c) { el.classes = el.classes.filter((x) => x !== c); },
      contains(c) { return el.classes.includes(c); },
      toggle(c, f) {
        const has = el.classes.includes(c);
        const want = f === undefined ? !has : !!f;
        if (want && !has) el.classes.push(c);
        if (!want && has) el.classes = el.classes.filter((x) => x !== c);
      },
    },
    appendChild(c) { c.parent = el; el.children.push(c); return c; },
    remove() { if (el.parent) el.parent.children = el.parent.children.filter((c) => c !== el); el.parent = null; },
    get parentNode() { return el.parent; },
    setAttribute(k, v) { el.attrs = el.attrs || {}; el.attrs[k] = String(v); },
    getAttribute(k) { return (el.attrs || {})[k]; },
    removeAttribute(k) { if (el.attrs) delete el.attrs[k]; },
    getBoundingClientRect() { return { left: 0, top: 0, right: 40, bottom: 40, width: 40, height: 40 }; },
    _qs: null,
    querySelector(sel) { return el._qs ? el._qs(sel) : null; },
    querySelectorAll() { return []; },
    addEventListener() {},
    closest(sel) {
      let node = el;
      const parts = String(sel).split(',');
      while (node) {
        for (const p of parts) { if (tokenMatches(node, p)) return node; }
        node = node.parent;
      }
      return null;
    },
  };
  return el;
}

function makeEnv({ mobile } = { mobile: true }) {
  const body = makeEl('body');
  const docListeners = []; // {type, fn, capture}
  function optionStub() {
    return {
      hidden: false,
      classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
      querySelector: () => ({}),
      setAttribute() {},
    };
  }
  const document = {
    body,
    createElement: (tag) => {
      const div = makeEl(tag);
      div._qs = () => optionStub();
      return div;
    },
    querySelector(sel) {
      if (String(sel).includes('.open')) {
        return body.children.find((c) => c.classes.includes('open')) || null;
      }
      return null;
    },
    querySelectorAll: () => [],
    getElementById() { const d = makeEl('div'); return d; },
    addEventListener(type, fn, capture) { docListeners.push({ type, fn, capture: !!capture }); },
    removeEventListener() {},
  };
  const timers = [];
  const win = {
    matchMedia: () => ({
      matches: !!mobile,
      addEventListener() {}, addListener() {},
    }),
    // Mirrors the real search.js helper: drops .open from every song menu.
    _closeAllMoreMenus: () => {
      body.children.forEach((c) => {
        if (c.classes.includes('song-context-menu')) c.classList.remove('open');
      });
    },
    _closeAllQueueMenus: () => {},
    MutationObserver: function () { this.observe = () => {}; this.disconnect = () => {}; },
    addEventListener() {},
    removeEventListener() {},
    setTimeout: (fn, ms) => { timers.push({ fn, ms: ms || 0 }); return timers.length; },
    clearTimeout: () => {},
    requestAnimationFrame: (fn) => { timers.push({ fn, ms: 0 }); return timers.length; },
    innerWidth: 400,
    innerHeight: 800,
    performance: { now: () => 0 },
  };
  const sandbox = {
    document, window: win, console,
    performance: win.performance,
    setTimeout: (fn, ms) => { timers.push({ fn, ms: ms || 0 }); return timers.length; },
    clearTimeout: () => {},
    MouseEvent: function () {},
  };
  const context = vm.createContext(sandbox);
  // Real script order from remote.html.
  vm.runInContext(load('song-context-menu.js'), context, { filename: 'song-context-menu.js' });
  vm.runInContext(load('long-press.js'), context, { filename: 'long-press.js' });
  vm.runInContext(load('mobile-context-sheet.js'), context, { filename: 'mobile-context-sheet.js' });

  // Song row with a hidden more-button, as built by search.js.
  const row = makeEl('div', ['result-swipe-wrapper']);
  row.dataset.videoId = 'vid123';
  const inner = makeEl('div', ['result-item-inner']);
  row.appendChild(inner);
  const moreBtn = makeEl('button', ['result-more-btn']);
  inner.appendChild(moreBtn);
  inner._qs = (sel) => (String(sel).includes('.result-more-btn') ? moreBtn : null);
  body.appendChild(row);
  const outside = makeEl('div', ['home-section']);
  body.appendChild(outside);

  let menuOpened = false;
  moreBtn.click = () => {
    menuOpened = true;
    sandbox.window.openSongContextMenu(
      { clientX: 50, clientY: 100, target: moreBtn, currentTarget: null },
      { video_id: 'vid123', title: 'Test Song', artist: 'Test Artist', thumbnail: '' }
    );
  };
  const menu = () => body.children.find((c) => c.classes.includes('song-context-menu'));
  const menuOpen = () => !!(menu() && menu().classes.includes('open'));

  function dispatch(type, target, props = {}) {
    const e = {
      type,
      target,
      clientX: 50, clientY: 100, pointerId: 7, pointerType: 'touch',
      timeStamp: 1000, buttons: 1, isTrusted: true,
      defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; },
      stopPropagation() { this._stopped = true; },
      stopImmediatePropagation() { this._stopped = true; this._imm = true; },
      ...props,
    };
    // Capture listeners on document (registration order), then bubble ones.
    for (const phase of [true, false]) {
      for (const l of docListeners.filter((l) => l.type === type && l.capture === phase)) {
        l.fn.call(document, e);
        if (e._imm || e._stopped) break;
      }
      if (e._imm || e._stopped) break;
    }
    return e;
  }
  const runTimersUpTo = (ms) => {
    let ran = true;
    while (ran) {
      ran = false;
      for (let i = 0; i < timers.length; i++) {
        if (timers[i].ms <= ms) {
          const { fn } = timers.splice(i, 1)[0];
          fn();
          ran = true;
          break;
        }
      }
    }
  };
  const reconcile = () => sandbox.window._reconcileContextSheets();
  return { row, inner, moreBtn, outside, menuOpened: () => menuOpened, menuOpen, dispatch, runTimersUpTo, reconcile, sandbox };
}

function holdOpenRelease(env) {
  // Finger down on the row, held past the 350ms long-press window.
  env.dispatch('pointerdown', env.inner, { timeStamp: 1000 });
  env.runTimersUpTo(350); // long-press timer fires -> moreBtn.click() -> menu opens
  if (!env.menuOpen()) return 'menu-never-opened';
  env.reconcile(); // observer/rAF presents the sheet while the finger is down
  env.dispatch('pointerup', env.inner, { timeStamp: 1500 });
  env.dispatch('click', env.inner, { timeStamp: 1510, buttons: 0 });
  return env.menuOpen() ? 'open' : 'closed';
}

{
  console.log('--- tap-and-hold keeps the song sheet open on release ---');
  const env = makeEnv({ mobile: true });
  const result = holdOpenRelease(env);
  check('menu opened from the hold', env.menuOpened(), `state=${result}`);
  check('sheet still open after the release click', result === 'open',
    `expected "open", got "${result}" (modal flashes then closes)`);
}

{
  console.log('--- a genuine later outside-tap still closes the sheet ---');
  const env = makeEnv({ mobile: true });
  const first = holdOpenRelease(env);
  check('precondition: sheet open', first === 'open', `got "${first}"`);
  if (first === 'open') {
    // New press outside the sheet: the sheet closes on pointerdown itself.
    env.dispatch('pointerdown', env.outside, { timeStamp: 3000 });
    env.dispatch('click', env.outside, { timeStamp: 3050, buttons: 0 });
    check('outside tap closed the sheet', !env.menuOpen());
  }
}

{
  console.log('--- desktop outside-click still closes (no behaviour change) ---');
  const env = makeEnv({ mobile: false });
  env.moreBtn.click();
  check('precondition: menu open on desktop', env.menuOpen());
  env.dispatch('click', env.outside, { pointerType: 'mouse', buttons: 0 });
  check('desktop outside click closed the menu', !env.menuOpen());
}

console.log(`\nhold-release-keeps-sheet: passed=${passed} failed=${failed}`);
process.exit(failed ? 1 : 0);
