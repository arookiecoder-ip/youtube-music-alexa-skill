// Regression coverage for album/playlist detail page mode.
// Run: node flask-server/tests/test_collection_detail_page.js

const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..');
const remote = fs.readFileSync(path.join(root, 'templates', 'remote.html'), 'utf8');
const jam = fs.readFileSync(path.join(root, 'templates', 'jam.html'), 'utf8');
const router = fs.readFileSync(path.join(root, 'templates', 'static', 'js', 'router.js'), 'utf8');
const album = fs.readFileSync(path.join(root, 'templates', 'static', 'js', 'album.js'), 'utf8');
const playlists = fs.readFileSync(path.join(root, 'templates', 'static', 'playlists.js'), 'utf8');
const search = fs.readFileSync(path.join(root, 'templates', 'static', 'js', 'search.js'), 'utf8');
const playlistCss = fs.readFileSync(path.join(root, 'templates', 'static', 'css', 'playlist.css'), 'utf8');
const artistCss = fs.readFileSync(path.join(root, 'templates', 'static', 'css', 'artist.css'), 'utf8');
const playerCss = fs.readFileSync(path.join(root, 'templates', 'static', 'css', 'player.css'), 'utf8');
const headerCss = fs.readFileSync(path.join(root, 'templates', 'static', 'css', 'header.css'), 'utf8');
const sidebarCss = fs.readFileSync(path.join(root, 'templates', 'static', 'css', 'sidebar.css'), 'utf8');

let passed = 0;
let failed = 0;
function check(name, condition, detail) {
  if (condition) {
    passed += 1;
    console.log(`PASS  ${name}`);
  } else {
    failed += 1;
    console.log(`FAIL  ${name}${detail ? `\n        ${detail}` : ''}`);
  }
}

const pageMarkup = remote.match(/<section id="collection-detail-page"[\s\S]*?<\/section>/);
check('remote has one shared collection page section', !!pageMarkup &&
  (remote.match(/id="collection-detail-page"/g) || []).length === 1 &&
  pageMarkup[0].includes('id="playlist-detail-body"') && pageMarkup[0].includes('id="collection-detail-shell"'));
check('collection detail is not an overlay container',
  !!pageMarkup && !pageMarkup[0].includes('history-modal-overlay') &&
  !pageMarkup[0].includes('class="history-modal"'));
check('jam shell uses a page stub rather than the old overlay stub',
  jam.includes('<section id="collection-detail-page" hidden></section>') &&
  !jam.includes('playlist-detail-modal-overlay'));
check('router maps album and playlist scroll state to the page',
  router.includes("if (route.indexOf('#album/') === 0) return 'collection-detail-page';") &&
  router.includes("if (route.indexOf('#playlist/') === 0) return 'collection-detail-page';"));
check('router mounts collection page for both routes',
  router.includes("var playlistPage = document.getElementById('collection-detail-page');") &&
  router.includes("var albumPage = document.getElementById('collection-detail-page');") &&
  router.includes('playlistPage.hidden = false') &&
  router.includes('albumPage.hidden = false'));
check('router hides collection page when leaving both detail routes',
  router.includes("hash.indexOf('#playlist/') !== 0 && hash.indexOf('#album/') !== 0") &&
  router.includes("collectionPage.hidden = true"));
check('album loader reveals the page, not an overlay',
  album.includes("document.getElementById('collection-detail-page')") &&
  album.includes('page.hidden = false') &&
  !album.includes('playlist-detail-modal-overlay'));
check('playlist loader reveals the page, not an overlay',
  playlists.includes("document.getElementById('collection-detail-page')") &&
  playlists.includes('page.hidden = false') &&
  !playlists.includes('playlist-detail-modal-overlay'));
check('search source detection includes collection page',
  search.includes("'collection-detail-page'") &&
  !search.includes('playlist-detail-modal-overlay'));
check('mobile page layout remains document-flow based',
  playlistCss.includes('body.playlists-route #playlist-detail-modal-overlay') === false &&
  playlistCss.includes('@media (max-width: 899px)') &&
  playlistCss.includes('.collection-detail-body'));
check('desktop page layout remains two-column and scrollable',
  playlistCss.includes('grid-template-columns: minmax(320px, 360px) minmax(0, 1fr)') &&
  playlistCss.includes('overflow-y: auto') &&
  playlistCss.includes('.collection-detail-body'));
check('desktop collection page does not lock document scrolling',
  !playlistCss.includes('body:not(.explore-route):not(.mood-route):not(.library-route):has(#collection-detail-page:not([hidden])) {\n  overflow: hidden;'));
check('desktop collection page establishes a definite-height scroll chain',
  playlistCss.includes('body:has(#collection-detail-page:not([hidden])) main {\n    height: 100dvh;') &&
  playlistCss.includes('body:has(#collection-detail-page:not([hidden])) main:has(.player-section.is-visible:not([hidden])) {\n    padding-bottom: var(--playbar-h);') &&
  playlistCss.includes('body:has(#collection-detail-page:not([hidden])) #collection-detail-page {\n    flex: 1 1 0;\n    min-height: 0;') &&
  playlistCss.includes('min-width: 900px'));
check('now-playing album return has no unreachable duplicate branch',
  !router.includes("var returnedCollectionPage = document.getElementById('collection-detail-page');"));
check('collection page keeps home/recommendations hidden',
  artistCss.includes('body:has(#collection-detail-page:not([hidden])) #home-section') &&
  artistCss.includes('body:has(#collection-detail-page:not([hidden])) #recs-section'));
function routeBranchContains(routePrefix, text) {
  const start = router.indexOf("} else if (hash.indexOf('" + routePrefix + "') === 0) {");
  if (start < 0) return false;
  const end = router.indexOf("} else if (", start + 10);
  return router.slice(start, end < 0 ? router.length : end).includes(text);
}
check('collection routes keep the persistent search shell visible',
  routeBranchContains('#playlist/', "setHidden('.play-section', false);") &&
  routeBranchContains('#album/', "setHidden('.play-section', false);"));
check('playlist route uses transparent header treatment',
  playerCss.includes('body.playlists-route:not(.results-open):not(.header-scrolled) main header') &&
  headerCss.includes('body.playlists-route:not(.results-open):not(.header-scrolled) main header'));
check('collection routes do not force the collapsed sidebar black',
  sidebarCss.includes(':not(.playlists-route):not(.album-route) .sidebar'));

console.log(`\ncollection-detail-page: passed=${passed} failed=${failed}`);
process.exit(failed ? 1 : 0);
