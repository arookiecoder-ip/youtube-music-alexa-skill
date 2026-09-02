// Mobile swipe-to-skip banner reveal: while the user drags the now-playing
// artwork, the adjacent track's cover is revealed beside it and settles in on
// commit, so the banner is never blank during (or right after) a swipe while
// the Alexa round-trip is in flight.
//
// Run: `node flask-server/tests/test_mobile_now_playing_swipe.js`

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

const player = fs.readFileSync(path.join(jsRoot, 'player.js'), 'utf8');
const playerCss = fs.readFileSync(path.join(cssRoot, 'player.css'), 'utf8');

check('swipe handler is mobile-only (desktop early-return)',
  /\(function wireMobileNowPlayingSwipe\(\) \{[\s\S]*?\(min-width: 900px\)/.test(player));

check('adjacent track is resolved from the server queue snapshot',
  /function findAdjacentTrack\(direction\) \{[\s\S]*?JSON\.parse\(state\._lastQueueJson \|\| '\[\]'\)[\s\S]*?state\._lastQueueIndex/.test(player));
check('adjacent track picks the neighbor (next -> +1, previous -> -1)',
  /const target = direction === 'next' \? index \+ 1 : index - 1;[\s\S]*?if \(target >= 0 && target < queue\.length\) return queue\[target\] \|\| null;/.test(player));
check('a second swipe during the round-trip is based on the optimistic id',
  /\(Date\.now\(\) - _swipeOptimisticAt < 10000\) \? _swipeOptimisticVideoId/.test(player) &&
  /queue\.findIndex\(\(item\) => item && \(item\.video_id \|\| item\.videoId\) === wantId\)/.test(player));
check('reveal falls back to the rendered queue rows when the snapshot queue is empty',
  /function adjacentTrackFromDom\(direction\) \{[\s\S]*?getElementById\('np-queue-list'\)[\s\S]*?querySelectorAll\('\.queue-swipe-wrapper'\)[\s\S]*?\_songContextTrack/.test(player) &&
  /return adjacentTrackFromDom\(direction\);/.test(player));

check('reveal layer exists and is inserted beneath the artwork',
  /layer\.id = 'np-swipe-incoming';[\s\S]*?layer\.className = 'np-swipe-incoming';[\s\S]*?art\.parentElement\.insertBefore\(layer, art\);/.test(player));
check('reveal layer parks off-screen on the side the gesture comes from',
  /layer\.style\.transform = 'translateX\(' \+ \(direction === 'next' \? w : -w\) \+ 'px\)';/.test(player));
check('incoming cover is preloaded for the confirm paint',
  /void resolveNowPlayingArtwork\(videoId\);/.test(player) &&
  /art\._swipeIncomingLayer = layer;/.test(player));

check('drag keeps the reveal flush against the moving artwork',
  /incoming\.layer\.style\.transform =\s*'translateX\(' \+ \(fromSide \+ limited\)\.toFixed\(1\) \+ 'px\)';/.test(player) &&
  /const fromSide = dragDirection === 'next' \? w : -w;/.test(player));
check('drag tracks the finger 1:1 below the resistance threshold, dampened + monotonic above it',
  /const over = Math\.max\(0, absX - RESISTANCE_START_PX\);\s*const limitedAbs = Math\.min\(absX, RESISTANCE_START_PX\)\s*\+ over \/ \(1 \+ over \/ RESISTANCE_DIVISOR_PX\);/.test(player));
check('direction is re-derived on every move (reversal re-points the layer)',
  /const dragDirection = dx < 0 \? 'next' : 'previous';[\s\S]*?incoming\.direction !== dragDirection/.test(player));

check('commit settles the reveal into the artwork home position',
  /layer\.style\.transition = 'transform ' \+ EXIT_MS \+ 'ms cubic-bezier\(\.22,1,\.36,1\)';[\s\S]*?layer\.style\.transform = 'translateX\(0\)';/.test(player));
check('commit promotes the adjacent track onto the artwork (no blank frame)',
  /promoteIncomingToArt\(incoming\.track\);/.test(player) &&
  /function promoteIncomingToArt\(track\) \{[\s\S]*?art\.style\.backgroundImage = 'url\(' \+ \(cachedHighRes \|\| thumb\) \+ '\)';[\s\S]*?art\.classList\.add\('has-thumb'\);/.test(player));
check('promotion records the optimistic id with an expiry',
  /_swipeOptimisticVideoId = videoId \|\| '';[\s\S]*?_swipeOptimisticAt = Date\.now\(\);/.test(player));
check('promotion keeps the hero title/artist coherent while the round-trip completes',
  /pageTitle\) pageTitle\.textContent = title;[\s\S]*?pageArtist\) pageArtist\.textContent = artist;/.test(player));

check('snap-back tucks the reveal away (identity-guarded removal)',
  /if \(incoming && incoming\.layer === layer\) hideIncoming\(\);/.test(player) &&
  /function hideIncoming\(\) \{[\s\S]*?incoming\.layer\.style\.visibility = 'hidden';/.test(player));
check('committed gesture re-points the layer if the finger reversed mid-drag',
  /if \(incoming && incoming\.direction !== direction\) \{[\s\S]*?findAdjacentTrack\(direction\);[\s\S]*?showIncomingLayer\(direction, adjacent\);/.test(player));

check('confirmed now-playing snapshot retires the optimistic banner',
  /const changed = fp !== _lastNpFingerprint;[\s\S]*?if \(changed\) \{[\s\S]*?_swipeOptimisticVideoId = '';[\s\S]*?swipeHero\._swipeIncomingLayer[\s\S]*?removeChild\(stale\);/.test(player));
check('a same-track confirmation is detected before the optimistic id is cleared',
  /const swipeBannerAlreadyShown = !!_swipeOptimisticVideoId[\s\S]*?_swipeOptimisticVideoId === \(info\.video_id \|\| info\.videoId\);\s*_swipeOptimisticVideoId = '';/.test(player));
check('confirming the swipe\'s own cover keeps the hero paint (no blank, no rendition swap)',
  /if \(swipeBannerAlreadyShown && el === npPageArt && !cachedHighRes\) return;[\s\S]*?el\.classList\.toggle\('image-loading',\s*!cachedHighRes && !\(swipeBannerAlreadyShown && el === npPageArt\)\);/.test(player));
check('confirming the swipe\'s own cover skips the entrance animation (no flicker)',
  /if \(swipeBannerAlreadyShown\) \{[\s\S]*?delete npPageArt\.dataset\.navDirection;[\s\S]*?\} else \{[\s\S]*?playArtworkSwapIn\(\);\s*\}/.test(player));
check('route close drops the reveal layer immediately',
  /hideSwipeFeedback\(\);\s*\/\/ The route is closing mid-gesture; drop the reveal layer immediately[\s\S]*?hideIncoming\(\);/.test(player));

check('reveal layer CSS is defined beneath the artwork and carries the bottom fade',
  /\.np-swipe-incoming \{\s*position: absolute;\s*border-radius: 0;\s*background: var\(--surface\) center \/ cover no-repeat;\s*box-shadow: 0 14px 34px rgba\(0,0,0,\.4\);\s*pointer-events: none;\s*z-index: 0;\s*\}/.test(playerCss) &&
  /\.np-swipe-incoming::after \{\s*content: '';\s*position: absolute;\s*inset: 0;\s*z-index: -1;\s*pointer-events: none;\s*background: linear-gradient\(to top,[\s\S]*?rgba\(0, 0, 0, \.55\) 40%[\s\S]*?rgba\(0, 0, 0, 0\) 100%\);\s*\}/.test(playerCss));

console.log(`\n${passed} passed, ${failed} failed`);
process.exitCode = failed ? 1 : 0;