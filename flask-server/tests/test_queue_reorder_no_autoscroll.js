// Regression test: dragging a song to a new queue position must not make the
// queue automatically scroll away (and re-position onto the active song).
//
// Bug: `reorderQueue`'s optimistic re-render flows through `renderNpQueue`,
// whose full-rebuild branch calls `_scrollQueueRowIntoView(list, idx, true)`
// with force=true — yanking the viewport back to the currently-playing row
// the moment a song is dropped. Worse, the optimistic edit only updated
// `state._lastQueueJson` while SSE/poll code compares against
// `window._lastQueueJson`, so the confirming poll snapshot (~500ms later)
// looked "changed" and triggered a SECOND full rebuild + scroll jump.
//
// Fix under test (queue.js):
//   * `reorderQueue` sets `state._suppressQueueScrollUntil` (now + 2500ms),
//     snapshots each queue list's scrollTop before the optimistic render and
//     restores it after, and mirrors the optimistic queue into
//     `window._lastQueueJson` / `window._lastQueueIndex`.
//   * `_scrollQueueRowIntoView` records `lastActiveIndex` but returns early
//     while the suppression window is active (no delayed scroll queues up).
//   * `window.scrollQueueToCurrent` (explicit player-open gesture) clears the
//     suppression window first.
//
// Run: `node flask-server/tests/test_queue_reorder_no_autoscroll.js`

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

// ---- extract _scrollQueueRowIntoView .. scrollQueueToCurrent ----------------
const SCROLL_START = SRC.indexOf('function _scrollQueueRowIntoView');
const SCROLL_END_MARKER = '  _scrollQueueRowIntoView(list, currentIndex, true);\n};';
const SCROLL_END = SRC.indexOf(SCROLL_END_MARKER);
if (SCROLL_START < 0 || SCROLL_END < 0) {
  console.log('FATAL: could not locate the queue scroll helpers in queue.js.');
  process.exit(1);
}
const SCROLL_SRC = SRC.slice(SCROLL_START, SCROLL_END + SCROLL_END_MARKER.length);

// ---- extract reorderQueue ---------------------------------------------------
const REORDER_START_MARKER = '/* ---- Reorder queue (drag complete) ---- */';
const REORDER_END_MARKER = '/* ---- Queue swipe gestures (mobile) ---- */';
const REORDER_START = SRC.indexOf(REORDER_START_MARKER);
const REORDER_END = SRC.indexOf(REORDER_END_MARKER);
if (REORDER_START < 0 || REORDER_END < 0 || REORDER_END <= REORDER_START) {
  console.log('FATAL: could not locate reorderQueue in queue.js.');
  process.exit(1);
}
const REORDER_SRC = SRC.slice(REORDER_START, REORDER_END);

// ---- tiny fake DOM ----------------------------------------------------------

function makeScrollEnv() {
  const scrollCalls = [];
  const container = {
    dataset: {},
    scrollTop: 0,
    getBoundingClientRect: () => ({ top: 0, bottom: 500, left: 0, right: 400, width: 400, height: 500 }),
    scrollTo(opts) { scrollCalls.push(opts); this.scrollTop = opts.top; },
    querySelector(sel) { return sel === '.queue-item.active' ? row : null; },
  };
  const wrapper = {
    getBoundingClientRect: () => ({ top: 720, bottom: 820, left: 0, right: 100, width: 100, height: 100 }),
    closest() { return wrapper; },
  };
  const row = {
    getBoundingClientRect: () => ({ top: 720, bottom: 820, left: 0, right: 100, width: 100, height: 100 }),
    closest() { return wrapper; },
  };
  const appState = { _lastQueueIndex: 4, _suppressQueueScrollUntil: 0 };
  const window = { __appState: appState };
  const sandbox = {
    document: { getElementById(id) { return id === 'np-queue-list' ? container : null; } },
    window,
    state: appState,
    requestAnimationFrame: (fn) => fn(),
    console,
  };
  sandbox.window.scrollQueueToCurrent = undefined;
  const context = vm.createContext(sandbox);
  vm.runInContext(SCROLL_SRC, context, { filename: 'queue-scroll-block.js' });
  return { container, scrollCalls, context, sandbox, appState };
}

