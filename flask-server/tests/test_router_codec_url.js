// Regression test for the trailing-slash paste-URL bug, on the JS side.
//
// router.js in /templates/static/js/router.js exposes a `__spaRouteCodec`
// factory loaded in the browser via {% include %}. In Node it can be exercised
// by evaluating the file with a minimal shim that defines `window`,
// `document`, `history`, etc., then calling the codec's urlToRoute and
// routeToUrl directly.
//
// Run: `node flask-server/tests/test_router_codec_url.js` (the file is in
// sits next to the Python regression test for parallel CI usage).

const fs = require('fs');
const vm = require('vm');

const ROUTER_PATH = require('path').join(
  __dirname, '..', 'templates', 'static', 'js', 'router.js');

const routerSrc = fs.readFileSync(ROUTER_PATH, 'utf8');

// Minimal browser shim: only what router.js touches at IIFE-load time.
const shim = `
const noop = () => {};
const fakeEl = () => ({
  hidden: false, classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
  addEventListener: noop, removeEventListener: noop, dispatchEvent: noop,
  querySelectorAll: () => [], scrollTop: 0, offsetHeight: 0,
  dataset: {}, style: { setProperty: noop, removeProperty: noop, getPropertyValue: () => '' },
});
global.window = {
  matchMedia: () => ({ matches: false, addEventListener: noop, addListener: noop }),
  addEventListener: noop, removeEventListener: noop,
  requestAnimationFrame: (fn) => setTimeout(fn, 0),
  scrollTo: noop,
  history: { pushState: noop, replaceState: noop, state: null },
  setTimeout, clearTimeout,
  getComputedStyle: () => ({ getPropertyValue: () => '' }),
};
global.history = global.window.history;  // router.js uses bare 'history'
global.document = {
  body: { classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
          style: { setProperty: noop, removeProperty: noop }, dataset: {} },
  querySelectorAll: () => [],
  querySelector: () => null, getElementById: () => null,
  documentElement: { style: { setProperty: noop, removeProperty: noop }, scrollTop: 0 },
  hidden: false, addEventListener: noop, readyState: 'complete',
};
global.addEventListener = noop;
global.requestAnimationFrame = (fn) => setTimeout(fn, 0);
global.location = { hash: '', pathname: '/home', search: '', protocol: 'https:' };
`;

// Helper: cross-global exposure. router.js's IIFE does
//   window.__spaRouteCodec = (function() {...})();
// which lives at `global.window.__spaRouteCodec` after vm.runInThisContext
// because our shim puts the window object on the node global. Reading
// `global.__spaRouteCodec` directly would be undefined (previous versions
// of this test used a `global.__spaRouteCodec = window.__spaRouteCodec`
// post-assignment line, but that misleads readers into thinking the IIFE
// sets a bare global; it doesn't).
function _loadCodec(runScriptFn) {
  runScriptFn();
  return global.window && global.window.__spaRouteCodec;
}

// Try evaluating router.js in full under the shim. The IIFE exposes
// `window.__spaRouteCodec` BEFORE the auto-bootstrap line
// (`setTimeout(initializeRoute, 0)` at the tail of the file), so the
// codec is reachable even if the bootstrap throws on our stub DOM
// (no /home nodes to render against). Bootstrap failure is recorded
// for the final error message, not a silent swallow.
let codec = null;
let bootstrapErr = null;
try {
  codec = _loadCodec(() => vm.runInThisContext(shim + '\n' + routerSrc));
} catch (e) {
  bootstrapErr = e;
}

// §(1): only fall back to truncating at the bootstrap line if the
// bootstrap truly threw. If the full-script run finished cleanly but
// the codec is still missing, the IIFE itself bailed and re-truncating
// cannot help. End-of-file anchored regex prevents mid-IIFE truncation
// if router.js contains an earlier `setTimeout(initializeRoute, ...)`
// living elsewhere in the file (which a naive substring indexOf did).
if (codec == null && bootstrapErr != null) {
  const bootstrapRe = /\n\s*setTimeout\s*\(\s*initializeRoute\s*,\s*0\s*\)\s*;?\s*$/m;
  const m = routerSrc.match(bootstrapRe);
  const truncatedRouterSrc = m ? routerSrc.slice(0, m.index) : routerSrc;
  codec = _loadCodec(() => vm.runInThisContext(shim + '\n' + truncatedRouterSrc));
}

if (codec == null) {
  const reason = bootstrapErr
    ? ' (bootstrap threw: ' + (bootstrapErr.message || bootstrapErr) + ')'
    : ' (IIFE itself never exposed __spaRouteCodec on window)';
  console.error('FAIL: __spaRouteCodec factory was not exposed by router.js' + reason);
  process.exit(1);
}

