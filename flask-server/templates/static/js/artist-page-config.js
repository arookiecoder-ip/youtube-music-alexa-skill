(function () {
  'use strict';
  // Single source of truth for artist-page presentation and interaction policy.
  window.ARTIST_PAGE_CONFIG = {
    hero: { descriptionLimit: 150 },
    sections: [
      { key: 'topSongs', label: 'Top songs', limit: 10 },
      { key: 'albums', label: 'Albums', limit: 12 },
      { key: 'singles', label: 'Singles', limit: 12 },
      { key: 'relatedArtists', label: 'Related artists', limit: 12 }
    ],
    actions: { showShuffle: true, showMix: true, showSubscribe: true, showContextMenu: true }
  };
})();