async function main() {
  console.log('--- force scroll is suppressed inside the reorder window ---');
  {
    const env = makeScrollEnv();
    env.appState._suppressQueueScrollUntil = Date.now() + 2500;
    env.context._scrollQueueRowIntoView(env.container, 4, true);
    checkTrue('suppressed force scroll does NOT move the viewport', env.scrollCalls.length === 0);
    checkEqual('suppressed call still records the index (no delayed scroll later)',
      env.container.dataset.lastActiveIndex, '4');
  }

  console.log('--- scroll works normally once the window expires ---');
  {
    const env = makeScrollEnv();
    env.appState._suppressQueueScrollUntil = Date.now() - 1;
    env.context._scrollQueueRowIntoView(env.container, 4, true);
    checkTrue('expired window scrolls again', env.scrollCalls.length === 1);
  }

  console.log('--- scrollQueueToCurrent clears a pending suppression ---');
  {
    const env = makeScrollEnv();
    env.appState._suppressQueueScrollUntil = Date.now() + 2500;
    env.sandbox.window.scrollQueueToCurrent();
    checkEqual('explicit player-open clears suppression', env.appState._suppressQueueScrollUntil, 0);
    checkTrue('explicit player-open scrolls to the active row', env.scrollCalls.length === 1);
  }

  console.log('--- reorderQueue holds the drop viewport and mirrors SSE state ---');
  {
    const queue = [
      { video_id: 'A', title: 'Song A' },
      { video_id: 'B', title: 'Song B' },
      { video_id: 'C', title: 'Song C' },
      { video_id: 'D', title: 'Song D' },
    ];
    const appState = {
      _lastQueueJson: JSON.stringify(queue),
      _lastQueueIndex: 0, // A is playing
      _suppressQueueScrollUntil: 0,
    };
    const lists = {
      'np-queue-list': { scrollTop: 400 },
      'queue-list': { scrollTop: 0 },
      'queue-modal-body': { scrollTop: 250 },
    };
    const window = { __appState: appState, _lastQueueJson: appState._lastQueueJson, _lastQueueIndex: 0 };
    const showQueueCalls = [];
    let polledDelay = null;
    const sandbox = {
      state: appState,
      window,
      document: { getElementById(id) { return lists[id] || null; } },
      // Simulate a full rebuild resetting scroll (what renderNpQueue does).
      showQueue(q, idx) {
        showQueueCalls.push({ order: q.map((t) => t.video_id).join(','), idx });
        for (const c of Object.values(lists)) c.scrollTop = 0;
      },
      refreshQueueModalIfOpen() {},
      api: async () => ({}),
      schedulePollNowPlaying(ms) { polledDelay = ms; },
      toast() {},
      console,
    };
    const context = vm.createContext(sandbox);
    vm.runInContext(REORDER_SRC, context, { filename: 'queue-reorder-block.js' });

    // Drag D (index 3) below the playing A -> [A, D, B, C].
    await context.reorderQueue(3, 1);

    checkTrue('suppression window is armed', appState._suppressQueueScrollUntil > Date.now());
    checkEqual('optimistic order applied', showQueueCalls[0] && showQueueCalls[0].order, 'A,D,B,C');
    checkEqual('active index still points at A', appState._lastQueueIndex, 0);
    checkEqual('SSE mirror updated (no phantom confirm rebuild)',
      window._lastQueueJson, appState._lastQueueJson);
    checkEqual('SSE index mirror updated', window._lastQueueIndex, 0);
    checkEqual('np-queue-list kept the drop viewport', lists['np-queue-list'].scrollTop, 400);
    checkEqual('queue-modal-body kept the drop viewport', lists['queue-modal-body'].scrollTop, 250);
    checkEqual('confirm poll still scheduled', polledDelay, 500);

    // The confirming poll snapshot equals the optimistic queue: with the
    // mirror in lockstep there is no "changed" rebuild, and even a forced
    // scroll inside the window must not move the viewport.
    const scrollEnv = makeScrollEnv();
    scrollEnv.appState._suppressQueueScrollUntil = appState._suppressQueueScrollUntil;
    scrollEnv.context._scrollQueueRowIntoView(scrollEnv.container, 0, true);
    checkTrue('confirm-path force scroll suppressed', scrollEnv.scrollCalls.length === 0);
  }

  console.log('--- during-drag auto-scroll stays gentle (source contracts) ---');
  {
    // Ferrying a song a few rows near the visible edge must not send the
    // list drifting: narrow zone, low top speed, dwell before engaging, and
    // the frame loop must re-evaluate (dwell expiry + a container that moved
    // under a stationary finger) instead of scrolling blindly.
    const zone = SRC.match(/const EDGE_ZONE = (\d+);/);
    checkTrue('edge zone is narrow (<= 28px)', zone && Number(zone[1]) <= 28);
    const speed = SRC.match(/const MAX_SPEED = (\d+);/);
    checkTrue('max auto-scroll speed is low (<= 8px/frame)', speed && Number(speed[1]) <= 8);
    const dwell = SRC.match(/const EDGE_DWELL_MS = (\d+);/);
    checkTrue('edge dwell is enforced (>= 100ms)', dwell && Number(dwell[1]) >= 100);
    checkTrue('speed is zeroed until the dwell expires',
      /now - _edgeSince < EDGE_DWELL_MS/.test(SRC));
    checkTrue('leaving the edge resets direction and speed',
      SRC.includes('if (dir === 0) {') && SRC.includes('_edgeDir = 0;') &&
      SRC.includes('_scrollSpeed = 0;'));
    checkTrue('frame loop re-evaluates speed every tick',
      /function tick\(\) \{[\s\S]*?_updateScrollSpeed\(_lastDragClientY\)/.test(SRC));
    checkTrue('endDrag stops the loop and resets edge state',
      /function stopAutoScroll\(\) \{[\s\S]*?_edgeDir = 0;/.test(SRC)
      && /function endDrag\(\) \{[\s\S]*?stopAutoScroll\(\);/.test(SRC));
  }

  console.log('--- rebuild never shrinks the rendered window ---');
  {
    // Live-trace verdict: a FULL-REBUILD rows=50->30 while scrolled deep
    // collapses the list, the browser clamps scrollTop, and the drop gets
    // blamed for the jump. Rebuilds must keep every already-rendered row.
    // (Deliberately no scroll compensation on top: same-scrollTop + same
    // rows already displays the new order correctly — an earlier anchor-glue
    // fought the browser's own scroll anchoring and doubled the shove.)
    checkTrue('np rebuild keeps rendered rows',
      /renderLimit = Math\.min\(queue\.length, Math\.max\(renderLimit, renderedArr\.length\)\)/.test(SRC));
    checkTrue('queue modal rebuild keeps rendered rows',
      /body\.querySelectorAll\(':scope > \.queue-swipe-wrapper'\)\.length\)\)/.test(SRC));
    checkTrue('anchor glue stays removed',
      !/function _glueReorderAnchor/.test(SRC) && !/function _queueVisibleAnchor/.test(SRC));
  }

  console.log('--- drop glides rows home (FLIP) instead of teleporting ---');
  {
    const FLIP_MARKER = SRC.indexOf('/* ---- Reorder scroll helpers (drop render)');
    const FLIP_SRC = SRC.slice(FLIP_MARKER < 0 ? REORDER_START : FLIP_MARKER, REORDER_START);
    const ROW_H = 60, LIST_TOP = 100;
    let rafQueue = [];
    let timeoutQueue = [];
    const sandbox = {
      window: { matchMedia: () => ({ matches: false }) },
      requestAnimationFrame: (fn) => { rafQueue.push(fn); },
      setTimeout: (fn) => { timeoutQueue.push(fn); return timeoutQueue.length; },
      document: {},
      console,
    };
    const context = vm.createContext(sandbox);
    vm.runInContext(FLIP_SRC, context, { filename: 'queue-flip-block.js' });

    function makeFlipContainer(n) {
      // Stable node identities across querySelectorAll calls, like a real DOM
      // (a fresh object per query would hide the styles the flip sets).
      let nodes = [];
      const container = {
        scrollTop: 0,
        scrollHeight: n * ROW_H,
        clientHeight: 600,
        offsetHeight: 600,
        getBoundingClientRect: () => ({ top: LIST_TOP, bottom: LIST_TOP + 600 }),
        querySelectorAll: () => nodes.slice(),
        querySelector: () => null,
      };
      function build(count) {
        nodes = [];
        for (let i = 0; i < count; i++) {
          const node = {
            dataset: { index: String(i) },
            style: {},
            isConnected: true,
            querySelector: () => null,
            getBoundingClientRect: () => {
              const top = LIST_TOP + (Number(node.dataset.index) * ROW_H - container.scrollTop);
              return { top, bottom: top + ROW_H };
            },
          };
          nodes.push(node);
        }
      }
      build(n);
      // Emulate the full rebuild with a new order (fresh data-index values).
      container.rebuild = (newOrder) => build(newOrder.length);
      return container;
    }

    // Move S7 (index 7) up to index 2.
    const container = makeFlipContainer(10);
    const first = context._captureRowTops(container);
    checkEqual('captured one top per rendered row', first.size, 10);
    container.rebuild([0, 1, 7, 2, 3, 4, 5, 6, 8, 9]);
    rafQueue = []; timeoutQueue = [];
    context._flipReorder(container, first, 7, 2);
    // Inversion applied synchronously; glide queued on rAF.
    const inverted = container.querySelectorAll().filter((w) => w.style.transform);
    checkTrue('moved range is inverted before paint', inverted.length === 6);
    // The dropped row (now index 2) starts 300px below its slot and glides up.
    const dropped = container.querySelectorAll().find((w) => w.dataset.index === '2');
    checkEqual('dropped row inverts from its old position',
      dropped && dropped.style.transform, 'translateY(300px)');
    // Untouched rows never get a transform.
    const untouched = container.querySelectorAll().filter((w) => ['0', '1', '8', '9'].includes(w.dataset.index));
    checkTrue('rows outside the move stay put', untouched.every((w) => !w.style.transform));
    // rAF fires: transitions run, transforms released.
    rafQueue.forEach((fn) => fn());
    const gliding = container.querySelectorAll().filter((w) => w.style.transition);
    checkTrue('glide transitions run on the moved range', gliding.length === 6);
    checkTrue('transforms release for the glide',
      container.querySelectorAll().every((w) => (w.style.transform || '') === ''));
    // Cleanup timer clears inline styles so later gestures start clean.
    checkTrue('cleanup timer scheduled', timeoutQueue.length === 1);
    timeoutQueue.forEach((fn) => fn());
    checkTrue('inline styles cleared after glide',
      container.querySelectorAll().every((w) => (w.style.transition || '') === '' && (w.style.transform || '') === ''));
  }

  console.log('--- drag reserves landing space (source collapses, full-row gap) ---');  {
    // The user's fix: at lift the source slot collapses and the target shows
    // a full-row gap, so the list already displays the final order mid-drag
    // and the release render swaps a same-size gap for the song (~zero
    // layout delta, no down/up shove on place).
    checkTrue('drop targets exclude the collapsed source',
      /\.queue-swipe-wrapper:not\(\.drag-source\)/.test(SRC));
    checkTrue('lift collapses the source row',
      /el\.style\.display = 'none'/.test(SRC) && /classList\.add\('drag-source'\)/.test(SRC));
    checkTrue('gap reserves the dragged row height',
      /placeholder\.style\.height = draggedHeight/.test(SRC));
    checkTrue('drop index needs no -1 fiddle (already post-removal)',
      !/toIdx -= 1/.test(SRC));
    checkTrue('release restores the source row for the no-op path',
      /classList\.remove\('drag-source'\)/.test(SRC) && /el\.style\.display = ''/.test(SRC));
  }

  console.log('--- drop moves the live row (no rebuild) ---');
  {
    const FLIP_MARKER = SRC.indexOf('/* ---- Reorder scroll helpers (drop render)');
    const HELP_SRC = SRC.slice(FLIP_MARKER < 0 ? REORDER_START : FLIP_MARKER, REORDER_START);
    const sandbox = {
      state: { isPlaying: true },
      document: {},
      console,
    };
    const context = vm.createContext(sandbox);
    vm.runInContext(HELP_SRC, context, { filename: 'queue-move-block.js' });
    checkTrue('DOM-move helper extracted', typeof context._moveQueueRowDom === 'function');

    function mkRow(i) {
      const item = {
        dataset: { index: String(i) },
        _cls: {},
        classList: { toggle(c, on) { item._cls[c] = !!on; } },
      };
      const node = {
        dataset: { index: String(i) },
        _item: item,
        _num: { textContent: String(i + 1) },
        _rm: { hidden: false },
        querySelector(sel) {
          if (!sel) return null;
          if (sel.includes('.queue-item')) return item;
          if (sel.includes('.queue-num')) return node._num;
          if (sel.includes('remove')) return node._rm;
          return null;
        },
      };
      return node;
    }
    function mkList(n, activeIdx) {
      let nodes = [];
      for (let i = 0; i < n; i++) {
        const node = mkRow(i);
        if (i === activeIdx) node._item._cls.active = true;
        nodes.push(node);
      }
      return {
        dataset: {},
        scrollTop: 0,
        scrollHeight: n * 60,
        clientHeight: 300,
        _lazyQueue: null,
        querySelectorAll(sel) { return sel.includes('.queue-swipe-wrapper') ? nodes.slice() : []; },
        querySelector(sel) { return sel.includes('sentinel') ? null : null; },
        insertBefore(node, ref) {
          nodes = nodes.filter((x) => x !== node);
          const k = ref ? nodes.indexOf(ref) : -1;
          if (k < 0) nodes.push(node); else nodes.splice(k, 0, node);
        },
        appendChild(node) { nodes = nodes.filter((x) => x !== node); nodes.push(node); },
        _nodes() { return nodes; },
      };
    }
    const ids = Array.from({ length: 10 }, (_, i) => ({ video_id: 'S' + i }));

    // Move down: S2 (idx 2) -> idx 7 with idx 0 active.
    {
      const c = mkList(10, 0);
      const moved = c._nodes()[2];
      const ok = context._moveQueueRowDom(c, ids, 2, 7, 0);
      checkTrue('down move handled without rebuild', ok === true);
      checkEqual('moved row sits at target', c._nodes().indexOf(moved), 7);
      checkTrue('rows renumbered in place',
        c._nodes().every((w, i) => w.dataset.index === String(i) && w._num.textContent === String(i + 1)));
      checkTrue('highlight follows the active index, not the moved row',
        c._nodes()[0]._item._cls.active === true && c._nodes()[7]._item._cls.active !== true);
      checkTrue('remove hidden exactly on the active row',
        c._nodes().every((w, i) => w._rm.hidden === (i === 0)));
      checkEqual('active guard index recorded', c.dataset.lastActiveIndex, '0');
      checkTrue('lazy state updated', !!c._lazyQueue && c._lazyQueue.currentIndex === 0);
    }
    // Move up: S7 -> idx 2 with idx 9 active.
    {
      const c = mkList(10, 9);
      const moved = c._nodes()[7];
      const ok = context._moveQueueRowDom(c, ids, 7, 2, 9);
      checkTrue('up move handled without rebuild', ok === true);
      checkEqual('moved row sits at target', c._nodes().indexOf(moved), 2);
      checkTrue('active highlight at the tail',
        c._nodes()[9]._item._cls.active === true && c._nodes()[2]._item._cls.active !== true);
    }
    // Fallbacks: unknown row / empty list / bad index -> rebuild path.
    {
      const c = mkList(10, 0);
      checkTrue('missing row falls back', context._moveQueueRowDom(c, ids, 99, 3, 0) === false);
      const empty = mkList(0, -1);
      checkTrue('empty list falls back', context._moveQueueRowDom(empty, [], 0, 0, 0) === false);
      checkTrue('out-of-range target falls back', context._moveQueueRowDom(c, ids, 2, 99, 0) === false);
      checkTrue('null container falls back', context._moveQueueRowDom(null, ids, 2, 3, 0) === false);
    }
    // Wiring: reorder prefers the DOM move; rows read live positions.
    checkTrue('reorder commits via DOM move first',
      /_moveQueueRowDom\(_npList/.test(SRC) && /if \(!_npHandled\) showQueue\(/.test(SRC));
    checkTrue('drag/tap/menu read live row positions',
      (SRC.match(/Number\(el\.dataset\.index\)/g) || []).length >= 3);
  }

  console.log(`\nqueue-reorder-no-autoscroll: passed=${passed} failed=${failed}`);
  process.exit(failed ? 1 : 0);
}

main().catch((e) => { console.log('FATAL: ' + (e && e.stack || e)); process.exit(1); });
