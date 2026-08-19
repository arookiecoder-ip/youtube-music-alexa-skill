// Regression test for the "YouTube icon sometimes fails to pause" bug.
//
// Bug (reported): clicking the YouTube icon in the player sometimes doesn't
// pause the music before navigating to YouTube Music.
//
// Cause: the click handler only sent the pause command when the client's
// locally-tracked `state.isPlaying` flag was true, and it silently swallowed
// any dispatch failure with `.catch(() => {})`. `state.isPlaying` is an
// optimistic client-side guess that can go stale (SSE lag, a race with
// another open tab/device, a missed update) -- when it read `false` while
// the device was actually still playing, clicking the icon skipped the pause
// call entirely and just opened YouTube with the track still audible. A
// genuine dispatch failure was also invisible to the user.
//
// Fix under test: the click handler now always attempts the pause (a no-op
// server-side if the device is already paused) and surfaces a toast if the
// API call fails, instead of gating on `state.isPlaying` and swallowing
// errors.
//
// This test does not load the full player.js (it has many DOM/module
// dependencies from device.js/api.js/toast.js/queue.js that a lean sandbox
// would have to fake wholesale). Instead it extracts the actual onClick
// function body by source text -- the same technique test_play_intent_ordering.js
// uses for cross-file behavioural guards -- and executes it against a
// controlled fake `api`/state/DOM, so this exercises the real shipped logic,
// not a re-implementation of it.
//
// Run: `node flask-server/tests/test_youtube_icon_pause.js`

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const JS_PATH = path.join(__dirname, '..', 'templates', 'static', 'js', 'player.js');
const SRC = fs.readFileSync(JS_PATH, 'utf8');

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

// ---------------------------------------------------------------------
// Extract the IIFE containing the np-url-toggle click handler by source
// text, then evaluate just that IIFE (not the whole file) against a fake
// environment. This isolates the exact shipped onClick logic.
// ---------------------------------------------------------------------
function extractIife(src, marker) {
  const markerIdx = src.indexOf(marker);
  if (markerIdx < 0) return null;
  // Walk back to the start of the enclosing "(function () {" that contains
  // the marker (the block is written as an immediately-invoked function
  // expression ending in "})();").
  const start = src.lastIndexOf('(function () {', markerIdx);
  if (start < 0) return null;
  const openBrace = src.indexOf('{', start);
  let depth = 0;
  for (let i = openBrace; i < src.length; i += 1) {
    if (src[i] === '{') depth += 1;
    else if (src[i] === '}') {
      depth -= 1;
      if (depth === 0) {
        // Consume through the trailing "})();"
        const closeParenIdx = src.indexOf(')', i);
        const semiIdx = src.indexOf(';', closeParenIdx);
        return src.slice(start, semiIdx + 1);
      }
    }
  }
  return null;
}

const iife = extractIife(SRC, "const onClick = (e) => {");
if (!iife) {
  console.log('FATAL: could not locate the np-url-toggle IIFE in player.js -- '
             + 'the source structure changed; update this test\'s marker/extraction.');
  process.exit(1);
}

function runScenario({ isPlayingBefore, currentVideoId, apiImpl, jamGuest }) {
  const events = {};
  const calls = [];
  const toasts = [];

  function fakeElement(id) {
    return {
      id,
      href: '',
      addEventListener(evt, handler) { events[id + ':' + evt] = handler; },
    };
  }

  const state = {
    isPlaying: isPlayingBefore,
    lastActionAt: 0,
    lastActionIntent: null,
    _currentVideoId: currentVideoId,
  };

  const sandbox = {
    console,
    document: {
      getElementById: (id) => (id === 'np-url-toggle' || id === 'mobile-player-youtube'
        ? fakeElement(id) : null),
    },
    JAM_GUEST: !!jamGuest,
    state,
    progress: { livePosition: () => 42000 },
    selectedSerial: () => 'DEVICE_SERIAL_1',
    api: (path, body) => { calls.push([path, body]); return apiImpl(path, body); },
    toast: (msg, kind) => { toasts.push([msg, kind]); },
    syncPlayPause: () => {},
    updateUrlBar: () => {},
    encodeURIComponent,
    Math,
    Date,
  };
  sandbox.window = sandbox;
  const context = vm.createContext(sandbox);
  vm.runInContext(iife, context, { filename: 'np-url-toggle-iife.js' });

  const handler = events['np-url-toggle:click'];
  if (!handler) throw new Error('click handler was not registered');

  const fakeEvent = { currentTarget: fakeElement('np-url-toggle'), preventDefault() {} };
  handler(fakeEvent);
  return { state, calls, toasts, fakeEvent };
}

