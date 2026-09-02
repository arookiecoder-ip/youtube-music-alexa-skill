// Behavioural harness for the mobile swipe-to-skip banner reveal.
//
// The swipe IIFE (wireMobileNowPlayingSwipe) is sliced out of player.js and run
// in a vm sandbox with a fake DOM, then driven with synthetic pointer events to
// prove the runtime behaviour:
//   - dragging the artwork reveals the adjacent track's cover beside it,
//   - a committed swipe settles that cover into place, promotes the adjacent
//     track onto the artwork (cover + title + artist) and fires the nav,
//   - an under-threshold drag springs back and tucks the cover away.
//
// Run: `node flask-server/tests/test_mobile_now_playing_swipe.js`

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.join(__dirname, '..');
const playerSrc = fs.readFileSync(
  path.join(root, 'templates', 'static', 'js', 'player.js'), 'utf8');

let passed = 0;
let failed = 0;
function check(name, condition, detail) {
  if (condition) {
    passed += 1;
    console.log(`PASS  ${name}`);
  } else {
    failed += 1;
    console.error(`FAIL  ${name}${detail ? `\n      ${detail}` : ''}`);
  }
}

// Slice out the swipe handler IIFE: from `(function wireMobileNowPlayingSwipe() {`
// up to (but excluding) the next top-level section comment `/* ---- volume ---- */`.
const startMarker = '(function wireMobileNowPlayingSwipe() {';
const endMarker = '/* ---- volume ---- */';
const start = playerSrc.indexOf(startMarker);
const end = playerSrc.indexOf(endMarker, start);
if (start < 0 || end < 0) {
  console.error('FATAL: could not locate the swipe IIFE in player.js');
  process.exit(1);
}
const SWIPE_SRC = playerSrc.slice(start, end);

// ---------------------------------------------------------------- fake DOM --

function makeClassList(initial) {
  const set = new Set(initial || []);
  return {
    contains: (c) => set.has(c),
    add: (...cs) => cs.forEach((c) => set.add(c)),
    remove: (...cs) => cs.forEach((c) => set.delete(c)),
    toggle: (c, force) => {
      const want = force === undefined ? !set.has(c) : !!force;
      if (want) set.add(c); else set.delete(c);
      return want;
    },
  };
}

function makeEl(id, parent) {
  const el = {
    id: id || '',
    className: '',
    parentElement: parent || null,
    children: [],
    style: {},
    dataset: {},
    classList: makeClassList(),
    listeners: {},
    rect: { top: 0, left: 0, width: 265, height: 265 },
    clientWidth: 265,
    offsetWidth: 265,
    isConnected: true,
    hidden: false,
    textContent: '',
    clicks: 0,
    addEventListener(type, fn) {
      (this.listeners[type] = this.listeners[type] || []).push(fn);
    },
    removeEventListener(type, fn) {
      this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn);
    },
    setPointerCapture() {},
    releasePointerCapture() {},
    matches() { return false; },
    closest() { return null; },
    getBoundingClientRect() { return this.rect; },
    click() { this.clicks += 1; },
    appendChild(child) { child.parentElement = this; this.children.push(child); },
    insertBefore(child) { child.parentElement = this; this.children.push(child); },
    removeChild(child) {
      this.children = this.children.filter((c) => c !== child);
      if (child.parentElement === this) child.parentElement = null;
    },
    setAttribute() {},
  };
  return el;
}

// ------------------------------------------------------------- swipe sandbox --

