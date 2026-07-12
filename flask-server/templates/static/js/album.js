(function () {
  'use strict';
  var cache = {};

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function trackArtist(track, fallback) {
    if (track.artist) return track.artist;
    if (Array.isArray(track.artists)) {
      return track.artists.map(function (artist) {
        return typeof artist === 'string' ? artist : artist && artist.name;
      }).filter(Boolean).join(', ');
    }
    return fallback || '';
  }

  function render(data) {
    var hero = document.getElementById('album-hero');
    var list = document.getElementById('album-track-list');
    if (!hero || !list) return;

    var tracks = data.tracks || [];
    var title = data.title || 'Album';
    var meta = [data.year, tracks.length + (tracks.length === 1 ? ' song' : ' songs')]
      .filter(Boolean).join(' \u00b7 ');
    var cover = data.thumbnail
      ? '<div class="playlist-collage playlist-collage-single"><img src="' + esc(data.thumbnail) + '" alt="" loading="eager"></div>'
      : '<div class="playlist-collage playlist-collage-single"><div class="collage-placeholder"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg></div></div>';

    hero.className = 'album-hero playlist-detail-hero';
    hero.innerHTML = cover +
      '<div class="playlist-detail-hero-info">' +
        '<h1 class="playlist-detail-page-title playlist-detail-hero-name">' + esc(title) + '</h1>' +
        (data.artist ? '<button class="album-artist-link" type="button" data-channel-id="' + esc(data.channelId) + '">' + esc(data.artist) + '</button>' : '') +
        (data.description ? '<div class="playlist-detail-hero-desc">' + esc(data.description) + '</div>' : '') +
        '<div class="playlist-detail-hero-meta">' + esc(meta) + '</div>' +
        (tracks.length ? '<div class="playlist-detail-hero-actions"><span class="playlist-hero-actions-left"></span><button class="playlist-hero-play album-play-all" type="button" aria-label="Play ' + esc(title) + '"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg></button><span class="playlist-hero-actions-right"></span></div>' : '') +
      '</div>';

    list.className = 'album-track-list history-list';
    list.innerHTML = '';
    if (!tracks.length) {
      list.innerHTML = '<div class="history-modal-empty">This album has no playable tracks.</div>';
    } else {
      tracks.forEach(function (track, index) {
        var artist = trackArtist(track, data.artist);
        var thumbnail = track.thumbnail || data.thumbnail || '/static/default-art.png';
        var wrapper = document.createElement('div');
        wrapper.className = 'result-swipe-wrapper';
        wrapper.dataset.videoId = track.video_id || track.videoId || '';
        wrapper._songContextTrack = {
          video_id: track.video_id || track.videoId || '',
          title: track.title || '',
          artist: artist,
          thumbnail: thumbnail
        };
        var row = document.createElement('div');
        row.className = 'history-item album-track';
        row.innerHTML =
          '<div class="playlist-track-art"><img src="' + esc(thumbnail) + '" class="queue-thumb" loading="lazy" alt="" onload="this.classList.add(\'loaded\')" onerror="this.style.opacity=\'1\'"></div>' +
          '<div class="queue-info"><div class="queue-title">' + esc(track.title || '') + '</div>' +
          '<div class="queue-artist">' + esc(artist) + '</div></div>';
        row.addEventListener('click', function () {
          if (window.playFromQueue) window.playFromQueue(track, index);
        });
        wrapper.appendChild(row);
        list.appendChild(wrapper);
      });
    }

    var artistButton = hero.querySelector('.album-artist-link');
    if (artistButton) artistButton.addEventListener('click', function () {
      if (this.dataset.channelId) {
        if (window.preloadNavigateArtist) window.preloadNavigateArtist(this.dataset.channelId);
        else window.navigateTo('#artist/' + encodeURIComponent(this.dataset.channelId));
      }
    });
    var playAll = hero.querySelector('.album-play-all');
    if (playAll) playAll.addEventListener('click', function () {
      var firstRow = list.querySelector('.history-item');
      if (firstRow) firstRow.click();
    });
  }

  async function loadAlbum(browseId) {
    var hero = document.getElementById('album-hero');
    var route = '#album/' + encodeURIComponent(browseId);
    var preloaded = window.consumePreload ? window.consumePreload(route) : null;
    var data = preloaded || cache[browseId];

    try {
      if (!data) data = await window.api('/api/album/' + encodeURIComponent(browseId));
      cache[browseId] = data;
      render(data);
    } catch (error) {
      if (hero) hero.innerHTML = '<div class="history-modal-empty">Could not load this album.</div>';
      if (window.toast) window.toast(error.message || 'Could not load album', 'error');
    }

    var section = document.getElementById('album-section');
    if (section) section.hidden = false;
  }

  window.loadAlbum = loadAlbum;
})();
