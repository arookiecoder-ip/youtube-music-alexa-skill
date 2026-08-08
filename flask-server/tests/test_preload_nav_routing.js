// Regression test for the navigator-level MPREb_-guard added to
// preload-nav.js:preloadNavigatePlaylist. Without this guard, an album
// browse_id miscategorised as a playlist (search/home shelves use
// `resultType`/`kind`, neither ground-truth) would land the user on
// `#playlist/<MPREb_id>` and the playlist view would render against a
// payload shape it doesn't understand. The fix delegates to
// `preloadNavigateAlbum` so the SPA URL matches the page renderer.
//
// Run: `node flask-server/tests/test_preload_nav_routing.js` from the repository root.

const fs = require('fs');
const vm = require('vm');

const ROUTER_PATH = require('path').join(
  __dirname, '..', 'templates', 'static', 'js', 'preload-nav.js');
const src = fs.readFileSync(ROUTER_PATH, 'utf8');

// Minimal browser shim. `noop` is declared INLINE inside the shim string
// because vm.runInThisContext evaluates the string in a fresh lexical
// scope that cannot see Node outer-scope const/let bindings.
const shim = `
const noop = () => {};
global.window = {
  matchMedia: () => ({ matches: false, addEventListener: noop, addListener: noop }),
  addEventListener: noop, removeEventListener: noop,
  requestAnimationFrame: (fn) => setTimeout(fn, 0),
  scrollTo: noop,
  history: { pushState: noop, replaceState: noop, state: null },
  setTimeout, clearTimeout,
  location: { hash: '', pathname: '/home', search: '', protocol: 'https:' },
  __explicitMocks: { calls: [] },
};
global.history = global.window.history;
global.document = {
  body: { classList: { add: noop, remove: noop, toggle: noop,
                       contains: () => false },
          style: { setProperty: noop, removeProperty: noop }, dataset: {} },
  querySelectorAll: () => [],
  querySelector: () => null, getElementById: () => null,
  documentElement: { style: { setProperty: noop, removeProperty: noop },
                     scrollTop: 0 },
  hidden: false, addEventListener: noop, readyState: 'complete',
};
global.addEventListener = noop;
global.requestAnimationFrame = (fn) => setTimeout(fn, 0);
global.location = global.window.location;
`;

let bootstrapErr = null;
try {
  vm.runInThisContext(shim + '\n' + src);
} catch (e) {
  bootstrapErr = e;
}

if (bootstrapErr) {
  console.error('FAIL: preload-nav.js threw on load:', bootstrapErr.message);
  process.exit(1);
}

const window = global.window;

// Spy timing matters. preload-nav.js's IIFE does
//   window.navigateWithPreload = function(...) {...}
// after the shim runs, so any pre-installed spy is OVERWRITTEN. To make
// the spy observable we install AFTER vm.runInThisContext completes.
// preload-nav.js's body inside the IIFE looks up `window.navigateWithPreload`
// (and `window.navigateTo`) at CALL TIME via property access, so our
// post-load overrides are picked up by every subsequent call without
// us having to mock any IIFE-internal locals.
window.navigateTo = function (hash) {
  window.__explicitMocks.calls.push({ fn: 'navigateTo', arg: hash });
  window.location.hash = hash;
};
window.navigateWithPreload = function (route, fetchFn, heroFn) {
  window.__explicitMocks.calls.push({
    fn: 'navigateWithPreload',
    route: route,
    hasFetchFn: typeof fetchFn === 'function',
    hasHeroFn: typeof heroFn === 'function',
  });
};

