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
    var videoId = track.video_id || track.videoId || '';
    var liked = window._playlistsData && window._playlistsData.liked_songs &&
      window._playlistsData.liked_songs.includes(videoId);
    var like = '<svg viewBox="0 0 24 24" fill="' + (liked ? 'currentColor' : 'none') + '" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>';
    var more = '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>';
    var duration = window.formatTrackDuration ? window.formatTrackDuration(track) : '';
    return (duration ? '<span class="track-duration playlist-track-duration">' + esc(duration) + '</span>' : '') +
      '<button class="result-like-btn' + (liked ? ' liked' : '') + '" type="button" title="' + (liked ? 'Dislike' : 'Like') + '" data-vid="' + esc(videoId) + '">' + like + '</button>' +
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

  function detailNodes() {
    var body = document.getElementById('playlist-detail-body');
    if (!body) return null;
    body.innerHTML = '<div id="collection-detail-hero"></div><div class="history-list" id="collection-detail-list"></div>';
    return {
      body: body,
      hero: document.getElementById('collection-detail-hero'),
      list: document.getElementById('collection-detail-list')
    };
  }

  function render(data) {
    var currentAlbumId = data.browseId || data.browse_id || data.albumId || '';
    var nodes = detailNodes();
    if (!nodes) return;
    var hero = nodes.hero;
    var list = nodes.list;

    var tracks = data.tracks || [];
    var title = data.title || 'Album';
    var meta = [data.year, tracks.length + (tracks.length === 1 ? ' song' : ' songs')]
      .filter(Boolean).join(' \u00b7 ');
    var cover = data.thumbnail
      ? '<div class="playlist-collage playlist-collage-single"><img src="' + esc(data.thumbnail) + '" alt="" loading="lazy"></div>'
      : '<div class="playlist-collage playlist-collage-single"><div class="collage-placeholder"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg></div></div>';

    // Same secondary-button set as the playlist detail page (Shuffle, Play
    // next, Share), so the album hero and playlist hero look like one design
    // system. Albums are not user-editable, so the "More" rename/delete menu
    // is intentionally omitted — there is nothing meaningful for it to do.
    var heroArtistIds = Array.isArray(data.artists)
      ? data.artists.map(function(artist) {
          return artist && (artist.id || artist.browseId || artist.channelId) || '';
        })
      : (data.channelId ? [data.channelId] : []);
    var heroArtistHtml = data.artist && window.artistLinksHtml
      ? window.artistLinksHtml(data.artist, heroArtistIds)
      : esc(data.artist || '');
    if (window.CollectionRenderer) {
      hero.outerHTML = window.CollectionRenderer.renderDetailHero({
        id: 'collection-detail-hero',
        className: 'playlist-detail-hero',
        coverHtml: cover,
        title: title,
        titleTag: 'h1',
        artistHtml: heroArtistHtml,
        description: data.description || '',
        meta: meta,
        showActions: !!tracks.length,
        showShare: true,
        showMore: true,
        moreDisabled: true
      });
      hero = document.getElementById('collection-detail-hero');
    }
    if (window.wireArtistLinks) window.wireArtistLinks(hero);

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
        row.className = 'history-item';
        var contextTrack = wrapper._songContextTrack;
        row.innerHTML =
          '<div class="playlist-track-num">' + (index + 1) + '</div>' +
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

    var playAll = hero.querySelector('.playlist-hero-play');
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
    // Share the canonical YouTube Music album/single URL, not the app route.
    var heroShare = hero.querySelector('.playlist-hero-share');
    if (heroShare) heroShare.addEventListener('click', async function () {
      var url = 'https://music.youtube.com/browse/' + encodeURIComponent(currentAlbumId);
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
    var body = document.getElementById('playlist-detail-body');
    var route = '#album/' + encodeURIComponent(browseId);
    var preloaded = window.consumePreload ? window.consumePreload(route) : null;
    var data = preloaded || cache[browseId];

    // Albums use same loader as playlist detail. Keep loading state inside
    // shared track-list component; no album-specific spinner or CSS.
    if (!data && body) {
      body.innerHTML = window.CollectionRenderer
        ? window.CollectionRenderer.renderLoadingState('Loading songs…')
        : '<div class="playlist-loading-indicator visible" role="status"><span class="playlist-loading-spinner" aria-hidden="true"></span><span>Loading songs…</span></div>';
    }

    try {
      if (!data) data = await window.api('/api/album/' + encodeURIComponent(browseId));
      cache[browseId] = data;
      render(data);
    } catch (error) {
      if (body) body.innerHTML = '<div class="history-modal-empty">Could not load this album.</div>';
      if (window.toast) window.toast(error.message || 'Could not load album', 'error');
    }

    var overlay = document.getElementById('playlist-detail-modal-overlay');
    if (overlay) {
      overlay.dataset.playlistId = String(browseId);
      overlay.classList.add('open');
    }
  }

  window.loadAlbum = loadAlbum;
})();