function makeEnv(opts) {
  const queue = opts.queue || [];
  const queueIndex = opts.queueIndex != null ? opts.queueIndex : 0;

  const leftPanel = makeEl('np-page-left');
  leftPanel.rect = { top: 100, left: 0, width: 390, height: 620 };
  const art = makeEl('np-page-art', leftPanel);
  art.rect = { top: 200, left: 62, width: 265, height: 265 };
  leftPanel.children.push(art);
  const pageTitle = makeEl('np-page-title');
  const pageArtist = makeEl('np-page-artist');
  const queueModal = null;
  const nextBtn = makeEl('nav-next');
  const prevBtn = makeEl('nav-prev');
  const body = makeEl('body');
  body.classList = makeClassList(['now-playing-route']);
  body.appendChild = function childOfBody(child) { child.parentElement = body; };

  const timeouts = [];
  const rafs = [];

  // Rows as renderNpQueue would build them (wrapper -> item -> text/thumbs).
  const domQueueRows = (opts.domQueue || []).map((r) => ({
    dataset: { videoId: r.video_id, index: String(r.index) },
    _songContextTrack: {
      video_id: r.video_id, title: r.title, artist: r.artist, thumbnail: r.thumbnail,
    },
    querySelector(sel) {
      if (sel === '.queue-title') return { textContent: r.title };
      if (sel === '.queue-artist') return { textContent: r.artist };
      if (sel === 'img.queue-thumb') return { src: r.thumbnail };
      if (sel === '.queue-item') return { classList: { contains: (c) => c === 'active' && !!r.active } };
      return null;
    },
  }));
  const npQueueList = { querySelectorAll: (sel) => (sel === '.queue-swipe-wrapper' ? domQueueRows : []) };

  const document = {
    hidden: false,
    body,
    getElementById(id) {
      if (id === 'np-page-art') return art;
      if (id === 'np-page-title') return pageTitle;
      if (id === 'np-page-artist') return pageArtist;
      if (id === 'queue-modal-overlay') return queueModal;
      if (id === 'np-swipe-indicator') return null;
      if (id === 'np-queue-list') return opts.domQueue && opts.domQueue.length ? npQueueList : null;
      if (id === 'np-swipe-incoming') {
        return leftPanel.children.find((c) => c.id === 'np-swipe-incoming') || null;
      }
      return null;
    },
    createElement() { return makeEl(''); },
    querySelector(sel) {
      if (sel.startsWith('button[data-action="')) return sel.includes('next') ? nextBtn : prevBtn;
      return null;
    },
    querySelectorAll(sel) {
      // canStart(): both nav buttons must be enabled.
      if (sel.indexOf('data-action="previous"') >= 0) return [prevBtn, nextBtn];
      return [];
    },
  };

  const matchMedia = (q) => ({
    matches: q.indexOf('min-width: 900px') >= 0 ? opts.desktop || false
      : q.indexOf('max-width: 899px') >= 0 ? !(opts.desktop || false)
      : false,
  });

  const windowStub = {
    matchMedia,
    addEventListener() {},
  };

  const appState = {
    isPlaying: false,
    _currentVideoId: queue[queueIndex] ? queue[queueIndex].video_id : '',
    _lastQueueJson: JSON.stringify(queue),
    _lastQueueIndex: queueIndex,
  };

  let now = 0;
  const clock = { now: () => now, advance: (ms) => { now += ms; } };

  const sandbox = {
    window: windowStub,
    document,
    navigator: { vibrate() {} },
    performance: { now: () => clock.now() },
    setTimeout(fn) { timeouts.push(fn); return timeouts.length; },
    clearTimeout() {},
    requestAnimationFrame(fn) { rafs.push(fn); return rafs.length; },
    // Shared file-scope helpers the IIFE relies on.
    _swipeOptimisticVideoId: '',
    _swipeOptimisticAt: 0,
    _swipeOptimisticId: '',
    _resolvedNowPlayingArt,
    resolveNowPlayingArtwork: (id) => Promise.resolve(''),
    state: appState,
    escHtml: (s) => String(s == null ? '' : s),
    console,
  };
  sandbox.window.__appState = appState;
  sandbox.__appState = appState;

  const context = vm.createContext(sandbox);
  let runError = null;
  try {
    vm.runInContext(SWIPE_SRC, context, { filename: 'swipe-iife.js' });
  } catch (e) {
    runError = e;
  }

  // Synthetic pointer events on the artwork. Every dispatched event advances
  // the sandbox clock by `dtMs` so velocity is computed like the real browser.
  function dispatch(type, x, y, optsEv = {}, dtMs = 16) {
    clock.advance(dtMs);
    const e = Object.assign({
      pointerId: 1,
      pointerType: 'touch',
      clientX: x,
      clientY: y,
      button: 0,
      target: art,
      preventDefault() {},
    }, optsEv);
    for (const fn of (art.listeners[type] || []).slice()) fn.call(art, e);
  }

  // `mid` hook runs after every pointermove so callers can sample the reveal
  // layer / artwork while the drag is still in flight, before pointerup.
  function drag(fromX, toX, { steps = 5, y = 100, dtMs = 16, mid = null } = {}) {
    dispatch('pointerdown', fromX, y);
    for (let i = 1; i <= steps; i += 1) {
      const x = fromX + ((toX - fromX) * i) / steps;
      dispatch('pointermove', x, y, {}, dtMs);
      if (mid) mid(x);
    }
    dispatch('pointerup', toX, y, {}, dtMs);
  }

  return {
    art, leftPanel, pageTitle, pageArtist, nextBtn, prevBtn, body, appState,
    timeouts, rafs, runError, dispatch, drag, clock, context,
    runTimeouts() {
      const pending = timeouts.splice(0);
      pending.forEach((fn) => fn());
    },
    // Run a snippet INSIDE the swipe sandbox so it can touch the IIFE's own
    // lexical variables (e.g. _swipeOptimisticVideoId), like showNowPlaying's
    // confirmed-track cleanup does in the real app.
    runInApp(code) {
      vm.runInContext(code, context);
    },
    layer() {
      return leftPanel.children.find((c) => c.id === 'np-swipe-incoming') || null;
    },
  };
}

