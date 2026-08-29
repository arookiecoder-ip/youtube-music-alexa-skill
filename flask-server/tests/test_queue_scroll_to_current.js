// Regression test for the "currently-playing song sits at the top of the queue
// whenever the player opens" fix.
//
// Requirement: when the player opens, the active/currently-playing queue row
// should be brought to the TOP of the queue list with a smooth scroll — but only
// when it actually fell out of the viewport. If it's already visible (even just
// peeking in below the top), the scroll position must be left alone.
//
// Fixed by scrolling the queue list's OWN scrollTop (container.scrollTo) rather
// than element.scrollIntoView: scrollIntoView scrolls every scrollable ancestor
// so the row lands at the top of the *viewport*, which on page-scroll layouts
// (mobile embeds the queue in the scrolling now-playing page) pushes the whole
// now-playing section upward. A container-scoped scroll never moves ancestors.
//
// Fix under test (queue.js):
//   * `_scrollQueueRowIntoView(container, index, force)` computes the pixel
//     delta needed and calls `container.scrollTo({ top, behavior: 'smooth' })`.
//     Forced renders (player opening) align to the list's top only when the row
//     is out of view; highlight shifts do a minimal 'nearest' adjustment.
//   * `window.scrollQueueToCurrent` forces that scroll when the player opens.
//
// Run: `node flask-server/tests/test_queue_scroll_to_current.js`

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const JS_PATH = path.join(__dirname, '..', 'templates', 'static', 'js', 'queue.js');
const SRC = fs.readFileSync(JS_PATH, 'utf8');

let passed = 0;
let failed = 0;

function checkTrue(name, actual, hint) {
  if (actual) { passed += 1; console.log(`PASS  ${name}`); }
  else { failed += 1; console.log(`FAIL  ${name}${hint ? '\n        ' + hint : ''}`); }
}
function checkEqual(name, actual, expected, hint) {
  checkTrue(name + ` (expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)})`, actual === expected, hint);
}

// Extract the scroll helpers from `_scrollQueueRowIntoView` through the end of
// `window.scrollQueueToCurrent`.
const START_MARKER = '// `force` marks a full render';
const END_MARKER = '  _scrollQueueRowIntoView(list, currentIndex, true);\n};';
const start = SRC.indexOf('function _scrollQueueRowIntoView');
const end = SRC.indexOf(END_MARKER);
if (start < 0 || end < 0) {
  console.log('FATAL: could not locate the queue scroll helpers in queue.js '
              + '-- the source structure changed; update this test\'s markers.');
  process.exit(1);
}
const BLOCK_SRC = SRC.slice(start, end + END_MARKER.length);

// ---- tiny fake DOM -------------------------------------------------------

function makeEnv({ containerRect, rowRect, indexKey }) {
  // `container.scrollTop` is what the list starts at; the row's rect is given
  // in document space AFTER that starting scroll. We feed the row rect as if
  // measured with getBoundingClientRect, so the delta is (rRect.top - cRect.top).
  const scrollCalls = [];
  const container = {
    dataset: {},
    scrollTop: 0,
    getBoundingClientRect: () => containerRect,
    scrollTo(opts) { scrollCalls.push(opts); this.scrollTop = opts.top; },
    querySelector(sel) { return sel === '.queue-item.active' ? row : null; },
  };
  const wrapper = {
    getBoundingClientRect: () => ({ top: rowRect.top, bottom: rowRect.bottom, left: 0, right: 100, width: 100, height: rowRect.bottom - rowRect.top }),
    closest(sel) { return sel === '.queue-swipe-wrapper' ? wrapper : null; },
  };
  const row = {
    getBoundingClientRect: () => ({ top: rowRect.top, bottom: rowRect.bottom, left: 0, right: 100, width: 100, height: rowRect.bottom - rowRect.top }),
    closest(sel) { return sel === '.queue-swipe-wrapper' ? wrapper : null; },
  };
  const document = { getElementById(id) { return id === 'np-queue-list' ? container : null; } };
  const window = { __appState: { _lastQueueIndex: indexKey } };
  const requestAnimationFrame = (fn) => fn();

  const sandbox = { document, window, requestAnimationFrame, console };
  const context = vm.createContext(sandbox);
  vm.runInContext(BLOCK_SRC, context, { filename: 'queue-scroll-block.js' });

  return { container, wrapper, scrollCalls, context, sandbox };
}

