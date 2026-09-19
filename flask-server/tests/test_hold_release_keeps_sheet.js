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
    MouseEvent: function (type, init) { this.type = type; Object.assign(this, init || {}); },
  };
  const context = vm.createContext(sandbox);
  // Real script order from remote.html.
  vm.runInContext(load('song-context-menu.js'), context, { filename: 'song-context-menu.js' });
  vm.runInContext(load('long-press.js'), context, { filename: 'long-press.js' });
  vm.runInContext(load('mobile-context-sheet.js'), context, { filename: 'mobile-context-sheet.js' });

  // Song row with a hidden more-button, as built by search.js.
  const row = makeEl('div', ['result-swipe-wrapper']);
  row.dataset.videoId = 'vid123';
  row._songContextTrack = { video_id: 'vid123', title: 'Test Song', artist: 'Test Artist', thumbnail: '' };
  const inner = makeEl('div', ['result-item-inner']);
  row.appendChild(inner);
  const moreBtn = makeEl('button', ['result-more-btn']);
  inner.appendChild(moreBtn);
  inner._qs = (sel) => (String(sel).includes('.result-more-btn') ? moreBtn : null);
  body.appendChild(row);
  const outside = makeEl('div', ['home-section']);
  body.appendChild(outside);

  // Unified hold path dispatches contextmenu on the surface: route it back
  // through the document listeners, like a real right-click.
  inner.dispatchEvent = function (evt) { dispatch((evt && evt.type) || 'contextmenu', inner); };
  row.dispatchEvent = function (evt) { dispatch((evt && evt.type) || 'contextmenu', row); };

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
  check('menu opened from the hold', result !== 'menu-never-opened', `state=${result}`);
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

