// Regression test for the "blank search page" UX bug.
//
// Bug (reported): searching navigated to an empty Results page immediately,
// then populated it once the API call resolved -- a visible blank-page flash
// on every search.
//
// Fix under test: runSearch() (search.js) now stays on whatever view is
// currently visible (Home, Artist, an already-open Results page, ...) while
// the request is in flight, showing only the top progress bar as feedback,
// and only calls openResults() once the data has actually arrived. The one
// exception is a bare cold-load direct link to #search?q=... with no other
// section visible yet -- there is nothing to "stay on" in that case, so it
// still opens Results immediately (matching the previous deep-link behavior).
//
// This test extracts the real runSearch function body by source text (the
// same technique test_play_intent_ordering.js and test_youtube_icon_pause.js
// use for behavior that depends on many cross-file globals) and executes it
// against a controlled fake environment, so it exercises the actual shipped
// logic.
//
// Run: `node flask-server/tests/test_search_stay_on_page.js`

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const JS_PATH = path.join(__dirname, '..', 'templates', 'static', 'js', 'search.js');
const SRC = fs.readFileSync(JS_PATH, 'utf8');
const ROUTER_SRC = fs.readFileSync(
  path.join(__dirname, '..', 'templates', 'static', 'js', 'router.js'),
  'utf8'
);

let passed = 0;
let failed = 0;

function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (ok) { passed += 1; console.log(`PASS  ${name}`); }
  else {
    failed += 1;
    console.log(`FAIL  ${name}\n        expected ${JSON.stringify(expected)}\n        actual   ${JSON.stringify(actual)}`);
  }
}

function checkTrue(name, actual, hint) {
  if (actual) { passed += 1; console.log(`PASS  ${name}`); }
  else { failed += 1; console.log(`FAIL  ${name}${hint ? '\n        ' + hint : ''}`); }
}