// Each case: [pathname, search, expected route hash, expected round-trip URL, why]
const cases = [
  ['/album',           '?browse=ABC',            '#album/ABC',              '/album?browse=ABC',          'canonical paste'],
  ['/album/',          '?browse=ABC',            '#album/ABC',              '/album?browse=ABC',          'THE BUG: trailing slash'],
  ['/album/',          '?browse=MPREb_x',        '#album/MPREb_x',          '/album?browse=MPREb_x',      'real-world browse_id'],
  ['/search',          '?q=hello',               '#search?q=hello',         '/search?q=hello',            'canonical search'],
  ['/search/',         '?q=hello',               '#search?q=hello',         '/search?q=hello',            'THE BUG: trailing slash search'],
  ['/search/',         '?q=99%20nights',         '#search?q=99+nights',     '/search?q=99+nights',        'encoded spaces in q (URLSearchParams canonically normalizes %20 to +)'],
  ['/playlist',        '?list=PLxxx',            '#playlist/PLxxx',         '/playlist?list=PLxxx',       'canonical playlist'],
  ['/playlist/',       '?list=PLxxx',            '#playlist/PLxxx',         '/playlist?list=PLxxx',       'THE BUG: trailing slash playlist'],
  ['/artist',          '?channel=UC',            '#artist/UC',              '/artist?channel=UC',         'canonical artist'],
  ['/artist/',         '?channel=UC',            '#artist/UC',              '/artist?channel=UC',         'THE BUG: trailing slash artist'],
  ['/artist/songs',    '?channel=UC',            '#artist/UC/songs',        '/artist/songs?channel=UC',   'canonical artist-songs'],
  ['/artist/songs/',   '?channel=UC',            '#artist/UC/songs',        '/artist/songs?channel=UC',   'THE BUG: trailing slash artist-songs'],
  ['/mood',            '?params=P&title=T',      '#mood/P?title=T',         '/mood?params=P&title=T',     'canonical mood'],
  ['/mood/',           '?params=P&title=T',      '#mood/P?title=T',         '/mood?params=P&title=T',     'THE BUG: trailing slash mood'],
  ['/',                '',                       '#home',                   '/home',                      'root'],
  ['//',               '',                       '#home',                   '/home',                      'pathological double-slash'],
  ['/unknown',         '',                       '#home',                   '/home',                      'unknown path'],
];

let pass = 0, fail = 0;
for (const [pathname, search, expectedRoute, expectedUrl, why] of cases) {
  const route = codec.urlToRoute({ pathname, search });
  // Simulate browser normalization by stripping trailing slash from pathname
  // (this is what the production code does in urlToRoute; the raw input is
  // what browsers actually send).
  const url = codec.routeToUrl(route);
  const okRoute = route === expectedRoute;
  const okUrl = url === expectedUrl;
  if (okRoute && okUrl) {
    pass++;
    console.log('PASS  ' + pathname.padEnd(20) + search.padEnd(30) +
                ' -> route=' + route + '  url=' + url + '  [' + why + ']');
  } else {
    fail++;
    console.log('FAIL  ' + pathname.padEnd(20) + search.padEnd(30) +
                ' -> route=' + route + ' (expected ' + expectedRoute + ')' +
                '  url=' + url + ' (expected ' + expectedUrl + ')' +
                '  [' + why + ']');
  }
}

// Also exercise decodeLocation: round-trip from URL → hash → URL must be stable.
console.log('');
console.log('decodeLocation round-trips:');
const decodeCases = [
  ['/album',          '?browse=ABC',  '#album/ABC',  '/album?browse=ABC'],
  ['/album/',         '?browse=ABC',  '#album/ABC',  '/album?browse=ABC'],
  ['/search/',        '?q=hello',     '#search?q=hello', '/search?q=hello'],
  ['/artist/songs/',  '?channel=UC',  '#artist/UC/songs', '/artist/songs?channel=UC'],
];
for (const [pathname, search, expectedRoute, expectedUrl] of decodeCases) {
  const decoded = codec.decodeLocation({ pathname, search });
  const ok = decoded.route === expectedRoute && decoded.url === expectedUrl;
  if (ok) {
    pass++;
    console.log('PASS  ' + pathname.padEnd(20) + search.padEnd(28) +
                ' -> route=' + decoded.route + '  url=' + decoded.url);
  } else {
    fail++;
    console.log('FAIL  ' + pathname.padEnd(20) + search.padEnd(28) +
                ' -> route=' + decoded.route + ' (expected ' + expectedRoute + ')' +
                '  url=' + decoded.url + ' (expected ' + expectedUrl + ')');
  }
}

console.log('');
console.log('router.js codec: passed=' + pass + ' failed=' + fail);
process.exit(fail ? 1 : 0);