function main() {
  console.log('--- player opens with the active row scrolled OUT of view ---');
  {
    // Container 0..500; the row sits at 720..820 (fully below the list's viewport).
    const env = makeEnv({ containerRect: { top: 0, bottom: 500, left: 0, right: 400, width: 400, height: 500 },
                          rowRect: { top: 720, bottom: 820 }, indexKey: 4 });
    env.context._scrollQueueRowIntoView(env.container, 4, true);
    checkEqual('full render scrolls the row to the list TOP', env.scrollCalls[0] ? env.scrollCalls[0].top : null, 720,
               'delta should be row.top - container.top = 720 - 0 = 720');
    checkEqual('scroll is smooth', env.scrollCalls[0] ? env.scrollCalls[0].behavior : null, 'smooth');
  }

  console.log('--- player opens with the row scrolled above the top ---');
  {
    // Container 0..500; the row sits above the list (partially cut off at top).
    const env = makeEnv({ containerRect: { top: 0, bottom: 500, left: 0, right: 400, width: 400, height: 500 },
                          rowRect: { top: -60, bottom: 120 }, indexKey: 1 });
    env.context._scrollQueueRowIntoView(env.container, 1, true);
    checkEqual('out-of-view (above) row scrolled to top', env.scrollCalls[0] ? env.scrollCalls[0].top : null, -60,
               'delta = -60 - 0 = -60 moves the row back under the top edge');
  }

  console.log('--- player opens while the active row is ALREADY visible ---');
  {
    // Container 0..500; the row is 100..200 — clearly inside, so no top yank.
    const env = makeEnv({ containerRect: { top: 0, bottom: 500, left: 0, right: 400, width: 400, height: 500 },
                          rowRect: { top: 100, bottom: 200 }, indexKey: 1 });
    env.context._scrollQueueRowIntoView(env.container, 1, true);
    checkTrue('visible row does NOT scroll at all', env.scrollCalls.length === 0,
              'an already-visible row must not be forced to the top');
  }

  console.log('--- player opens while the active row just peeks below the top ---');
  {
    // Row fully inside but below the top edge.
    const env = makeEnv({ containerRect: { top: 0, bottom: 500, left: 0, right: 400, width: 400, height: 500 },
                          rowRect: { top: 260, bottom: 380 }, indexKey: 1 });
    env.context._scrollQueueRowIntoView(env.container, 1, true);
    checkTrue('partially-visible row does NOT scroll at all', env.scrollCalls.length === 0,
              'a row already in the viewport must not be forced to the top');
  }

  console.log('--- lightweight highlight shift only does a minimal nearest scroll ---');
  {
    // Row fully below viewport; non-forced shift should bring bottom edge into view.
    const env = makeEnv({ containerRect: { top: 0, bottom: 500, left: 0, right: 400, width: 400, height: 500 },
                          rowRect: { top: 720, bottom: 820 }, indexKey: 2 });
    env.context._scrollQueueRowIntoView(env.container, 2, false);
    checkEqual('non-forced shift scrolls just enough to reveal it', env.scrollCalls[0] ? env.scrollCalls[0].top : null, 320,
               'bottom edge 820 -> list bottom 500 means delta = 820 - 500 = 320 (nearest, not to top)');
  }

  console.log('--- repeated non-forced calls do not re-scroll (guard) ---');
  {
    const env = makeEnv({ containerRect: { top: 0, bottom: 500, left: 0, right: 400, width: 400, height: 500 },
                          rowRect: { top: 720, bottom: 820 }, indexKey: 3 });
    env.context._scrollQueueRowIntoView(env.container, 3, false);
    const firstCount = env.scrollCalls.length;
    env.context._scrollQueueRowIntoView(env.container, 3, false);
    checkTrue('second call with unchanged index is a no-op', env.scrollCalls.length === firstCount,
              'the lastActiveIndex guard must prevent needless re-scrolling on poll noise');
  }

  console.log('--- window.scrollQueueToCurrent forces the open scroll ---');
  {
    const env = makeEnv({ containerRect: { top: 0, bottom: 500, left: 0, right: 400, width: 400, height: 500 },
                          rowRect: { top: 900, bottom: 1000 }, indexKey: 7 });
    // Simulate a reopen: the DOM already holds rows, so scrollQueueToCurrent is
    // the path that must force the top alignment.
    env.sandbox.window.scrollQueueToCurrent();
    checkEqual('reopen forces the active row to the TOP of the list', env.scrollCalls[0] ? env.scrollCalls[0].top : null, 900);
    checkEqual('uses the stored queue index', env.container.dataset.lastActiveIndex, '7');
  }

  console.log(`\nqueue-scroll-to-current: passed=${passed} failed=${failed}`);
  process.exit(failed ? 1 : 0);
}

main();