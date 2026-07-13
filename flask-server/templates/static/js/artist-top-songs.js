(function () {
  'use strict';

  window.navigateArtistTopSongs = function (channelId) {
    if (!channelId || !window.navigateTo) return;
    window.navigateTo('#artist/' + encodeURIComponent(channelId) + '/songs');
  };
})();
