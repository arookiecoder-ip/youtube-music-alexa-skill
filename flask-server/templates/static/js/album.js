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

    list.innerHTML = (data.tracks || []).map(function (track, index) {
      var isSameArtist = !track.artist || track.artist === data.artist;
      var artistSpan = isSameArtist ? '' : '<span>' + esc(track.artist) + '</span>';
      return '<button class="album-track" type="button" data-index="' + index + '">' +
        '<span class="album-track-number">' + (index + 1) + '</span>' +
        '<span class="album-track-info"><strong>' + esc(track.title) + '</strong>' + artistSpan + '</span>' +
        '</button>';
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
      row.addEventListener('click', function () {
        var track = data.tracks[Number(this.dataset.index)];
        if (track && window.playFromQueue) window.playFromQueue(track, Number(this.dataset.index));
      });
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

  var back = document.getElementById('album-back');
  if (back) back.addEventListener('click', function () { history.back(); });
  window.loadAlbum = loadAlbum;
})();