// The artwork cache is shared at file scope in the real module.
const _resolvedNowPlayingArt = new Map();

// --------------------------------------------------------------- behaviours --

{
  const e = makeEnv({
    queue: [
      { video_id: 'TRACK1', title: 'One', artist: 'Artist A', thumbnail: 'https://img/1.jpg' },
      { video_id: 'TRACK2', title: 'Two', artist: 'Artist B', thumbnail: 'https://img/2.jpg' },
      { video_id: 'TRACK3', title: 'Three', artist: 'Artist C', thumbnail: 'https://img/3.jpg' },
    ],
    queueIndex: 0,
  });

  check('swipe IIFE parses and installs without throwing', e.runError === null,
        e.runError && e.runError.stack);

  // Fast left drag: dx way past 50% of 265px and high velocity -> commit.
  let midDragTransform = null;
  let midDragArtTransform = null;
  e.drag(200, -200, { steps: 8, dtMs: 8, mid: () => {
    const l = e.layer();
    midDragTransform = l ? l.style.transform : null;
    midDragArtTransform = e.art.style.transform;
  } });

  const layer = e.layer();
  const artMidX = midDragArtTransform ? parseFloat(midDragArtTransform.replace('translateX(', '')) : 0;
  check('drag created the incoming cover layer', !!layer);
  check('cover layer sits beneath the artwork', layer.parentElement === e.leftPanel);

  if (layer) {
    // Mid-drag: layer and artwork translate together, layer parked one cover
    // width to the right for a left (next) swipe.
    check('during the drag the cover is revealed beside the artwork (transform set)',
          artMidX < 0 && /-?[\d.]+px/.test(midDragTransform || ''),
          'layer.transform = ' + midDragTransform + ', art.transform = ' + midDragArtTransform);    const midLayerX = parseFloat((midDragTransform || '').replace('translateX(', ''));
    check('reveal stays flush against the artwork edge (source direction)',
          midLayerX === (layer.offsetWidth + artMidX).toFixed(1) * 1,
          'expected ~ ' + (layer.offsetWidth + artMidX).toFixed(1) + 'px, got ' + midDragTransform);
    check('after commit the cover settles into the artwork home position',
          layer.style.transform === 'translateX(0)' && /ms/.test(layer.style.transition || ''),
          'layer.transform = ' + layer.style.transform + ', transition = ' + layer.style.transition);
  }

  check('committed swipe promotes the adjacent track onto the artwork (no blank)',
        e.art.style.backgroundImage === 'url(https://img/2.jpg)',
        'art background = ' + e.art.style.backgroundImage);
  check('promotion paints the hero title/artist optimistically',
        e.pageTitle.textContent === 'Two' && e.pageArtist.textContent === 'Artist B',
        `title="${e.pageTitle.textContent}" artist="${e.pageArtist.textContent}"`);
  check('swipe direction is remembered for the swap-in animation',
        e.art.dataset.navDirection === 'next');
  check('committed swipe fires the next button',
        e.nextBtn.clicks === 1 && e.prevBtn.clicks === 0);
}

