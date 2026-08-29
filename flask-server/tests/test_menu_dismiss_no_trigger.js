// Regression test for the "dismissing a 3-dot menu must not trigger the
// section underneath" fix.
//
// Bug (reported, mobile): with a 3-dot menu open (opened by tap or by
// tap-and-hold), tapping any other section closed the menu but ALSO fired
// that section's own action -- the row played, the artist page opened, etc.
//
// Fix under test (search.js): the capture-phase pointerdown handler that
// dismisses open menus records when the press started while a menu was open,
// and a capture-phase click handler then swallows the single dismissal click
// (preventDefault + stopImmediatePropagation) so no section handler below
// ever sees it. Exclusions keep the obvious exceptions working: taps inside a
// menu, taps on the 3-dot buttons themselves (switching menus), taps on text
// inputs, and programmatic long-press clicks.
//
// Run: `node flask-server/tests/test_menu_dismiss_no_trigger.js`

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const JS_PATH = path.join(__dirname, '..', 'templates', 'static', 'js', 'search.js');
const SRC = fs.readFileSync(JS_PATH, 'utf8');

let passed = 0;
let failed = 0;

function checkTrue(name, actual, hint) {
  if (actual) { passed += 1; console.log(`PASS  ${name}`); }
  else { failed += 1; console.log(`FAIL  ${name}${hint ? '\n        ' + hint : ''}`); }
}

// Extract the suppression block: from `let suppressMenuDismissClick` through
// the end of the capture click handler that swallows the dismissal click.
const START_MARKER = 'let suppressMenuDismissClick = false;';
const END_MARKER = 'if (window._closeNpMoreMenu) window._closeNpMoreMenu();';
const start = SRC.indexOf(START_MARKER);
const closeMarker = SRC.indexOf(END_MARKER);
if (start < 0 || closeMarker < 0) {
  console.log('FATAL: could not locate the menu-dismiss suppression block in search.js '
              + '-- the source structure changed; update this test\'s markers.');
  process.exit(1);
}
const end = SRC.indexOf('}, true);', closeMarker) + '}, true);'.length;
const BLOCK_SRC = SRC.slice(start, end);

// ---- tiny fake DOM -------------------------------------------------------

function matches(el, selector) {
  if (selector.startsWith('.')) return el.classes.includes(selector.slice(1));
  if (selector.startsWith('#')) return el.id === selector.slice(1);
  if (selector.startsWith('[')) {
    const attr = selector.slice(1, -1);
    return !!el.attrs[attr];
  }
  return el.tag === selector;
}

function closest(el, selectorStr) {
  const parts = selectorStr.split(',').map((s) => s.trim());
  let cur = el;
  while (cur) {
    for (const p of parts) {
      if (matches(cur, p)) return cur;
    }
    cur = cur.parent;
  }
  return null;
}

function makeElement(tag, { id = '', classes = [], attrs = {} } = {}) {
  return {
    tag,
    id,
    classes,
    attrs,
    parent: null,
    childOf(parent) { this.parent = parent; return this; },
    closest(sel) { return closest(this, sel); },
    onclick: null,
    clicks: 0,
  };
}

function makeEnv() {
  const state = {
    openRowMenus: [],   // elements with class 'result-more-menu open'
    npWrapOpen: false,  // .np-more-wrap.open
    npMenuMobileOpen: false, // #np-more-menu.mobile-open
    npMenuClosedByDismiss: 0,
  };

  const docCapture = {};
  const docBubble = {};

  const document = {
    addEventListener(type, fn, capture) {
      const bucket = capture ? docCapture : docBubble;
      (bucket[type] = bucket[type] || []).push(fn);
    },
    querySelector(sel) {
      if (sel === '.result-more-menu.open, .queue-more-menu.open') return state.openRowMenus[0] || null;
      if (sel === '.np-more-wrap.open, #np-more-menu.mobile-open') {
        return (state.npWrapOpen || state.npMenuMobileOpen) ? {} : null;
      }
      return null;
    },
  };

  const win = {
    _closeAllQueueMenus: () => { state.openRowMenus = state.openRowMenus.filter((m) => !m.classes.includes('queue-more-menu')); },
    _closeNpMoreMenu: () => { state.npMenuClosedByDismiss += 1; state.npWrapOpen = false; state.npMenuMobileOpen = false; },
  };

  // The real script's function declaration, provided like the app would.
  function _closeAllMoreMenus() { state.openRowMenus = []; }

  const sandbox = { document, window: win, _closeAllMoreMenus, console };
  const context = vm.createContext(sandbox);
  vm.runInContext(BLOCK_SRC, context, { filename: 'menu-dismiss-block.js' });

  function dispatch(type, target, { isTrusted = true } = {}) {
    const e = {
      target,
      isTrusted,
      preventDefault() { this.defaultPrevented = true; },
      stopPropagation() { this.stopped = true; },
      stopImmediatePropagation() { this.stoppedImmediate = true; this.stopped = true; },
      defaultPrevented: false,
      stopped: false,
      stoppedImmediate: false,
    };
    // Capture phase (document) -- registration order.
    for (const fn of (docCapture[type] || [])) {
      fn.call(document, e);
      if (e.stoppedImmediate) break;
    }
    // Target (bubble) handler: the "section action" the bug used to trigger.
    // Only real clicks reach it -- pointerdown never triggers a section.
    if (type === 'click' && !e.stoppedImmediate && target.onclick) target.onclick(e);
    // Bubble phase (document).
    if (!e.stoppedImmediate) {
      for (const fn of (docBubble[type] || [])) fn.call(document, e);
    }
    return e;
  }

  return { state, document, win, dispatch };
}