function extractFunctionBody(src, marker) {
  const start = src.indexOf(marker);
  if (start < 0) return null;
  const openBrace = src.indexOf('{', start);
  let depth = 0;
  for (let i = openBrace; i < src.length; i += 1) {
    if (src[i] === '{') depth += 1;
    else if (src[i] === '}') {
      depth -= 1;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  return null;
}

const runSearchSrc = extractFunctionBody(SRC, 'async function runSearch(query, options) {');
if (!runSearchSrc) {
  console.log('FATAL: could not locate runSearch() in search.js -- the source '
             + 'structure changed; update this test\'s marker.');
  process.exit(1);
}

function makeSandbox({ initialSectionsHidden, resultsOpenInitially, apiImpl, openResultsSpy }) {
  const events = [];
  const state = {
    _searchSeq: 0,
    _resultsOpen: !!resultsOpenInitially,
    _searchCategorized: {},
    _activeCategory: 'songs',
    _resultsPage: {},
  };

  const sections = Object.assign({
    'home-section': true, 'jam-home-section': true,
    'recs-section': true, 'artist-section': true,
  }, initialSectionsHidden);

  const resultsListEl = { innerHTML: '' };
  const resultsSectionEl = { hidden: !resultsOpenInitially };

  function fakeElementById(id) {
    if (id === 'results-list') return resultsListEl;
    if (id === 'results-section') return resultsSectionEl;
    if (id in sections) return { hidden: sections[id] };
    if (id === 'query') return { value: '' };
    return null;
  }

  const sandbox = {
    console,
    state,
    window: null,  // filled below
    document: {
      getElementById: fakeElementById,
      querySelectorAll: () => [],
      querySelector: (selector) => selector === 'main' ? { scrollTop: 0 } : null,
      documentElement: { scrollTop: 0 },
      body: { scrollTop: 0, classList: { remove: () => {} } },
    },
    toast: (msg, kind) => events.push(['toast', msg, kind]),
    api: (path) => { events.push(['api', path]); return apiImpl(path); },
    escHtml: (s) => s,
    encodeURIComponent,
    openResults: (opts) => {
      events.push(['openResults', opts]);
      state._resultsOpen = true;
      resultsSectionEl.hidden = false;
      sections['home-section'] = true;
      if (openResultsSpy) openResultsSpy(opts);
    },
    renderResults: () => events.push(['renderResults']),
    resetResultsScroll: () => events.push(['resetResultsScroll']),
  };
  sandbox.window = {
    startTopProgress: () => events.push(['startTopProgress']),
    completeTopProgress: () => events.push(['completeTopProgress']),
    abortTopProgress: () => events.push(['abortTopProgress']),
    closeSearchSuggestions: undefined,
    navigateTo: undefined,
    getRoute: () => state._route || '#home',
    syncUiState: () => {
      const isSearchRoute = (state._route || '').indexOf('#search?') === 0;
      const preserve = state._searchPreservePreviousView;
      const sourceRoute = state._searchPreviousRoute || '#home';
      if (isSearchRoute && preserve && sourceRoute === '#home') sections['home-section'] = false;
      else if (isSearchRoute && sourceRoute === '#home') sections['home-section'] = true;
    },
  };
  sandbox.window.scrollTo = () => events.push(['scrollTo']);

  const context = vm.createContext(sandbox);
  vm.runInContext(runSearchSrc, context, { filename: 'runSearch.js' });
  // The extracted source is a bare function *declaration* statement; wrap and
  // invoke it via a small harness that returns the callable.
  const wrapped = vm.runInContext(
    `(${runSearchSrc})`, context, { filename: 'runSearch-wrapped.js' });
  return { wrapped, state, events, sections, resultsListEl, resultsSectionEl, window: sandbox.window };
}

async function main() {
  console.log('--- route handoff: scroll reset waits for Results ---');
  checkTrue('router leaves Search scroll ownership to the Results handoff',
            (ROUTER_SRC.match(/var isSearchRoute = route\.indexOf\('\#search\?'\) === 0/g) || []).length >= 1 &&
            ROUTER_SRC.includes("route !== '#now-playing' && !isSearchRoute"),
            'the visible Home page must not be globally reset on Search route entry');
  checkTrue('popstate also leaves Search scroll ownership to Results',
            ROUTER_SRC.includes("var isSearchRoute = window.__route.indexOf('#search?') === 0") &&
            ROUTER_SRC.includes("window.__route !== '#now-playing' && !isSearchRoute"),
            'Back/Forward into Search must use the same no-jump behavior');
  checkTrue('all Search route entries defer queue/layout changes',
            ROUTER_SRC.includes("var deferSearchLayout = hash.indexOf('#search?') === 0") &&
            ROUTER_SRC.includes('if (!deferSearchLayout)'),
            'direct links and browser navigation must not shift the current page before data arrives');
  checkTrue('Results handoff owns the scroll reset',
            SRC.includes('function resetResultsScroll()') &&
            SRC.includes('if (main) main.scrollTop = 0;') &&
            SRC.includes('if (list) list.scrollTop = 0;'),
            'the reset belongs at the Results handoff, not route entry');
  checkTrue('router keeps the source overlays/pages during pending Search',
            ROUTER_SRC.includes("if (!preserveSearchShell && hash.indexOf('#playlist/') !== 0)") &&
            ROUTER_SRC.includes("if (!preserveSearchShell && hash !== '#history')") &&
            ROUTER_SRC.includes("if (!preserveSearchShell && hash !== '#explore')") &&
            ROUTER_SRC.includes("if (!preserveSearchShell && hash.indexOf('#mood/') !== 0)") &&
            ROUTER_SRC.includes("if (!preserveSearchShell && hash !== '#library')"),
            'Artist/Playlist/History/Explore/Library must remain visible until results arrive');

  console.log('--- searching from Home: stays on Home until data arrives ---');
  {
    let resolveApi;
    const pending = new Promise((r) => { resolveApi = r; });
    const { wrapped, state, events, sections } = makeSandbox({
      initialSectionsHidden: { 'home-section': false },  // Home is visible
      resultsOpenInitially: false,
      apiImpl: () => pending,
    });
    const runPromise = wrapped('some song', { fromRoute: true });
    // Before the API resolves: must NOT have opened Results, must still be
    // showing the progress bar as the only feedback.
    checkTrue('did not open Results while the request is in flight',
              !events.some(([fn]) => fn === 'openResults'),
              'the bug: navigating to Results before data arrives shows a blank page');
    checkTrue('top progress bar was started', events.some(([fn]) => fn === 'startTopProgress'));
    check('Home section is still visible mid-request', sections['home-section'], false);

    resolveApi({ songs: [{ video_id: 'v1' }] });
    await runPromise;
    checkTrue('Results opened once data arrived', events.some(([fn]) => fn === 'openResults'));
    checkTrue('progress bar completed', events.some(([fn]) => fn === 'completeTopProgress'));
  }

  console.log('\n--- Enter submission: route handoff keeps Home until data arrives ---');
  {
    let resolveApi;
    const pending = new Promise((r) => { resolveApi = r; });
    const sandbox = makeSandbox({
      initialSectionsHidden: { 'home-section': false },
      resultsOpenInitially: false,
      apiImpl: () => pending,
    });
    // The real Enter path calls runSearch() without fromRoute, which then
    // navigates synchronously and lets the router call runSearch() again with
    // fromRoute: true. Reproduce that handoff instead of testing only the
    // route-owned half of the implementation.
    let routePromise;
    sandbox.window.__spaRouteCodec = {
      searchRoute: (query) => '#search?q=' + encodeURIComponent(query),
    };
    sandbox.window.navigateTo = () => {
      // Simulate the router's route handoff and its final UI-state sync. The
      // real sync must leave Home mounted while the pending flag is set.
      sandbox.state._route = '#search?q=enter+query';
      // The router's route setup hides Home before invoking the route-owned
      // search handler. The handler must restore it from its captured snapshot.
      sandbox.sections['home-section'] = true;
      sandbox.window.syncUiState();
      routePromise = sandbox.wrapped('enter query', { fromRoute: true });
      sandbox.window.syncUiState();
    };
    const enterPromise = sandbox.wrapped('enter query');
    checkTrue('Enter path does not open Results while loading',
              !sandbox.events.some(([fn]) => fn === 'openResults'),
              'the real button/Enter path must preserve the current page too');
    check('Home remains visible during the Enter request',
          sandbox.sections['home-section'], false);
    checkTrue('Enter path records the previous Home view',
              sandbox.state._searchPreviousHomeVisible === true);
    resolveApi({ songs: [{ video_id: 'enter-v1' }] });
    // The first promise resolves after it invokes navigateTo(); the route
    // handler returns its own promise because the real app performs the
    // handoff synchronously inside the router call stack.
    await enterPromise;
    await routePromise;
    checkTrue('Enter path opens Results after data arrives',
              sandbox.events.some(([fn]) => fn === 'openResults'));
  }

  console.log('\n--- searching after leaving Results: stale rows stay hidden ---');
  {
    let resolveApi;
    const pending = new Promise((r) => { resolveApi = r; });
    const sandbox = makeSandbox({
      initialSectionsHidden: { 'home-section': false },
      resultsOpenInitially: false,
      apiImpl: () => pending,
    });
    sandbox.resultsListEl.innerHTML = '<div>previous search results</div>';
    // Model the real route-away cleanup: the Results section is hidden and
    // the search state is invalidated, but its old list DOM still exists.
    sandbox.resultsSectionEl.hidden = true;
    sandbox.state._resultsOpen = false;
    sandbox.state._searchSeq += 1;
    const runPromise = sandbox.wrapped('new query', { fromRoute: true });
    check('old results are cleared while searching from Home',
          sandbox.resultsListEl.innerHTML, '');
    checkTrue('Results remain closed while the replacement search loads',
              !sandbox.events.some(([fn]) => fn === 'openResults'));
    resolveApi({ songs: [{ video_id: 'replacement-v1' }] });
    await runPromise;
    checkTrue('replacement results open after the new data arrives',
              sandbox.events.some(([fn]) => fn === 'openResults'));
  }

  console.log('\n--- searching from Artist: stays on Artist until data arrives ---');
  {
    let resolveApi;
    const pending = new Promise((r) => { resolveApi = r; });
    const sandbox = makeSandbox({
      initialSectionsHidden: {
        'home-section': true,
        'artist-section': false,
      },
      resultsOpenInitially: false,
      apiImpl: () => pending,
    });
    sandbox.state._route = '#artist/UC-artist';
    sandbox.window.getRoute = () => sandbox.state._route;
    sandbox.window.__spaRouteCodec = {
      searchRoute: (query) => '#search?q=' + encodeURIComponent(query),
    };
    let routePromise;
    sandbox.window.navigateTo = (route) => {
      sandbox.state._route = route;
      routePromise = sandbox.wrapped('artist query', { fromRoute: true });
    };
    const runPromise = sandbox.wrapped('artist query');
    check('Artist remains visible before the route handoff',
          sandbox.sections['artist-section'], false);
    check('Home does not become visible from an Artist search',
          sandbox.sections['home-section'], true);
    check('source route is captured for the pending search',
          sandbox.state._searchPreviousRoute, '#artist/UC-artist');
    resolveApi({ songs: [{ video_id: 'artist-v1' }] });
    await runPromise;
    await routePromise;
    checkTrue('Artist search opens Results only after data arrives',
              sandbox.events.some(([fn]) => fn === 'openResults'));
  }

  console.log('\n--- searching while already on Results: no flicker, updates in place ---');
  {
    const { wrapped, events, resultsListEl } = makeSandbox({
      initialSectionsHidden: {},
      resultsOpenInitially: true,
      apiImpl: () => Promise.resolve({ songs: [{ video_id: 'v2' }] }),
    });
    resultsListEl.innerHTML = '<div>previous results</div>';
    await wrapped('another song', { fromRoute: true });
    checkTrue('did not call openResults again (already open)',
              !events.some(([fn]) => fn === 'openResults'),
              'reopening an already-open page is unnecessary churn');
    check('previous results were NOT cleared before the new data arrived',
          resultsListEl.innerHTML, '<div>previous results</div>');
    checkTrue('renderResults was called once data arrived',
              events.some(([fn]) => fn === 'renderResults'));
  }

  console.log('\n--- cold-load deep link with nothing else visible: opens immediately ---');
  {
    let resolveApi;
    const pending = new Promise((r) => { resolveApi = r; });
    const { wrapped, events, sections } = makeSandbox({
      // Nothing else is visible -- simulates a fresh page load landing
      // directly on a #search?q=... URL.
      initialSectionsHidden: {},
      resultsOpenInitially: false,
      apiImpl: () => pending,
    });
    const runPromise = wrapped('deep link query', { fromRoute: true });
    checkTrue('Results opened immediately (nothing else to show)',
              events.some(([fn]) => fn === 'openResults'),
              'a bare deep link must not leave the user on a totally blank page');
    resolveApi({ songs: [] });
    await runPromise;
  }

  console.log('\n--- a newer search supersedes an older one\'s late response ---');
  {
    let resolveFirst;
    const firstPending = new Promise((r) => { resolveFirst = r; });
    const sandbox = makeSandbox({
      initialSectionsHidden: { 'home-section': false },
      resultsOpenInitially: false,
      apiImpl: () => firstPending,
    });
    const firstRun = sandbox.wrapped('first query', { fromRoute: true });
    // A second search starts before the first resolves.
    sandbox.state._searchSeq += 1;  // simulate what the second runSearch call does
    resolveFirst({ songs: [{ video_id: 'stale' }] });
    await firstRun;
    checkTrue('a superseded search does not render stale data',
              !sandbox.events.some(([fn]) => fn === 'renderResults'),
              'the first search\'s late response must be dropped once superseded');
  }

  console.log(`\nsearch-stay-on-page: passed=${passed} failed=${failed}`);
  process.exit(failed ? 1 : 0);
}

main();
