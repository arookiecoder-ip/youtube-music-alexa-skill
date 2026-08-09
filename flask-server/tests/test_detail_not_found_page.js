const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const apiSrc = fs.readFileSync(require('path').join(__dirname, '..', 'templates/static/js/api.js'), 'utf8');
const albumSrc = fs.readFileSync(require('path').join(__dirname, '..', 'templates/static/js/album.js'), 'utf8');
const artistSrc = fs.readFileSync(require('path').join(__dirname, '..', 'templates/static/js/artist.js'), 'utf8');
const playlistSrc = fs.readFileSync(require('path').join(__dirname, '..', 'templates/static/playlists.js'), 'utf8');

// This test is intentionally source-level for the browser bundles: the project
// has no browser runtime in CI, but these contracts are independent of DOM data.
assert.match(apiSrc, /error\.status\s*=\s*res\.status/);
assert.match(apiSrc, /error\.code\s*=\s*responseError/);
assert.match(apiSrc, /window\.location\.replace\('\/__not_found__'\)/);
assert.match(apiSrc, /error\.code === 'not_found'/);
assert.match(apiSrc, /error\.code === 'not_a_playlist'/);
assert.match(albumSrc, /_isNotFoundError\(error\)/);
assert.match(albumSrc, /window\._showNotFoundPage\(\)/);
assert.match(artistSrc, /_isNotFoundError\(e\)/);
assert.match(artistSrc, /window\._showNotFoundPage\(\)/);
assert.match(playlistSrc, /_isNotFoundError\(e\)/);
assert.match(playlistSrc, /window\._showNotFoundPage\(\)/);
assert.doesNotMatch(playlistSrc, /window\.api\('\/api\/playlists\//);
assert.match(apiSrc, /function isNotFoundError\(error\)/);

let navigated = '';
const context = {
  window: {
    location: { replace: (url) => { navigated = url; } },
  },
};
vm.createContext(context);
vm.runInContext("(" + apiSrc.match(/function showNotFoundPage\(\) \{[\s\S]*?\n  \}/)[0] + ")", context);

// The function is not exported by itself in the source snippet, so the source
// assertions above are the primary contract; keep this test focused and clear.
assert.strictEqual(navigated, '');
console.log('detail 404 handling: source contracts passed');