async function main() {
  console.log('--- clicking while isPlaying=false must still attempt pause ---');
  {
    let resolveFn;
    const pausePromise = new Promise((r) => { resolveFn = r; });
    const { calls } = runScenario({
      isPlayingBefore: false,   // stale/incorrect local flag
      currentVideoId: 'VIDEOID00001',
      apiImpl: () => pausePromise,
    });
    checkTrue('pause was dispatched even though isPlaying was false',
              calls.length === 1 && calls[0][0] === '/alexa/command/'
              && calls[0][1].action === 'pause',
              'the fix must not gate the pause call on the (possibly stale) '
              + 'isPlaying flag');
    resolveFn();
  }

  console.log('\n--- clicking while isPlaying=true also pauses (unchanged) ---');
  {
    const { calls } = runScenario({
      isPlayingBefore: true,
      currentVideoId: 'VIDEOID00002',
      apiImpl: () => Promise.resolve({}),
    });
    checkTrue('pause was dispatched', calls.length === 1 && calls[0][1].action === 'pause');
  }

  console.log('\n--- jam guest: clicking the YouTube icon must not pause the shared device ---');
  {
    const { calls } = runScenario({
      isPlayingBefore: true,
      currentVideoId: 'VIDEOID00006',
      jamGuest: true,
      apiImpl: () => Promise.resolve({}),
    });
    check('no pause dispatched for a jam guest (shared playback device)', calls, []);
  }

  console.log('\n--- no device selected: no pause call, no crash ---');
  {
    const events = {};
    const state = { isPlaying: true, _currentVideoId: 'VIDEOID00003' };
    const calls = [];
    function fakeElement(id) { return { id, href: '', addEventListener(evt, h) { events[id + ':' + evt] = h; } }; }
    const sandbox = {
      console,
      document: { getElementById: (id) => (id === 'np-url-toggle' ? fakeElement(id) : null) },
      state,
      progress: { livePosition: () => 0 },
      selectedSerial: () => null,  // no device picked
      api: (p, b) => { calls.push([p, b]); return Promise.resolve({}); },
      toast: () => {},
      syncPlayPause: () => {},
      updateUrlBar: () => {},
      encodeURIComponent,
      Math, Date,
    };
    sandbox.window = sandbox;
    vm.runInContext(iife, vm.createContext(sandbox), { filename: 'x.js' });
    const handler = events['np-url-toggle:click'];
    handler({ currentTarget: fakeElement('np-url-toggle'), preventDefault() {} });
    check('no api call made without a selected device', calls, []);
  }

  console.log('\n--- no song playing: navigation is prevented, no pause attempted ---');
  {
    const { calls, fakeEvent, toasts } = runScenario({
      isPlayingBefore: true,
      currentVideoId: '',   // nothing playing
      apiImpl: () => Promise.resolve({}),
    });
    check('no api call made when nothing is playing', calls, []);
    checkTrue('an error toast was shown', toasts.some(([, kind]) => kind === 'error'));
  }

  console.log('\n--- a failed pause dispatch surfaces an error instead of being swallowed ---');
  {
    const { toasts } = runScenario({
      isPlayingBefore: true,
      currentVideoId: 'VIDEOID00004',
      apiImpl: () => Promise.reject(new Error('device offline')),
    });
    // Let the rejected promise's .catch() run.
    await new Promise((r) => setTimeout(r, 0));
    checkTrue('a failure toast was shown instead of being silently swallowed',
              toasts.some(([msg, kind]) => kind === 'error' && /pause/i.test(msg)),
              'the old code had .catch(() => {}) here -- a failed pause was invisible');
  }

  console.log('\n--- successful pause updates local isPlaying/lastActionIntent ---');
  {
    const { state } = runScenario({
      isPlayingBefore: true,
      currentVideoId: 'VIDEOID00005',
      apiImpl: () => Promise.resolve({}),
    });
    await new Promise((r) => setTimeout(r, 0));
    check('isPlaying flipped to false after a successful pause', state.isPlaying, false);
    check('lastActionIntent recorded as pause (false)', state.lastActionIntent, false);
  }

  console.log(`\nyoutube-icon-pause: passed=${passed} failed=${failed}`);
  process.exit(failed ? 1 : 0);
}

main();
