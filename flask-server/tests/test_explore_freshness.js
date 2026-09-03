// Regression coverage for Explore freshness.
//
// Bug: loadExplore() guarded on `loaded && !force` BEFORE consuming the
// navigation preload, and the UI never passes force. So once Explore had
// loaded in a page/tab session it never fetched again -- the preload fetched
// fresh data on every sidebar click and then threw it away, and a tab/PWA
// left open for days kept showing the first day's Top songs.
//
// This test evaluates explore.js in the host context with a fake DOM and an
// empty payload (the section renderers early-return on empty data, so no DOM
// is needed) and checks the fetch/preload/staleness decisions directly.
// Note: it must NOT run explore.js in a `vm` context -- awaiting a vm-realm
// promise from host code deadlocks the vm's separate microtask queue.
//
// Run: `node flask-server/tests/test_explore_freshness.js`

const fs = require('fs');
const path = require('path');

const source = fs.readFileSync(
  path.join(__dirname, '..', 'templates', 'static', 'js', 'explore.js'),
  'utf8'
);

const EMPTY_EXPLORE = '<div class="explore-empty">Nothing to explore right now. Please try again later.</div>';
const realDateNow = Date.now;
const clock = { now: 0 };
const body = { innerHTML: '' };
const state = { fetchCount: 0, preloadValue: null };

global.window = {
  __appState: { _loggedIn: true },
  consumePreload() {
    const value = state.preloadValue;
    state.preloadValue = null; // one-shot, like the real consumePreload
    return value;
  },
  api: async () => {
    state.fetchCount += 1;
    return {};
  },
};
global.document = {
  getElementById(id) {
    return id === 'explore-page-body' ? body : null;
  },
};
Date.now = () => clock.now;

let passed = 0;
let failed = 0;
function check(name, ok) {
  if (ok) {
    passed += 1;
    console.log('PASS  ' + name);
  } else {
    failed += 1;
    console.log('FAIL  ' + name);
  }
}

(async () => {
  try {
    eval(source); // explore.js IIFE runs, exposing window.openExplorePage
    const loadExplore = global.window.openExplorePage;

    // 1. Cold load fetches exactly once.
    await loadExplore();
    check('cold load fetches the explore data', state.fetchCount === 1);

    // 2. An immediate revisit (no preload, within the TTL) reuses the session copy.
    body.innerHTML = 'sentinel';
    await loadExplore();
    check('revisit within TTL does not re-fetch', state.fetchCount === 1 && body.innerHTML === 'sentinel');

    // 3. A revisit WITH a fresh preload consumes it instead of discarding it
    //    (old bug: the loaded guard returned before consuming, body untouched).
    body.innerHTML = 'sentinel';
    state.preloadValue = {};
    await loadExplore();
    check('fresh preload is consumed even though loaded', body.innerHTML === EMPTY_EXPLORE);
    check('preloaded revisit does not re-fetch', state.fetchCount === 1);

    // 4. After the 5-minute staleness window a plain revisit re-fetches.
    clock.now = 6 * 60 * 1000;
    body.innerHTML = 'sentinel';
    await loadExplore();
    check('stale revisit re-fetches after the TTL', state.fetchCount === 2 && body.innerHTML === EMPTY_EXPLORE);

    // 5. A fresh revisit right after that stays cached again (TTL reset).
    await loadExplore();
    check('fresh revisit after refetch stays cached', state.fetchCount === 2);

    console.log(`\nexplore-freshness: passed=${passed} failed=${failed}`);
    process.exit(failed ? 1 : 0);
  } catch (error) {
    console.error(error);
    process.exitCode = 1;
  } finally {
    Date.now = realDateNow;
    delete global.window;
    delete global.document;
  }
})();