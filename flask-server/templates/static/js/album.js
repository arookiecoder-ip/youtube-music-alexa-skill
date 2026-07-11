(function () {
  'use strict';
  var cache = {};

  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function render(data) {
    var hero = document.getElementById('album-hero');
    var list = document.getElementById('album-track-list');
    if (!hero || !list) return;
    hero.innerHTML =
      (data.thumbnail ? '<img src="' + esc(data.thumbnail) + '" alt="">' : '<div class="album-art-placeholder"></div>') +
(function () {
  'use strict';
  var cache = {};

  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function render(data) {
    var hero = document.getElementById('album-hero');
    var list = document.getElementById('album-track-list');
    if (!hero || !list) return;
    hero.innerHTML =
      (data.thumbnail ? '<img src="' + esc(data.thumbnail) + '" alt="">' : '<div class="album-art-placeholder"></div>') +
      '<div class="album-hero-info"><span>Album</span><h1>' + esc(data.title) + '</h1>' +
      '<button class="album-artist-link" type="button" data-channel-id="' + esc(data.channelId) + '">' + esc(data.artist) + '</button>' +
      '<p>' + esc([data.year, (data.tracks || []).length + ' songs'].filter(Boolean).join(' · ')) + '</p>' +
      (data.description ? '<div class="album-description">' + esc(data.description) + '</div>' : '') +
      '<button class="btn-accent album-play-all" type="button" title="Play all" aria-label="Play all"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg></button></div>';

    var queueAddSvg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>';
    var moreSvg = '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>';
    var heartSvg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>';
    var heartFilledSvg = '<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>';

    list.innerHTML = (data.tracks || []).map(function (track, index) {
      var isSameArtist = !track.artist || track.artist === data.artist;
      var artistSpan = isSameArtist ? '' : '<span>' + esc(track.artist) + '</span>';
      var isLiked = typeof window._playlistsData !== 'undefined' && window._playlistsData.liked_songs && window._playlistsData.liked_songs.includes(track.video_id);
      return '<div class="album-track" data-index="' + index + '">' +
        '<span class="album-track-number">' + (index + 1) + '</span>' +
        '<span class="album-track-info"><strong>' + esc(track.title) + '</strong>' + artistSpan + '</span>' +
        '<button class="result-like-btn ' + (isLiked ? 'liked' : '') + '" type="button" title="Like" data-vid="' + esc(track.video_id) + '">' + (isLiked ? heartFilledSvg : heartSvg) + '</button>' +
        '<button class="result-queue-btn" type="button" title="Add to queue">' + queueAddSvg + '</button>' +
        '<button class="result-more-btn" type="button" title="More options">' + moreSvg + '</button>' +
        '<div class="result-more-menu">' +
          '<div class="result-menu-option" data-action="play-next"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg> Play next</div>' +
          '<div class="result-menu-option" data-action="add-to-queue"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg> Add to queue</div>' +
          '<div class="result-menu-option" data-action="play-radio"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4.93 19.07A10 10 0 1 1 19.07 4.93 10 10 0 0 1 4.93 19.07z"/><circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="M2 12h2"/><path d="M20 12h2"/></svg> Play Radio</div>' +
          '<div class="result-menu-option" data-action="save-playlist"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path><line x1="12" y1="11" x2="12" y2="17"></line><line x1="9" y1="14" x2="15" y2="14"></line></svg> Save to Playlist</div>' +
        '</div>' +
        '</div>';
    }).join('') || '<div class="history-modal-empty">This album has no playable tracks.</div>';

    var artistBtn = hero.querySelector('.album-artist-link');
    if (artistBtn) artistBtn.addEventListener('click', function () {
      if (this.dataset.channelId) window.navigateTo('#artist/' + encodeURIComponent(this.dataset.channelId));
    });
    var playAll = hero.querySelector('.album-play-all');
    if (playAll && data.tracks && data.tracks.length) playAll.addEventListener('click', function () {
      window.playFromQueue(data.tracks[0], 0);
    });
    list.querySelectorAll('.album-track').forEach(function (row) {
      var track = data.tracks[Number(row.dataset.index)];
      row.addEventListener('click', function () {
        if (track && window.playFromQueue) window.playFromQueue(track, Number(row.dataset.index));
      });
      var qBtn = row.querySelector('.result-queue-btn');
      if (qBtn) qBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        if (window.addToQueue) window.addToQueue(track, 'last');
      });
      var likeBtn = row.querySelector('.result-like-btn');
      if (likeBtn) likeBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        if (window.toggleLike) window.toggleLike(track, likeBtn);
      });
      var moreBtn = row.querySelector('.result-more-btn');
      var moreMenu = row.querySelector('.result-more-menu');
      if (moreBtn && moreMenu) {
        moreMenu.addEventListener('click', function (e) { e.stopPropagation(); });
        moreBtn.addEventListener('click', function (e) {
          e.stopPropagation();
          var wasOpen = moreMenu.classList.contains('open');
          if (window._closeAllMoreMenus) window._closeAllMoreMenus();
          if (!wasOpen) {
            moreBtn.classList.add('open');
            var rect = moreBtn.getBoundingClientRect();
            var menuHeight = 88;
            var spaceBelow = window.innerHeight - rect.bottom;
            var openAbove = spaceBelow < menuHeight + 8;
            moreMenu.style.left = '';
            moreMenu.style.top = '';
            moreMenu.style.bottom = '';
            moreMenu.style.right = '';
            if (openAbove) {
              moreMenu.classList.add('above');
              moreMenu.style.bottom = (window.innerHeight - rect.top + 4) + 'px';
            } else {
              moreMenu.classList.remove('above');
              moreMenu.style.top = (rect.bottom + 4) + 'px';
            }
            var left = rect.right - 180;
            if (left < 8) left = 8;
            moreMenu.style.left = left + 'px';
            moreMenu.classList.add('open');
            moreMenu._home = row;
            document.body.appendChild(moreMenu);
          }
        });
        row.addEventListener('contextmenu', function (e) {
          e.preventDefault();
          e.stopPropagation();
          moreBtn.click();
        });
        var playNext = moreMenu.querySelector('[data-action="play-next"]');
        if (playNext) playNext.addEventListener('click', function (e) {
          e.stopPropagation();
          if (window._closeAllMoreMenus) window._closeAllMoreMenus();
          if (window.addToQueue) window.addToQueue(track, 'next');
        });
        var addQueue = moreMenu.querySelector('[data-action="add-to-queue"]');
        if (addQueue) addQueue.addEventListener('click', function (e) {
          e.stopPropagation();
          if (window._closeAllMoreMenus) window._closeAllMoreMenus();
          if (window.addToQueue) window.addToQueue(track, 'last');
        });
        var playRadio = moreMenu.querySelector('[data-action="play-radio"]');
        if (playRadio) playRadio.addEventListener('click', function (e) {
          e.stopPropagation();
          if (window._closeAllMoreMenus) window._closeAllMoreMenus();
          if (window.playResult) window.playResult(track, false, true);
        });
        var saveOpt = moreMenu.querySelector('[data-action="save-playlist"]');
        if (saveOpt) saveOpt.addEventListener('click', function (e) {
          e.stopPropagation();
          if (window._closeAllMoreMenus) window._closeAllMoreMenus();
          if (window.openAddToPlaylistModal) window.openAddToPlaylistModal(track);
        });
      }
    });
  }

  async function loadAlbum(browseId) {
    var hero = document.getElementById('album-hero');
    var list = document.getElementById('album-track-list');
    if (hero) hero.innerHTML = '<div class="history-modal-empty">Loading album…</div>';
    if (list) list.innerHTML = '';
    try {
      var data = cache[browseId] || await window.api('/api/album/' + encodeURIComponent(browseId));
      cache[browseId] = data;
      render(data);
    } catch (e) {
      if (hero) hero.innerHTML = '<div class="history-modal-empty">Could not load this album.</div>';
      if (window.toast) window.toast(e.message || 'Could not load album', 'error');
    }
  }

  // back button removed
  window.loadAlbum = loadAlbum;
})();
