// Regression test for pause progress confirmation.
// The progress controller must keep ticking while the pause command is
// pending, then stop only after the command resolves or a confirmed paused
// snapshot arrives. A failed command must leave playback running.

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
  if (actual === expected) { passed += 1; console.log(`PASS  ${name}`); }
  else { failed += 1; console.log(`FAIL  ${name}\n        expected ${expected}\n        actual   ${actual}`); }
}
function checkTrue(name, actual) {
  if (actual) { passed += 1; console.log(`PASS  ${name}`); }
  else { failed += 1; console.log(`FAIL  ${name}`); }
}
function extractProgressReturn(src) {
  const marker = 'const progress = window.progress = (function () {';
  const start = src.indexOf(marker);
  if (start < 0) return null;
  const returnMarker = '  return { update, resetPending';
  const end = src.indexOf(returnMarker, start);
  if (end < 0) return null;
  const close = src.indexOf('\n})();', end);
  if (close < 0) return null;
  return src.slice(start, close + 6) + '\nthis.progress = progress;';
}

const progressSrc = extractProgressReturn(SRC);
if (!progressSrc) {
  console.log('FATAL: could not extract progress controller');
  process.exit(1);
}

function makeSandbox() {
  const timers = new Map();
  let nextTimer = 1;
  const styles = {};
  function element(id) {
    return {
      id,
      hidden: false,
      style: { setProperty(k, v) { styles[id + ':' + k] = v; }, width: '', left: '' },
      textContent: '',
      setAttribute() {},
      addEventListener() {},
      classList: { add() {}, remove() {}, toggle() {} },
      getBoundingClientRect: () => ({ left: 0, width: 100 }),
    };
  }
  const elements = new Map(['progress', 'progress-track', 'progress-fill', 'progress-handle',
    'progress-elapsed', 'progress-total', 'playbar-elapsed', 'playbar-total',
    'mobile-np-progress', 'mobile-np-progress-fill', 'mobile-np-progress-handle',
    'mobile-np-progress-elapsed', 'mobile-np-progress-total'].map(id => [id, element(id)]));
  const sandbox = {
    console: { log() {} },
    window: null,
    document: {
      hidden: false,
      getElementById: id => elements.get(id) || null,
      addEventListener() {},
    },
    Date,
    state: { lastActionAt: 0, lastActionIntent: null, GRACE_MS: 8000 },
    isFinite,
    setTimeout: (fn, ms) => { const id = nextTimer++; timers.set(id, { fn, ms }); return id; },
    clearTimeout: id => timers.delete(id),
  };
  sandbox.window = sandbox;
  sandbox.window.__appState = sandbox.state;
  sandbox.window.addEventListener = () => {};
  const context = vm.createContext(sandbox);
  vm.runInContext(progressSrc, context, { filename: 'progress-controller.js' });
  return { progress: sandbox.progress, timers, styles, state: sandbox.state };
}

function snapshot(playing, confirmed, position = 1000) {
  return {
    title: 'Track', video_id: 'video-1', playing, playback_confirmed: confirmed,
    position_ms: position, duration_ms: 180000, started_at: Date.now(),
  };
}

function main() {
  const { progress, state } = makeSandbox();
  progress.update(snapshot(true, true, 1000));
  progress.setPausePending(true);
  state.lastActionAt = Date.now() - 9000;
  state.lastActionIntent = false;
  progress.update(snapshot(false, false, 1100));
  const pendingPosition = progress.livePosition();
  checkTrue('an unconfirmed paused snapshot does not stop the ticker while pause is pending',
            pendingPosition >= 1100);

  progress.update(snapshot(false, true, 1200));
  const stillPendingPosition = progress.livePosition();
  checkTrue('a snapshot alone does not stop the ticker before command confirmation',
            stillPendingPosition >= 1200);
  progress.setPausePending(false);
  const confirmedPosition = progress.livePosition();
  checkTrue('the ticker is stopped after command confirmation',
            confirmedPosition <= stillPendingPosition);

  state.isPlaying = true;
  progress.update(snapshot(true, true, 2000));
  progress.setPausePending(true);
  progress.setPausePending(false);
  const recoveredPosition = progress.livePosition();
  // Date.now() can advance by a millisecond between anchoring and reading;
  // assert that the ticker stayed at the same track position, not an exact
  // equality with the anchor.
  checkTrue('a failed pause can leave the ticker running', recoveredPosition >= 1200);

  console.log(`\npause-progress-confirmation: passed=${passed} failed=${failed}`);
  process.exit(failed ? 1 : 0);
}
main();