const cases = [
  // Original bug repro.
  ['album-browse-id routes to #album/',
   'MPREb_OqFjF74lTQu', '#album/MPREb_OqFjF74lTQu'],
  // Case-fold catches a hand-typed paste / any code path that
  // lowercases the id before calling.
  ['lowercase mpreb_ routes to #album/',
   'mpreb_oqfjf74ltqu', '#album/mpreb_oqfjf74ltqu'],
  // Regression: a real PL id must NOT be hijacked by the new guard.
  ['real PL id stays at #playlist/',
   'PLabc123def', '#playlist/PLabc123def'],
  // Liked Music stays on the playlist path; its LM branch in
  // _fetchPlaylist returns the same {title, trackCount, tracks} shape
  // the playlist view already reads.
  ['LM stays at #playlist/LM',
   'LM', '#playlist/LM'],
  // VL-wrapped playlist — same prefix check covers VL; route preserved.
  ['VL-wrapped playlist stays at #playlist/',
   'VLPLabc123', '#playlist/VLPLabc123'],
  // Edge: empty plId is a no-op (covered by `if (!plId) return;`).
  ['empty plId is no-op', '', null],
  // Edge: null plId is also a no-op. Pin the contract that *any* falsy
  // input routes through the early-out without reaching
  // encodeURIComponent — a future rewrite that turns `!plId` into a
  // property access (e.g. `plId.length === 0`, `plId.indexOf('…')`,
  // `plId.slice(0, n)`) would (a) throw on null AND (b) let truthy
  // non-strings like `{}` / `[]` slip past to `#playlist/[object Object]`,
  // which is the actual UX hazard we want to keep locked down.
  ['null plId is no-op', null, null],
  // Edge: short-but-prefix-matching id (defends the prefix check against
  // tightening that would accidentally exclude short ids).
  ['short MPREb_ id routes to #album/',
   'MPREb_x', '#album/MPREb_x'],
  // Edge: VLMPREb_ — VL-prefixed album id forwarded from somewhere that
  // did not strip VL first. The current prefix check operates on the
  // raw id (no VL strip), so this stays on the playlist path. Pin the
  // behaviour so a future contributor who decides to strip VL inside
  // _looksLikeAlbumBrowseId has a regression row to update.
  ['VLMPREb_ stays at #playlist/ (no VL strip in current guard)',
   'VLMPREb_FAKE_xxx', '#playlist/VLMPREb_FAKE_xxx'],
  // Edge (post-tightening pinning row): we deliberately trimmed the
  // speculative `MPRP_` prefix from the allowlist. Real YTM payloads
  // have not been confirmed to use this prefix; if a future contributor
  // adds it back, this row fails with expected #album/, got #playlist/.
  ['MPRP_ stays at #playlist/ (post-tightening contract)',
   'MPRP_oldx', '#playlist/MPRP_oldx'],
  // Edge (object-coerce pin): a *truthy* non-string id (e.g. an object
  // that a careless caller passes) is NOT caught by the `!plId`
  // short-circuit (`!{}` is false) but is then caught-or-not by
  // `String(id).toUpperCase()` in `_looksLikeAlbumBrowseId` — which
  // today returns false because `'[OBJECT OBJECT]'` does NOT start with
  // `'MPREB_'`. So `{}` flows through and ends up at
  // `'#playlist/' + encodeURIComponent({})` =
  // `'#playlist/%5Bobject%20Object%5D'`. Pinning this row *documents
  // today's hazard* and gives a future guard change (e.g. adding
  // `typeof plId !== 'string'` early-return) a regression row that
  // surfaces loudly instead of silently producing working-but-broken
  // URL aliases. This is NOT the desired behaviour — the row name says
  // it isn't.
  ['object plId coerces to URL-encoded string (documents hazard)',
   {}, '#playlist/%5Bobject%20Object%5D'],
  // Edge (array-coerce pin): `![]` is false (empty arrays are truthy
  // in JS), so an empty array bypasses `!plId` and reaches
  // `'#playlist/' + encodeURIComponent([])`. `encodeURIComponent([...])`
  // is `encodeURIComponent(String([...]))` = `encodeURIComponent('')`
  // = `''`, producing a BARE `#playlist/` with no id after the slash.
  // This is a *different* URL shape from the `{}` case (a trailing-slash
  // bare hash, not an `[object Object]` slug). Worth its own row to
  // pin both shapes distinctly.
  ['empty-array plId produces bare #playlist/ (different shape, also hazard)',
   [], '#playlist/'],
];

let pass = 0, fail = 0;
for (const [name, id, expectedRoute] of cases) {
  window.__explicitMocks.calls.length = 0;
  window.location.hash = '';
  try {
    window.preloadNavigatePlaylist(id);
  } catch (e) {
    fail++;
    console.log(`FAIL ${name}: threw: ${e.message}`);
    continue;
  }
  const calls = window.__explicitMocks.calls;
  // Extract the recorded route. The spy may run via either
  // `navigateWithPreload` (from preloadNavigateAlbum or
  // preloadNavigatePlaylist's body) or `navigateTo` (from inside the
  // real navigateWithPreload, which we have stubbed out to a no-op,
  // but spy still gets through if the IIFE calls it directly).
  //
  // The "unexpected(...) calls" sentinel in the fall-through case
  // pins an interesting regression: a future "simplification" that
  // relocates the `null` short-circuit (e.g. collapsing the ternary
  // to `calls.length === 0 ? null : …`) would still pass the `null`
  // row but silently fail any EXPECTED-ROUTE row that accidentally
  // produced zero spy calls. Naming the sentinel loudly here surfaces
  // that asymmetry at the failure site without rendering the JSON
  // payload ambiguous.
  const route = calls.length === 1 && calls[0].fn === 'navigateWithPreload'
    ? calls[0].route
    : (calls.length === 1 && calls[0].fn === 'navigateTo'
        ? calls[0].arg
        : (calls.length === 0 && expectedRoute === null
            ? null
            : `unexpected(${calls.length} calls)`));

  if (route === expectedRoute) {
    pass++;
    console.log(`PASS ${name}  (id=${JSON.stringify(id)} -> ${JSON.stringify(route)})`);
  } else {
    fail++;
    console.log(`FAIL ${name}  (id=${JSON.stringify(id)}): ` +
                `expected ${JSON.stringify(expectedRoute)}, ` +
                `got ${JSON.stringify(route)}  calls=${JSON.stringify(calls)}`);
  }
}

// Mutual-existence sanity: preloadNavigateAlbum exists and emits an
// `#album/<id>` route arg directly. Without this the delegate in the
// new guard could route to a function that no-ops without warning.
const hasAlbum = (typeof window.preloadNavigateAlbum === 'function');
const hasPlaylist = (typeof window.preloadNavigatePlaylist === 'function');
if (hasAlbum && hasPlaylist) {
  window.__explicitMocks.calls.length = 0;
  window.location.hash = '';
  window.preloadNavigateAlbum('MPREb_xyz_test_direct');
  if (window.__explicitMocks.calls.length === 1 &&
      window.__explicitMocks.calls[0].route === '#album/MPREb_xyz_test_direct') {
    pass++;
    console.log('PASS preloadNavigateAlbum direct emits #album/<id>');
  } else {
    fail++;
    console.log('FAIL preloadNavigateAlbum: expected exactly one ' +
                'navigateWithPreload(#album/MPREb_xyz_test_direct) call, ' +
                'got ' + JSON.stringify(window.__explicitMocks.calls));
  }
} else {
  fail++;
  console.log(`FAIL: preload-nav.js IIFE did not expose both ` +
              `preloadNavigateAlbum and preloadNavigatePlaylist ` +
              `(album=${hasAlbum}, playlist=${hasPlaylist})`);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
