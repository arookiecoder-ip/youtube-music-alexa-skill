(function () {
  'use strict';
  const playerTrace = (event, details) => window.__playerDebugLog && window.__playerDebugLog(event, details);
  function escHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  const state = window.__appState = window.__appState || {};
  if (state.isPlaying === undefined) state.isPlaying = false;
  if (state.lastActionAt === undefined) state.lastActionAt = 0;
  if (state.lastActionIntent === undefined) state.lastActionIntent = null;
  if (state._lastPlayAttemptVideoId === undefined) state._lastPlayAttemptVideoId = '';
  if (state.GRACE_MS === undefined) state.GRACE_MS = 8000;
  if (state._currentVideoId === undefined) state._currentVideoId = '';
  if (state._currentThumbnail === undefined) state._currentThumbnail = '';
  if (state._hasTrack === undefined) state._hasTrack = false;
  if (state._resultsOpen === undefined) state._resultsOpen = false;
  if (state._searchResults === undefined) state._searchResults = [];
  if (state._searchSeq === undefined) state._searchSeq = 0;
  if (state._lastQueueJson === undefined) state._lastQueueJson = '';
  if (state._lastQueueIndex === undefined) state._lastQueueIndex = -1;
  if (state.volumeUserActive === undefined) state.volumeUserActive = false;
  if (state.volumeGraceUntil === undefined) state.volumeGraceUntil = 0;
  if (state.VOLUME_GRACE_MS === undefined) state.VOLUME_GRACE_MS = 4000;
  if (state._volCommandSeq === undefined) state._volCommandSeq = 0;
  if (state.playbackProcessing === undefined) state.playbackProcessing = false;

const deviceEl = document.getElementById('device');
const volumeEl = document.getElementById('volume');

// These controls belong to the mobile player only. Keep that separation in
// the DOM instead of relying on later CSS rules to undo their visibility on
// desktop.
const mobilePlayerMedia = window.matchMedia('(max-width: 899px)');
function syncMobilePlayerMarkup() {
  const showMobileControls = mobilePlayerMedia.matches;
  for (const el of document.querySelectorAll('.mobile-np-controls, .mobile-np-progress')) {
    el.hidden = !showMobileControls;
  }
}
syncMobilePlayerMarkup();
if (mobilePlayerMedia.addEventListener) {
  mobilePlayerMedia.addEventListener('change', syncMobilePlayerMarkup);
} else {
  mobilePlayerMedia.addListener(syncMobilePlayerMarkup);
}

function syncTrackPlaybackIndicators() {
  const currentId = state._currentVideoId || '';
  for (const card of document.querySelectorAll(
    '.home-item[data-video-id], .result-swipe-wrapper[data-video-id]'
  )) {
    const isCurrent = !!currentId && card.dataset.videoId === currentId;
    card.classList.toggle('current-track', isCurrent);
    card.classList.toggle('playing', isCurrent && state.isPlaying);
  }
}

// On mobile, most song titles are direct playback affordances. Home feed track
// titles are the exception: home.js owns them so they open the track album.
document.addEventListener('click', (event) => {
  if (!window.matchMedia('(max-width: 899px)').matches) return;
  const title = event.target.closest(
    '.home-item-title, .hscroll-card-title, .artist-song-title, .queue-title, .result-title, .top-result-title'
  );
  if (!title) return;
  const root = title.closest('[data-video-id]');
  if (!root || !root.dataset.videoId || typeof window.playFromQueue !== 'function') return;
  if (root.matches('.home-item[data-kind="track"], .mood-songs-shelf .home-item-song')) return;

  const track = root._songContextTrack || {
    video_id: root.dataset.videoId,
    title: title.textContent.trim(),
    artist: root.querySelector('.home-item-subtitle, .artist-song-artist, .queue-artist, .result-artist, .top-result-subtitle')?.textContent.trim() || '',
    thumbnail: root.querySelector('img')?.src || ''
  };
  event.preventDefault();
  event.stopPropagation();
  window.playFromQueue(track);
}, true);

function syncPlayPause() {
  for (const btn of [document.getElementById('pp-btn'), document.getElementById('np-page-art-overlay'), document.getElementById('mobile-np-play')]) {
    if (!btn) continue;
    const p = btn.querySelector('.icon-play, .mobile-np-play-icon');
    if (p) p.style.display = state.isPlaying ? 'none' : 'block';
    const pa = btn.querySelector('.icon-pause, .mobile-np-pause-icon');
    if (pa) pa.style.display = state.isPlaying ? 'block' : 'none';
    const processing = !!state.playbackProcessing;
    btn.classList.toggle('playback-processing', processing);
    btn.setAttribute('aria-busy', processing ? 'true' : 'false');
    const label = processing ? 'Processing playback…' : (state.isPlaying ? 'Pause' : 'Play');
    btn.title = label;
    btn.setAttribute('aria-label', label);
  }
  syncTrackPlaybackIndicators();
  if (window.updateQueuePlaying) window.updateQueuePlaying(state.isPlaying);
  if (window.syncPageTitle) window.syncPageTitle();
}

// These controls are permanent DOM nodes, so wire their proxy actions once.
// Binding them from syncPlayPause() accumulated another listener after every
// SSE/state repaint and eventually made one mobile tap dispatch many commands.
(function wireMobileNowPlayingControls() {
  const mobileNpPlay = document.getElementById('mobile-np-play');
  const playPauseButton = document.getElementById('pp-btn');
  if (mobileNpPlay && playPauseButton) {
    // The artwork overlay is visual-only (and is hidden on the mobile
    // now-playing route), so forwarding to it leaves the mobile button with
    // no action. The persistent play/pause button owns the command logic.
    mobileNpPlay.addEventListener('click', () => playPauseButton.click());
  }
  const mobileNpShuffle = document.getElementById('mobile-np-shuffle');
  const shuffleButton = document.getElementById('shuffle-btn');
  if (mobileNpShuffle && shuffleButton) {
    mobileNpShuffle.addEventListener('click', () => shuffleButton.click());
  }
  const mobileNpLike = document.getElementById('mobile-np-like');
  const miniLikeButton = document.getElementById('np-like-btn');
  if (mobileNpLike && miniLikeButton) {
    mobileNpLike.addEventListener('click', () => miniLikeButton.click());
  }
})();

/* ---- now-playing display (single element, no dual placeholder bug) ---- */
// Last-rendered track fingerprint — used to skip redundant DOM writes.
let _lastNpFingerprint = '';
// Optimistic adjacent-track identity used by the mobile swipe-to-skip banner:
// after a committed swipe the hero already shows the next/previous cover while
// the Alexa round-trip is in flight, and this id lets a second quick swipe
// compute its own neighbour from the *shown* track instead of the stale
// server track. Cleared once a confirmed now-playing snapshot lands (or left
// to expire so a failed nav can't poison future gestures).
let _swipeOptimisticVideoId = '';
let _swipeOptimisticAt = 0;
// Keep the sharp foreground artwork and the first ambient-preview artwork
// separately. A repeated now-playing update must not replay the blur simply
// because the server sent the original small thumbnail again.
const _resolvedNowPlayingArt = new Map();
const _ambientNowPlayingArt = new Map();
const _pendingNowPlayingArt = new Map();

// Play the "track changed" entrance animation on the artwork + meta block.
// Called when showNowPlaying detects a new track (fp mismatch) so the user
// gets a same-frame visual cue regardless of how the change arrived:
// swipe-exit + new content, button click + SSE update, voice command, or
// the queue's own SSE-poll catching a remote change. Skipped while a
// swipe-exit is still in flight so we don't yank the artwork away from the
// gesture trajectory in the middle of its slide-off.
let _lastSwapInAt = 0;

function playArtworkSwapIn() {
  const art = document.getElementById('np-page-art');
  if (!art) return;
  // Coalesce rapid successive updates into a single banner animation. On a
  // track change the server can emit several correlated snapshots within a
  // moment (a leaning one, then a richer one with slightly different artist
  // text), each with a distinct fingerprint, so showNowPlaying() would run
  // this animation back-to-back — the banner visibly flickers/fades twice
  // before settling. Skipping any call within a short window after a swap
  // just played means only the first of the burst animates; later, genuinely
  // new tracks animate normally again.
  const now = Date.now();
  if (now - _lastSwapInAt < 500) return;
  _lastSwapInAt = now;
  // The swipe-exit sets inline `translateX(\u00b1120vw)`. While that transform is
  // active, the swipe handler's transitionend hasn't run yet, so fighting
  // it with a fresh keyframe frame would clip the artwork's exit short.
  const inline = art.style.transform;
  if (inline && inline !== 'none' && inline !== '') return;
  // Banner shift direction: a nav swipe records the direction on the artwork so
  // the incoming banner slides in from the opposite edge ("next" -> from the
  // right, "previous" -> from the left). Voice/auto changes default to a forward
  // slide from the right; the recorded direction is consumed so a later change
  // never replays a stale side.
  const npDir = art.dataset.navDirection;
  art.style.setProperty('--np-in-x', npDir === 'previous' ? '-70vw' : '70vw');
  delete art.dataset.navDirection;
  const meta = document.querySelector('.np-page-meta');
  const targets = art ? [art] : [];
  if (meta) targets.push(meta);
  for (const el of targets) {
    el.classList.remove('track-changed');
    // Force a reflow so the next class-add restarts the CSS animation; only
    // one class is active at a time so a re-trigger cleanly replaces it.
    void el.offsetWidth;
    el.classList.add('track-changed');
    // Clean up once the animation has played so a future track change can
    // re-add the class and re-trigger without colliding with the previous
    // animation's keyframe retention.
    el.addEventListener('animationend', function cleanup(ev) {
      if (ev.animationName && ev.animationName.indexOf('SwapIn') === -1) return;
      el.classList.remove('track-changed');
      el.removeEventListener('animationend', cleanup);
    });
  }
}

function resolveNowPlayingArtwork(videoId) {
  // Jam guests receive only public playback metadata. The account-backed
  // catalog lookup is forbidden by the guest API policy, but the public
  // i.ytimg.com renditions need no account and still upgrade the hero to HD.
  if (!videoId) return Promise.resolve('');
  if (_resolvedNowPlayingArt.has(videoId)) return Promise.resolve(_resolvedNowPlayingArt.get(videoId));
  if (_pendingNowPlayingArt.has(videoId)) return _pendingNowPlayingArt.get(videoId);

  // Probe direct image URLs immediately. Catalog lookup happens alongside the
  // probe, so high-resolution artwork never delays the play command.
  const directCandidates = ['maxresdefault', 'sddefault', 'hqdefault']
    .map((rendition) => 'https://i.ytimg.com/vi/' + encodeURIComponent(videoId) + '/' + rendition + '.jpg');
  const catalogCandidates = !window.JAM_GUEST && typeof window.api === 'function'
    ? window.api('/api/track/' + encodeURIComponent(videoId) + '/artwork')
      .then((result) => (result && result.thumbnails || []).concat(result && result.thumbnail || []))
      .catch(() => [])
    : Promise.resolve([]);
  const isHdArtwork = (image) => image.naturalWidth >= 1000 && image.naturalHeight >= 600;
  const loadCandidate = (candidates, index = 0) => {
    if (index >= candidates.length) return Promise.resolve('');
    const highResUrl = candidates[index];
    return new Promise((resolve) => {
      const highResImage = new Image();
      highResImage.onload = () => {
        if (isHdArtwork(highResImage)) return resolve(highResUrl);
        resolve(loadCandidate(candidates, index + 1));
      };
      highResImage.onerror = () => resolve(loadCandidate(candidates, index + 1));
      highResImage.src = highResUrl;
    });
  };
  const request = loadCandidate(directCandidates)
    .then((directUrl) => directUrl || catalogCandidates.then((urls) => loadCandidate([...new Set(urls.filter(Boolean))])))
    .then((highResUrl) => {
      if (highResUrl) _resolvedNowPlayingArt.set(videoId, highResUrl);
      return highResUrl;
    })
    .catch(() => '')
    .finally(() => _pendingNowPlayingArt.delete(videoId));
  _pendingNowPlayingArt.set(videoId, request);
  return request;
}

function preloadNowPlayingArtwork(info) {
  if (info && info.video_id) void resolveNowPlayingArtwork(info.video_id);
}

function upgradeLowResNowPlayingArt(info, fingerprint, artwork, npPageArt) {
  return resolveNowPlayingArtwork(info.video_id)
    .then((highResUrl) => {
      if (_lastNpFingerprint !== fingerprint || !highResUrl) return false;
      const url = 'url(' + highResUrl + ')';
      artwork.forEach((el) => {
        el.style.backgroundImage = url;
        el.classList.remove('image-loading');
      });
      if (npPageArt) npPageArt.closest('.np-page').classList.remove('image-loading');
      state._currentThumbnail = highResUrl;
      if (state._currentTrack) state._currentTrack.thumbnail = highResUrl;
      return true;
    });
}

function nowPlayingArtistEntries(info) {
  const raw = Array.isArray(info && info.artists) ? info.artists : [];
  const entries = raw.map((artist) => {
    if (typeof artist === 'string') return { name: artist, id: '' };
    if (!artist || typeof artist !== 'object') return null;
    return {
      name: artist.name || '',
      id: artist.id || artist.browseId || artist.channelId || artist.channel_id || ''
    };
  }).filter((artist) => artist && artist.name);
  if (entries.length) return entries;
  const fallbackName = String(info && info.artist || '').trim();
  if (!fallbackName) return [];
  return [{
    name: fallbackName,
    id: info && (info.channelId || info.channel_id || info.artistId || info.artist_id) || ''
  }];
}

function mergeNowPlayingArtistEntries(incoming, existing) {
  const knownByName = new Map((Array.isArray(existing) ? existing : []).map((artist) => [
    String(artist && artist.name || '').trim().toLowerCase(), artist
  ]));
  return (Array.isArray(incoming) ? incoming : []).map((artist) => {
    const known = knownByName.get(String(artist.name || '').trim().toLowerCase());
    return {
      name: artist.name,
      id: artist.id || (known && known.id) || ''
    };
  });
}

function nowPlayingArtistText(info) {
  const supplied = String(info && info.artist || '').trim();
  return supplied || nowPlayingArtistEntries(info).map((artist) => artist.name).join(', ');
}

function showNowPlaying(info) {
  const np = document.getElementById('now-playing');
  if (!info || (!info.title && !info.video_id)) {
    // Only update if we had a track before.
    if (state._hasTrack || _lastNpFingerprint) {
      np.classList.add('visible');
      document.getElementById('np-title').textContent = 'Nothing is playing';
      document.getElementById('np-artist').textContent = '';
      const art = document.getElementById('np-art');
      if (art) {
        art.style.backgroundImage = '';
        art.classList.remove('has-thumb', 'image-loading');
      }
      // Clear now-playing-section elements
      const npPageArt = document.getElementById('np-page-art');
      if (npPageArt) {
        npPageArt.style.backgroundImage = '';
        npPageArt.classList.remove('has-thumb', 'image-loading');
        const npPage = npPageArt.closest('.np-page');
        npPage.style.removeProperty('--np-cover');
        npPage.classList.remove('image-loading');
        document.body.style.removeProperty('--np-cover');
      }
      const npPageTitle = document.getElementById('np-page-title');
      if (npPageTitle) npPageTitle.textContent = 'Nothing is playing';
      const npPageArtist = document.getElementById('np-page-artist');
      if (npPageArtist) npPageArtist.textContent = '';
      state._hasTrack = false;
      state._currentVideoId = '';
      state._currentThumbnail = '';
      state._currentTrack = null;
      state._nowPlayingArtistFingerprint = '';
      syncTrackPlaybackIndicators();
      _lastNpFingerprint = '';
      // Playback is gone — don't leave an empty expanded player on screen.
      if (document.body.classList.contains('now-playing-route')) {
        window.closeNowPlayingOverlay();
      }
    }
    if (window.syncPageTitle) window.syncPageTitle();
    return;
  }
  // Fingerprint: the track identity deliberately excludes the thumbnail URL.
  // The now-playing poll can return a differently-sized (or signed) URL for
  // the same cover. Treating that as a new track rebuilt the mini-player
  // title and background image while the full player was being minimized,
  // producing a visible flash. Artwork is set when the track changes and
  // remains on that stable image for the lifetime of the track.
  const fp = (info.video_id || '') + '|' + info.title + '|' + (info.artist || '');
  let artistEntries = nowPlayingArtistEntries(info);
  const currentTrackEntries = state._currentTrack && state._currentTrack.video_id === (info.video_id || info.videoId)
    ? (Array.isArray(state._currentTrack.artists) ? state._currentTrack.artists : [])
    : [];
  // Polls can briefly omit structured credits after a richer snapshot. Keep
  // known IDs for the same video instead of replacing exact links with an
  // ambiguous name-only fallback.
  if (currentTrackEntries.some((artist) => artist && artist.id)) {
    artistEntries = mergeNowPlayingArtistEntries(artistEntries, currentTrackEntries);
  }
  const artistText = nowPlayingArtistText(info);
  const artistIds = artistEntries.map((artist) => artist.id);
  // Structured credits are ground truth for how many artists exist: pass them
  // as an array so a single entry ("Simon & Garfunkel" — one artist whose
  // name contains "&") renders as one link. A bare string fallback (voice /
  // auto-advanced snapshots carry only `artist` + `channelId`, no `artists`)
  // may be a joined multi-artist credit, so pass the id as a plain string:
  // the renderer then splits "A and B"-style credits (primary id stays on the
  // first link, the rest resolve by name) instead of collapsing every click
  // onto the primary artist's page.
  const hasStructuredCredits = Array.isArray(info && info.artists) &&
    info.artists.some((artist) => artist && typeof artist === 'object' && artist.name);
  const artistIdArg = hasStructuredCredits ? artistIds : (artistIds[0] || '');
  // Keep the exact song ID on name-only links as a last-resort resolver. This
  // covers voice/auto-advanced snapshots whose artist credit has no channel ID.
  const artistMarkup = window.artistLinksHtml(artistText, artistIdArg, info.video_id || info.videoId || '');
  const artistFingerprint = JSON.stringify(artistEntries);
  const artistMarkupChanged = artistFingerprint !== (state._nowPlayingArtistFingerprint || '');
  state._nowPlayingArtistFingerprint = artistFingerprint;
  preloadNowPlayingArtwork(info);
  const changed = fp !== _lastNpFingerprint;
  if (changed) {
    _lastNpFingerprint = fp;
    // A confirmed now-playing snapshot supersedes the swipe-optimistic
    // adjacent cover: drop the optimistic identity and retire the temporary
    // reveal layer (harmless if no swipe is in flight — the artwork paint
    // below has already been promoted / updated). Capture whether this
    // confirmation is for the very cover the swipe already painted on the
    // hero — in that case the banner is already displayed, so the confirm
    // must NOT blank it, repaint a different rendition, or replay the entrance
    // animation (each of those reads as a flicker after the swipe).
    const swipeBannerAlreadyShown = !!_swipeOptimisticVideoId
      && _swipeOptimisticVideoId === (info.video_id || info.videoId);
    _swipeOptimisticVideoId = '';
    const swipeHero = document.getElementById('np-page-art');
    if (swipeHero && swipeHero._swipeIncomingLayer) {
      const stale = swipeHero._swipeIncomingLayer;
      swipeHero._swipeIncomingLayer = null;
      if (stale && stale.parentElement) stale.parentElement.removeChild(stale);
    }
    np.classList.add('visible');
    document.getElementById('np-title').textContent = info.title;
    document.getElementById('np-artist').innerHTML = artistMarkup;
    const art = document.getElementById('np-art');
    const npPageArt = document.getElementById('np-page-art');
    const npPageTitle = document.getElementById('np-page-title');
    const npPageArtist = document.getElementById('np-page-artist');
    if (npPageTitle) npPageTitle.textContent = info.title;
    if (npPageArtist) {
      npPageArtist.innerHTML = artistMarkup;
    }
    if (info.thumbnail) {
      const cachedHighRes = info.video_id && _resolvedNowPlayingArt.get(info.video_id);
      if (info.video_id && !_ambientNowPlayingArt.has(info.video_id)) {
        _ambientNowPlayingArt.set(info.video_id, info.thumbnail);
      }
      const ambientThumbnail = (info.video_id && _ambientNowPlayingArt.get(info.video_id)) || info.thumbnail;
      const foregroundThumbnail = cachedHighRes || info.thumbnail;
      const url = 'url(' + foregroundThumbnail + ')';
      const ambientUrl = 'url(' + ambientThumbnail + ')';
      const artwork = [art, npPageArt].filter(Boolean);
      // Hold both the mini-player art and the expanded hero invisible until
      // the final image has fully loaded and decoded — never paint a
      // half-arrived cover. A cached HD rendition was already decoded during
      // an earlier playback update, so it is safe to paint immediately.
      // The compact player keeps its original shelf thumbnail; only the
      // expanded hero is upgraded after its HD rendition is decoded.
      artwork.forEach((el) => {
        // When the confirmation is for the cover the swipe already put on the
        // hero and the HD rendition isn't resolved yet, keep that paint as-is:
        // toggling image-loading fades the hero to transparent and reassigning
        // the background force-swaps renditions — both look like a flicker on
        // top of artwork that is already correct. The decode + HD upgrade path
        // below still applies once it completes.
        if (swipeBannerAlreadyShown && el === npPageArt && !cachedHighRes) return;
        el.style.backgroundImage = url;
        el.classList.toggle('image-loading',
          !cachedHighRes && !(swipeBannerAlreadyShown && el === npPageArt));
        el.classList.add('has-thumb');
      });
      if (npPageArt) {
        const npPage = npPageArt.closest('.np-page');
        if (cachedHighRes) npPageArt.style.backgroundImage = 'url(' + cachedHighRes + ')';
        npPage.style.setProperty('--np-cover', ambientUrl);
        npPage.classList.toggle('image-loading', !cachedHighRes && !swipeBannerAlreadyShown);
        document.body.style.setProperty('--np-cover', ambientUrl);
      }
      if (!cachedHighRes) {
      const img = new Image();
      img.onload = () => {
        if (_lastNpFingerprint !== fp) return;
        // The shelf thumbnail is what the mini player shows — it has fully
        // decoded, so reveal the compact art now. The expanded hero may need
        // a sharper rendition before it is revealed.
        if (art) art.classList.remove('image-loading');
        // Small shelf thumbnails look hazy when enlarged in the player. Keep
        // the hero hidden while the server resolves the track's best art.
        const isLowResolution = img.naturalWidth < 1000 || img.naturalHeight < 600;
        if (!isLowResolution) {
          if (info.video_id) _resolvedNowPlayingArt.set(info.video_id, info.thumbnail);
          if (npPageArt) {
            npPageArt.classList.remove('image-loading');
            npPageArt.closest('.np-page').classList.remove('image-loading');
          }
          return;
        }
        upgradeLowResNowPlayingArt(info, fp, npPageArt ? [npPageArt] : [], npPageArt)
          .then((upgraded) => {
            if (_lastNpFingerprint !== fp) return;
            // The compact art was already revealed above; only the hero still
            // needs revealing when the HD upgrade had nothing to offer.
            if (!upgraded && npPageArt) {
              npPageArt.classList.remove('image-loading');
              npPageArt.closest('.np-page').classList.remove('image-loading');
            }
          });
      };
      img.onerror = () => {
        if (_lastNpFingerprint === fp) {
          if (art) art.classList.remove('image-loading');
          if (npPageArt) {
            npPageArt.classList.remove('image-loading');
            npPageArt.closest('.np-page').classList.remove('image-loading');
          }
        }
      };
      img.src = info.thumbnail;
      }
    } else {
      art.style.backgroundImage = '';
      art.classList.remove('has-thumb', 'image-loading');
      if (npPageArt) {
        npPageArt.style.backgroundImage = '';
        npPageArt.classList.remove('has-thumb', 'image-loading');
        const npPage = npPageArt.closest('.np-page');
        npPage.style.removeProperty('--np-cover');
        npPage.classList.remove('image-loading');
        document.body.style.removeProperty('--np-cover');
      }
    }
    // Track video_id for the URL button. Clear it when the new track's id is
    // unknown (optimistic plain-text play) so the "Open on YouTube Music"
    // link never keeps pointing at the previous song.
    state._currentVideoId = info.video_id || '';
    state._currentThumbnail = (info.video_id && _resolvedNowPlayingArt.get(info.video_id)) || info.thumbnail || '';
    state._currentTrack = {
      video_id: info.video_id || '', title: info.title || '', artist: artistText,
      artists: artistEntries,
      thumbnail: state._currentThumbnail,
      channelId: artistEntries[0] && artistEntries[0].id || '',
      artist_id: artistEntries[0] && artistEntries[0].id || '',
      album_id: info.album_id || info.albumId || info.album_browse_id || ''
    };
    updateUrlBar();
    syncTrackPlaybackIndicators();
    // Visual cue for the song change. Called AFTER background-image / text
    // updates so the artwork already shows the new cover as it animates in.
    // Skipped internally when a swipe-exit is still in flight, so a swipe
    // and a same-frame SSE update don't fight each other. A confirmation that
    // merely officializes the swipe's own optimistic cover is NOT a fresh
    // entrance: replaying the slide/fade here would flicker a banner that has
    // been sitting on screen for seconds. Consume the stale direction instead
    // so a later genuine change animates from the default side.
    if (swipeBannerAlreadyShown) {
      delete npPageArt.dataset.navDirection;
    } else {
      playArtworkSwapIn();
    }
  }

  if (!changed && artistMarkupChanged) {
    const compactArtist = document.getElementById('np-artist');
    const pageArtist = document.getElementById('np-page-artist');
    if (compactArtist) compactArtist.innerHTML = artistMarkup;
    if (pageArtist) pageArtist.innerHTML = artistMarkup;
    if (state._currentTrack) {
      state._currentTrack.artist = artistText;
      state._currentTrack.artists = artistEntries;
      state._currentTrack.channelId = artistEntries[0] && artistEntries[0].id || '';
      state._currentTrack.artist_id = artistEntries[0] && artistEntries[0].id || '';
    }
  }

  refreshNpLikeButton();

  const wasTrack = state._hasTrack;
  state._hasTrack = true;
  if (changed || !wasTrack) {
    syncUiState();
    updateResultsActive();
  }
  if (window.syncPageTitle) window.syncPageTitle();
}

function refreshNpLikeButton() {
  if (!state._currentVideoId || typeof _playlistsData === 'undefined' || !_playlistsData.liked_songs) return;
  const isLiked = _playlistsData.liked_songs.includes(state._currentVideoId);
  // Thumbs-up (like), filled when liked — playbar + now-playing page buttons
  const svg = isLiked
    ? `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M2 10h4v12H2zM8 22V10l3.5-7.5c.3-.7 1.1-1.1 1.8-.8l.2.1c1.1.5 1.6 1.7 1.3 2.8L14 10h6.2c1.3 0 2.3 1.2 2 2.5l-1.5 7.5c-.2 1.2-1.2 2-2.4 2H8z"/></svg>`
    : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>`;
  for (const id of ['np-like-btn', 'np-page-like-btn', 'np-menu-like', 'mobile-np-like']) {
    const btn = document.getElementById(id);
    if (!btn) continue;
    btn.classList.toggle('liked', isLiked);
    btn.title = isLiked ? 'Dislike' : 'Like';
    if (id === 'np-menu-like') {
      btn.innerHTML = svg + `<span>${isLiked ? 'Dislike' : 'Like'}</span>`;
    } else {
      // Mobile action box carries a label under its icon (Like/Shuffle/etc.) —
      // swap only the heart so the label isn't wiped by the rewrite.
      const label = btn.querySelector('.mobile-np-action-label');
      if (label) {
        const oldSvg = btn.querySelector('svg');
        if (oldSvg) oldSvg.remove();
        btn.insertAdjacentHTML('afterbegin', svg);
        label.textContent = isLiked ? 'Dislike' : 'Like';
      } else {
        btn.innerHTML = svg;
      }
    }
  }
}

// Liked Songs changed somewhere else (voice "like this song", another open
// device): the SSE snapshot carries a liked_version counter; on change,
// re-fetch playlists so heart icons reflect the new state. null = baseline
// not yet seen (the first snapshot must not trigger a refetch).
let _lastLikedVersion = null;
function checkLikedVersion(np) {
  if (!np || np.liked_version === undefined) return;
  const first = _lastLikedVersion === null;
  if (np.liked_version === _lastLikedVersion) return;
  _lastLikedVersion = np.liked_version;
  if (first || window.JAM_GUEST || typeof loadLibrary !== 'function') return;
  loadLibrary().then(() => refreshNpLikeButton()).catch(() => {});
}

/* ---- Progress bar ----
   The server sends an anchor (position_ms at started_at, plus duration_ms); we
   tick locally for a smooth bar and only talk to the server when the user drags
   to seek. Opening the app partway through a song still shows the right spot
   because started_at is server truth, not a local guess. */
const progress = window.progress = (function () {
  const wrap = document.getElementById('progress');
  const track = document.getElementById('progress-track');
  const fill = document.getElementById('progress-fill');
  const handle = document.getElementById('progress-handle');
  const elapsedEl = document.getElementById('progress-elapsed');
  const totalEl = document.getElementById('progress-total');
  const barElapsedEl = document.getElementById('playbar-elapsed');
  const barTotalEl = document.getElementById('playbar-total');

  let durationMs = 0;
  let positionMs = 0;    // anchor position (ms into the track)
  let anchorClientMs = 0;// client Date.now() when positionMs was captured
  let lastServerAnchor = 0; // server started_at of the last update (change detector)
  let playing = false;
  let pausePending = false;
  let pausePendingTimer = null;
  let playPending = false;
  let playPendingTimer = null;
  let seekPending = false;
  let seekPendingTimer = null;
  let dragging = false;
  let dragMs = 0;        // previewed position while dragging
  let rafId = null;
  const FALLBACK_SEEK_MS = 5 * 60 * 1000;
  // While waiting for the device to actually start a freshly-requested track we
  // hold the bar at 0:00 (see resetPending). The local ticker must not run in
  // this window, otherwise the timer climbs against a track that hasn't started
  // and then visibly snaps back to 0 when the real PlaybackStarted anchor lands.
  let awaitingStart = false;
  // video_id of the track resetPending() was called for, when the caller
  // already knows it (next/prev, queue clicks, direct links). A confirmed
  // SSE/poll snapshot can only end awaitingStart if it's reporting on *this*
  // track — otherwise a stale "playback_confirmed" push that's still in-flight
  // for the *previous* (still-playing) track would prematurely clear
  // awaitingStart and let the bar tick against the old position for a moment,
  // before the real confirmation for the new track lands and snaps it to 0.
  // null = identity unknown (plain-text search, where the server picks the
  // track); then any confirmed snapshot is accepted. Titles can't be used as
  // a fallback identity: the optimistic title is the raw query text and the
  // server replaces it with the canonical track title before confirming, so
  // strict comparison would never match and the bar would stay stuck at 0:00.
  let pendingVideoId = null;
  let pendingSince = 0;      // when resetPending() started waiting
  // Snapshot history lets every open client show the same processing feedback
  // for playback started elsewhere (another browser, mobile PWA, or Alexa).
  let activeVideoId = null;
  let lastKnownVideoId = null;
  let lastSnapshotPlaying = null;
  let lastSnapshotProcessing = false;
  // After a local drag-to-seek, snapshots generated before the server
  // processed the seek still carry the old position; re-anchoring to them
  // snaps the bar back to where it was before jumping to the seek target.
  // Until this deadline, only accept a server anchor that agrees with the
  // local (sought) position.
  let localSeekUntil = 0;
  let _inactivityTimer = null;
  const _INACTIVITY_TIMEOUT_MS = 30000;

  function syncProcessingVisuals() {
    const processing = awaitingStart || pausePending || playPending || seekPending;
    state.playbackProcessing = processing;
    for (const id of ['progress', 'mobile-np-progress']) {
      const el = document.getElementById(id);
      if (!el) continue;
      el.classList.toggle('playback-processing', processing);
      el.setAttribute('aria-busy', processing ? 'true' : 'false');
    }
    for (const id of ['pp-btn', 'mobile-np-play', 'np-page-art-overlay']) {
      const el = document.getElementById(id);
      if (!el) continue;
      el.classList.toggle('playback-processing', processing);
      el.setAttribute('aria-busy', processing ? 'true' : 'false');
    }
    // Keep the accessible label/title in lockstep with the visual spinner,
    // including when a safety timeout clears a lost confirmation.
    if (typeof syncPlayPause === 'function') syncPlayPause();
  }

  function resetInactivityTimer() {
    clearTimeout(_inactivityTimer);
    if (document.hidden) return;  // hidden already handled by syncLoop
    _inactivityTimer = setTimeout(() => {
      // No state change for 30s while visible — fully stop the loop.
      // RAF will auto-restart on next track state change via update().
      if (rafId != null) { clearTimeout(rafId); rafId = null; }
    }, _INACTIVITY_TIMEOUT_MS);
  }

  function fmt(ms) {
    if (!isFinite(ms) || ms < 0) ms = 0;
    const total = Math.floor(ms / 1000);
    const m = Math.floor(total / 60);
    const s = total % 60;
    return m + ':' + String(s).padStart(2, '0');
  }

  // Live position from the anchor. We measure elapsed time against the *client*
  // clock (anchorClientMs), so a client/server clock difference never skews the
  // bar — the server's position_ms is treated as "position as of when this
  // update arrived". While paused, the position is frozen.
  function livePosition() {
    if (dragging) return dragMs;
    // Waiting for the real track to start: pin at the anchor (0) and don't let
    // wall-clock time advance the timer.
    if (awaitingStart) return Math.max(0, positionMs);
    let p = positionMs;
    if (playing && anchorClientMs) {
      p += Date.now() - anchorClientMs;
    }
    if (durationMs) p = Math.min(p, durationMs);
    return Math.max(0, p);
  }

  function paint() {
    const pos = livePosition();
    const visualMax = durationMs || seekLimitMs();
    const pct = visualMax ? Math.min(100, (pos / visualMax) * 100) : 0;
    fill.style.width = pct + '%';
    handle.style.left = pct + '%';
    elapsedEl.textContent = fmt(pos);
    totalEl.textContent = durationMs ? fmt(durationMs) : '--:--';
    // Playbar timer (next to the transport buttons, desktop)
    if (barElapsedEl) barElapsedEl.textContent = fmt(pos);
    if (barTotalEl) barTotalEl.textContent = durationMs ? fmt(durationMs) : '--:--';
    track.setAttribute('aria-valuenow', String(Math.floor(pos / 1000)));
    track.setAttribute('aria-valuemax', String(Math.floor(visualMax / 1000)));
    const mobileNpFill = document.getElementById('mobile-np-progress-fill');
    const mobileNpHandle = document.getElementById('mobile-np-progress-handle');
    const mobileNpElapsed = document.getElementById('mobile-np-progress-elapsed');
    const mobileNpTotal = document.getElementById('mobile-np-progress-total');
    if (mobileNpFill) mobileNpFill.style.width = pct + '%';
    if (mobileNpHandle) mobileNpHandle.style.left = pct + '%';
    if (mobileNpElapsed) mobileNpElapsed.textContent = fmt(pos);
    if (mobileNpTotal) mobileNpTotal.textContent = durationMs ? fmt(durationMs) : '--:--';

  }

  function loop() {
    paint();
    rafId = setTimeout(loop, 250);
  }
  function syncLoop() {
    const shouldRun = (playing || dragging) && !awaitingStart && !wrap.hidden && !document.hidden;
    if (shouldRun && rafId == null) rafId = setTimeout(loop, 250);
    else if (!shouldRun && rafId != null) { clearTimeout(rafId); rafId = null; }
  }

  // Fed from now-playing updates (SSE / poll).
  function update(np) {
    const hasTrack = !!(np && np.title);
    const incomingVideoId = np && np.video_id ? String(np.video_id) : '';
    const isConfirmed = !!(np && np.playback_confirmed);
    const knownVideoId = activeVideoId || lastKnownVideoId;

    // The backend marker covers freshly opened clients and all device-originated
    // transitions. Keep the heuristic below as a compatibility fallback for
    // older server snapshots that do not include it.
    const snapshotProcessing = np && np.playback_processing === true && !isConfirmed;
    const processingStarted = snapshotProcessing && !lastSnapshotProcessing;
    if (processingStarted && !awaitingStart) {
      if (incomingVideoId && (!knownVideoId || incomingVideoId !== knownVideoId)) resetPending(incomingVideoId);
      else if (np.playing === true) setPlayPending(true);
      else if (np.playing === false) setPausePending(true);
    }
    if (!snapshotProcessing && (lastSnapshotProcessing || isConfirmed)) {
      if (playPending) setPlayPending(false);
      if (pausePending) setPausePending(false);
    }
    lastSnapshotProcessing = snapshotProcessing;

    // A local click calls resetPending() before its request. For playback
    // initiated on another client or by Alexa, the first unconfirmed snapshot
    // is the equivalent signal: begin the same loading state here so every
    // open browser shows the transition, not just the device that sent it.
    const externalTrackPreparing = hasTrack && incomingVideoId && knownVideoId &&
      incomingVideoId !== knownVideoId && !isConfirmed && !awaitingStart;
    const externalResumePreparing = hasTrack && incomingVideoId &&
      incomingVideoId === activeVideoId && lastSnapshotPlaying === false &&
      np.playing === true && !isConfirmed;
    const externalPausePreparing = hasTrack && incomingVideoId &&
      incomingVideoId === activeVideoId && lastSnapshotPlaying === true &&
      np.playing === false && !isConfirmed;
    if (externalTrackPreparing) resetPending(incomingVideoId);
    else if (externalResumePreparing) setPlayPending(true);
    else if (externalPausePreparing) setPausePending(true);
    if (hasTrack && incomingVideoId) {
      activeVideoId = incomingVideoId;
      lastKnownVideoId = incomingVideoId;
    }
    if (hasTrack && typeof np.playing === 'boolean') lastSnapshotPlaying = np.playing;

    // Only hide the bar when there is genuinely no track. While awaitingStart
    // the bar stays visible but frozen at 0:00, so the user can see feedback
    // that a play was requested. Hiding it during awaitingStart (as the recent
    // refactor did) also kills syncLoop (which gates on !wrap.hidden), leaving
    // the bar stuck at 0 even after the track confirms.
    wrap.hidden = !hasTrack;

    if (!hasTrack) {
      activeVideoId = null;
      lastSnapshotProcessing = false;
      // Keep lastKnownVideoId through a transient empty snapshot. Alexa and
      // the device can briefly publish no title between tracks; forgetting the
      // prior identity would suppress processing feedback for the next one.
      lastSnapshotPlaying = null;
      awaitingStart = false;
      pendingVideoId = null;
      pendingSince = 0;
      pausePending = false;
      playPending = false;
      seekPending = false;
      clearTimeout(seekPendingTimer);
      clearTimeout(pausePendingTimer);
      clearTimeout(playPendingTimer);
      durationMs = 0;
      positionMs = 0;
      anchorClientMs = 0;
      lastServerAnchor = 0;
      playing = false;
      syncProcessingVisuals();
      paint();
      syncLoop();
      return;
    }
    // While waiting on a specific track, a snapshot that's still describing a
    // *different* one (a stale push still in flight for the track that was
    // playing before this one) must not leak its duration into the display —
    // otherwise the total-time label flashes the old song's length for a
    // moment before the real confirmation for the new track arrives.
    //
    // When the pending track's id is unknown (plain-text search — the server
    // picks the track), identity can't be compared, so use position instead:
    // a freshly started track confirms near 0:00, while a stale push for the
    // previous track carries its old mid-song position. Without this, that
    // stale push briefly flashes the old song's position/duration on the bar
    // before the real confirmation snaps it back to 0. The 15s escape hatch
    // accepts anything after a long wait so the bar can never wedge at 0:00.
    const serverPosNow = Number(np.position_ms) || 0;
    const matchesPending =
      (pendingVideoId ? np.video_id === pendingVideoId : serverPosNow < 10000) ||
      (awaitingStart && pendingSince && Date.now() - pendingSince > 15000);

    if (!awaitingStart || matchesPending) {
      durationMs = Number(np.duration_ms) || 0;
    }
    // Duration can arrive before the Echo actually starts fetching audio. Only
    // a proxy fetch / PlaybackStarted webhook confirms that the timer may tick
    // -- and only if that confirmation is actually for the track we're
    // waiting on, not a stale push for the previous track.
    if (awaitingStart && isConfirmed && matchesPending) {
      awaitingStart = false;
      pendingVideoId = null;
      pendingSince = 0;
      // Force-anchor to the server's position right now to avoid using a stale
      // anchor from pre-playback SSE updates.
      positionMs = Number(np.position_ms) || 0;
      anchorClientMs = Date.now();
      lastServerAnchor = Number(np.started_at) || 0;
    }
    // Re-anchor to the server's reported position when it sends a new anchor
    // (started_at changes on every snapshot / seek / pause). Anchor against the
    // client clock so there's no skew. Skip mid-drag so we don't fight the user,
    // and skip while still awaiting the real start so we don't snap to a stale
    // server position for the previous track.
    const serverAnchor = Number(np.started_at) || 0;
    if (!dragging && !awaitingStart && (serverAnchor !== lastServerAnchor || !anchorClientMs)) {
      const serverPos = Number(np.position_ms) || 0;
      if (Date.now() < localSeekUntil && Math.abs(serverPos - livePosition()) > 3000) {
        // Stale pre-seek snapshot: consume the anchor but keep our position,
        // so the next (fresher) anchor still triggers a re-check.
        lastServerAnchor = serverAnchor;
      } else {
        localSeekUntil = 0;
        seekPending = false;
        clearTimeout(seekPendingTimer);
        lastServerAnchor = serverAnchor;
        positionMs = serverPos;
        anchorClientMs = Date.now();
      }
    }
    if (playPending && isConfirmed && np.playing) {
      playPending = false;
      clearTimeout(playPendingTimer);
    }
    if (pausePending && isConfirmed && np.playing === false) {
      pausePending = false;
      clearTimeout(pausePendingTimer);
    }
    if (typeof np.playing === 'boolean' && !awaitingStart) {
      const reported = np.playing && !!np.playback_confirmed;
      // A pause request remains visually live until the command response
      // confirms it. Snapshots are intentionally not allowed to clear this
      // flag: an older confirmed snapshot can arrive while the pause request
      // is still in flight and would stop the bar too early.
      // Mirror the top-level grace guard: a snapshot that contradicts the
      // user's just-clicked play/pause intent can be a stale confirmation for
      // the *start* of playback (already in flight when pause was clicked) --
      // accepting it would resume the ticking bar right after the user paused.
      const inGrace = (Date.now() - state.lastActionAt) < state.GRACE_MS;
      const contradictsIntent = inGrace && state.lastActionIntent !== null && reported !== state.lastActionIntent;
      if (!contradictsIntent) playing = pausePending ? true : reported;
    }
    syncProcessingVisuals();
    syncLoop();
    paint();
    resetInactivityTimer();
  }

  // Called on an optimistic play (GO button / queue click): show the bar reset
  // to 0:00 with no duration yet, but DON'T tick — hold until the server confirms
  // the track actually started. This kills the phantom pre-start timer that used
  // to climb and then snap back to 0.
  //
  // videoId identifies the track being requested, so a confirmed snapshot
  // that's still describing the *previous* track (a stale SSE push already in
  // flight, or a slow poll response) can't prematurely end the wait — only a
  // snapshot for this specific track can. Pass it when the caller already
  // resolved it (next/prev, queue clicks, direct links); omit it for
  // plain-text searches, where the server picks the track and any confirmed
  // snapshot is accepted.
  function resetPending(videoId) {
    awaitingStart = true;
    if (videoId) {
      activeVideoId = String(videoId);
      lastKnownVideoId = String(videoId);
    }
    pendingVideoId = videoId || null;
    pendingSince = Date.now();
    localSeekUntil = 0;
    durationMs = 0;
    positionMs = 0;
    anchorClientMs = Date.now();
    lastServerAnchor = 0;
    playing = false;
    pausePending = false;
    clearTimeout(pausePendingTimer);
    playPending = false;
    clearTimeout(playPendingTimer);
    seekPending = false;
    clearTimeout(seekPendingTimer);
    // Keep the bar visible but frozen at 0:00 while we await the track start.
    // Hiding it here (as a recent refactor did) kills syncLoop (which gates
    // on !wrap.hidden) and leaves the bar stuck forever once the track lands.
    wrap.hidden = false;
    const mobileNpWrap = document.getElementById('mobile-np-progress');
    if (mobileNpWrap) mobileNpWrap.hidden = false;
    syncProcessingVisuals();
    syncLoop();
    paint();
  }
  // ---- drag to seek (fires on release) ----
  function seekLimitMs() {
    return durationMs || Math.max(FALLBACK_SEEK_MS, livePosition() + 60 * 1000);
  }
  let _activeTrack = track; // which progress-track is being dragged
  function posFromEvent(e) {
    const rect = _activeTrack.getBoundingClientRect();
    const x = (e.touches ? e.touches[0].clientX : e.clientX) - rect.left;
    const frac = Math.min(1, Math.max(0, x / rect.width));
    return frac;
  }
  function updateTooltip(e, container) {
    const tooltip = container.querySelector('.progress-tooltip');
    if (!tooltip) return;
    const trackEl = container.classList.contains('progress-track') ? container : container.querySelector('.progress-track');
    const rect = trackEl.getBoundingClientRect();
    const xClamped = Math.max(0, Math.min(rect.width, (e.touches ? e.touches[0].clientX : e.clientX) - rect.left));
    const frac = xClamped / rect.width;
    const ms = frac * seekLimitMs();
    tooltip.style.left = xClamped + 'px';
    tooltip.textContent = fmt(Math.round(ms));
  }
  function handleTooltipMove(e) {
    updateTooltip(e, e.currentTarget);
  }
  function beginDrag(e) {
    // The compact mobile player is display-only: dragging its progress strip
    // competes with page gestures and is deliberately disabled there.
    if (e.currentTarget === track && window.matchMedia('(max-width: 899px)').matches) return;
    if (awaitingStart) return;
    // Track which progress-track was touched
    _activeTrack = e.currentTarget;
    dragging = true;
    _activeTrack.classList.add('dragging');
    dragMs = posFromEvent(e) * seekLimitMs();
    paint();
    updateTooltip(e, _activeTrack);
    e.preventDefault();
  }
  function moveDrag(e) {
    if (!dragging) return;
    dragMs = posFromEvent(e) * seekLimitMs();
    paint();
    updateTooltip(e, _activeTrack);
    e.preventDefault();
  }
  async function endDrag(e) {
    if (!dragging) return;
    dragging = false;
    _activeTrack.classList.remove('dragging');
    const target = Math.round(dragMs);
    // Hold the bar at the target and do NOT tick yet -- the device hasn't
    // actually moved to this position until the seek round-trips and the
    // real PlaybackStarted webhook confirms it. Ticking immediately (as if
    // already playing from `target`) made the bar visibly race ahead of the
    // real audio, then snap back to `target` a beat later when the genuine
    // confirmation landed. Mirrors the server, which also drops
    // playing/playback_confirmed to false for the same window (see
    // alexa_seek). syncLoop() naturally stops the RAF loop since `playing`
    // is now false, so livePosition() just returns the frozen target.
    positionMs = target;
    anchorClientMs = Date.now();
    playing = false;
    seekPending = true;
    clearTimeout(seekPendingTimer);
    seekPendingTimer = setTimeout(() => {
      seekPending = false;
      syncProcessingVisuals();
    }, 8000);
    syncProcessingVisuals();
    syncLoop();
    paint();
    const serial = deviceEl.value;
    if (!serial) {
      seekPending = false;
      clearTimeout(seekPendingTimer);
      syncProcessingVisuals();
      toast('Pick a device first.', 'error');
      return;
    }
    state.lastActionAt = Date.now();
    localSeekUntil = Date.now() + 8000;
    toast('Seeking to ' + fmt(target) + '\u2026');
    try {
      const res = await api('/alexa/seek/', { serial, position_ms: target });
      // paused: the server only moved the frozen anchor (no playback dispatch);
      // the track stays paused and resume will pick up from here.
      if (res && res.paused) {
        seekPending = false;
        clearTimeout(seekPendingTimer);
      }
      syncProcessingVisuals();
      toast(res && res.paused
        ? 'Paused at ' + fmt(target) + ' — press play to resume here'
        : 'Seeked to ' + fmt(target), 'ok');
    } catch (err) {
      // Seek failed: drop the hold so the next server push restores truth.
      localSeekUntil = 0;
      seekPending = false;
      clearTimeout(seekPendingTimer);
      syncProcessingVisuals();
      toast(err.message, 'error');
    }
  }

  if (track) {
    track.addEventListener('mousedown', beginDrag);
    track.addEventListener('touchstart', beginDrag, { passive: false });
  }
  if (wrap) {
    wrap.addEventListener('mousemove', handleTooltipMove);
    wrap.addEventListener('touchmove', handleTooltipMove, { passive: true });
  }
  const mobileNpTrack = document.getElementById('mobile-np-progress-track');
  const mobileNpWrap = document.getElementById('mobile-np-progress');
  if (mobileNpTrack) {
    mobileNpTrack.addEventListener('mousedown', beginDrag);
    mobileNpTrack.addEventListener('touchstart', beginDrag, { passive: false });
  }
  if (mobileNpWrap) {
    mobileNpWrap.addEventListener('mousemove', handleTooltipMove);
    mobileNpWrap.addEventListener('touchmove', handleTooltipMove, { passive: true });
  }
  // Global move/end handlers work for both tracks
  window.addEventListener('mousemove', moveDrag);
  window.addEventListener('mouseup', endDrag);
  window.addEventListener('touchmove', moveDrag, { passive: false });
  window.addEventListener('touchend', endDrag);
  // Keyboard: arrow keys nudge +/- 5s. If duration is unknown, use a temporary
  // seek window so the scrubber still works while metadata catches up.
  if (track) {
    track.addEventListener('keydown', (e) => {
      let delta = 0;
      if (e.key === 'ArrowRight') delta = 5000;
      else if (e.key === 'ArrowLeft') delta = -5000;
      else return;
      e.preventDefault();
      const target = Math.min(seekLimitMs(), Math.max(0, Math.round(livePosition()) + delta));
      dragMs = target;
      dragging = true;
      endDrag();
    });
  }

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      // Tab became visible: restart loop + reset inactivity timer
      syncLoop();
      resetInactivityTimer();
    } else {
      clearTimeout(_inactivityTimer);
    }
  });

  function setPausePending(pending) {
    pausePending = !!pending;
    if (pausePending) {
      playPending = false;
      clearTimeout(playPendingTimer);
      clearTimeout(pausePendingTimer);
      // A pause can take longer than the command response. Keep the spinner
      // visible while the device settles, but cap it so a lost webhook cannot
      // wedge the controls forever.
      pausePendingTimer = setTimeout(() => {
        pausePending = false;
        playing = !!(window.__appState && window.__appState.isPlaying);
        syncProcessingVisuals();
        syncLoop();
        paint();
      }, 10000);
      playing = true;
    } else {
      clearTimeout(pausePendingTimer);
      playing = !!(window.__appState && window.__appState.isPlaying);
    }
    syncProcessingVisuals();
    syncLoop();
    paint();
  }

  function setPlayPending(pending) {
    playPending = !!pending;
    if (playPending) {
      pausePending = false;
      clearTimeout(playPendingTimer);
      // A resume confirmation normally arrives through SSE. The timeout is a
      // safety valve for an accepted command whose device never reports back;
      // it removes the spinner without claiming that audio started.
      playPendingTimer = setTimeout(() => {
        playPending = false;
        playing = !!(window.__appState && window.__appState.isPlaying);
        syncProcessingVisuals();
        syncLoop();
        paint();
      }, 10000);
    } else {
      clearTimeout(playPendingTimer);
    }
    syncProcessingVisuals();
    syncLoop();
    paint();
  }

  function cancelPending() {
    awaitingStart = false;
    pendingVideoId = null;
    pendingSince = 0;
    pausePending = false;
    clearTimeout(pausePendingTimer);
    playPending = false;
    seekPending = false;
    clearTimeout(playPendingTimer);
    clearTimeout(seekPendingTimer);
    syncProcessingVisuals();
    syncLoop();
    paint();
  }

  return { update, resetPending, cancelPending, setPausePending, setPlayPending, livePosition, getDuration: () => durationMs, syncLoop };
})();

/* ---- API ---- */
// Shared 401 handling for api/apiDelete/apiPatch. For a jam guest a 401
// usually means the host ended the jam (or it expired) — but it can also be
// an endpoint guests simply aren't allowed to hit. Probe an always-allowed
// endpoint to tell them apart: only a genuinely dead jam gets the
// full-screen ended state; a mere permission refusal gets a toast.

async function playResult(item, suppressRadio, forceRadio, openPlaybackPage) {
  const serial = selectedSerial();
  if (!serial) return;
  // Claim the intent and paint the UI *before* the await. Doing it afterwards
  // meant the UI followed response order rather than click order, so clicking
  // A, B, C could show C, then B, then C again.
  const mySeq = window.beginPlayIntent(item.video_id);
  state._lastPlayAttemptVideoId = item.video_id;
  preloadNowPlayingArtwork(item);
  showNowPlaying(item);
  progress.resetPending(item.video_id);
  state.isPlaying = true;
  state.lastActionIntent = true;
  syncPlayPause();
  toast(forceRadio
    ? 'Starting radio from \u201c' + item.title + '\u201d\u2026'
    : 'Playing \u201c' + item.title + '\u201d\u2026');
  try {
    await api('/alexa/play_queue/', {
      serial,
      video_id: item.video_id,
      title: item.title,
      artist: item.artist,
      artists: item.artists || [],
      artist_id: item.artist_id || item.artistId || item.channelId || item.channel_id || '',
      thumbnail: item.thumbnail,
      duration_ms: item.duration_ms,
      suppress_radio: !!suppressRadio,
      // "Play Radio" on a track already in the current queue: force a fresh
      // queue seeded from just this track instead of silently reusing the
      // existing one (see alexa_play_queue's force_radio handling).
      force_radio: !!forceRadio,
      // Lets the server drop this play if a later click supersedes it.
      intent_seq: mySeq,
    });
    // Superseded by a later click: that click owns the UI now.
    if (!window.isCurrentPlayIntent(mySeq)) return;
    toast(forceRadio ? 'Radio started' : 'Playing', 'ok');
    // Only search-result plays opt into opening the expanded playback page.
    if (openPlaybackPage && window.matchMedia('(min-width: 900px)').matches) window.navigateTo('#now-playing');
    state._lastQueueJson = '';
    schedulePollNowPlaying(3000);
  } catch (e) {
    if (!window.isCurrentPlayIntent(mySeq)) return;
    // Painting before the await means a failed play has already rendered as
    // playing. Undo the optimistic state and let server state take over.
    window.settlePlayIntent(item.video_id);
    if (window.progress && window.progress.cancelPending) window.progress.cancelPending();
    state.isPlaying = false;
    state.lastActionIntent = false;
    syncPlayPause();
    schedulePollNowPlaying(0);
    toast(e.message, 'error');
  }
}

/* Lock page scroll while any bottom-sheet overlay is open. Re-checked on
   every open/close so stacked sheets (now-playing popup + queue modal on top)
   keep the lock until the last one closes. */
function syncModalScrollLock() {
  const anyOpen = ['queue-modal-overlay'].some((id) => {
    const el = document.getElementById(id);
    return el && el.classList.contains('open');
  });
  // Locking body scrolling removes the native scrollbar on desktop. Reserve
  // that exact width while the sheet is open so centered content underneath
  // does not shift sideways. Mobile overlay scrollbars normally measure 0px,
  // so this is a no-op there.
  const body = document.body;
  if (state._modalOriginalPaddingRight === undefined) {
    state._modalOriginalPaddingRight = body.style.paddingRight || '';
  }
  const scrollbarWidth = Math.max(0, window.innerWidth - document.documentElement.clientWidth);
  if (anyOpen && scrollbarWidth > 0) {
    body.style.paddingRight = scrollbarWidth + 'px';
  } else if (!anyOpen) {
    body.style.paddingRight = state._modalOriginalPaddingRight;
    state._modalOriginalPaddingRight = undefined;
  }
  document.body.classList.toggle('modal-open', anyOpen);
}

/* ---- compact player opens the shared now-playing route ---- */
(function wireNowPlayingRoute() {
  const playerBar = document.querySelector('.player-section');
  const expandBtn = document.getElementById('player-expand-btn');
  const compactTrackInfo = document.querySelector('.player-section .np-info');
  if (!playerBar) return;

  function openNowPlaying(event) {
    if (event) {
      event.preventDefault();
      event.stopPropagation();
    }
    if (!state._hasTrack || !window.navigateTo) return;
    if (document.body.classList.contains('now-playing-route')) {
      if (window.closeNowPlayingOverlay) window.closeNowPlayingOverlay();
      return;
    }
    window.navigateTo('#now-playing');
  }

  function openCurrentTrackAlbum(event) {
    event.preventDefault();
    event.stopPropagation();
    const track = state._currentTrack;
    if (!track || !track.video_id || !window.navigateTo) return;

    const navigate = (albumId) => {
      if (!albumId) {
        if (window.toast) window.toast('Album unavailable for this song', 'error');
        return;
      }
      track.album_id = albumId;
      if (window.preloadNavigateAlbum) window.preloadNavigateAlbum(albumId);
      else window.navigateTo('#album/' + encodeURIComponent(albumId));
    };

    if (track.album_id) {
      navigate(track.album_id);
    } else if (typeof window.api === 'function') {
      window.api('/api/album/resolve/' + encodeURIComponent(track.video_id))
        .then((details) => navigate(details && details.album_id))
        .catch(() => navigate(''));
    }
  }

  if (expandBtn) expandBtn.addEventListener('click', openNowPlaying);
  for (const title of [document.getElementById('np-title'), document.getElementById('np-page-title')]) {
    if (!title) continue;
    title.addEventListener('click', (event) => {
      // On the compact mobile player, the track details are one clear target:
      // open the full player. Keep the title-to-album shortcut on desktop and
      // in the expanded player.
      if (title.id === 'np-title' && window.matchMedia('(max-width: 899px)').matches) {
        openNowPlaying(event);
        return;
      }
      openCurrentTrackAlbum(event);
    });
  }
  if (compactTrackInfo) {
    // Artist names are rendered as links. Capture their click before the link
    // handler so title and artist taps behave identically on a mobile mini
    // player instead of navigating away to an artist page.
    compactTrackInfo.addEventListener('click', (event) => {
      if (!window.matchMedia('(max-width: 899px)').matches) return;
      openNowPlaying(event);
    }, true);
  }
  playerBar.addEventListener('click', (event) => {
    if (event.target.closest('button, a, input, [role="slider"], .progress-track, .artist-name')) return;
    openNowPlaying(event);
  });
})();


function clearUiAfterPlaybackReset() {
  const mainEl = document.querySelector('main');
  const resultsSection = document.getElementById('results-section');
  const queueSection = document.getElementById('queue-section');
  const input = document.getElementById('query');
  const wasShowingResults = state._resultsOpen && !resultsSection.hidden;
  const wasShowingQueue = mainEl.classList.contains('has-queue') && !queueSection.hidden;
  const shouldStageExit = wasShowingResults || wasShowingQueue;

  input.value = '';
  input.dispatchEvent(new Event('input'));  // hides the X, closes suggestions
  state._searchSeq++;
  state._searchResults = [];
  state._lastQueueJson = '';
  state._lastQueueIndex = -1;

  clearTimeout(resultsSection._hideTimer);
  clearTimeout(resultsSection._showTimer);
  clearTimeout(queueSection._hideTimer);
  resultsSection.classList.remove('is-visible');
  queueSection.classList.remove('is-visible');

  const finish = () => animatePlaySectionLayout(() => {
    state._resultsOpen = false;
    resultsSection.hidden = true;
    queueSection.hidden = true;
    mainEl.classList.remove('has-queue');
    showNowPlaying(null);
    progress.update({});     // hides the progress bar
    syncUiState();
  });

  if (shouldStageExit) setTimeout(finish, 320);
  else finish();
}

/* ---- clear everything (confirmed) ---- */
async function doClearAll() {
  const serial = deviceEl.value || null;
  toast('Clearing\u2026');
  try {
    const data = await api('/alexa/clear/', serial ? { serial } : {});
    state.isPlaying = false;
    state.lastActionIntent = false;
    syncPlayPause();
    clearUiAfterPlaybackReset();
    if (window._closeQueueModal) window._closeQueueModal();
    if (data.stop_error) toast('Cleared here, but the device may still be playing: ' + data.stop_error, 'error');
    else toast('Cleared', 'ok');
  } catch (e) {
    toast(e.message, 'error');
  }
}

(function () {
  // The standalone Clear button was removed from the search bar; the confirm
  // dialog wiring only attaches if some entry point for it still exists.
  const overlay = document.getElementById('confirm-clear');
  const trigger = document.getElementById('clear-all-btn');
  if (!overlay || !trigger) return;
  const cancelBtn = document.getElementById('confirm-clear-cancel');
  const yesBtn = document.getElementById('confirm-clear-yes');
  trigger.addEventListener('click', () => overlay.classList.add('open'));
  cancelBtn.addEventListener('click', () => overlay.classList.remove('open'));
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.classList.remove('open'); });
  yesBtn.addEventListener('click', () => { overlay.classList.remove('open'); doClearAll(); });
})();

let _playPauseBusy = false;
let _queuedPlayPauseAction = null;
let _playPauseDesiredState = null;
let _confirmedPlayPauseState = null;
let _playPauseServerPlaying = null;
let _playPauseServerUpdatedAt = 0;
let _playPauseServerRevision = 0;
let _playPauseServerSeq = 0;
let _playPauseWaiters = [];
const PLAY_PAUSE_CONFIRM_TIMEOUT_MS = 3000;

function waitForPlayPauseServerState(expected, timeoutMs, minSeq = _playPauseServerSeq, minUpdatedAt = _playPauseServerUpdatedAt, minRevision = _playPauseServerRevision) {
  if (_playPauseServerPlaying === expected &&
      (_playPauseServerRevision > minRevision || _playPauseServerUpdatedAt > minUpdatedAt || _playPauseServerSeq > minSeq)) {
    return Promise.resolve(true);
  }
  return new Promise((resolve) => {
    const waiter = { expected, minSeq, minUpdatedAt, minRevision, resolve };
    const timer = setTimeout(() => {
      _playPauseWaiters = _playPauseWaiters.filter((item) => item !== waiter);
      resolve(false);
    }, timeoutMs);
    waiter.resolve = (matched) => {
      clearTimeout(timer);
      resolve(matched);
    };
    _playPauseWaiters.push(waiter);
  });
}

function notifyPlayPauseServerState(playing, updatedAt, revision) {
  const marker = Number(updatedAt) || 0;
  const playbackRevision = Number(revision) || 0;
  // Ignore delayed snapshots that belong to an older server state. Without
  // this guard, an in-flight playing snapshot can arrive after Alexa has
  // paused externally and put the stale desired state back to true, making the
  // next website click send pause again.
  const freshByRevision = playbackRevision > 0 && playbackRevision > _playPauseServerRevision;
  // Some Alexa webhook paths update updated_at without bumping the playback
  // revision. A newer server timestamp is therefore valid even when the
  // revision is unchanged; requiring both would discard a real external pause.
  const freshByMarker = marker > 0 && marker > _playPauseServerUpdatedAt;
  // Legacy snapshots without either marker are usable only as the first
  // baseline. Once metadata-bearing state has been seen, accepting every
  // marker-less update would let an old in-flight snapshot undo a newer pause.
  const freshWithoutMarker = playbackRevision <= 0 && marker <= 0 && _playPauseServerSeq === 0;
  if (!freshByRevision && !freshByMarker && !freshWithoutMarker) return;

  _playPauseServerPlaying = !!playing;
  // Alexa can change playback outside the website. When no web command is
  // being processed, make the next button toggle follow that authoritative
  // server state too; otherwise state.isPlaying becomes false while the
  // stale desired state still says true and the next click sends pause again.
  // Never overwrite a live web intent or queued rapid toggle here.
  if (!_playPauseBusy && _queuedPlayPauseAction === null && _playPauseWaiters.length === 0) {
    _confirmedPlayPauseState = _playPauseServerPlaying;
    _playPauseDesiredState = _playPauseServerPlaying;
  }
  if (marker > _playPauseServerUpdatedAt) _playPauseServerUpdatedAt = marker;
  if (playbackRevision > _playPauseServerRevision) _playPauseServerRevision = playbackRevision;
  _playPauseServerSeq += 1;
  const currentSeq = _playPauseServerSeq;
  const remaining = [];
  _playPauseWaiters.forEach((waiter) => {
    const freshByRevision = playbackRevision > 0 && playbackRevision > waiter.minRevision;
    const freshByMarker = playbackRevision <= 0 && marker > 0 && marker > waiter.minUpdatedAt;
    const freshBySequence = playbackRevision <= 0 && marker <= 0 && waiter.minSeq < currentSeq;
    if (waiter.expected === _playPauseServerPlaying && (freshByRevision || freshByMarker || freshBySequence)) waiter.resolve(true);
    else remaining.push(waiter);
  });
  _playPauseWaiters = remaining;
}

window._notifyPlayPauseServerState = notifyPlayPauseServerState;

function applyPlayPauseIntent(action) {
  state.lastActionAt = Date.now();
  // Pause is reflected immediately; resume remains visually paused until a
  // server snapshot confirms that the device has actually started again.
  // This avoids showing a playing icon while the Echo is still processing the
  // preceding pause command.
  state.isPlaying = action === 'pause' ? false : false;
  state.lastActionIntent = action === 'play';
  if (window.progress) {
    if (window.progress.setPausePending) window.progress.setPausePending(action === 'pause');
    if (window.progress.setPlayPending) window.progress.setPlayPending(action === 'play');
  }
  syncPlayPause();
}

async function drainPlayPause(action) {
  const previousPlaying = _confirmedPlayPauseState;
  applyPlayPauseIntent(action);
  let succeeded = false;
  let error = null;
  let confirmed = false;
  const commandBaselineSeq = _playPauseServerSeq;
  const commandBaselineUpdatedAt = _playPauseServerUpdatedAt;
  const commandBaselineRevision = _playPauseServerRevision;
  // Register before dispatch: /alexa/command updates the server snapshot
  // before its HTTP response is returned, so a quick pause confirmation can
  // otherwise arrive between the request and waiter registration. A queued
  // Resume does not wait for the pause snapshot: the accepted Pause response
  // already orders the next command, and waiting here made quick Resume feel
  // broken when the snapshot was delayed.
  const shouldWaitForConfirmation = !(action === 'pause' &&
    _queuedPlayPauseAction && _queuedPlayPauseAction !== action);
  const serverConfirmation = shouldWaitForConfirmation
    ? waitForPlayPauseServerState(
        action === 'play', PLAY_PAUSE_CONFIRM_TIMEOUT_MS,
        commandBaselineSeq, commandBaselineUpdatedAt, commandBaselineRevision
      )
    : null;
  try {
    await api('/alexa/command/', { serial: selectedSerial(), action });
    succeeded = true;
    if (window.schedulePollNowPlaying) window.schedulePollNowPlaying(0);
    const queuedAfterDispatch = _queuedPlayPauseAction && _queuedPlayPauseAction !== action;
    confirmed = queuedAfterDispatch && action === 'pause'
      ? false
      : serverConfirmation ? await serverConfirmation : false;
    _confirmedPlayPauseState = confirmed ? action === 'play' : previousPlaying;
  } catch (e) {
    error = e;
  }

  // Read the queue only after command confirmation. A third click during the
  // pause/apply wait must replace the older queued action, never be discarded.
  const nextAction = _queuedPlayPauseAction;
  _queuedPlayPauseAction = null;
  if (nextAction && nextAction !== action && succeeded &&
      (confirmed || action === 'pause')) {
    // The first command was accepted. For pause → resume, the short settle
    // fallback above preserves command order even if the paused snapshot is
    // late; never drop the user's queued Resume.
    return drainPlayPause(nextAction);
  }
  if (nextAction && nextAction !== action && (!succeeded || !confirmed)) {
    _playPauseBusy = false;
    state.isPlaying = previousPlaying;
    _playPauseDesiredState = state.isPlaying;
    state.lastActionIntent = state.isPlaying;     if (window.progress) {
       if (window.progress.setPausePending) window.progress.setPausePending(false);
       if (window.progress.setPlayPending) window.progress.setPlayPending(false);
     }
     syncPlayPause();
     toast(error && error.message ? error.message : 'Playback command failed; try again.', 'error');
    return;
  }

  _playPauseBusy = false;
  if (succeeded && confirmed) {
    state.isPlaying = action === 'play';
    state.lastActionIntent = state.isPlaying;
    _playPauseDesiredState = state.isPlaying;
    if (window.progress) {
      if (window.progress.setPausePending) window.progress.setPausePending(false);
      if (window.progress.setPlayPending) window.progress.setPlayPending(false);
    }
    syncPlayPause();
    toast(action === 'pause' ? 'Paused' : 'Resumed', 'ok');
  } else if (succeeded && action === 'pause' && !confirmed) {
    // The transport accepted the request, but the device did not confirm it.
    // Do not pretend the transition completed; the next click can retry.
    state.isPlaying = false;
    state.lastActionIntent = false;
    _playPauseDesiredState = false;
    // Keep the processing state until the device confirms the pause or the
    // progress controller's bounded safety timeout expires.
    if (window.progress && window.progress.setPlayPending) window.progress.setPlayPending(false);
    syncPlayPause();
    toast('Pause is still processing…', 'info');
  } else if (succeeded && action === 'play' && !confirmed) {
    // Alexa/device confirmation can arrive after the command HTTP response.
    // An accepted resume is not an error: keep the icon paused and let the
    // later confirmed snapshot switch it to playing. This prevents the old
    // "Playback did not confirm" false failure without claiming audio started.
    state.isPlaying = false;
    state.lastActionIntent = true;
    _playPauseDesiredState = true;
    // Keep the processing indicator until a confirmed playing snapshot arrives
    // (or the progress controller's safety timeout expires).
    if (window.progress && window.progress.setPausePending) window.progress.setPausePending(false);
    if (window.progress && window.progress.setPlayPending) window.progress.setPlayPending(true);
    if (window.schedulePollNowPlaying) window.schedulePollNowPlaying(0);
    syncPlayPause();
    toast('Resume requested…', 'info');
  } else {
    // Restore the last confirmed state after a failed command; never leave a
    // failed resume looking like active playback.
    state.isPlaying = previousPlaying;
    _playPauseDesiredState = state.isPlaying;
    if (window.progress) {
      if (window.progress.setPausePending) window.progress.setPausePending(false);
      if (window.progress.setPlayPending) window.progress.setPlayPending(false);
    }
    state.lastActionIntent = state.isPlaying;
    syncPlayPause();
    toast(error && error.message ? error.message : 'Playback command failed.', 'error');
  }
}

function requestPlayPause(action) {
  _playPauseDesiredState = action === 'play';
  if (_playPauseBusy) {
    _queuedPlayPauseAction = action;
    // Keep the controls responsive while the latest intent waits its turn.
    applyPlayPauseIntent(action);
    return;
  }
  _playPauseBusy = true;
  _queuedPlayPauseAction = null;
  _confirmedPlayPauseState = state.isPlaying;
  void drainPlayPause(action);
}

// Space is a playback shortcut only inside the expanded now-playing view.
// Keep normal page scrolling, text entry, and focused control activation
// unchanged everywhere else.
document.addEventListener('keydown', (event) => {
  if (event.defaultPrevented || event.repeat ||
      (event.code !== 'Space' && event.key !== ' ')) return;

  const nowPlayingSection = document.getElementById('now-playing-section');
  if (!document.body.classList.contains('now-playing-route') ||
      document.body.classList.contains('now-playing-closing') ||
      !nowPlayingSection || nowPlayingSection.hidden) return;

  const target = event.target;
  const playerExpandButton = document.getElementById('player-expand-btn');
  const isModalOpenerFocus = target === playerExpandButton;
  if (!isModalOpenerFocus && target && target.closest && target.closest(
    'button, a, input, select, textarea, [contenteditable]:not([contenteditable="false"]), [role="button"], [role="slider"]'
  )) return;

  const playPauseButton = document.getElementById('pp-btn');
  if (!playPauseButton || playPauseButton.disabled) return;

  event.preventDefault();
  playPauseButton.click();
});

document.getElementById('pp-btn').onclick = () => {
  const serial = selectedSerial();
  if (!serial) return;
  // Base the next toggle on the latest confirmed server state whenever the
  // control is idle. Alexa may have paused the Echo while a website command
  // was in flight, leaving _playPauseDesiredState stale; using the latest
  // server value guarantees this click sends play after an external pause.
  // During an active web command, preserve the queued user intent instead.
  const desiredPlaying = !_playPauseBusy && _playPauseServerPlaying !== null
    ? _playPauseServerPlaying
    : (_playPauseDesiredState === null ? state.isPlaying : _playPauseDesiredState);
  const action = desiredPlaying ? 'pause' : 'play';
  toast((action === 'pause' ? 'Pausing' : 'Resuming') + '\u2026');
  requestPlayPause(action);
};

const npPageArt = document.getElementById('np-page-art');
if (npPageArt) {
  npPageArt.onclick = (e) => {
    e.stopPropagation();
    // On mobile, tapping the banner while the volume popover is open is a
    // dismiss action, not a playback toggle. The document-level volume
    // listener closes the popover too, but it must not fall through here and
    // pause the current track.
    const mobileVolumePopover = document.getElementById('mobile-volume-popover');
    if (mobileVolumePopover && mobileVolumePopover.classList.contains('open') &&
        window.matchMedia('(max-width: 899px)').matches) {
      mobileVolumePopover.classList.remove('open');
      const mobileVolumeButton = document.getElementById('mobile-player-volume');
      if (mobileVolumeButton) mobileVolumeButton.setAttribute('aria-expanded', 'false');
      return;
    }
    // The mobile now-playing banner is informational; only its dedicated
    // playback control may pause or resume the track.
    if (window.matchMedia('(max-width: 899px)').matches) return;
    const overlay = document.getElementById('np-page-art-overlay');
    if (overlay) {
      overlay.classList.remove('flash');
      void overlay.offsetWidth;
      overlay.classList.add('flash');
      setTimeout(() => overlay.classList.remove('flash'), 520);
    }
    document.getElementById('pp-btn').click();
  };
}

document.getElementById('shuffle-btn').addEventListener('click', async (e) => {
  const btn = e.currentTarget;
  if (btn.disabled) return;
  btn.disabled = true;
  try {
    await api('/alexa/shuffle_queue/', {});
    btn.classList.add('shuffle-active');
    state._lastQueueJson = '';
    schedulePollNowPlaying(300);
    toast('Queue shuffled', 'ok');
  } catch (err) {
    toast(err.message || 'Shuffle failed', 'error');
  } finally {
    btn.disabled = false;
  }
});

/* ---- compact player more menu ---- */
(function () {
  const wrap = document.querySelector('.np-more-wrap');
  const button = document.getElementById('np-more-btn');
  const menu = document.getElementById('np-more-menu');
  if (!wrap || !button || !menu) return;
  const mobileButton = document.getElementById('mobile-player-more');
  const close = () => {
    wrap.classList.remove('open');
    menu.classList.remove('mobile-open');
    button.setAttribute('aria-expanded', 'false');
    if (menu.parentElement === document.body) {
      document.querySelector('.player-section .np-more-wrap')?.appendChild(menu);
      menu.classList.remove('mobile-now-playing-menu');
    }
  };
  // Exposed so a tap that dismisses an open menu (see search.js) can also
  // close the player's more menu without re-triggering anything underneath.
  window._closeNpMoreMenu = close;
  button.addEventListener('click', (e) => {
    e.stopPropagation();
    const open = !wrap.classList.contains('open');
    close();
    if (open) { wrap.classList.add('open'); button.setAttribute('aria-expanded', 'true'); }
  });
  if (mobileButton) mobileButton.addEventListener('click', (e) => {
    e.stopPropagation();
    const open = !menu.classList.contains('mobile-open');
    close();
    if (open) {
      document.body.appendChild(menu);
      menu.classList.add('mobile-now-playing-menu', 'mobile-open');
    }
  });
  document.addEventListener('click', (e) => { if (!wrap.contains(e.target)) close(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
  // Shared by the "..." menu entry, the playbar thumb, and the now-playing
  // page thumb. toggleLike updates the clicked button itself; refresh after
  // so every like button reflects the new state.
  function likeCurrentTrack(btn) {
    if (!(state._currentTrack && state._currentTrack.video_id && typeof toggleLike === 'function')) return;
    Promise.resolve(toggleLike(state._currentTrack, btn)).then(() => {
      if (window.refreshNpLikeButton) window.refreshNpLikeButton();
    });
  }
  const npMenuLike = document.getElementById('np-menu-like');
  if (npMenuLike) npMenuLike.addEventListener('click', () => {
    likeCurrentTrack(document.getElementById('np-like-btn'));
    close();
  });
  for (const id of ['np-like-btn', 'np-page-like-btn']) {
    const btn = document.getElementById(id);
    if (btn) btn.addEventListener('click', (e) => {
      e.stopPropagation(); // .np cluster click would toggle the player view
      likeCurrentTrack(btn);
    });
  }
  const npMenuPlaylist = document.getElementById('np-menu-playlist');
  if (npMenuPlaylist) npMenuPlaylist.addEventListener('click', () => {
    if (state._currentTrack && state._currentTrack.video_id && typeof openAddToPlaylistModal === 'function')
      openAddToPlaylistModal(state._currentTrack);
    close();
  });
  function resolveCurrentTrackDetails() {
    const track = state._currentTrack;
    if (!track || !track.video_id) return Promise.resolve(null);
    if (track.album_id && track.artist_id) return Promise.resolve(track);
    if (typeof window.api !== 'function') return Promise.resolve(track);
    return window.api('/api/album/resolve/' + encodeURIComponent(track.video_id))
      .then((details) => {
        if (details) {
          track.album_id = track.album_id || details.album_id || '';
          track.artist_id = track.artist_id || details.artist_id || '';
        }
        return track;
      })
      .catch(() => track);
  }
  const npMenuAlbum = document.getElementById('np-menu-album');
  if (npMenuAlbum) npMenuAlbum.addEventListener('click', () => {
    resolveCurrentTrackDetails().then((track) => {
      if (!track || !track.album_id) {
        if (window.toast) window.toast('Album unavailable for this song', 'error');
        return;
      }
      if (window.preloadNavigateAlbum) window.preloadNavigateAlbum(track.album_id);
      else if (window.navigateTo) window.navigateTo('#album/' + encodeURIComponent(track.album_id));
    });
    close();
  });
  const npMenuArtist = document.getElementById('np-menu-artist');
  if (npMenuArtist) npMenuArtist.addEventListener('click', () => {
    resolveCurrentTrackDetails().then((track) => {
      const artistId = track && (track.artist_id || track.channelId);
      if (!artistId) {
        if (window.toast) window.toast('Artist unavailable for this song', 'error');
        return;
      }
      if (window.preloadNavigateArtist) window.preloadNavigateArtist(artistId);
      else if (window.navigateTo) window.navigateTo('#artist/' + encodeURIComponent(artistId));
    });
    close();
  });
  const npMenuRadio = document.getElementById('np-menu-radio');
  if (npMenuRadio) npMenuRadio.addEventListener('click', (e) => {
    if (!state._currentTrack) { e.preventDefault(); return; }
    if (typeof playRadio === 'function') {
      playRadio(state._currentTrack);
    }
    close();
  });
})();

/* ---- prev / next ----
   Alexa processes each spoken command in turn (speech recognition + NLU +
   skill round-trip), which takes a few seconds. Clicking again before that
   finishes overwrites the pending arm (server._arm_play is single-slot per
   device) and races a new spoken command against one Alexa hasn't acted on
   yet, so the device silently stays on the old track. Disable the buttons
   for the duration of one round-trip so clicks queue up as intent (via the
   disabled state) rather than firing concurrently. */
let _navBusy = false;
for (const btn of document.querySelectorAll('[data-action="previous"], [data-action="next"]')) {
  btn.onclick = () => {
    if (_navBusy) return;
    const serial = selectedSerial();
    if (!serial) return;
    const navigationDirection = btn.dataset.action === 'next' ? 'next' : 'previous';
    _navBusy = true;
    document.querySelectorAll('[data-action="previous"], [data-action="next"]')
      .forEach(b => b.disabled = true);
    toast(btn.title + '\u2026');
    // Guard the optimistic state.isPlaying=true below from the server's own
    // playing:false push (set synchronously by /alexa/command/ while the new
    // track is still loading) \u2014 without this, that SSE message arrives before
    // playback is confirmed and immediately flips the UI back to "paused".        state.lastActionAt = Date.now();
    // Next/previous has no reliable new video id yet, so start an identity-
    // neutral processing state before the command round-trip.
    progress.resetPending();
    api('/alexa/command/', { serial, action: btn.dataset.action })
      .then((data) => {
        if (data.now_playing) showNowPlaying(data.now_playing);
        progress.resetPending(data.now_playing && data.now_playing.video_id);
        state.isPlaying = true;
        state.lastActionIntent = true;
        syncPlayPause();
        // New track incoming: hold the bar at 0:00 until PlaybackStarted
        // confirms *this* video_id (not a stale push for the track we just left).
        // Schedule one fallback poll; SSE remains the primary transition path.
        state._lastQueueJson = '';
        schedulePollNowPlaying(2000);
        schedulePollNowPlaying(5000);
        schedulePollNowPlaying(8000);
      })
      .catch(e => {
        if (progress.cancelPending) progress.cancelPending();
        toast(e.message, 'error');
      })
      .finally(() => {
        // Don't keep buttons disabled for Alexa's full skill round-trip
        // (a few seconds) -- otherwise rapid skip gestures can't register
        // until each prior one finishes processing. 600 ms is short enough
        // for a ~2 Hz skip rhythm to feel responsive, while still longer
        // than Alexa's NLU + skill-arm window so the slot isn't overwritten
        // by the next request. The swipe handler's canStart() reads the
        // same disabled state, so swipes feel the same lock as the buttons.
        setTimeout(() => {
          _navBusy = false;
          document.querySelectorAll('[data-action="previous"], [data-action="next"]')
            .forEach(b => b.disabled = false);
        }, 600);
      });
  };
}

/* ---- mobile swipe-to-skip on the full-player banner (artwork) ----
   Mapping (standard iOS / Spotify / YT Music convention):
     swipe left  (finger right → left) → next song
     swipe right (finger left → right) → previous song
   The artwork slides off in the direction of the gesture, and the new
   track appears from the opposite side after the round-trip with Alexa.
   Edge cases handled:
     - Vertical scroll is preserved (touch-action: pan-y + axis lock by
       pointer move distance, never by `preventDefault` on small motion).
     - Only the np-page-art element responds; touches anywhere else on the
       route pass through to the title/progress/controls/scroll as before.
     - Respects the existing _navBusy lock (~600 ms window) by checking
       that the on-screen next/previous buttons are still enabled before
       firing. The lock is short enough that rapid skip gestures register
       back-to-back at a ~2 Hz rhythm, while still longer than Alexa's
       NLU + skill-arm window so the slot isn't overwritten by the next
       request.
     - Skipped whenever the queue modal sheet (queue-modal-overlay) is open
       on top, the now-playing route is closing, the device picker is empty,
       or the body is hidden.
     - Multi-touch: a second pointerdown while one swipe is in flight is
       ignored. Pointercancel / pointerleave / contextmenu / blurred tab all
       fall back to a clean snap-back without firing nav.
     - Velocity is honored alongside distance: a short fast flick counts even
       when the drag stayed under the distance threshold.
     - Soft resistance past the artwork's width so a wild fling doesn't
       visually leap across the screen.
     - Exit animation is driven by inline style; the mobile route's stronger
       `transform: none !important` rule (player.css) is intentionally
       overridden via inline styles, which beat !important declarations.
     - Click that the browser fires at pointerup is suppressed for ~600 ms
       after a real commit in case the np-page-art click handler ever grows
       beyond its current mobile no-op. */
(function wireMobileNowPlayingSwipe() {
  if (window.matchMedia && window.matchMedia('(min-width: 900px)').matches) return;
  const art = document.getElementById('np-page-art');
  if (!art) return;

  // Drag-and-release commits only once the next/previous banner has been
  // revealed past half its width (i.e. the finger is lifted while it holds
  // more than 50% of the banner). A quick swipe (fast flick) is a separate
  // path and commits on velocity regardless of distance. Slow drags that never
  // reach 50% spring back and the current song stays.
  const SWIPE_DISTANCE_FRACTION = 0.5;  // reveal threshold = 50% of banner width
  const SWIPE_VELOCITY_PX_MS = 0.45;    // quick-swipe flicks win on velocity
  const AXIS_LOCK_PX = 8;             // pointer must travel this far to commit axis
  const AXIS_BIAS = 1.25;             // vertical wins when |dy| > |dx| * bias
  const RESISTANCE_START_PX = 240;    // start dampening once past the artwork width
  const RESISTANCE_DIVISOR_PX = 80;   // every additional 80px halves extra travel
  const EXIT_MS = 220;                // match CSS feel of other route transitions
  const SUPPRESS_CLICK_MS = 600;      // how long after a commit to drop the synthetic click

  let active = null; // {pointerId,startX,startY,lastX,lastY,startedAt,axis}

  // Optimistic adjacent-track banner: while the user drags, the next/previous
  // cover is revealed flush against the artwork and settles into place on
  // commit, so the banner is never blank during (or right after) a swipe while
  // the Alexa round-trip is still in flight. The layer sits directly beneath
  // the artwork (inserted before it in DOM order), so at rest the opaque art
  // fully hides it; the art's own `overflow:hidden` ancestors would clip it,
  // so it lives as a sibling on .np-page-left and is clipped by that panel's
  // overflow:hidden until the drag pulls it in from the screen edge.
  let incomingLayer = null; // <div class="np-swipe-incoming">, reused across gestures
  let incoming = null;     // { layer, track, direction } while a reveal is live

  function queueItemThumb(item) {
    if (!item) return '';
    if (typeof item.thumbnail === 'string' && item.thumbnail) return item.thumbnail;
    if (item.thumbnail && item.thumbnail.url) return item.thumbnail.url;
    if (Array.isArray(item.thumbnails) && item.thumbnails.length) {
      const last = item.thumbnails[item.thumbnails.length - 1];
      return (last && last.url) || '';
    }
    return '';
  }

  // Fallback source for findAdjacentTrack when the server queue snapshot is
  // empty: the nav command handler resets state._lastQueueJson, and a snapshot
  // that omits the queue never repopulates it — but the now-playing page's
  // rendered queue rows persist in the DOM and keep their own order/identity.
  function adjacentTrackFromDom(direction) {
    const list = document.getElementById('np-queue-list');
    if (!list || !list.querySelectorAll) return null;
    const rows = Array.prototype.slice.call(list.querySelectorAll('.queue-swipe-wrapper'));
    if (!rows.length) return null;
    const ordered = rows
      .map((row) => ({
        row,
        index: Number(row.dataset && row.dataset.index),
        videoId: (row.dataset && row.dataset.videoId) || '',
      }))
      .filter((row) => Number.isFinite(row.index))
      .sort((a, b) => a.index - b.index);
    if (!ordered.length) return null;
    const optimisticId = (Date.now() - _swipeOptimisticAt < 10000) ? _swipeOptimisticVideoId : '';
    const wantId = optimisticId || state._currentVideoId || '';
    let pos = ordered.findIndex((row) => row.videoId === wantId);
    if (pos < 0) {
      // Fall back to the row the UI currently marks as playing.
      const activeRow = rows.find((row) => {
        const item = row.querySelector && row.querySelector('.queue-item');
        return item && item.classList && item.classList.contains('active');
      });
      if (activeRow) pos = ordered.findIndex((row) => row.row === activeRow);
    }
    if (pos < 0) return null;
    const target = direction === 'next' ? pos + 1 : pos - 1;
    if (target < 0 || target >= ordered.length) return null;
    const row = ordered[target].row;
    const track = row._songContextTrack || {};
    const title = (typeof track.title === 'string' && track.title)
      ? track.title
      : ((row.querySelector && row.querySelector('.queue-title')) || {}).textContent || '';
    const artist = (typeof track.artist === 'string' && track.artist)
      ? track.artist
      : ((row.querySelector && row.querySelector('.queue-artist')) || {}).textContent || '';
    let thumbnail = queueItemThumb(track);
    if (!thumbnail) {
      const img = row.querySelector && row.querySelector('img.queue-thumb');
      thumbnail = img ? img.src || '' : '';
    }
    return {
      video_id: ordered[target].videoId,
      title,
      artist,
      thumbnail,
    };
  }

  // The track a swipe in `direction` will land on, per the server queue.
  // Returns null when there is no adjacent entry (queue start/end, empty).
  function findAdjacentTrack(direction) {
    let queue = [];
    try { queue = JSON.parse(state._lastQueueJson || '[]'); } catch (_) {}
    if (Array.isArray(queue) && queue.length) {
      let index = state._lastQueueIndex;
      // After a committed swipe the banner already shows the adjacent track
      // while the round-trip is in flight; base a second swipe on that
      // optimistic id so it reveals the *next* neighbor, not the same cover.
      const optimisticId = (Date.now() - _swipeOptimisticAt < 10000) ? _swipeOptimisticVideoId : '';
      const wantId = optimisticId || state._currentVideoId || '';
      if (wantId) {
        const byId = queue.findIndex((item) => item && (item.video_id || item.videoId) === wantId);
        if (byId >= 0) index = byId;
      }
      if (index >= 0) {
        const target = direction === 'next' ? index + 1 : index - 1;
        if (target >= 0 && target < queue.length) return queue[target] || null;
      }
    }
    // The snapshot queue can be empty here even though the app is mid-queue
    // (see adjacentTrackFromDom); fall back to the rendered DOM rows.
    return adjacentTrackFromDom(direction);
  }

  // Put the adjacent track's cover into the reveal layer, parked off-screen on
  // the side `direction` enters from ("next" waits off the right edge,
  // "previous" off the left), flush against the artwork's resting box.
  function showIncomingLayer(direction, track) {
    let layer = incomingLayer;
    if (!layer) {
      layer = document.createElement('div');
      layer.id = 'np-swipe-incoming';
      layer.className = 'np-swipe-incoming';
      layer.setAttribute('aria-hidden', 'true');
      art.parentElement.insertBefore(layer, art);
      incomingLayer = layer;
    } else {
      // Re-insert to guarantee it still paints beneath the artwork.
      if (layer.parentElement !== art.parentElement || layer.nextSibling !== art) {
        art.parentElement.insertBefore(layer, art);
      }
    }
    const artRect = art.getBoundingClientRect();
    const parentRect = art.parentElement.getBoundingClientRect();
    layer.style.top = (artRect.top - parentRect.top) + 'px';
    layer.style.left = (artRect.left - parentRect.left) + 'px';
    layer.style.width = artRect.width + 'px';
    layer.style.height = artRect.height + 'px';
    layer.style.visibility = 'visible';
    layer.style.backgroundImage = 'url(' + queueItemThumb(track) + ')';
    // Preload the confirmed artwork now so the hero paint showNowPlaying
    // performs after the round-trip is instant (no image-loading blank).
    const videoId = track && (track.video_id || track.videoId || '');
    if (videoId) void resolveNowPlayingArtwork(videoId);
    const w = artRect.width || art.clientWidth || 1;
    layer.style.transition = 'none';
    layer.style.transform = 'translateX(' + (direction === 'next' ? w : -w) + 'px)';
    incoming = { layer, track, direction };
    art._swipeIncomingLayer = layer;
  }

  function hideIncoming() {
    if (!incoming) return false;
    if (incoming.layer) {
      incoming.layer.style.visibility = 'hidden';
      incoming.layer.style.transform = '';
      incoming.layer.style.transition = '';
    }
    incoming = null;
    if (art._swipeIncomingLayer) art._swipeIncomingLayer = null;
    return true;
  }

  // On commit the artwork itself exits off-screen; promote the adjacent track
  // onto the art (cover + title + artist) so that when the exit transform is
  // cleared the banner returns already showing the new cover — no blank frame
  // while the Alexa round-trip completes. showNowPlaying() overwrites every
  // field here the moment the server confirms the real track.
  function promoteIncomingToArt(track) {
    const thumb = queueItemThumb(track);
    if (!thumb) return;
    const videoId = track && (track.video_id || track.videoId || '');
    const cachedHighRes = videoId && _resolvedNowPlayingArt.get(videoId);
    art.style.backgroundImage = 'url(' + (cachedHighRes || thumb) + ')';
    art.classList.add('has-thumb');
    art.classList.remove('image-loading');
    const title = (typeof track.title === 'string' && track.title) ? track.title : '';
    const artist = (typeof track.artist === 'string' && track.artist)
      ? track.artist
      : (Array.isArray(track.artists)
          ? track.artists.map((a) => a && (a.name || a)).filter(Boolean).join(', ')
          : '');
    const pageTitle = document.getElementById('np-page-title');
    const pageArtist = document.getElementById('np-page-artist');
    if (pageTitle) pageTitle.textContent = title;
    if (pageArtist) pageArtist.textContent = artist;
    // Remember the optimistic id so a second swipe during the round-trip asks
    // for the track AFTER this one.
    _swipeOptimisticVideoId = videoId || '';
    _swipeOptimisticAt = Date.now();
  }

  function canStart() {
    if (!art.isConnected || art.hidden) return false;
    if (!window.matchMedia('(max-width: 899px)').matches) return false;
    if (!document.body.classList.contains('now-playing-route')) return false;
    if (document.body.classList.contains('now-playing-closing')) return false;
    const queueModal = document.getElementById('queue-modal-overlay');
    if (queueModal && queueModal.classList.contains('open')) return false;
    if (document.hidden) return false;
    // If either navigation button is currently disabled, the existing _navBusy
    // ~600 ms lock is engaged: ignore the swipe so we don't double-arm the
    // Alexa backend, which races the in-flight spoken command. _navBusy
    // toggles both [data-action="previous"] and [data-action="next"] in
    // tandem; checking either is correct in practice, but check both so the
    // gate stays correct if a future call site ever disables them
    // independently.
    const navBtns = document.querySelectorAll(
      'button[data-action="previous"]:not([disabled]), button[data-action="next"]:not([disabled])'
    );
    return navBtns.length >= 2;
  }

  function clearInlineTransform() {
    art.style.transition = 'none';
    art.style.transform = '';
    // Force a reflow so the `none` sticks before the next event resets it.
    void art.offsetWidth;
    art.style.transition = '';
  }

  function snapBack() {
    art.style.transition = 'transform 200ms cubic-bezier(.22,1,.36,1)';
    art.style.transform = '';
    // Clean the styling after the snap so later code that reads transitions
    // doesn't see a lingering 200 ms curve.
    setTimeout(clearInlineTransform, 220);
  }

  function commitExit(direction) {
    art.style.transition = 'transform ' + EXIT_MS + 'ms cubic-bezier(.22,1,.36,1)';
    // Match physics: swipe left (next) slides the artwork off-screen to the
    // left; swipe right (previous) slides it off-screen to the right. This
    // matches the visual convention used by iOS/Spotify/YT Music, where the
    // artwork leaving in a direction implies "the next one comes from the
    // opposite side".
    const distance = direction === 'next' ? '-120vw' : '120vw';
    art.style.transform = 'translateX(' + distance + ')';
    const onDone = (ev) => {
      // The inline transition could fire for a separate property; only react
      // to the transform one we just set.
      if (ev && ev.propertyName && ev.propertyName !== 'transform') return;
      art.removeEventListener('transitionend', onDone);
      clearInlineTransform();
    };
    art.addEventListener('transitionend', onDone);
    // Safety net: transitionend won't fire if the element is hidden mid-flight
    // (e.g., the route closes, or a new track arrived and reset things).
    setTimeout(() => {
      art.removeEventListener('transitionend', onDone);
      if (art.style.transform !== '' && art.style.transform !== 'none') {
        clearInlineTransform();
      }
    }, EXIT_MS + 80);
  }

  function fireNav(direction) {
    // Piggyback the on-screen next/previous button so the existing _navBusy
    // lock, toast, showNowPlaying call, and progress.resetPending all fire
    // unchanged. This is exactly the same command the player plays when the
    // user taps the next/previous icon.
    const btn = document.querySelector('button[data-action="' + direction + '"]');
    if (btn) btn.click();
  }

  function onPointerDown(e) {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    if (active) return; // Ignore secondary fingers.
    if (!canStart()) return;
    // A new gesture starts clean: any layer left over from a previous commit
    // (settled as the banner) or snap-back must not be reused with stale
    // artwork — the first horizontal move rebuilds it for this gesture.
    hideIncoming();
    // Defensive: if any descendant overlay ever becomes interactive on mobile
    // again, treat taps inside it as separate targets, not swipe starts.
    if (e.target.closest('.np-page-art-overlay')) return;
    active = {
      pointerId: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      lastX: e.clientX,
      startedAt: performance.now(),
      axis: null,
    };
    if (art.setPointerCapture) {
      try { art.setPointerCapture(e.pointerId); } catch (_) {}
    }
  }

  function onPointerMove(e) {
    if (!active || e.pointerId !== active.pointerId) return;
    // Coalesced events feed smoother drags; keep only the latest pointer.
    const dx = e.clientX - active.startX;
    const dy = e.clientY - active.startY;
    const absX = Math.abs(dx);
    const absY = Math.abs(dy);

    if (active.axis === null) {
      if (Math.max(absX, absY) < AXIS_LOCK_PX) return; // bogus micro motion
      if (absY > absX * AXIS_BIAS) {
        // Vertical wins: hand it back to the page. Re-enable transitions so
        // any in-flight style change from a prior gesture isn't stuck.
        active = null;
        return;
      }
      if (absX > absY * AXIS_BIAS) {
        active.axis = 'h';
        art.style.transition = 'none';
      }
    }
    if (active.axis !== 'h') return;
    // Soft resistance past the visible artwork width so a wild fling doesn't
    // visually leap across the page: EXACT 1:1 tracking below
    // RESISTANCE_START_PX (the artwork must follow the finger precisely), then
    // above that each further RESISTANCE_DIVISOR_PX adds half as much visual
    // travel as the one before (asymptotically ~RESISTANCE_START_PX + 2x
    // DIVISOR). The offset must also stay MONOTONIC in the finger travel — an
    // earlier dampening formula (dx/(1+over/80)) declined past ~240px, sliding
    // the banner BACKWARD the further the finger pushed, and a first attempt at
    // fixing it accidentally made every sub-240px drag snap to 240px instead of
    // tracking the finger.
    const over = Math.max(0, absX - RESISTANCE_START_PX);
    const limitedAbs = Math.min(absX, RESISTANCE_START_PX)
      + over / (1 + over / RESISTANCE_DIVISOR_PX);
    const limited = (dx >= 0 ? 1 : -1) * limitedAbs;
    art.style.transform = 'translateX(' + limited.toFixed(1) + 'px)';
    // Reveal the adjacent cover beside the artwork as it slides. Direction is
    // re-derived on every move: if the finger reverses across the axis, the
    // layer re-points at the other neighbour (rare, but keeps the revealed
    // cover matching the direction the gesture will commit in).
    const dragDirection = dx < 0 ? 'next' : 'previous';
    if (!incoming || incoming.direction !== dragDirection) {
      const adjacent = findAdjacentTrack(dragDirection);
      if (adjacent && queueItemThumb(adjacent)) {
        showIncomingLayer(dragDirection, adjacent);
      } else {
        hideIncoming();
      }
    }
    if (incoming) {
      // Sync the layer by the same (resistance-limited) delta so it stays
      // flush against the artwork's moving edge, sliding in from the screen
      // edge the incoming track should come from: "next" (swiping left) is
      // parked off the RIGHT edge (+w), "previous" (swiping right) off the
      // LEFT edge (-w) — the same base showIncomingLayer parked it at.
      const w = incoming.layer.offsetWidth || art.clientWidth || 1;
      const fromSide = dragDirection === 'next' ? w : -w;
      incoming.layer.style.transform =
        'translateX(' + (fromSide + limited).toFixed(1) + 'px)';
    }
    active.lastX = e.clientX;
  }

  function showSwipeFeedback(direction) {
    // Tiny haptic blip on commit. navigator.vibrate is supported by Android
    // Chrome (and quietly returns false on iOS Safari / desktop) so iOS users
    // simply don't feel anything rather than seeing an error prompt.
    if (navigator.vibrate) {
      try { navigator.vibrate(20); } catch (_) {}
    }
    // Visual: a small pill that pops in above the artwork, then fades out
    // ~360 ms later. Lives on document.body with position:fixed so it
    // isn't dragged along with the artwork's exit transform.
    //
    // Position is fixed in CSS (top: 14vh) -- we deliberately do NOT read
    // art.getBoundingClientRect() to pick a top value. The artwork may
    // still hold an inline translateX residue from the user's drag at the
    // moment of release (endGesture clears it after commitExit, not before),
    // so reading the rect here would offset the pill sideways to wherever
    // the dragged artwork currently sits instead of staying centered.
    let pill = document.getElementById('np-swipe-indicator');
    if (!pill) {
      pill = document.createElement('div');
      pill.id = 'np-swipe-indicator';
      pill.className = 'np-swipe-indicator';
      pill.setAttribute('role', 'status');
      pill.setAttribute('aria-live', 'polite');
      document.body.appendChild(pill);
    }
    const nextLabel = 'Next \u2192';
    const prevLabel = '\u2190 Previous';
    // Only rewrite the live region if the label actually changed. Replaying
    // the same text into an aria-live=polite region on every commit is a
    // known anti-pattern and asks the screen-reader debouncer to work
    // harder than it needs to on rapid double-swipes.
    const desired = direction === 'next' ? nextLabel : prevLabel;
    if (pill.textContent !== desired) pill.textContent = desired;
    // Restart the entrance animation cleanly in case another commit fires
    // before the previous pill has faded out (rapid double-swipe).
    pill.classList.remove('visible');
    void pill.offsetWidth;
    pill.classList.add('visible');
    clearTimeout(pill._hideTimer);
    pill._hideTimer = setTimeout(() => pill.classList.remove('visible'), 360);
  }

  // If the user closes the Now Playing route in the 360 ms window between
  // the pill's show setTimeout and its hide setTimeout, force-hide the pill
  // now so it can't sit on top of whatever page they returned to. The
  // existing hashchange listener already cancels any in-flight gesture.
  // Empty the textContent too so the polite live region can't keep stale
  // "Next \u2192" / "\u2190 Previous" text accessible to screen readers after the
  // visual layer has faded.
  function hideSwipeFeedback() {
    const pill = document.getElementById('np-swipe-indicator');
    if (!pill) return;
    pill.classList.remove('visible');
    pill.textContent = '';
    clearTimeout(pill._hideTimer);
  }

  function endGesture(commit) {
    if (!active) return;
    const wasActive = active;
    active = null;
    if (!commit) {
      // Tuck the revealed cover back off-screen as the artwork springs back,
      // so the snap doesn't pop the neighbour out from under it. The layer is
      // removed once it has slid back under the art (identity-guarded so a
      // queued removal can never kill a newer gesture's layer).
      if (incoming) {
        const layer = incoming.layer;
        const w = layer.offsetWidth || art.clientWidth || 1;
        const side = incoming.direction === 'next' ? 1 : -1;
        layer.style.transition = 'transform 200ms cubic-bezier(.22,1,.36,1)';
        layer.style.transform = 'translateX(' + (side * w) + 'px)';
        setTimeout(() => {
          if (incoming && incoming.layer === layer) hideIncoming();
        }, 210);
      }
      snapBack();
      return;
    }
    const direction = wasActive.lastX < wasActive.startX ? 'next' : 'previous';
    // A committed gesture always settles in the layer it revealed; re-point it
    // at the committed direction's neighbour if the finger reversed mid-drag.
    if (incoming && incoming.direction !== direction) {
      const adjacent = findAdjacentTrack(direction);
      if (adjacent && queueItemThumb(adjacent)) {
        showIncomingLayer(direction, adjacent);
      } else {
        hideIncoming();
      }
    }
    if (incoming) {
      const layer = incoming.layer;
      // The neighbour settles into the artwork's home position, becoming the
      // banner; meanwhile the artwork exits off-screen and returns with the
      // same cover promoted onto it, so there is no blank frame either way.
      layer.style.transition = 'transform ' + EXIT_MS + 'ms cubic-bezier(.22,1,.36,1)';
      layer.style.transform = 'translateX(0)';
      promoteIncomingToArt(incoming.track);
    }
    // Remember the incoming-track side so the swap-in banner can slide in from
    // the opposite edge ("next" -> from the right, "previous" -> from the left).
    art.dataset.navDirection = direction;
    art._swipeSuppressClick = true;
    setTimeout(() => { art._swipeSuppressClick = false; }, SUPPRESS_CLICK_MS);
    // Show instantaneous confirmation (haptic + small pill) before the
    // artwork exit and the server nav fire so the user gets a same-frame
    // "got it" reply even though the Alexa round-trip won't surface new
    // track content for a few seconds.
    showSwipeFeedback(direction);
    commitExit(direction);
    fireNav(direction);
  }

  function onPointerUp(e) {
    if (!active || e.pointerId !== active.pointerId) return;
    const dx = active.lastX - active.startX;
    const dt = Math.max(1, performance.now() - active.startedAt);
    const velocity = Math.abs(dx) / dt;
    // Commit on drag-and-release only once the banner is revealed past 50% of
    // its own width; otherwise the gesture springs back and the current song
    // stays. A quick swipe (velocity) still commits at any distance.
    const revealThreshold = art.clientWidth * SWIPE_DISTANCE_FRACTION;
    const committed = active.axis === 'h' &&
      (Math.abs(dx) >= revealThreshold || velocity >= SWIPE_VELOCITY_PX_MS);
    endGesture(committed);
  }

  function onPointerCancel() {
    if (!active) return;
    const wasPointerId = active.pointerId;
    if (wasPointerId != null && art.releasePointerCapture) {
      try { art.releasePointerCapture(wasPointerId); } catch (_) {}
    }
    endGesture(false);
  }

  art.addEventListener('pointerdown', onPointerDown);
  art.addEventListener('pointermove', onPointerMove);
  art.addEventListener('pointerup', onPointerUp);
  art.addEventListener('pointercancel', onPointerCancel);
  art.addEventListener('pointerleave', onPointerCancel);
  art.addEventListener('lostpointercapture', onPointerCancel);
  // Long-press: cancel any in-progress gesture cleanly. The browser's
  // contextmenu keeps its default on mobile (which is a no-op anyway).
  art.addEventListener('contextmenu', () => {
    if (active) onPointerCancel();
  });
  // Capture the synthetic click that lands immediately after a swipe so it
  // doesn't leak to anything else. The np-page-art click handler is a no-op
  // on mobile today, so this is forward-compatible for future handlers.
  art.addEventListener('click', (e) => {
    if (art._swipeSuppressClick) {
      e.stopImmediatePropagation();
      e.preventDefault();
    }
  }, true);
  // If the now-playing route is closed mid-swipe, drop the gesture rather
  // than finishing an animation against an element that's now invisible.
  // Also hide the swipe-feedback pill so it can't bleed onto the page that
  // replaced the now-playing route during the pill's 360 ms auto-hide.
  window.addEventListener('hashchange', () => {
    if (active && document.body.classList.contains('now-playing-closing')) {
      onPointerCancel();
    }
    if (document.body.classList.contains('now-playing-closing')) {
      hideSwipeFeedback();
      // The route is closing mid-gesture; drop the reveal layer immediately
      // so it can't bleed onto the page that replaces the player.
      hideIncoming();
    }
  });
})();

/* ---- volume ---- */

function updateUrlBar() {
  const ytmBtn = document.getElementById('np-url-toggle');
  const mobileYtmBtn = document.getElementById('mobile-player-youtube');
  if (state._currentVideoId) {
    const seconds = Math.floor(progress.livePosition() / 1000);
    const url = 'https://music.youtube.com/watch?v=' + encodeURIComponent(state._currentVideoId)
      + (seconds > 0 ? '&t=' + seconds : '');
    if (ytmBtn) { ytmBtn.href = url; ytmBtn.style.display = ''; }
    if (mobileYtmBtn) { mobileYtmBtn.href = url; mobileYtmBtn.classList.remove('is-hidden'); }
  } else {
    if (ytmBtn) { ytmBtn.removeAttribute('href'); ytmBtn.style.display = 'none'; }
    if (mobileYtmBtn) { mobileYtmBtn.removeAttribute('href'); mobileYtmBtn.classList.add('is-hidden'); }
  }
}

(function () {
  const ytmBtn = document.getElementById('np-url-toggle');
  const mobileYtmBtn = document.getElementById('mobile-player-youtube');
  updateUrlBar();

  const onClick = (e) => {
    if (!state._currentVideoId) {
      e.preventDefault();
      toast('No song playing.', 'error');
      return;
    }
    // Refresh the href with the latest position right before navigating,
    // since livePosition() keeps ticking while the button just sits there.
    const seconds = Math.floor(progress.livePosition() / 1000);
    e.currentTarget.href = 'https://music.youtube.com/watch?v=' + encodeURIComponent(state._currentVideoId)
      + (seconds > 0 ? '&t=' + seconds : '');

    const serial = selectedSerial();
    // Jam guests share the host's playback device: opening the song on
    // YouTube must not pause the music for everyone in the jam.
    if (serial && !window.JAM_GUEST) {
      // Always attempt the pause, even if the locally-tracked isPlaying flag
      // says it's already paused: that flag is an optimistic client guess (SSE
      // lag, a race with another tab, etc.) and can be stale. Sending 'pause'
      // to an already-paused device is a harmless no-op server-side, whereas
      // skipping it on a stale "false" left music playing behind the YouTube
      // tab. A failed dispatch is now surfaced instead of silently swallowed.
      const previousPlaying = state.isPlaying;
      state.lastActionAt = Date.now();
      state.lastActionIntent = false;
      if (window.progress && window.progress.setPausePending) {
        window.progress.setPausePending(true);
      }
      api('/alexa/command/', { serial, action: 'pause' })
        .then(() => {
          state.isPlaying = false;
          state.lastActionIntent = false;
          if (window.progress && window.progress.setPausePending) {
            window.progress.setPausePending(false);
          }
          syncPlayPause();
        })
        .catch(() => {
          state.isPlaying = previousPlaying;
          state.lastActionIntent = previousPlaying;
          if (window.progress && window.progress.setPausePending) {
            window.progress.setPausePending(false);
          }
          syncPlayPause();
          toast('Could not pause playback.', 'error');
        });
    }
  };
  if (ytmBtn) ytmBtn.addEventListener('click', onClick);
  if (mobileYtmBtn) mobileYtmBtn.addEventListener('click', onClick);
})();

  window.syncPlayPause = syncPlayPause;
  window.showNowPlaying = showNowPlaying;
  window.syncTrackPlaybackIndicators = syncTrackPlaybackIndicators;
  window.refreshNpLikeButton = refreshNpLikeButton;
  window.checkLikedVersion = checkLikedVersion;
  window.playResult = playResult;
  window.syncModalScrollLock = syncModalScrollLock;
  window.clearUiAfterPlaybackReset = clearUiAfterPlaybackReset;
  window.doClearAll = doClearAll;
  window.updateUrlBar = updateUrlBar;
  window.preloadNowPlayingArtwork = preloadNowPlayingArtwork;
})();
