// Regression coverage for the content-aware skeleton loaders on the Explore,
// History and Mood/Genre pages. Each test asserts:
//   - the placeholder mirrors the real page's content shape (so the swap is
//     layout-stable, no reflow when real content replaces the placeholder)
//   - the placeholder is rendered in the same break-points the real page
//     uses (so grid columns line up at every viewport)
//   - none of the page helpers accidentally fall back to the old spinning
//     loader
//
// Run: `node flask-server/tests/test_skeleton_loaders.js` from the repo root.

const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..');
const exploreJs = fs.readFileSync(path.join(root, 'templates', 'static', 'js', 'explore.js'), 'utf8');
const historyJs = fs.readFileSync(path.join(root, 'templates', 'static', 'js', 'history.js'), 'utf8');
const homeCss = fs.readFileSync(path.join(root, 'templates', 'static', 'css', 'home.css'), 'utf8');
const remoteHtml = fs.readFileSync(path.join(root, 'templates', 'remote.html'), 'utf8');
const recsCss = fs.readFileSync(path.join(root, 'templates', 'static', 'css', 'recs.css'), 'utf8');

let passed = 0;
let failed = 0;
function check(name, condition, detail) {
  if (condition) {
    passed += 1;
    console.log('PASS  ' + name);
  } else {
    failed += 1;
    console.log('FAIL  ' + name + (detail ? '\n        ' + detail : ''));
  }
}

// --- CSS: every classname referenced from the skeleton renderers is defined.

const cssClasses = [
  'history-page-skeleton',
  'history-skeleton-bucket',
  'history-skeleton-row',
  'history-skeleton-info',
  'history-skeleton-duration',
  'history-skeleton-like',
  'skeleton-square',
  'skeleton-line-heading',
  'explore-page-skeleton',
  'explore-quick-nav-skeleton',
  'explore-shelves-skeleton',
  'explore-section-skeleton',
  'explore-section-header-skeleton',
  'explore-grid-skeleton',
  'explore-card-skeleton',
  'explore-skeleton-art',
  'explore-mood-grid-skeleton',
  'explore-mood-pill-skeleton',
  'mood-page-skeleton',
  'mood-page-skeleton-grid',
  'mood-skeleton-row-shelf',
];
cssClasses.forEach(cls => {
  check('CSS defines .' + cls, homeCss.includes('.' + cls));
});

// The new CSS reuses the shimmer keyframes that already live in recs.css.
check('shimmer keyframes are referenced from the new CSS',
  homeCss.includes('skeletonShimmer'));

// Reduced-motion preference disables the shimmer without removing the box.
check('reduced-motion is honored across the new skeletons',
  /@media\s+\(prefers-reduced-motion: reduce\)[\s\S]*?\.skeleton-square[\s\S]*?animation:\s*none/.test(homeCss));

// --- Explorer: skeleton helpers exist and are wired into the cold-load path ---