{
  // Previous swipe from index 2 reveals index 1 and clicks previous.
  const e = makeEnv({
    queue: [
      { video_id: 'TRACK1', title: 'One', artist: 'Artist A', thumbnail: 'https://img/1.jpg' },
      { video_id: 'TRACK2', title: 'Two', artist: 'Artist B', thumbnail: 'https://img/2.jpg' },
      { video_id: 'TRACK3', title: 'Three', artist: 'Artist C', thumbnail: 'https://img/3.jpg' },
    ],
    queueIndex: 2,
  });
  e.drag(200, 500, { steps: 8, dtMs: 8 });
  check('previous swipe reveals the track before the current one and clicks previous',
        e.art.style.backgroundImage === 'url(https://img/2.jpg)' &&
        e.art.dataset.navDirection === 'previous' &&
        e.prevBtn.clicks === 1 && e.nextBtn.clicks === 0,
        `bg=${e.art.style.backgroundImage} dir=${e.art.dataset.navDirection} ` +
        `prevClicks=${e.prevBtn.clicks}`);
}

{
  // Slow drag under the 50% reveal threshold springs back: no nav, no promote,
  // cover tucked away (visibility hidden).
  const e = makeEnv({
    queue: [
      { video_id: 'TRACK1', title: 'One', artist: 'Artist A', thumbnail: 'https://img/1.jpg' },
      { video_id: 'TRACK2', title: 'Two', artist: 'Artist B', thumbnail: 'https://img/2.jpg' },
      { video_id: 'TRACK3', title: 'Three', artist: 'Artist C', thumbnail: 'https://img/3.jpg' },
    ],
    queueIndex: 0,
  });
  // 40px drag over a long time: below 0.5*265=132.5px reveal AND slow velocity.
  // 40px / 2000ms = 0.02 px/ms, well under the 0.45 flick threshold.
  e.drag(200, 160, { steps: 4, dtMs: 500 });
  const layer = e.layer();
  check('under-threshold drag does not navigate',
        e.nextBtn.clicks === 0 && e.prevBtn.clicks === 0);
  check('under-threshold drag does not promote the adjacent track',
        !e.art.style.backgroundImage || e.art.style.backgroundImage === '');
  e.runTimeouts(); // let the 210ms tuck timer fire
  check('under-threshold drag tucks the cover out of sight',
        !!layer && layer.style.visibility === 'hidden',
        'layer visibility = ' + (layer && layer.style.visibility));
  check('under-threshold drag leaves the artwork at home',
        !e.art.style.transform || e.art.style.transform === '');
}

{
  // No queue means no reveal and no promotion — graceful fallback, nav still fires.
  const e = makeEnv({ queue: [], queueIndex: -1 });
  e.drag(200, -200, { steps: 8, dtMs: 8 });
  check('empty queue: swipe still navigates, no layer, no promotion',
        e.nextBtn.clicks === 1 && !e.layer() &&
        !e.art.style.backgroundImage,
        `nextClicks=${e.nextBtn.clicks} layer=${!!e.layer()}`);
}

