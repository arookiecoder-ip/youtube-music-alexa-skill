// Regression test for the shared route-transition nudge.
//
// Navigation must not reset every mounted page/modal container. Doing so
// mutates the page underneath the destination and is visible as a small jump
// when Enter or a result click navigates from any screen. Route swaps also
// disable shell geometry transitions for the handoff; normal UI transitions
// remain enabled after the destination settles.
//
// Run: `node flask-server/tests/test_navigation_no_shift.js` from the repository root.

const fs = require('fs');
const path = require('path');

const router = fs.readFileSync(
  path.join(__dirname, '..', 'templates', 'static', 'js', 'router.js'), 'utf8');
const baseCss = fs.readFileSync(
  path.join(__dirname, '..', 'templates', 'static', 'css', 'base.css'), 'utf8');

function extractFunction(src, marker) {
  const start = src.indexOf(marker);
  if (start < 0) return null;
  const open = src.indexOf('{', start);
  let depth = 0;
  for (let i = open; i < src.length; i += 1) {
    if (src[i] === '{') depth += 1;
    else if (src[i] === '}') {
      depth -= 1;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  return null;
}

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

const resetStart = router.indexOf('function resetRouteScroll(route)');
const resetEnd = router.indexOf('\n  // Route classes intentionally', resetStart);
const resetBody = resetStart >= 0 && resetEnd >= 0
  ? router.slice(resetStart, resetEnd)
  : '';

check(
  'route scroll reset accepts the destination route',
  resetStart >= 0 && resetBody.includes("_routeScrollId(route || window.__route)"),
  'the reset must be scoped to the destination, not every mounted view'
);
check(
  'route scroll reset does not iterate every view container',
  resetBody && resetBody.includes('window.scrollTo(0, 0)') &&
    resetBody.includes('document.documentElement.scrollTop = 0') &&
    resetBody.includes('document.body.scrollTop = 0') &&
    !resetBody.includes("'results-section', 'results-list'") &&
    !resetBody.includes("'artist-section', 'artist-songs-section'") &&
    !resetBody.includes("'playlist-detail-modal-overlay'") &&
    !resetBody.includes('forEach(function(id)'),
  'the shared viewport is reset only after the old view has been synchronously swapped out'
);
check(
  'normal navigation uses the destination-scoped reset',
  router.includes('applyRouteForNavigation(route, shouldResetScroll);') &&
    router.includes('if (resetScroll) resetRouteScroll(route);'),
  'navigateTo() must swap views before resetting the shared viewport'
);
check(
  'Back/Forward uses the destination-scoped reset',
  router.includes('applyRouteForNavigation(window.__route, shouldResetScroll);') &&
    router.includes('var shouldResetScroll = window.__route !=='),
  'popstate must use the same scoped behavior'
);
check(
  'navigation applies the route through the snap handoff',
  (router.match(/applyRouteForNavigation\(/g) || []).length >= 3,
  'navigateTo, popstate, and initial route setup should all use the shared handoff'
);
check(
  'layout snap releases after two paint opportunities',
  router.includes("document.body.classList.add('layout-snap')") &&
    router.includes("document.body.classList.remove('layout-snap')") &&
    (router.match(/requestAnimationFrame\(function\(\) \{/g) || []).length >= 2,
  'route transitions should not animate shell geometry during the handoff'
);
check(
  'layout snap freezes the shell transition properties',
  baseCss.includes('.layout-snap main') &&
    baseCss.includes('.layout-snap header') &&
    baseCss.includes('.layout-snap header::before') &&
    baseCss.includes('.layout-snap #artist-hero') &&
    baseCss.includes('transition: none !important'),
  'header and artist hero geometry can otherwise visibly move during routing'
);

// Behavioral shim: verify the handoff ordering and snap token at runtime.
const applySrc = extractFunction(router, 'function applyRouteForNavigation(route, resetScroll) {');
const resetSrc = extractFunction(router, 'function resetRouteScroll(route) {');
const rafQueue = [];
const classes = new Set();
const main = { scrollTop: 23 };
const artist = { scrollTop: 17 };
const body = {
  scrollTop: 42,
  classList: {
    add: value => classes.add(value),
    remove: value => classes.delete(value),
    contains: value => classes.has(value),
  },
};
const documentShim = {
  body,
  documentElement: { scrollTop: 41 },
  querySelector: selector => selector === 'main' ? main : null,
  getElementById: id => id === 'artist-section' ? artist : null,
};
const scrollCalls = [];
try {
  const runtime = require('vm').createContext({
    document: documentShim,
    window: { scrollTo: (x, y) => scrollCalls.push([x, y]) },
    requestAnimationFrame: callback => rafQueue.push(callback),
    applyRoute: () => { body.homeVisible = false; body.artistVisible = true; },
    _routeScrollId: () => 'artist-section',
    console,
  });
  require('vm').runInContext(
    `var _layoutSnapSeq = 0; ${resetSrc}; ${applySrc}; this.resetRouteScroll = resetRouteScroll; this.applyRouteForNavigation = applyRouteForNavigation;`,
    runtime,
    { filename: 'navigation-transition-helpers.js' }
  );
  runtime.applyRouteForNavigation('#artist/UC1');
  check('old page scroll is unchanged during route application', main.scrollTop === 23 && artist.scrollTop === 17);
  check('layout snap is active during route application', classes.has('layout-snap'));
  runtime.resetRouteScroll('#artist/UC1');
  check('destination reset happens after the view swap',
    scrollCalls.length === 1 && scrollCalls[0][1] === 0 &&
      documentShim.documentElement.scrollTop === 0 && body.scrollTop === 0 &&
      artist.scrollTop === 0 && main.scrollTop === 23);

  runtime.applyRouteForNavigation('#artist/UC2');
  const firstFrame = rafQueue.shift();
  const secondFrame = rafQueue.shift();
  firstFrame();
  secondFrame();
  check('older navigation cannot release newer snap state', classes.has('layout-snap'));
  while (rafQueue.length) rafQueue.shift()();
  check('latest navigation releases snap after settling', !classes.has('layout-snap'));
} catch (error) {
  check('behavioral navigation shim executes', false, error.message);
}

console.log(`\nnavigation-no-shift: passed=${passed} failed=${failed}`);
process.exit(failed ? 1 : 0);