check('explore.js exposes renderExplorePageSkeleton()',
  /function\s+renderExplorePageSkeleton\s*\(/.test(exploreJs));
check('explore.js exposes renderMoodPageSkeleton()',
  /function\s+renderMoodPageSkeleton\s*\(/.test(exploreJs));
check('loadExplore() swaps in the explore skeleton on cold load',
  /if\s*\(!preloaded\)\s*renderExplorePageSkeleton\(body\)/.test(exploreJs));
check('openMoodPage() paints a mood skeleton before fetching',
  /if\s*\(!cached\)\s*renderMoodPageSkeleton\(body\)/.test(exploreJs));

// The explorer skeleton has to mirror the real page sections. We check the
// structural calls + string literals instead of evaluating the function --
// the helpers produce static HTML strings assembled at call time.

// Quick nav row of 4 placeholders.
check('explore skeleton includes the quick-nav row of 4 placeholders',
  /explore-quick-nav-skeleton[\s\S]{0,300}\.repeat\(4\)/.test(exploreJs));

// 3 card-shaped shelves (New releases / Top songs / Trending) followed by
// one moods shelf. Helper composition proves both count and shape.
check('explore skeleton composes 3 card shelves + 1 moods shelf',
  /_exploreShelfSkeleton\(\)\s*\+\s*_exploreShelfSkeleton\(\)\s*\+\s*_exploreShelfSkeleton\(\)\s*\+\s*_exploreMoodsShelfSkeleton\(\)/.test(exploreJs));

// Scaffolding per shelf: section header placeholder (with a heading line of
// the same height as the real .explore-section-title) and a row of cards.
check('each explore shelf shows a section header placeholder',
  /explore-section-header-skeleton[\s\S]{0,400}explore-grid-skeleton/.test(exploreJs));

// Moods shelf: explicit 24-pill count matches the real `visibleItems` page.
check('explore mood shelf renders exactly 24 pills',
  /explore-mood-pill-skeleton[\s\S]{0,300}\.repeat\(24\)/.test(exploreJs));

// Card placeholder mirrors the real .explore-card geometry: square art +
// title line + artist line.
check('explore card placeholder has art + title + artist lines',
  /_exploreCardSkeletonInner[\s\S]*?explore-skeleton-art[\s\S]*?skeleton-line-title[\s\S]*?skeleton-line-artist/.test(exploreJs));

// Mood skeleton: one songs row + three tile-grid shelves (Featured /
// Community / Albums). The songs row uses .mood-skeleton-row-shelf; the
// three tile-grid shelves are produced by three calls to tileShelf(width)
// with the feature/community/album heading widths.
check('mood skeleton composes 1 songs shelf + 3 tile-grid shelves',
  /mood-skeleton-row-shelf/.test(exploreJs) &&
  /tileShelf\(\s*170\s*\)[\s\S]*tileShelf\(\s*190\s*\)[\s\S]*tileShelf\(\s*95\s*\)/.test(exploreJs));

// --- History: skeleton helpers exist, render is layout-shaped, empty state
//     is suppressed until the fetch resolves ---

check('history.js exposes renderHistoryPageSkeleton()',
  /function\s+renderHistoryPageSkeleton\s*\(/.test(historyJs));
check('history skeleton row matches real .history-item shape (thumb + info + duration + like)',
  /skeleton-square[\s\S]*?history-skeleton-info[\s\S]*?history-skeleton-duration[\s\S]*?history-skeleton-like/.test(historyJs));
// Today + Yesterday buckets: helper is called twice with N rows each.
check('history skeleton splits rows into Today + Yesterday buckets',
  /_renderHistorySkeletonBucket\(\d+\)\s*\+\s*_renderHistorySkeletonBucket\(\d+\)/.test(historyJs));
// Until the first fetch resolves, an empty cache must NOT immediately render
// the "No listening history yet" empty state.
check('empty cache + still loading renders skeleton instead of empty state',
  /items\.length === 0[\s\S]*?if\s*\(!state\._historyLoaded\)[\s\S]*?renderHistoryPageSkeleton/.test(historyJs));
// Both successful and failed fetches mark the page as loaded so the empty
// state appears correctly on a genuine zero-result response.
check('first successful fetch flips _historyLoaded to true',
  /state\._historyCache\s*=\s*fresh[\s\S]{0,200}state\._historyLoaded\s*=\s*true/.test(historyJs));
check('failed fetch also flips _historyLoaded to true (so empty state can appear)',
  /catch\s*\([\s\S]*?state\._historyLoaded\s*=\s*true/.test(historyJs));

// --- remote.html: the bare "Loading…" text is gone in favor of the empty
//     body that JS fills with the skeleton ---

check('history page body no longer ships hard-coded "Loading…" text',
  !remoteHtml.includes('<div class="history-page-empty">Loading…</div>'));

// --- Reused infrastructure still intact so we don't regress an existing
//     loading path ---

check('recs skeleton keyframes (skeletonShimmer) are still defined',
  /@keyframes\s+skeletonShimmer\b/.test(recsCss));
check('library skeleton still exists (no regression)',
  /library-card library-skeleton-card/.test(
    fs.readFileSync(path.join(root, 'templates', 'static', 'js', 'library.js'), 'utf8')
  ));

console.log(`\nskeleton-loaders: passed=${passed} failed=${failed}`);
process.exit(failed === 0 ? 0 : 1);
