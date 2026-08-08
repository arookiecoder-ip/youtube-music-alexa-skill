// Regression test for a rapid pause -> resume click.
// A second click must be queued, sent only after a fresh paused snapshot, and
// must not make the UI claim playback until a fresh confirmed playing snapshot.

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC = fs.readFileSync(
  path.join(__dirname, 'templates', 'static', 'js', 'player.js'),
  'utf8'
);
let passed = 0;
let failed = 0;
function check(name, actual, expected) {
  if (JSON.stringify(actual) === JSON.stringify(expected)) {
    passed += 1; console.log(`PASS  ${name}`);
  } else {
    failed += 1; console.log(`FAIL  ${name}\n        expected ${JSON.stringify(expected)}\n        actual   ${JSON.stringify(actual)}`);
  }
}
function checkTrue(name, actual) {
  if (actual) { passed += 1; console.log(`PASS  ${name}`); }
  else { failed += 1; console.log(`FAIL  ${name}`); }
}
function extractControlBlock(src) {
  const start = src.indexOf('let _playPauseBusy = false;');
  const end = src.indexOf('\nconst npPageArt =', start);
  return start >= 0 && end > start ? src.slice(start, end) : null;
}
function makeSandbox(api) {
  const events = {};
  const calls = [];
  const toasts = [];
  const button = { disabled: false, click() { this.onclick(); }, onclick: null, querySelector: () => null };
  const noop = () => {};
  const fakeClassList = { contains: () => false, add: noop, remove: noop, toggle: noop };
  const state = { isPlaying: true, lastActionAt: 0, lastActionIntent: true };
  const elements = { 'pp-btn': button, 'now-playing-section': { hidden: false }, 'player-expand-button': {} };
  const sandbox = {
    console, Date, Math, Error, setTimeout, clearTimeout,
    selectedSerial: () => 'SERIAL-1',
    api: (url, body) => { calls.push({ url, action: body.action }); return api(body.action); },
    toast: (message) => toasts.push(message), syncPlayPause: noop,
    progress: { setPausePending: noop }, state,
    document: { body: { classList: fakeClassList }, getElementById: (id) => elements[id] || null,
      addEventListener: (name, handler) => { events[name] = handler; } },
  };
  sandbox.window = sandbox;
  sandbox.__appState = state;
  return { sandbox, button, calls, toasts };
}

async function main() {
  const controlBlock = extractControlBlock(SRC);
  if (!controlBlock) { console.log('FATAL: control block not found'); process.exit(1); }
  let resolvePause;
  let resolveResume;
  const pausePending = new Promise((resolve) => { resolvePause = resolve; });
  const resumePending = new Promise((resolve) => { resolveResume = resolve; });
  const { sandbox, button, calls, toasts } = makeSandbox((action) => ({ pause: pausePending, play: resumePending }[action]));
  vm.runInContext(controlBlock, vm.createContext(sandbox), { filename: 'play-pause-controls.js' });

  checkTrue('play/pause handler is wired', typeof button.onclick === 'function');
  button.click();
  button.click();
  check('only pause is initially dispatched', calls.map((call) => call.action), ['pause']);
  check('queued resume does not claim playback early', sandbox.state.isPlaying, false);

  resolvePause({ ok: true });
  await new Promise((resolve) => setTimeout(resolve, 0));
  await new Promise((resolve) => setTimeout(resolve, 0));
  check('resume is not dropped while pause confirmation is delayed', calls.map((call) => call.action), ['pause', 'play']);
  sandbox._notifyPlayPauseServerState(false, 1);
  await new Promise((resolve) => setTimeout(resolve, 0));
  check('resume remains ordered after pause confirmation', calls.map((call) => call.action), ['pause', 'play']);

  resolveResume({ ok: true });
  await new Promise((resolve) => setTimeout(resolve, 0));
  check('resume remains visually paused before confirmation', sandbox.state.isPlaying, false);
  sandbox._notifyPlayPauseServerState(true, 2);
  await new Promise((resolve) => setTimeout(resolve, 0));
  check('final state is playing after fresh confirmation', sandbox.state.isPlaying, true);
  checkTrue('resume confirmation toast was shown', toasts.some((message) => /resumed/i.test(message)));
  check('no command was dropped or duplicated', calls.length, 2);

  // Alexa voice can pause the Echo while the website is idle. The next website
  // click must use the externally confirmed paused state and send play, not a
  // second pause based on a stale desired-state value.
  const external = makeSandbox(() => Promise.resolve({ ok: true }));
  external.sandbox.state.isPlaying = true;
  vm.runInContext(controlBlock, vm.createContext(external.sandbox), { filename: 'play-pause-external.js' });
  external.sandbox._notifyPlayPauseServerState(false, 10, 7);
  external.button.click();
  await new Promise((resolve) => setTimeout(resolve, 0));  check('button resumes after external Alexa pause', external.calls.map((call) => call.action), ['play']);

  // Once a marked snapshot has been accepted, an old marker-less snapshot
  // cannot undo the externally confirmed pause.
  const markerlessExternal = makeSandbox(() => Promise.resolve({ ok: true }));
  vm.runInContext(controlBlock, vm.createContext(markerlessExternal.sandbox), { filename: 'play-pause-markerless.js' });
  markerlessExternal.sandbox._notifyPlayPauseServerState(true, 30, 5);
  markerlessExternal.sandbox._notifyPlayPauseServerState(false, 31, 6);
  markerlessExternal.sandbox._notifyPlayPauseServerState(true, 0, 0);
  markerlessExternal.button.click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  check('markerless stale state cannot cause a second pause', markerlessExternal.calls.map((call) => call.action), ['play']);

  // A webhook can carry a newer timestamp while retaining the same revision.
  // It must still update the idle toggle state rather than being discarded.
  const timestampedExternal = makeSandbox(() => Promise.resolve({ ok: true }));
  vm.runInContext(controlBlock, vm.createContext(timestampedExternal.sandbox), { filename: 'play-pause-timestamped.js' });
  timestampedExternal.sandbox._notifyPlayPauseServerState(true, 20, 4);
  timestampedExternal.sandbox._notifyPlayPauseServerState(false, 21, 4);
  // Delayed older "playing" data must not undo the confirmed pause.
  timestampedExternal.sandbox._notifyPlayPauseServerState(true, 20, 3);
  timestampedExternal.button.click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  check('newer timestamped Alexa pause sends play', timestampedExternal.calls.map((call) => call.action), ['play']);

  console.log(`\nrapid-pause-resume: passed=${passed} failed=${failed}`);
  process.exit(failed ? 1 : 0);
}
main();
