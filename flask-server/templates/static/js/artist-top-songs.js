(function () {
  'use strict';

  window.navigateArtistTopSongs = function (channelId) {
    if (!channelId) return;
    if (window.preloadNavigateArtistSongs) window.preloadNavigateArtistSongs(channelId);
    else if (window.navigateTo) window.navigateTo('#artist/' + encodeURIComponent(channelId) + '/songs');
  };
})();
