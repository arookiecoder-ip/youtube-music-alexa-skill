// Regression test for the home page "Add to queue" error.
//
// Bug (reported): right-clicking some home cards (albums, artists, and other
// non-track cards inside mixed shelves) opened the track menu — "Like / Play
// next / Add to queue / Play Radio / Save to Playlist" — even though those
// cards have no video id. Clicking "Add to queue" then POSTed an empty
// video_id to /alexa/queue_add/ and the server rejected it with 400
// `missing or invalid "video_id"`.
//
// Fix under test (home.js): the context-menu isPlaylist gate now also treats
// any card without a video id as playlist-like, so those cards only show
// Shuffle/Play (which work via the card's playlist id) and the track actions
// stay hidden.
//
// Run: `node flask-server/tests/test_home_menu_track_actions.js`

const fs = require('fs');
const path = require('path');

const JS_PATH = path.join(__dirname, '..', 'templates', 'static', 'js', 'home.js');
const SRC = fs.readFileSync(JS_PATH, 'utf8');

let passed = 0;
let failed = 0;

function checkTrue(name, actual, hint) {
  if (actual) { passed += 1; console.log(`PASS  ${name}`); }
  else { failed += 1; console.log(`FAIL  ${name}${hint ? '\n        ' + hint : ''}`); }
}

// The gate must treat cards without a video id as playlist-like so the menu
// hides track-only actions on albums/artists/unknown kinds.
checkTrue('cards without a video id are treated as playlist-like',
  /var isPlaylist = kind === 'playlist' \|\| kind === 'station' \|\| !videoId;/.test(SRC),
  'expected the isPlaylist line to include `|| !videoId`');

// The five track actions must be hidden for playlist-like cards, so
// "Add to queue" can never fire with an empty video_id.
const menuVisibility = SRC.slice(SRC.indexOf('sharedMoreMenu.querySelector(\'[data-action="shuffle-play"]\').hidden = !isPlaylist;'));
checkTrue('Like is hidden on playlist-like cards',
  /toggle-like/.test(menuVisibility) && /\.hidden = isPlaylist;/.test(menuVisibility));
checkTrue('Play next is hidden on playlist-like cards',
  /data-action="play-next"[^;]*\.hidden = isPlaylist;/.test(menuVisibility));
checkTrue('Add to queue is hidden on playlist-like cards',
  /data-action="add-to-queue"[^;]*\.hidden = isPlaylist;/.test(menuVisibility));
checkTrue('Play Radio is hidden on playlist-like cards',
  /data-action="play-radio"[^;]*\.hidden = isPlaylist;/.test(menuVisibility));
checkTrue('Save to Playlist is hidden on playlist-like cards',
  /data-action="save-playlist"[^;]*\.hidden = isPlaylist;/.test(menuVisibility));

// The track built for the menu must carry the card's video id, so any track
// that does reach Add to queue has a real video_id.
checkTrue('menu track carries video_id from the card dataset',
  /sharedMoreMenu\._track = \{\s*video_id: videoId,/.test(SRC));

// Only Shuffle/Play remain for playlist-like cards.
checkTrue('Shuffle play stays for playlist-like cards',
  /data-action="shuffle-play"[^;]*\.hidden = !isPlaylist;/.test(menuVisibility));
checkTrue('Play stays for playlist-like cards',
  /data-action="play-home"[^;]*\.hidden = !isPlaylist;/.test(menuVisibility));

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