function makeCardEnv(kind, mobile) {
  // Same harness as makeEnv, but loads the album + playlist menu modules and
  // builds a holdable card instead of a song row:
  //   kind 'album'    -> .hscroll-card[data-album-id] (artist Albums/Singles
  //                      shelves, search Albums shelf)
  //   kind 'playlist' -> .hscroll-card[data-playlist-id] (search Playlists)
  const body = makeEl('body');
  const docListeners = [];
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
      matches: mobile !== false,
      addEventListener() {}, addListener() {},
    }),
    // Mirrors production search.js: drops .open from EVERY result menu.
    _closeAllMoreMenus: () => {
      body.children.forEach((c) => {
        if (c.classes.includes('result-more-menu') && c.classes.includes('open')) {
          c.classList.remove('open');
        }
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
  vm.runInContext(load('playlist-context-menu.js'), context, { filename: 'playlist-context-menu.js' });
  vm.runInContext(load('album-context-menu.js'), context, { filename: 'album-context-menu.js' });
  vm.runInContext(load('long-press.js'), context, { filename: 'long-press.js' });
  vm.runInContext(load('mobile-context-sheet.js'), context, { filename: 'mobile-context-sheet.js' });

  const card = makeEl('div', ['hscroll-card'].concat(kind === 'album' ? ['album-card'] : []));
  if (kind === 'album') card.dataset.albumId = 'MPREb_test123';
  else card.dataset.playlistId = 'PLtest123';
  card.dataset.title = kind === 'album' ? 'Test Album' : 'Test Playlist';
  const art = makeEl('div', ['hscroll-card-art']);
  card.appendChild(art);
  const title = makeEl('div', ['hscroll-card-title']);
  title.textContent = card.dataset.title;
  card.appendChild(title);
  body.appendChild(card);
  const outside = makeEl('div', ['home-section']);
  body.appendChild(outside);

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
    for (const phase of [true, false]) {
      for (const l of docListeners.filter((l) => l.type === type && l.capture === phase)) {
        l.fn.call(document, e);
        if (e._imm || e._stopped) break;
      }
      if (e._imm || e._stopped) break;
    }
    return e;
  }
  // long-press.js opens card menus via el.dispatchEvent(new MouseEvent(...)).
  // Route that straight back through the document listeners for the card.
  card.dispatchEvent = function () { dispatch('contextmenu', card); };
  const menuOpen = () => body.children.some((c) =>
    c.classes.includes('open') &&
    (c.classes.includes('album-context-menu') || c.classes.includes('playlist-context-menu')));
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
  return { card, art, outside, menuOpen, dispatch, runTimersUpTo, reconcile, sandbox };
}

function holdOpenReleaseCard(env) {
  // Finger down on the card's blank art area (not a button), held past the
  // 350ms long-press window. The hold dispatches contextmenu -> the card menu
  // opens; reconcile() presents the sheet while the finger is down.
  env.dispatch('pointerdown', env.art, { timeStamp: 1000 });
  env.runTimersUpTo(350);
  if (!env.menuOpen()) return 'menu-never-opened';
  env.reconcile();
  env.dispatch('pointerup', env.art, { timeStamp: 1500 });
  // The finger drifts: the release click lands on the BLANK area outside the
  // card (not on the card itself, where long-press.js's own suppressor would
  // hide the bug). It is untrusted so the bottom-sheet capture swallow lets
  // it through on purpose — the menu-level closers are the only protection,
  // which is exactly the guard this regression pins.
  env.dispatch('click', env.outside, { timeStamp: 1510, buttons: 0, isTrusted: false });
  return env.menuOpen() ? 'open' : 'closed';
}

for (const kind of ['album', 'playlist']) {
  console.log(`--- tap-and-hold keeps the ${kind} sheet open on blank-area release ---`);
  const env = makeCardEnv(kind, true);
  const result = holdOpenReleaseCard(env);
  check(`${kind} menu opened from the hold`, result !== 'menu-never-opened', `state=${result}`);
  check(`${kind} sheet still open after the blank-area release click`, result === 'open',
    `expected "open", got "${result}" (modal flashes then closes)`);
}

{
  console.log('--- a genuine later outside-tap still closes card sheets ---');
  for (const kind of ['album', 'playlist']) {
    const env = makeCardEnv(kind, true);
    const first = holdOpenReleaseCard(env);
    check(`precondition: ${kind} sheet open`, first === 'open', `got "${first}"`);
    if (first === 'open') {
      env.dispatch('pointerdown', env.outside, { timeStamp: 3000 });
      env.dispatch('click', env.outside, { timeStamp: 3050, buttons: 0 });
      check(`outside tap closed the ${kind} sheet`, !env.menuOpen());
    }
  }
}

{
  console.log('--- desktop outside-click still closes card menus (no behaviour change) ---');
  for (const kind of ['album', 'playlist']) {
    const env = makeCardEnv(kind, false);
    // Desktop has no hold path (long-press ignores mice): open via right-click.
    env.dispatch('contextmenu', env.card, { pointerType: 'mouse', timeStamp: 1000 });
    check(`precondition: ${kind} menu open on desktop`, env.menuOpen());
    env.dispatch('click', env.outside, { pointerType: 'mouse', buttons: 0 });
    check(`desktop outside click closed the ${kind} menu`, !env.menuOpen());
  }
}

function makeSurfaceEnv(kind, mobile) {
  // Hold surfaces from every page, served only by the shared document-level
  // menu handlers (song / album / playlist) — no page module required.
  const body = makeEl('body');
  const docListeners = [];
  function optionStub() {
    return {
      hidden: false,
      classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
      querySelector: () => ({}),
      setAttribute() {},
      addEventListener() {},
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
      matches: mobile !== false,
      addEventListener() {}, addListener() {},
    }),
    _closeAllMoreMenus: () => {
      body.children.forEach((c) => {
        if (c.classes.includes('result-more-menu') && c.classes.includes('open')) {
          c.classList.remove('open');
        }
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
    MouseEvent: function (type, init) { this.type = type; Object.assign(this, init || {}); },
  };
  const context = vm.createContext(sandbox);
  vm.runInContext(load('song-context-menu.js'), context, { filename: 'song-context-menu.js' });
  vm.runInContext(load('playlist-context-menu.js'), context, { filename: 'playlist-context-menu.js' });
  vm.runInContext(load('album-context-menu.js'), context, { filename: 'album-context-menu.js' });
  vm.runInContext(load('long-press.js'), context, { filename: 'long-press.js' });
  vm.runInContext(load('mobile-context-sheet.js'), context, { filename: 'mobile-context-sheet.js' });

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
    for (const phase of [true, false]) {
      for (const l of docListeners.filter((l) => l.type === type && l.capture === phase)) {
        l.fn.call(document, e);
        if (e._imm || e._stopped) break;
      }
      if (e._imm || e._stopped) break;
    }
    return e;
  }
  const wire = (el) => {
    el.dispatchEvent = function (evt) { dispatch((evt && evt.type) || 'contextmenu', el); };
  };
  const track = (vid) => ({ video_id: vid, title: 'T', artist: 'A', thumbnail: '' });

  let press;
  if (kind === 'artist-row') {
    const wrapper = makeEl('div', ['result-swipe-wrapper']);
    wrapper.dataset.videoId = 'vidA';
    wrapper._songContextTrack = track('vidA');
    const row = makeEl('div', ['artist-song-row']);
    row._songContextTrack = track('vidA');
    const art = makeEl('div', ['artist-song-art']);
    row.appendChild(art);
    row.appendChild(makeEl('button', ['result-more-btn', 'artist-song-more-btn']));
    wrapper.appendChild(row);
    body.appendChild(wrapper);
    wire(row);
    press = art;
  } else if (kind === 'history-row') {
    const row = makeEl('div', ['history-item']);
    row.dataset.videoId = 'vidH';
    row._songContextTrack = track('vidH');
    body.appendChild(row);
    wire(row);
    press = row;
  } else if (kind === 'album-row') {
    // Album/playlist detail rows: .history-item inside a swipe wrapper that
    // owns the track (the row itself carries no _songContextTrack).
    const wrapper = makeEl('div', ['result-swipe-wrapper']);
    wrapper.dataset.videoId = 'vidB';
    wrapper._songContextTrack = Object.assign(track('vidB'), { album_id: 'MPREb_x' });
    const row = makeEl('div', ['history-item']);
    wrapper.appendChild(row);
    body.appendChild(wrapper);
    wire(row);
    press = row;
  } else if (kind === 'home-card') {
    const card = makeEl('div', ['home-item']);
    card.dataset.videoId = 'vidC';
    card.dataset.kind = 'track';
    const title = makeEl('div', ['home-item-title']);
    title.textContent = 'T';
    card.appendChild(title);
    body.appendChild(card);
    wire(card);
    press = title;
  } else if (kind === 'recs-tile') {
    const tile = makeEl('div', ['recs-tile']);
    tile.dataset.videoId = 'vidR';
    tile._songContextTrack = track('vidR');
    body.appendChild(tile);
    wire(tile);
    press = tile;
  } else if (kind === 'top-hero') {
    const hero = makeEl('div', ['top-result-card', 'is-song']);
    hero.dataset.videoId = 'vidT';
    hero._songContextTrack = track('vidT');
    body.appendChild(hero);
    wire(hero);
    press = hero;
  } else if (kind === 'queue-row') {
    const wrapper = makeEl('div', ['queue-swipe-wrapper']);
    wrapper.dataset.videoId = 'vidQ';
    wrapper.dataset.index = '0';
    wrapper._songContextTrack = track('vidQ');
    const item = makeEl('div', ['queue-item']);
    wrapper.appendChild(item);
    body.appendChild(wrapper);
    wire(item);
    wire(wrapper);
    press = item;
  } else if (kind === 'library-card') {
    const card = makeEl('div', ['library-card']);
    card.dataset.playlistContext = 'PLlib';
    card.dataset.playlistTitle = 'Lib Playlist';
    const title = makeEl('div', ['library-card-title']);
    title.textContent = 'Lib Playlist';
    card.appendChild(title);
    body.appendChild(card);
    wire(card);
    press = title;
  }
  const outside = makeEl('div', ['page-blank']);
  body.appendChild(outside);

  const menuOpen = () => body.children.some((c) =>
    c.classes.includes('result-more-menu') && c.classes.includes('open'));
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
  return { press, outside, menuOpen, dispatch, runTimersUpTo, reconcile, sandbox };
}

function holdOpenReleaseSurface(env, releaseTarget) {
  env.dispatch('pointerdown', env.press, { timeStamp: 1000 });
  env.runTimersUpTo(350);
  if (!env.menuOpen()) return 'menu-never-opened';
  env.reconcile();
  env.dispatch('pointerup', env.press, { timeStamp: 1500 });
  env.dispatch('click', releaseTarget || env.press, { timeStamp: 1510, buttons: 0 });
  return env.menuOpen() ? 'open' : 'closed';
}

{
  console.log('--- tap-and-hold opens the menu on every page surface ---');
  for (const kind of ['artist-row', 'history-row', 'album-row', 'home-card', 'recs-tile', 'top-hero', 'queue-row', 'library-card']) {
    const onSurface = makeSurfaceEnv(kind, true);
    check(`${kind}: sheet survives release on the surface`,
      holdOpenReleaseSurface(onSurface, null) === 'open');
    const onBlank = makeSurfaceEnv(kind, true);
    const blankResult = holdOpenReleaseSurface(onBlank, onBlank.outside);
    check(`${kind}: sheet survives release on a blank area`, blankResult === 'open',
      `got "${blankResult}"`);
  }
}

{
  console.log('--- release before the sheet reconciles still keeps it open (sync arm) ---');
  // No reconcile() call: the finger lifts within a frame of the menu opening,
  // before the MutationObserver/rAF presentation could arm anything.
  const env = makeEnv({ mobile: true });
  env.dispatch('pointerdown', env.inner, { timeStamp: 1000 });
  env.runTimersUpTo(350);
  check('precondition: song menu opened from the hold', env.menuOpen());
  env.dispatch('pointerup', env.inner, { timeStamp: 1500 });
  env.dispatch('click', env.inner, { timeStamp: 1505, buttons: 0 });
  check('song sheet still open without reconcile', env.menuOpen());

  const cardEnv = makeCardEnv('album', true);
  cardEnv.dispatch('pointerdown', cardEnv.art, { timeStamp: 1000 });
  cardEnv.runTimersUpTo(350);
  check('precondition: album menu opened from the hold', cardEnv.menuOpen());
  cardEnv.dispatch('pointerup', cardEnv.art, { timeStamp: 1500 });
  cardEnv.dispatch('click', cardEnv.art, { timeStamp: 1505, buttons: 0 });
  check('album sheet still open without reconcile', cardEnv.menuOpen());
}

{
  console.log('--- a drifted hold never eats the next genuine tap (suppress expiry) ---');
  // long-press.js alone: hold, release on blank (suppress stays armed with no
  // click to consume it), then a fresh tap on the surface must pass through.
  const body = makeEl('body');
  const docListeners = [];
  const document = {
    body,
    createElement: (t) => makeEl(t),
    querySelector: () => null,
    querySelectorAll: () => [],
    getElementById: () => makeEl('div'),
    addEventListener(type, fn, capture) { docListeners.push({ type, fn, capture: !!capture }); },
    removeEventListener() {},
  };
  const timers = [];
  const win = {
    matchMedia: () => ({ matches: true, addEventListener() {}, addListener() {} }),
    MutationObserver: function () { this.observe = () => {}; },
    addEventListener() {},
    setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    clearTimeout: () => {},
  };
  const sandbox = {
    document, window: win, console,
    setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    clearTimeout: () => {},
    MouseEvent: function (type, init) { this.type = type; Object.assign(this, init || {}); },
  };
  vm.runInContext(load('long-press.js'), vm.createContext(sandbox), { filename: 'long-press.js' });
  const surface = makeEl('div', ['result-item-inner']);
  body.appendChild(surface);
  const blank = makeEl('div', ['page-blank']);
  body.appendChild(blank);
  function dispatch(type, target, props = {}) {
    const e = {
      type, target, clientX: 50, clientY: 100, pointerId: 7, pointerType: 'touch',
      timeStamp: 1000, buttons: 1, isTrusted: true,
      preventDefault() {}, stopPropagation() { this._stopped = true; },
      stopImmediatePropagation() { this._stopped = true; this._imm = true; },
      ...props,
    };
    for (const phase of [true, false]) {
      for (const l of docListeners.filter((l) => l.type === type && l.capture === phase)) {
        l.fn.call(document, e);
        if (e._imm || e._stopped) break;
      }
      if (e._imm || e._stopped) break;
    }
    return e;
  }
  surface.dispatchEvent = function (evt) { dispatch((evt && evt.type) || 'contextmenu', surface); };
  const runTimers = (ms) => {
    for (let i = 0; i < timers.length; i++) {
      if (timers[i].ms <= ms) timers.splice(i, 1)[0].fn();
    }
  };
  dispatch('pointerdown', surface, { timeStamp: 1000 });
  runTimers(350);
  const armed = dispatch('click', surface, { timeStamp: 1500 });
  check('by-product click on the surface is swallowed', !!armed._stopped);
  // Fresh gesture, then a genuine tap: must NOT be swallowed.
  dispatch('pointerdown', surface, { timeStamp: 3000 });
  const laterTap = dispatch('click', surface, { timeStamp: 3100 });
  check('later genuine tap passes through', !laterTap._stopped);
}

console.log(`\nhold-release-keeps-sheet: passed=${passed} failed=${failed}`);
process.exit(failed ? 1 : 0);
