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

  function songActions(track) {
    var liked = window._playlistsData && window._playlistsData.liked_songs &&
      window._playlistsData.liked_songs.includes(track.video_id);
    var like = '<svg viewBox="0 0 24 24" fill="' + (liked ? 'currentColor' : 'none') + '" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>';
    var more = '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>';
    var duration = window.formatTrackDuration ? window.formatTrackDuration(track) : '';
    return (duration ? '<span class="track-duration">' + esc(duration) + '</span>' : '') +
      '<button class="result-like-btn' + (liked ? ' liked' : '') + '" type="button" title="Like" data-vid="' + esc(track.video_id) + '">' + like + '</button>' +
      '<button class="result-more-btn" type="button" title="More options">' + more + '</button>';
  }

  function wireSongActions(row, track) {
    row.querySelector('.result-like-btn').addEventListener('click', function (event) {
      event.stopPropagation();
      if (window.toggleLike) window.toggleLike(track, this);
    });
    row.querySelector('.result-more-btn').addEventListener('click', function (event) {
      event.stopPropagation();
      if (window.openSongContextMenu) window.openSongContextMenu(event, track);
    });
  }

  function render(data) {
    var currentAlbumId = data.browseId || data.browse_id || data.albumId || '';
    var hero = document.getElementById('album-hero');
    var list = document.getElementById('album-track-list');
    if (!hero || !list) return;

    var tracks = data.tracks || [];
    var title = data.title || 'Album';
    var meta = [data.year, tracks.length + (tracks.length === 1 ? ' song' : ' songs')]
      .filter(Boolean).join(' \u00b7 ');
    var cover = data.thumbnail
      ? '<div class="playlist-collage playlist-collage-single"><img src="' + esc(data.thumbnail) + '" alt="" loading="lazy"></div>'
      : '<div class="playlist-collage playlist-collage-single"><div class="collage-placeholder"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg></div></div>';

    hero.className = 'album-hero playlist-detail-hero';
    // Same secondary-button set as the playlist detail page (Shuffle, Play
    // next, Share), so the album hero and playlist hero look like one design
    // system. Albums are not user-editable, so the "More" rename/delete menu
    // is intentionally omitted — there is nothing meaningful for it to do.
    var shuffleBtnHtml = '<button class="playlist-hero-btn playlist-hero-shuffle" type="button" title="Shuffle" aria-label="Shuffle"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/><polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/><line x1="4" y1="4" x2="9" y2="9"/></svg></button>';
    var playNextBtnHtml = '<button class="playlist-hero-btn playlist-hero-play-next" type="button" title="Play next" aria-label="Play next"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg></button>';
    var shareBtnHtml = '<button class="playlist-hero-btn playlist-hero-share is-muted" type="button" title="Sharing unavailable for this album" aria-label="Sharing unavailable for this album" aria-disabled="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/></svg></button>';
    var moreBtnHtml = '<button class="playlist-hero-btn playlist-hero-more is-muted" type="button" title="Options unavailable for this album" aria-label="Options unavailable for this album" aria-disabled="true"><svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg></button>';
    var actionsRowHtml = tracks.length
      ? '<div class="playlist-detail-hero-actions"><div class="playlist-hero-actions-left">' + shuffleBtnHtml + playNextBtnHtml + '</div><button class="playlist-hero-play album-play-all" type="button" aria-label="Play ' + esc(title) + '"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg></button><div class="playlist-hero-actions-right">' + shareBtnHtml + moreBtnHtml + '</div></div>'
      : '';
    var heroArtistIds = Array.isArray(data.artists)
      ? data.artists.map(function(artist) {
          return artist && (artist.id || artist.browseId || artist.channelId) || '';
        })
      : (data.channelId ? [data.channelId] : []);
    var heroArtistHtml = data.artist && window.artistLinksHtml
      ? window.artistLinksHtml(data.artist, heroArtistIds)
      : esc(data.artist || '');
    hero.innerHTML = cover +
      '<div class="playlist-detail-hero-info">' +
        '<h1 class="playlist-detail-page-title playlist-detail-hero-name">' + esc(title) + '</h1>' +
        (data.artist ? '<div class="album-artist-link">' + heroArtistHtml + '</div>' : '') +
        (data.description ? '<div class="playlist-detail-hero-desc">' + esc(data.description) + '</div>' : '') +
        '<div class="playlist-detail-hero-meta">' + esc(meta) + '</div>' +
        actionsRowHtml +
      '</div>';
    if (window.wireArtistLinks) window.wireArtistLinks(hero);

    list.className = 'album-track-list history-list';
    list.innerHTML = '';
    if (!tracks.length) {
      list.innerHTML = '<div class="history-modal-empty">This album has no playable tracks.</div>';
    } else {
      tracks.forEach(function (track, index) {
        var artistParts = [];
        var artistChannelIds = [];
        if (Array.isArray(track.artists)) {
          track.artists.forEach(function(a) {
            var name = typeof a === 'string' ? a : (a && a.name);
            if (name) {
              artistParts.push(name);
              artistChannelIds.push((a && a.id) || '');
            }
          });
        }
        var artist = artistParts.length ? artistParts.join(', ') : trackArtist(track, data.artist);
        var thumbnail = track.thumbnail || data.thumbnail || '/static/default-art.png';
        var wrapper = document.createElement('div');
        wrapper.className = 'result-swipe-wrapper';
        wrapper.dataset.videoId = track.video_id || track.videoId || '';
        wrapper._songContextTrack = {
          video_id: track.video_id || track.videoId || '',
          title: track.title || '',
          artist: artist,
          thumbnail: thumbnail,
          duration: track.duration || '',
          duration_seconds: track.duration_seconds || 0,
          album_id: currentAlbumId
        };
        var row = document.createElement('div');
        row.className = 'history-item album-track';
        var contextTrack = wrapper._songContextTrack;
        row.innerHTML =
          '<div class="playlist-track-art"><img src="' + esc(thumbnail) + '" class="queue-thumb" loading="lazy" alt="" onload="this.classList.add(\'loaded\')" onerror="this.style.opacity=\'1\'">' +
          '<span class="playlist-track-playback-indicator" aria-hidden="true"><svg class="playlist-track-play-glyph" viewBox="0 0 24 24" fill="currentColor"><polygon points="7,4 20,12 7,20"/></svg>' +
          '<span class="music-bars"><i></i><i></i><i></i><i></i><i></i></span></span></div>' +
          '<div class="queue-info"><div class="queue-title">' + esc(track.title || '') + '</div>' +
          '<div class="queue-artist">' + window.artistLinksHtml(artist, artistChannelIds.length ? artistChannelIds : (track.channelId || track.channel_id || '')) + '</div></div>' + songActions(contextTrack);
        row.addEventListener('click', function () {
          if (window.playFromQueue) window.playFromQueue(track, index);
        });
        if (window.wireArtistLinks) window.wireArtistLinks(row);
        wireSongActions(row, contextTrack);
        wrapper.appendChild(row);
        list.appendChild(wrapper);
      });
      if (window.syncTrackPlaybackIndicators) window.syncTrackPlaybackIndicators();
    }

    var playAll = hero.querySelector('.album-play-all');
    if (playAll) playAll.addEventListener('click', function () {
      var firstRow = list.querySelector('.history-item');
      if (firstRow) firstRow.click();
    });
    // Shuffle: enable device shuffle mode (same path the playbar Shuffle
    // button uses), then start playback from the first row.
    var heroShuffle = hero.querySelector('.playlist-hero-shuffle');
    if (heroShuffle) heroShuffle.addEventListener('click', async function () {
      var firstRow = list.querySelector('.history-item');
      if (!firstRow) return;
      try {
        var sb = document.getElementById('shuffle-btn');
        if (sb && window.api && !sb.classList.contains('shuffle-active')) {
          await window.api('/alexa/shuffle_queue/', {});
          sb.classList.add('shuffle-active');
        }
      } catch (e) { /* best-effort: still play the row even if shuffle toggle fails */ }
      firstRow.click();
    });
    // Play next: queue the first track of the album at the "next" slot.
    // Reuses the existing addToQueue helper from queue.js.
    var heroPlayNext = hero.querySelector('.playlist-hero-play-next');
    if (heroPlayNext) heroPlayNext.addEventListener('click', function () {
      var firstWrapper = list.querySelector('.result-swipe-wrapper');
      var track = firstWrapper && firstWrapper._songContextTrack;
      if (track && window.addToQueue) window.addToQueue(track, 'next');
    });
    // Share: copy the current page URL to the clipboard. Falls back to the
    // legacy execCommand path on browsers without the async clipboard API.
    var heroShare = hero.querySelector('.playlist-hero-share');
    if (heroShare) heroShare.addEventListener('click', async function () {
      if (heroShare.classList.contains('is-muted')) return;
      var url = window.location.href;
      if (navigator.share) {
        try {
          await navigator.share({ title: data.title || 'Album', text: 'Listen to ' + (data.title || 'this album'), url: url });
          return;
        } catch (e) {
          if (e && e.name === 'AbortError') return;
        }
      }
      var copied = false;
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(url);
          copied = true;
        }
      } catch (e) { /* fall through to legacy path */ }
      if (!copied) {
        var tmp = document.createElement('textarea');
        tmp.value = url;
        tmp.style.position = 'fixed';
        tmp.style.opacity = '0';
        document.body.appendChild(tmp);
        tmp.select();
        try { copied = document.execCommand('copy'); } catch (e) { copied = false; }
        document.body.removeChild(tmp);
      }
      if (window.toast) {
        window.toast(copied ? 'Link copied to clipboard' : 'Could not copy link', copied ? 'ok' : 'error');
      }
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
