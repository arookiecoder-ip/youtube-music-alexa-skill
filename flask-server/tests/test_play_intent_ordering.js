// Regression test for the rapid-click UI flap in the web remote.
//
// Bug (reported): clicking song A, then B, then C showed C, then B, then C
// again a few moments later. Clicking rapidly down the queue made the
// selected-row highlight glitch the same way.
//
// Cause: each play path did `await api('/alexa/play_queue/', ...)` and only
// *then* rendered its optimistic now-playing state. Response times for that
// endpoint range from ~400ms to several seconds (radio/playlist expansion), so
// responses complete out of order and an earlier click's late response
// overwrote a later click's UI. Independently, `handleNpUpdate` applied every
// server snapshot unconditionally, and the server needs a moment to catch up
// after each click — so its in-flight SSE pushes and poll responses re-rendered
// the previous track.
//
// Fix under test: `_playIntentSeq` / `_playIntentVideoId` in ui-state.js (the
// same "superseded by a later click" pattern the volume controls already use
// via `_volCommandSeq`), consumed by the play paths and by handleNpUpdate.
//
// Run: `node flask-server/tests/test_play_intent_ordering.js`

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const JS_DIR = path.join(__dirname, '..', 'templates', 'static', 'js');

let passed = 0;
let failed = 0;

function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (ok) {
    passed += 1;
    console.log(`PASS  ${name}`);
  } else {
    failed += 1;
    console.log(`FAIL  ${name}\n        expected ${JSON.stringify(expected)}\n        actual   ${JSON.stringify(actual)}`);
  }
}

function checkTrue(name, actual, hint) {
  if (actual) {
    passed += 1;
    console.log(`PASS  ${name}`);
  } else {
    failed += 1;
    console.log(`FAIL  ${name}${hint ? '\n        ' + hint : ''}`);
  }
}

/* ------------------------------------------------------------------ *
 * Minimal browser shim. Only what ui-state.js and sse.js touch at
 * load time, plus the render hooks we want to observe.
 * ------------------------------------------------------------------ */

