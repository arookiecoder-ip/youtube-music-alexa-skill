const fs = require('fs');
const path = require('path');
const vm = require('vm');

const routerSource = fs.readFileSync(
  path.join(__dirname, '..', 'templates', 'static', 'js', 'router.js'),
  'utf8'
);
const playerSource = fs.readFileSync(
  path.join(__dirname, '..', 'templates', 'static', 'js', 'player.js'),
  'utf8'
);

let passed = 0;
let failed = 0;
function check(name, actual, expected) {
  if (JSON.stringify(actual) === JSON.stringify(expected)) {
    passed += 1;
    console.log(`PASS  ${name}`);
  } else {
    failed += 1;
    console.log(`FAIL  ${name}\n        expected ${JSON.stringify(expected)}\n        actual   ${JSON.stringify(actual)}`);
  }
}

function element(id) {
  return {
    id,
    style: { display: '', setProperty() {}, removeProperty() {} },
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    addEventListener() {},
    closest(selector) {
      if (selector === '.np-page' && id === 'np-page-art') {
        return {
          style: { setProperty() {}, removeProperty() {} },
          classList: { add() {}, remove() {}, toggle() {} },
        };
      }
      return null;
    },
    textContent: '',
    innerHTML: '',
    hidden: false,
  };
}

const elements = new Map();
const sandbox = {
  console,
  escHtml(value) { return String(value == null ? '' : value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;'); },
  URLSearchParams,
  URL,
  location: { pathname: '/home', search: '', hash: '' },
  history: { state: null, replaceState() {}, pushState() {} },
  document: {
    body: { classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } }, style: { removeProperty() {}, setProperty() {} } },
    documentElement: { style: {} },
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, element(id));
      return elements.get(id);
    },
    querySelectorAll() { return []; },
    querySelector() { return null; },
    addEventListener() {},
  },
  window: null,
  addEventListener() {},
  matchMedia() { return { matches: false, addEventListener() {}, addListener() {} }; },
  requestAnimationFrame(fn) { fn(); },
  setTimeout,
  clearTimeout,
  Image: function Image() {},
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(routerSource, sandbox, { filename: 'router.js' });

const links = sandbox.artistLinksHtml(
  'Real Artist and Guest Artist',
  ['UC_REAL', 'UC_GUEST']
);
check('multi-artist link markup keeps each exact channel id', links, '<span class="artist-name" data-artist-name="Real Artist" data-channel-id="UC_REAL">Real Artist</span> and <span class="artist-name" data-artist-name="Guest Artist" data-channel-id="UC_GUEST">Guest Artist</span>');

// Load only the player helper functions needed to exercise showNowPlaying.
const playerStart = playerSource.indexOf('function nowPlayingArtistEntries');
const playerEnd = playerSource.indexOf('\nfunction refreshNpLikeButton', playerStart);
if (playerStart < 0 || playerEnd < playerStart) {
  console.log('FATAL: now-playing helper block not found');
  process.exit(1);
}
const state = { _hasTrack: false, _currentVideoId: '', _currentThumbnail: '', _currentTrack: null };
sandbox.__appState = state;
sandbox.state = state;
sandbox._resolvedNowPlayingArt = new Map();
sandbox._ambientNowPlayingArt = new Map();
sandbox._pendingNowPlayingArt = new Map();
sandbox._lastNpFingerprint = '';
sandbox.preloadNowPlayingArtwork = () => {};
sandbox.resolveNowPlayingArtwork = () => Promise.resolve('');
sandbox.updateUrlBar = () => {};
sandbox.syncTrackPlaybackIndicators = () => {};
sandbox.playArtworkSwapIn = () => {};
sandbox.syncUiState = () => {};
sandbox.updateResultsActive = () => {};
sandbox.refreshNpLikeButton = () => {};
vm.runInContext(playerSource.slice(playerStart, playerEnd), sandbox, { filename: 'player-artist-helpers.js' });

const info = {
  video_id: 'VIDEO-1',
  title: 'Same Song',
  artist: 'Real Artist and Guest Artist',
  artists: [
    { name: 'Real Artist', id: 'UC_REAL' },
    { name: 'Guest Artist', browseId: 'UC_GUEST' },
  ],
};
const entries = sandbox.nowPlayingArtistEntries(info);
check('now-playing normalizes structured artist entries', entries, [
  { name: 'Real Artist', id: 'UC_REAL' },
  { name: 'Guest Artist', id: 'UC_GUEST' },
]);
check('now-playing keeps canonical supplied credit text', sandbox.nowPlayingArtistText(info), 'Real Artist and Guest Artist');
check('now-playing markup uses exact ids', sandbox.artistLinksHtml(
  sandbox.nowPlayingArtistText(info), entries.map((artist) => artist.id), info.video_id
), '<span class="artist-name" data-artist-name="Real Artist" data-video-id="VIDEO-1" data-channel-id="UC_REAL">Real Artist</span> and <span class="artist-name" data-artist-name="Guest Artist" data-video-id="VIDEO-1" data-channel-id="UC_GUEST">Guest Artist</span>');

// The artist IDs can arrive in a later poll for the same song. Exercise the
// actual renderer rather than only its formatting helpers: both compact and
// expanded playback links must be replaced with exact-ID links.
sandbox.showNowPlaying({ video_id: 'LATE-1', title: 'Late Metadata', artist: 'Real Artist' });
sandbox.showNowPlaying({
  video_id: 'LATE-1', title: 'Late Metadata', artist: 'Real Artist',
  artists: [{ name: 'Real Artist', id: 'UC_LATE_REAL' }],
});
const compactLate = elements.get('np-artist').innerHTML;
const pageLate = elements.get('np-page-artist').innerHTML;
check('late artist metadata updates compact playback link', compactLate, '<span class="artist-name" data-artist-name="Real Artist" data-video-id="LATE-1" data-channel-id="UC_LATE_REAL">Real Artist</span>');
check('late artist metadata updates expanded playback link', pageLate, '<span class="artist-name" data-artist-name="Real Artist" data-video-id="LATE-1" data-channel-id="UC_LATE_REAL">Real Artist</span>');

// A partial later snapshot must preserve the ID already known for the other
// collaborator, and a new track must not inherit those IDs.
sandbox.showNowPlaying({
  video_id: 'PARTIAL-1', title: 'Collab', artist: 'Real Artist and Guest Artist',
  artists: [{ name: 'Real Artist', id: 'UC_REAL' }, { name: 'Guest Artist', id: 'UC_GUEST' }],
});
sandbox.showNowPlaying({
  video_id: 'PARTIAL-1', title: 'Collab', artist: 'Real Artist and Guest Artist',
  artists: [{ name: 'Real Artist', id: 'UC_REAL_REFRESHED' }, { name: 'Guest Artist' }],
});
check('partial metadata preserves collaborator ids', elements.get('np-artist').innerHTML, '<span class="artist-name" data-artist-name="Real Artist" data-video-id="PARTIAL-1" data-channel-id="UC_REAL_REFRESHED">Real Artist</span> and <span class="artist-name" data-artist-name="Guest Artist" data-video-id="PARTIAL-1" data-channel-id="UC_GUEST">Guest Artist</span>');
sandbox.showNowPlaying({ video_id: 'NEW-1', title: 'New Song', artist: 'New Artist' });
check('new track does not inherit previous artist id', elements.get('np-artist').innerHTML, '<span class="artist-name" data-artist-name="New Artist" data-video-id="NEW-1">New Artist</span>');

console.log(`\nartist-link-metadata: passed=${passed} failed=${failed}`);
process.exit(failed ? 1 : 0);
