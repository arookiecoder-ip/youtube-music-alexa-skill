const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..');
const jsRoot = path.join(root, 'templates', 'static', 'js');
const cssRoot = path.join(root, 'templates', 'static', 'css');

let passed = 0;
let failed = 0;
function check(name, condition, detail) {
  if (condition) {
    passed += 1;
    console.log(`PASS ${name}`);
  } else {
    failed += 1;
    console.error(`FAIL ${name}${detail ? ` — ${detail}` : ''}`);
  }
}

const queue = fs.readFileSync(path.join(jsRoot, 'queue.js'), 'utf8');
const search = fs.readFileSync(path.join(jsRoot, 'search.js'), 'utf8');
const album = fs.readFileSync(path.join(jsRoot, 'album.js'), 'utf8');
const playlists = fs.readFileSync(path.join(root, 'templates', 'static', 'playlists.js'), 'utf8');
const artist = fs.readFileSync(path.join(jsRoot, 'artist.js'), 'utf8');
const searchCss = fs.readFileSync(path.join(cssRoot, 'search.css'), 'utf8');

check('shared helper is exported',
  /window\.attachResultSwipeGesture\s*=\s*_attachSwipeGesture/.test(queue));
check('right swipe means play next',
  /if \(currentX > SWIPE_THRESHOLD\)[\s\S]*?addToQueue\(item, 'next'\)/.test(queue));
check('left swipe means append to queue',
  /else if \(currentX < -SWIPE_THRESHOLD\)[\s\S]*?addToQueue\(item, 'last'\)/.test(queue));
check('vertical movement preserves scrolling',
  /gesture = 'scroll'/.test(queue) && /touch-action:\s*pan-y/.test(searchCss));
check('committed swipe suppresses click',
  /inner\._swipeSuppressClick\s*=\s*true/.test(queue) &&
  /inner\.addEventListener\('click', \(e\) => \{/.test(queue));
check('search rows attach shared swipe',
  /attachResultSwipeGesture\(wrapper, inner, item\)/.test(search));
check('album rows attach shared swipe',
  /attachResultSwipeGesture\(wrapper, row, contextTrack\)/.test(album));
check('playlist rows attach shared swipe',
  /attachResultSwipeGesture\(wrapper, row, contextTrack\)/.test(playlists));
check('artist rows use a swipe wrapper',
  /wrapper\.className = 'result-swipe-wrapper'/.test(artist) &&
  /attachResultSwipeGesture\(wrapper, row, item\)/.test(artist));
check('result underlays remain available',
  !/#results-section \.result-swipe-underlay\s*\{\s*display:\s*none/.test(searchCss));
check('artist and collection rows sit above underlay',
  /\.result-swipe-wrapper > \.history-item,\s*\.result-swipe-wrapper > \.artist-song-row/.test(searchCss));
check('result rows turn opaque while swiping so only the swiped strip reveals the underlay',
  /\.result-swipe-wrapper\.swiping-right > \.result-item-inner/.test(searchCss) &&
  /\.result-swipe-wrapper\.swiping-right > \.history-item/.test(searchCss) &&
  /\.result-swipe-wrapper\.swiping-right > \.artist-song-row/.test(searchCss) &&
  /background: var\(--surface\) !important/.test(searchCss));
check('search rows use an ID-scoped opaque override to beat the transparent !important rule',
  /#results-section \.result-swipe-wrapper\.swiping-right > \.result-item-inner/.test(searchCss) &&
  /#results-section \.result-swipe-wrapper\.swiping-left > \.result-item-inner/.test(searchCss));
check('mobile search clear button shares the fixed bar padding so it stays inside the input frame',
  /body\.mobile-search-open \.header-search-wrap \.search-clear \{ right: 14px; \}/.test(searchCss));
check('mobile search suggestions match the search bar width (share the 14px inset)',
  /body\.mobile-search-open \.suggest-list \{[\s\S]*?left: 14px;[\s\S]*?right: 14px;/.test(searchCss));

check('queue right swipe toggles through toggleLike',
  /else if \(committedLike\)[\s\S]*?toggleLike\(item\)/.test(queue));
check('queue swipe label reflects liked state',
  /queue-swipe-like-label/.test(queue) &&
  /label\.textContent = liked \? 'Unlike' : 'Like'/.test(queue) &&
  /window\._playlistsData/.test(queue));
check('queue menu uses Unlike wording',
  /const likeText = isLiked \? "Unlike" : "Like"/.test(queue) &&
  /isLikedNow \? "Unlike" : "Like"/.test(queue));
const likeSource = fs.readFileSync(path.join(jsRoot, 'like.js'), 'utf8');
check('like.js sends INDIFFERENT for an already-liked song',
  /var newAction = isCurrentlyLiked \? 'INDIFFERENT' : 'LIKE'/.test(likeSource));
check('like.js prevents overlapping same-song toggles',
  /_likeRequestsInFlight\.has\(videoId\)/.test(likeSource) &&
  /_likeRequestsInFlight\.delete\(videoId\)/.test(likeSource));
check('queue short horizontal movement does not suppress click',
  /if \(committedDelete \|\| committedLike\) \{[\s\S]*?el\._swipeSuppressClick = true/.test(queue) &&
  /else \{[\s\S]*?el\._swipeAllowClick = true/.test(queue));
check('queue swipe tracks fast horizontal gestures',
  !/HOLD_DURATION|holdReady/.test(queue) &&
  /if \(gesture !== 'swipe'\) return;/.test(queue));
const playerCss = fs.readFileSync(path.join(cssRoot, 'player.css'), 'utf8');
check('desktop-only now-playing queue overrides preserve mobile swipe styles',
  /@media \(min-width: 900px\) \{[\s\S]*?#np-queue-list \.queue-delete-underlay/.test(playerCss));

console.log(`\n${passed} passed, ${failed} failed`);
process.exitCode = failed ? 1 : 0;