function makeContext() {
  const renders = { nowPlaying: [], queueActive: [], volume: [], progress: [] };
  const noop = () => {};

  const fakeClassList = () => ({
    add: noop, remove: noop, toggle: noop, contains: () => false,
  });
  const fakeEl = (id) => ({
    id: id || '', hidden: false, value: 'SERIAL123',
    classList: fakeClassList(),
    style: { setProperty: noop, removeProperty: noop, paddingRight: '' },
    dataset: {}, textContent: '',
    addEventListener: noop, removeEventListener: noop,
    querySelectorAll: () => [], querySelector: () => null,
    closest: () => ({ style: { removeProperty: noop }, classList: fakeClassList() }),
    getBoundingClientRect: () => ({ top: 0, bottom: 0, height: 0 }),
  });

  const sandbox = {
    console,
    setTimeout, clearTimeout, setInterval, clearInterval,
    JSON, Date, Math, Number, String, Object, Array, Boolean, Error, isNaN,
    performance: { now: () => Date.now() },
    requestAnimationFrame: (fn) => { fn(); return 1; },
    MutationObserver: class { observe() {} disconnect() {} },
    ResizeObserver: class { observe() {} disconnect() {} },
    EventSource: class { constructor() {} close() {} addEventListener() {} },
    getComputedStyle: () => ({
      getPropertyValue: () => '', transform: 'none', visibility: 'visible',
    }),
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;

  sandbox.document = {
    body: { classList: fakeClassList(), style: { setProperty: noop, removeProperty: noop }, dataset: {}, className: '' },
    documentElement: { style: { setProperty: noop, removeProperty: noop }, clientWidth: 1000, scrollTop: 0 },
    getElementById: (id) => fakeEl(id),
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: noop, removeEventListener: noop,
    readyState: 'complete', hidden: false,
  };
  sandbox.matchMedia = () => ({ matches: false, addEventListener: noop, addListener: noop });
  sandbox.location = { hash: '#home' };
  sandbox.localStorage = { getItem: () => null, setItem: noop };
  sandbox.innerWidth = 1000;
  sandbox.addEventListener = noop;
  sandbox.removeEventListener = noop;

  // Render hooks we assert on.
  sandbox.showNowPlaying = (info) => {
    renders.nowPlaying.push(info && info.video_id ? info.video_id : null);
  };
  sandbox.updateQueueActive = (i) => { renders.queueActive.push(i); };
  sandbox.updateQueueModalActive = (i) => { renders.queueActive.push(i); };
  sandbox.syncVolume = (v) => { renders.volume.push(v); };
  sandbox.syncPlayPause = noop;
  sandbox.getRoute = () => '#home';
  sandbox.progress = {
    update: (np) => renders.progress.push(np),
    resetPending: noop,
    syncLoop: noop,
  };
  sandbox.toast = noop;
  sandbox.api = async () => ({});
  sandbox.showQueue = noop;
  sandbox.renderNpQueue = noop;
  sandbox.loadHistory = noop;
  sandbox.checkLikedVersion = noop;
  sandbox._cacheNowPlaying = noop;
  sandbox.selectedDeviceOnline = () => true;
  sandbox.refreshVolume = noop;
  sandbox.JAM_GUEST = false;

  const context = vm.createContext(sandbox);
  for (const file of ['ui-state.js', 'sse.js']) {
    vm.runInContext(fs.readFileSync(path.join(JS_DIR, file), 'utf8'), context,
                    { filename: file });
  }
  return { sandbox, renders };
}

/* ------------------------------------------------------------------ *
 * The intent primitives
 * ------------------------------------------------------------------ */

console.log('--- play-intent primitives ---');
{
  const { sandbox } = makeContext();
  const s = sandbox.__appState;

  const a = sandbox.beginPlayIntent('AAAAAAAAAAA');
  const b = sandbox.beginPlayIntent('BBBBBBBBBBB');
  const c = sandbox.beginPlayIntent('CCCCCCCCCCC');

  check('sequence increments per click', [a, b, c], [a, a + 1, a + 2]);
  checkTrue('newest click is current', sandbox.isCurrentPlayIntent(c));
  checkTrue('older click A is superseded', !sandbox.isCurrentPlayIntent(a));
  checkTrue('older click B is superseded', !sandbox.isCurrentPlayIntent(b));
  check('intent tracks the newest video_id', s._playIntentVideoId, 'CCCCCCCCCCC');

  checkTrue('a stale snapshot for A is outranked',
            sandbox.playIntentSupersedes('AAAAAAAAAAA'),
            'server state for a clicked-away track must not re-render');
  checkTrue('a snapshot for the intended track is NOT outranked',
            !sandbox.playIntentSupersedes('CCCCCCCCCCC'),
            'authoritative state for the intended track must always apply');
  checkTrue('an empty video_id is never outranked',
            !sandbox.playIntentSupersedes(null));

  // Expiry is the safety valve. Both the intent and the abandoned-track
  // records age off the same clock, so simulate real elapsed time by rewinding
  // each of them (production has no separate knob).
  const rewind = s.PLAY_INTENT_GRACE_MS + 100;
  s._playIntentAt = Date.now() - rewind;
  for (const [videoId, at] of sandbox.__abandonedPlayIntents) {
    sandbox.__abandonedPlayIntents.set(videoId, at - rewind);
  }
  checkTrue('intent expires so server state can take over again',
            !sandbox.playIntentSupersedes('AAAAAAAAAAA'),
            'a failed play must not freeze the UI forever');

  // Settling releases the intent so later genuine changes apply at once.
  sandbox.beginPlayIntent('CCCCCCCCCCC');
  sandbox.settlePlayIntent('CCCCCCCCCCC');
  checkTrue('settling releases the intent',
            !sandbox.playIntentSupersedes('DDDDDDDDDDD'),
            'the queue advancing on its own must not be blocked');
  checkTrue('settling a different id leaves the intent intact', (() => {
    sandbox.beginPlayIntent('CCCCCCCCCCC');
    sandbox.settlePlayIntent('ZZZZZZZZZZZ');
    return sandbox.playIntentSupersedes('AAAAAAAAAAA');
  })());
}

/* ------------------------------------------------------------------ *
 * handleNpUpdate: stale server snapshots must not re-render
 * ------------------------------------------------------------------ */

console.log('\n--- handleNpUpdate vs stale server state ---');
{
  const { sandbox, renders } = makeContext();
  sandbox.beginPlayIntent('CCCCCCCCCCC');

  // The server is still reporting B when the SSE push lands.
  sandbox.handleNpUpdate({
    video_id: 'BBBBBBBBBBB', title: 'Song B', playing: true, volume: 40,
  });
  check('stale snapshot did not re-render the banner', renders.nowPlaying, []);
  check('stale snapshot still synced volume (track-independent)',
        renders.volume, [40]);

  // Then the server catches up to C.
  sandbox.handleNpUpdate({
    video_id: 'CCCCCCCCCCC', title: 'Song C', playing: true,
  });
  check('the intended track is rendered when it arrives',
        renders.nowPlaying, ['CCCCCCCCCCC']);

  // After settling, a genuine advance to the next track applies immediately.
  sandbox.handleNpUpdate({
    video_id: 'DDDDDDDDDDD', title: 'Song D', playing: true,
  });
  check('a later genuine track change is applied',
        renders.nowPlaying, ['CCCCCCCCCCC', 'DDDDDDDDDDD']);

  // Cross-device/Alexa snapshots must reach the shared progress controller;
  // that controller owns the processing-state decision for every client.
  const progressBeforeExternal = renders.progress.length;
  sandbox.handleNpUpdate({
    video_id: 'DDDDDDDDDDD', title: 'Song D', playing: true,
    playback_confirmed: false, position_ms: 0,
  });
  sandbox.handleNpUpdate({
    video_id: 'DDDDDDDDDDD', title: 'Song D', playing: true,
    playback_confirmed: true, position_ms: 0,
  });
  check('SSE forwards external preparing and confirmed snapshots to progress',
        renders.progress.slice(progressBeforeExternal).map((snapshot) => !!snapshot.playback_confirmed),
        [false, true]);
}

/* ------------------------------------------------------------------ *
 * The exact reported sequence, with out-of-order responses
 * ------------------------------------------------------------------ */

console.log('\n--- reported sequence: click A, B, C with out-of-order responses ---');
{
  const { sandbox, renders } = makeContext();

  // Faithful stand-in for the fixed play path: claim + render synchronously,
  // then discard the post-await work if a newer click has superseded it.
  async function play(videoId, responseDelayMs) {
    const mySeq = sandbox.beginPlayIntent(videoId);
    sandbox.showNowPlaying({ video_id: videoId });
    await new Promise((r) => setTimeout(r, responseDelayMs));
    if (!sandbox.isCurrentPlayIntent(mySeq)) return 'discarded';
    return 'applied';
  }

  // C is clicked last but its response returns FIRST; B's slow response
  // (playlist/radio expansion) lands afterwards. This is what produced
  // "shows C, then B, then C again".
  const results = [];
  const clicks = [
    play('AAAAAAAAAAA', 120).then((r) => results.push(['A', r])),
    play('BBBBBBBBBBB', 90).then((r) => results.push(['B', r])),
    play('CCCCCCCCCCC', 10).then((r) => results.push(['C', r])),
  ];

  (async () => {
    await Promise.all(clicks);

    check('banner followed click order, not response order',
          renders.nowPlaying,
          ['AAAAAAAAAAA', 'BBBBBBBBBBB', 'CCCCCCCCCCC']);
    checkTrue('C (last click) was applied',
              results.some(([k, v]) => k === 'C' && v === 'applied'));
    checkTrue('A was discarded as superseded',
              results.some(([k, v]) => k === 'A' && v === 'discarded'));
    checkTrue('B was discarded as superseded',
              results.some(([k, v]) => k === 'B' && v === 'discarded'),
              "B's late response must not overwrite C — this is the reported flap");

    // And the server's lagging snapshots for A/B must not undo C either.
    sandbox.handleNpUpdate({ video_id: 'AAAAAAAAAAA', title: 'A', playing: true });
    sandbox.handleNpUpdate({ video_id: 'BBBBBBBBBBB', title: 'B', playing: true });
    check('lagging server snapshots did not re-render A or B',
          renders.nowPlaying,
          ['AAAAAAAAAAA', 'BBBBBBBBBBB', 'CCCCCCCCCCC']);

    /* -------------------------------------------------------------- *
     * Queue highlight: rapid clicks down the queue
     * -------------------------------------------------------------- */
    console.log('\n--- queue highlight under rapid clicks ---');
    const q = makeContext();

    async function playRow(videoId, index, responseDelayMs) {
      const mySeq = q.sandbox.beginPlayIntent(videoId);
      q.sandbox.updateQueueActive(index);
      await new Promise((r) => setTimeout(r, responseDelayMs));
      if (!q.sandbox.isCurrentPlayIntent(mySeq)) return;
      q.sandbox.updateQueueActive(index);
    }

    await Promise.all([
      playRow('R1111111111', 3, 100),
      playRow('R2222222222', 7, 60),
      playRow('R3333333333', 11, 5),
    ]);

    check('highlight moved in click order', q.renders.queueActive, [3, 7, 11, 11]);
    checkTrue('highlight settled on the last clicked row',
              q.renders.queueActive[q.renders.queueActive.length - 1] === 11,
              'an earlier row must not steal the highlight back');

    // A lagging server snapshot pointing at an old row must not move it.
    q.sandbox.handleNpUpdate({
      video_id: 'R2222222222', title: 'Row 2', playing: true,
      queue: [{ video_id: 'R2222222222' }], queue_index: 0,
    });
    check('lagging snapshot did not move the highlight',
          q.renders.queueActive, [3, 7, 11, 11]);

    /* -------------------------------------------------------------- *
     * Source-order guards on the REAL play paths.
     *
     * The behavioural tests above drive the intent primitives through a
     * stand-in for the play path, because player.js / queue.js need a far
     * heavier DOM than this shim provides. These static checks cover the part
     * that a stand-in cannot: that the real functions actually claim the intent
     * and paint *before* their await, and re-check it after. Moving the render
     * back below the await is precisely the original bug.
     * -------------------------------------------------------------- */
    console.log('\n--- source order in the real play paths ---');

    function fnBody(src, startMarker) {
      const start = src.indexOf(startMarker);
      if (start < 0) return null;
      // Walk braces from the function's opening brace to its close.
      let i = src.indexOf('{', start);
      if (i < 0) return null;
      let depth = 0;
      for (let j = i; j < src.length; j += 1) {
        if (src[j] === '{') depth += 1;
        else if (src[j] === '}') {
          depth -= 1;
          if (depth === 0) return src.slice(start, j + 1);
        }
      }
      return null;
    }

    const paths = [
      ['player.js', 'async function playResult(', true],
      ['queue.js', 'async function playFromQueue(', true],
      // The collection path resolves its first track from the response, so it
      // has nothing to paint synchronously — but it must still sequence.
      ['queue.js', 'async function playCollection(', false],
    ];

    for (const [file, marker, expectSyncRender] of paths) {
      const src = fs.readFileSync(path.join(JS_DIR, file), 'utf8');
      const body = fnBody(src, marker);
      checkTrue(`${file}: found ${marker.slice(0, 40)}`, !!body);
      if (!body) continue;

      const begin = body.indexOf('beginPlayIntent');
      const awaitApi = body.indexOf("await api('/alexa/play_queue/'");
      const recheck = body.indexOf('isCurrentPlayIntent');

      checkTrue(`${file}: claims the intent before the await`,
                begin >= 0 && awaitApi > begin,
                'beginPlayIntent must run synchronously at click time');
      checkTrue(`${file}: re-checks the intent after the await`,
                recheck > awaitApi,
                'without this, an earlier click\'s late response overwrites a newer one');

      // The re-check must gate the success path, not merely exist somewhere
      // below (an `isCurrentPlayIntent` in the catch block alone would satisfy
      // the looser assertion above while leaving the success path unguarded).
      const successSideEffect = body.indexOf('schedulePollNowPlaying(');
      checkTrue(`${file}: the re-check gates the success path`,
                recheck > awaitApi && successSideEffect > recheck,
                'the supersede check must run before the success-path side '
                + 'effects (toast / navigate / poll), or a stale response still '
                + 'takes over the UI');

      if (expectSyncRender) {
        const render = body.indexOf('showNowPlaying(');
        checkTrue(`${file}: paints now-playing before the await`,
                  render >= 0 && render < awaitApi,
                  'rendering after the await makes the UI follow response order, '
                  + 'which is the reported "C, then B, then C again" flap');
      }

      // The server can only order a rapid-click burst if the client tells it
      // which click this is. Without intent_seq in the payload every request
      // claims, dispatches, and the Echo plays all of them in turn.
      const callArgs = body.slice(awaitApi, body.indexOf('});', awaitApi));
      checkTrue(`${file}: sends intent_seq so the server can drop stale clicks`,
                callArgs.indexOf('intent_seq') >= 0
                && /intent_seq:\s*mySeq/.test(callArgs),
                'the POST body must carry this click\'s sequence number');
    }

    const sseSrc = fs.readFileSync(path.join(JS_DIR, 'sse.js'), 'utf8');
    const npBody = fnBody(sseSrc, 'function handleNpUpdate(');
    checkTrue('sse.js: handleNpUpdate guards on playIntentSupersedes',
              !!npBody && npBody.indexOf('playIntentSupersedes') >= 0,
              'without the guard, lagging server snapshots re-render the old track');
    checkTrue('sse.js: the guard precedes the banner render',
              !!npBody
              && npBody.indexOf('playIntentSupersedes') < npBody.indexOf('showNowPlaying'),
              'the guard must short-circuit before any track rendering');

    /* -------------------------------------------------------------- *
     * Failure path: an optimistic paint must be undone
     * -------------------------------------------------------------- */
    console.log('\n--- failed play rolls back the optimistic paint ---');
    {
      const f = makeContext();
      const s = f.sandbox.__appState;

      async function playFailing(videoId) {
        const mySeq = f.sandbox.beginPlayIntent(videoId);
        f.sandbox.showNowPlaying({ video_id: videoId });
        s.isPlaying = true;
        try {
          throw new Error('device unreachable');
        } catch (e) {
          if (!f.sandbox.isCurrentPlayIntent(mySeq)) return;
          f.sandbox.settlePlayIntent(videoId);
          s.isPlaying = false;
          s.lastActionIntent = false;
        }
      }

      await playFailing('FFFFFFFFFFF');
      checkTrue('isPlaying reset after a failed play', s.isPlaying === false,
                'the play/pause button must not claim playback that never started');
      checkTrue('intent released after a failed play',
                !f.sandbox.playIntentSupersedes('OTHERTRACK1'),
                'a failed play must not keep outranking real server state');

      // Server state now flows again and corrects the UI.
      f.sandbox.handleNpUpdate({ video_id: 'OTHERTRACK1', title: 'Real', playing: true });
      check('server state corrects the UI after a failure',
            f.renders.nowPlaying, ['FFFFFFFFFFF', 'OTHERTRACK1']);

      // Source guard: both play paths must actually roll back.
      for (const [file, marker] of [['player.js', 'async function playResult('],
                                    ['queue.js', 'async function playFromQueue(']]) {
        const body = fnBody(fs.readFileSync(path.join(JS_DIR, file), 'utf8'), marker);
        const catchIdx = body.lastIndexOf('} catch');
        const tail = catchIdx >= 0 ? body.slice(catchIdx) : '';
        checkTrue(`${file}: failure path resets isPlaying`,
                  /state\.isPlaying\s*=\s*false/.test(tail),
                  'painting before the await requires undoing it on failure');
        checkTrue(`${file}: failure path releases the intent`,
                  tail.indexOf('settlePlayIntent') >= 0);
      }
    }

    /* -------------------------------------------------------------- *
     * The five-click report: newest snapshot first, older ones after
     * -------------------------------------------------------------- */
    console.log('\n--- 5 clicks: server publishes 5 first, then 1,2,3,4 ---');
    {
      const g = makeContext();
      const ids = ['S1111111111', 'S2222222222', 'S3333333333',
                   'S4444444444', 'S5555555555'];
      // Five rapid clicks; the UI paints each synchronously.
      for (const id of ids) {
        g.sandbox.beginPlayIntent(id);
        g.sandbox.showNowPlaying({ video_id: id });
      }
      check('paints followed click order', g.renders.nowPlaying, ids);

      // Concurrent server handlers publish out of order: the newest lands
      // first (settling the intent), then the four stragglers arrive. This is
      // the exact reported regression: "shows 5, then jumps back to 1, 2, 3, 4".
      g.sandbox.handleNpUpdate({ video_id: 'S5555555555', title: '5', playing: true });
      for (const id of ['S1111111111', 'S2222222222', 'S3333333333', 'S4444444444']) {
        g.sandbox.handleNpUpdate({ video_id: id, title: id, playing: true });
      }
      check('no jump back through the abandoned tracks',
            g.renders.nowPlaying, ids.concat(['S5555555555']));

      // A genuine advance to a brand-new track still applies.
      g.sandbox.handleNpUpdate({ video_id: 'S6666666666', title: '6', playing: true });
      check('a genuine next track still applies',
            g.renders.nowPlaying, ids.concat(['S5555555555', 'S6666666666']));
    }

    console.log('\n--- re-clicking an abandoned track makes it wanted again ---');
    {
      const h = makeContext();
      h.sandbox.beginPlayIntent('T1111111111');
      h.sandbox.beginPlayIntent('T2222222222');   // T1 abandoned
      checkTrue('T1 is rejected while abandoned',
                h.sandbox.playIntentSupersedes('T1111111111'));
      h.sandbox.beginPlayIntent('T1111111111');   // user clicks back to T1
      checkTrue('T1 is accepted again after being re-clicked',
                !h.sandbox.playIntentSupersedes('T1111111111'),
                'clicking back to an earlier song must work immediately');
      h.sandbox.handleNpUpdate({ video_id: 'T1111111111', title: 'T1', playing: true });
      check('re-clicked track renders', h.renders.nowPlaying, ['T1111111111']);
    }

    console.log(`\nplay-intent ordering: passed=${passed} failed=${failed}`);
    process.exit(failed ? 1 : 0);
  })();
}