{
  // After a nav the server queue snapshot is reset to '' (the command handler
  // clears state._lastQueueJson and some snapshots omit the queue entirely).
  // The rendered queue rows on the now-playing page remain the source of
  // truth, so the reveal must still work for the NEXT-next song.
  const e = makeEnv({
    queue: [], // empty snapshot, exactly what the nav handler leaves behind
    queueIndex: -1,
    domQueue: [
      { video_id: 'TRACK1', title: 'One', artist: 'Artist A', thumbnail: 'https://img/1.jpg', index: 0, active: false },
      { video_id: 'TRACK2', title: 'Two', artist: 'Artist B', thumbnail: 'https://img/2.jpg', index: 1, active: true },
      { video_id: 'TRACK3', title: 'Three', artist: 'Artist C', thumbnail: 'https://img/3.jpg', index: 2, active: false },
    ],
  });
  e.appState._currentVideoId = 'TRACK2'; // rounded-trip confirmed song 2
  let midBg = null;
  e.drag(200, -200, { steps: 8, dtMs: 8, mid: () => {
    const l = e.layer();
    midBg = l ? l.style.backgroundImage : null;
  } });
  check('empty snapshot queue: reveal still works from the rendered queue rows',
        midBg === 'url(https://img/3.jpg)',
        'layer bg = ' + midBg);
  check('empty snapshot queue: promote + nav still fire',
        e.art.style.backgroundImage === 'url(https://img/3.jpg)' &&
        e.pageTitle.textContent === 'Three' && e.nextBtn.clicks === 1,
        `bg=${e.art.style.backgroundImage} nextClicks=${e.nextBtn.clicks}`);
}

{
  // A second rapid swipe during the round-trip must reveal the NEXT-next
  // track, not replay the cover already promoted onto the artwork by the
  // previous (still unconfirmed) commit.
  const e = makeEnv({
    queue: [
      { video_id: 'TRACK1', title: 'One', artist: 'Artist A', thumbnail: 'https://img/1.jpg' },
      { video_id: 'TRACK2', title: 'Two', artist: 'Artist B', thumbnail: 'https://img/2.jpg' },
      { video_id: 'TRACK3', title: 'Three', artist: 'Artist C', thumbnail: 'https://img/3.jpg' },
    ],
    queueIndex: 0,
  });
  e.drag(200, -200, { steps: 8, dtMs: 8 }); // commit 1 -> banner shows TRACK2
  let midBg = null;
  e.drag(200, -200, { steps: 8, dtMs: 8, mid: () => {
    const l = e.layer();
    midBg = l ? l.style.backgroundImage : null;
  } });
  check('second swipe reveals the track AFTER the promoted one',
        midBg === 'url(https://img/3.jpg)',
        'layer bg = ' + midBg);
  check('second commit promotes the next-next track and navigates again',
        e.art.style.backgroundImage === 'url(https://img/3.jpg)' &&
        e.nextBtn.clicks === 2 && e.prevBtn.clicks === 0,
        `bg=${e.art.style.backgroundImage} nextClicks=${e.nextBtn.clicks}`);
}

{
  // Song 1 -> swipe (commit) -> server CONFIRMS song 2 (showNowPlaying changed
  // branch retires the optimistic layer) -> swipe from song 2 must reveal song 3
  // exactly like the first swipe did.
  const e = makeEnv({
    queue: [
      { video_id: 'TRACK1', title: 'One', artist: 'Artist A', thumbnail: 'https://img/1.jpg' },
      { video_id: 'TRACK2', title: 'Two', artist: 'Artist B', thumbnail: 'https://img/2.jpg' },
      { video_id: 'TRACK3', title: 'Three', artist: 'Artist C', thumbnail: 'https://img/3.jpg' },
    ],
    queueIndex: 0,
  });
  e.drag(200, -200, { steps: 8, dtMs: 8 }); // song 1 -> song 2 (optimistic)

  // Server round-trip completes: SSE/poll paints song 2 as confirmed.
  e.appState._currentVideoId = 'TRACK2';
  e.appState._lastQueueIndex = 1;
  e.runInApp(
    "_swipeOptimisticVideoId = '';"
    + "var swipeHero = document.getElementById('np-page-art');"
    + "if (swipeHero && swipeHero._swipeIncomingLayer) {"
    + "  var stale = swipeHero._swipeIncomingLayer;"
    + "  swipeHero._swipeIncomingLayer = null;"
    + "  if (stale && stale.parentElement) stale.parentElement.removeChild(stale);"
    + "}"
  );

  let midBg = null;
  e.drag(200, -200, { steps: 8, dtMs: 8, mid: () => {
    const l = e.layer();
    midBg = l ? l.style.backgroundImage : null;
  } });
  check('swipe after a confirmed advance reveals the NEXT song\'s banner',
        midBg === 'url(https://img/3.jpg)',
        'layer bg = ' + midBg);
  check('swipe after a confirmed advance promotes and navigates again',
        e.art.style.backgroundImage === 'url(https://img/3.jpg)' &&
        e.pageTitle.textContent === 'Three' &&
        e.nextBtn.clicks === 2,
        `bg=${e.art.style.backgroundImage} nextClicks=${e.nextBtn.clicks}`);
}