// ---- scenarios -----------------------------------------------------------

function main() {
  console.log('--- dismiss must not trigger the section underneath ---');
  {
    const env = makeEnv();
    const row = makeElement('div', { classes: ['result-item-inner'] });
    const menu = makeElement('div', { classes: ['result-more-menu', 'open'] });
    env.state.openRowMenus.push(menu);
    row.onclick = () => { row.clicks += 1; };

    env.dispatch('pointerdown', row);
    const click = env.dispatch('click', row);

    checkTrue('row click was swallowed', row.clicks === 0,
              'the tap that dismissed the menu must not also play/navigate the row');
    checkTrue('dismissal click was default-prevented', click.defaultPrevented === true);
    checkTrue('menu was closed by the pointerdown', env.state.openRowMenus.length === 0);
  }

  console.log('--- tapping a 3-dot button still opens its own menu ---');
  {
    const env = makeEnv();
    const menuA = makeElement('div', { classes: ['result-more-menu', 'open'] });
    env.state.openRowMenus.push(menuA);
    const moreBtn = makeElement('button', { classes: ['result-more-btn'] });
    moreBtn.onclick = () => { moreBtn.clicks += 1; };

    env.dispatch('pointerdown', moreBtn);
    env.dispatch('click', moreBtn);

    checkTrue('more-btn click was NOT swallowed', moreBtn.clicks === 1,
              'a 3-dot button must still be able to open a fresh menu');
  }

  console.log('--- no menu open: taps behave normally ---');
  {
    const env = makeEnv();
    const row = makeElement('div', { classes: ['result-item-inner'] });
    row.onclick = () => { row.clicks += 1; };

    env.dispatch('pointerdown', row);
    env.dispatch('click', row);

    checkTrue('ordinary row tap still fires', row.clicks === 1);
  }

  console.log('--- taps inside the open menu still work ---');
  {
    const env = makeEnv();
    const menu = makeElement('div', { classes: ['result-more-menu', 'open'] });
    env.state.openRowMenus.push(menu);
    const option = makeElement('div', { classes: ['result-menu-option'] }).childOf(menu);
    option.onclick = () => { option.clicks += 1; };

    env.dispatch('pointerdown', option);
    env.dispatch('click', option);

    checkTrue('menu option click was NOT swallowed', option.clicks === 1);
    checkTrue('menu stayed open while interacting with it', env.state.openRowMenus.length === 1);
  }

  console.log('--- text inputs are still focusable while a menu is open ---');
  {
    const env = makeEnv();
    const menu = makeElement('div', { classes: ['result-more-menu', 'open'] });
    env.state.openRowMenus.push(menu);
    const input = makeElement('input', { attrs: { type: 'text' } });
    input.onclick = () => { input.clicks += 1; };

    env.dispatch('pointerdown', input);
    env.dispatch('click', input);

    checkTrue('input click was NOT swallowed', input.clicks === 1,
              'dismissing a menu must not block focusing a search box');
  }

  console.log('--- programmatic (long-press) clicks still open the menu ---');
  {
    const env = makeEnv();
    const menuA = makeElement('div', { classes: ['result-more-menu', 'open'] });
    env.state.openRowMenus.push(menuA);
    const row = makeElement('div', { classes: ['result-item-inner'] });
    row.onclick = () => { row.clicks += 1; };

    // Press dismisses menu A, then long-press.js fires an untrusted click
    // (activeButton.click()) to open the held row's menu.
    env.dispatch('pointerdown', row);
    env.dispatch('click', row, { isTrusted: false });

    checkTrue('untrusted long-press click was NOT swallowed', row.clicks === 1,
              'long-press synthetic clicks must still reach their handlers');
  }

  console.log('--- np-more (player) menu: dismiss closes it without triggering ---');
  {
    const env = makeEnv();
    env.state.npWrapOpen = true;
    const row = makeElement('div', { classes: ['result-item-inner'] });
    row.onclick = () => { row.clicks += 1; };

    env.dispatch('pointerdown', row);
    env.dispatch('click', row);

    checkTrue('row click was swallowed', row.clicks === 0);
    checkTrue('np-more menu was closed by the dismissal', env.state.npMenuClosedByDismiss === 1);
  }

  console.log('--- np-more menu options still work ---');
  {
    const env = makeEnv();
    env.state.npMenuMobileOpen = true;
    const npMenu = makeElement('div', { id: 'np-more-menu', classes: ['mobile-open'] });
    const option = makeElement('div').childOf(npMenu);
    option.onclick = () => { option.clicks += 1; };

    env.dispatch('pointerdown', option);
    env.dispatch('click', option);

    checkTrue('np-more option click was NOT swallowed', option.clicks === 1);
    checkTrue('np-more menu stayed open', env.state.npMenuClosedByDismiss === 0);
  }

  console.log(`\nmenu-dismiss-no-trigger: passed=${passed} failed=${failed}`);
  process.exit(failed ? 1 : 0);
}

main();
