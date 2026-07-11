/**
 * like.js — Liked Songs state, loading, and toggle.
 *
 * Exposes:
 *   window._playlistsData   { liked_songs: string[] }   (video-id list)
 *   window.loadLikedSongs()  async → fetches /api/liked_songs/ and refreshes UI
 *   window.toggleLike(item, btn?)  async → like/unlike a track, update UI
 */
(function () {
  'use strict';

  // Shared data store referenced by player.js, queue.js, search.js, etc.
  var _playlistsData = window._playlistsData = window._playlistsData || { liked_songs: [] };

  /**
   * Fetch the current liked video IDs from the server and refresh all heart
   * buttons on the page.
   */
  async function loadLikedSongs() {
    if (!window.IS_AUTHENTICATED || window.JAM_GUEST) return;
    try {
      var data = await window.api('/api/liked_songs/');
      if (Array.isArray(data.liked_songs)) {
        _playlistsData.liked_songs = data.liked_songs;
      }
    } catch (e) {
      // Non-fatal: heart buttons just stay in their current state
      console.warn('[like.js] loadLikedSongs failed:', e);
    }
    // Refresh the now-playing heart buttons
    if (window.refreshNpLikeButton) window.refreshNpLikeButton();
  }

  /**
   * Toggle like/unlike for a track.
   *
   * @param {object} item   Must have .video_id (and optionally .title)
   * @param {Element|null} btn  The heart button element (optimistic UI update)
   * @returns {Promise<void>}
   */
  async function toggleLike(item, btn) {
    if (!item || !item.video_id) return;
    if (!window.IS_AUTHENTICATED) {
      if (window.toast) window.toast('Sign in with a YouTube Music account to like songs.', 'error');
      return;
    }

    var videoId = item.video_id;
    var liked_songs = _playlistsData.liked_songs || [];
    var isCurrentlyLiked = liked_songs.includes(videoId);
    var newAction = isCurrentlyLiked ? 'INDIFFERENT' : 'LIKE';
    var willBeLiked = !isCurrentlyLiked;

    // --- Optimistic update ---
    if (willBeLiked) {
      _playlistsData.liked_songs = [videoId].concat(liked_songs.filter(function(v) { return v !== videoId; }));
    } else {
      _playlistsData.liked_songs = liked_songs.filter(function(v) { return v !== videoId; });
    }

    // Update the specific button that was clicked
    _applyLikeStyle(btn, willBeLiked);

    // Refresh all other heart buttons that show this video
    _refreshAllLikeButtons(videoId, willBeLiked);

    // Refresh now-playing like buttons
    if (window.refreshNpLikeButton) window.refreshNpLikeButton();

    try {
      var res = await window.api('/alexa/like/', {
        video_id: videoId,
        action: newAction
      });
      if (!res.ok) {
        throw new Error(res.error || 'Like failed');
      }
      if (window.toast) {
        window.toast(willBeLiked ? '\u2764 Added to Liked Songs' : 'Removed from Liked Songs', 'ok');
      }
    } catch (e) {
      // Revert optimistic update on failure
      if (willBeLiked) {
        _playlistsData.liked_songs = (_playlistsData.liked_songs || []).filter(function(v) { return v !== videoId; });
      } else {
        _playlistsData.liked_songs = [videoId].concat((_playlistsData.liked_songs || []).filter(function(v) { return v !== videoId; }));
      }
      _applyLikeStyle(btn, !willBeLiked);
      _refreshAllLikeButtons(videoId, !willBeLiked);
      if (window.refreshNpLikeButton) window.refreshNpLikeButton();
      if (window.toast) window.toast('Could not update like: ' + (e.message || e), 'error');
    }
  }

  /** Apply liked/unliked styling to a specific button element. */
  function _applyLikeStyle(btn, liked) {
    if (!btn) return;
    var heartFilled = '<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="1" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>';
    var heartEmpty = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>';
    btn.innerHTML = liked ? heartFilled : heartEmpty;
    btn.classList.toggle('liked', !!liked);
    btn.title = liked ? 'Dislike' : 'Like';
  }

  /** Refresh all .result-like-btn elements on the page that match a video ID. */
  function _refreshAllLikeButtons(videoId, liked) {
    var heartFilled = '<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="1" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>';
    var heartEmpty = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>';
    document.querySelectorAll('.result-like-btn[data-vid="' + videoId + '"]').forEach(function(b) {
      b.innerHTML = liked ? heartFilled : heartEmpty;
      b.classList.toggle('liked', !!liked);
      b.title = liked ? 'Dislike' : 'Like';
    });
  }

  // Expose globals
  window._playlistsData = _playlistsData;
  window.loadLikedSongs = loadLikedSongs;
  window.toggleLike = toggleLike;

  // Load liked songs on init if authenticated
  if (window.IS_AUTHENTICATED && !window.JAM_GUEST) {
    var state = window.__appState = window.__appState || {};
    // Defer so other scripts (player.js, etc.) have time to set up
    if (state._loggedIn) {
      loadLikedSongs();
    }
  }

  // Also expose as loadLibrary hook: device.js calls window.loadLibrary on login
  // We piggyback liked songs onto the same trigger via device.js changes,
  // but also wire it here as a fallback.
  var _origLoadLibrary = window.loadLibrary;
  window.loadLibrary = async function() {
    var p1 = _origLoadLibrary ? _origLoadLibrary() : Promise.resolve();
    var p2 = loadLikedSongs();
    return Promise.all([p1, p2]);
  };
})();