{
  // The artwork must track the finger EXACTLY (1:1) for small drags — a
  // resistance bug that snapped every sub-240px drag straight to 240px made a
  // tiny finger movement fling the banner ~90% of the way across. Sample the
  // offset after a 30px and a 120px drag (both below the 240px resistance
  // threshold) and require x == dx.
  const e = makeEnv({
    queue: [
      { video_id: 'TRACK1', title: 'One', artist: 'Artist A', thumbnail: 'https://img/1.jpg' },
      { video_id: 'TRACK2', title: 'Two', artist: 'Artist B', thumbnail: 'https://img/2.jpg' },
      { video_id: 'TRACK3', title: 'Three', artist: 'Artist C', thumbnail: 'https://img/3.jpg' },
    ],
    queueIndex: 0,
  });

  const offsetAt = (dx, steps) => {
    const env = makeEnv({
      queue: [
        { video_id: 'T1', title: 'One', artist: 'A', thumbnail: 'https://img/1.jpg' },
        { video_id: 'T2', title: 'Two', artist: 'B', thumbnail: 'https://img/2.jpg' },
        { video_id: 'T3', title: 'Three', artist: 'C', thumbnail: 'https://img/3.jpg' },
      ],
      queueIndex: 0,
    });
    let last = 0;
    env.drag(200, 200 + dx, { steps: steps || 3, dtMs: 8, mid: () => {
      const t = env.art.style.transform;
      last = parseFloat((t || '0').replace('translateX(', '')) || 0;
    } });
    return last;
  };
  check('small 30px drag tracks the finger exactly (1:1, no snap)',
        offsetAt(30) === 30,
        'offset = ' + offsetAt(30));
  check('120px drag tracks the finger exactly (1:1, no snap)',
        offsetAt(120) === 120,
        'offset = ' + offsetAt(120));

  // Beyond the resistance threshold the drag still keeps moving (monotonic) -
  // a formula that slides BACKWARD past ~240px read as "stuck after a %."
  const e2 = makeEnv({
    queue: [
      { video_id: 'T1', title: 'One', artist: 'A', thumbnail: 'https://img/1.jpg' },
      { video_id: 'T2', title: 'Two', artist: 'B', thumbnail: 'https://img/2.jpg' },
      { video_id: 'T3', title: 'Three', artist: 'C', thumbnail: 'https://img/3.jpg' },
    ],
    queueIndex: 0,
  });
  const offsets = [];
  e2.drag(200, -700, { steps: 10, dtMs: 8, mid: () => {
    const t = e2.art.style.transform;
    offsets.push(parseFloat((t || '0').replace('translateX(', '')));
  } });
  let monotonic = offsets.length > 1;
  for (let i = 1; i < offsets.length; i += 1) {
    if (offsets[i] > offsets[i - 1] + 0.001) monotonic = false;
  }
  check('drag offset decreases monotonically with finger travel (no backward slide)',
        monotonic,
        'offsets = ' + offsets.join(', '));
  check('long push keeps the banner fully revealed, not receding',
        offsets.length && offsets[offsets.length - 1] <= -280 &&
        offsets[offsets.length - 1] >= -330,
        'final offset = ' + offsets[offsets.length - 1]);
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exitCode = failed ? 1 : 0;