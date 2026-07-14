(function () {
  'use strict';
  const state = window.__appState = window.__appState || {};
  if (state.volumeUserActive === undefined) state.volumeUserActive = false;
  if (state.volumeGraceUntil === undefined) state.volumeGraceUntil = 0;
  if (state.VOLUME_GRACE_MS === undefined) state.VOLUME_GRACE_MS = 4000;
  if (state._volCommandSeq === undefined) state._volCommandSeq = 0;
  if (state.lastVolumeRefreshAt === undefined) state.lastVolumeRefreshAt = 0;

const deviceEl = document.getElementById('device');
const volumeEl = document.getElementById('volume');
const mobileVolumeEl = document.getElementById('mobile-np-volume');
const mobileVolumeValue = document.getElementById('mobile-np-volume-value');

function isYoutubeLinkLike(value) {
  return /^(https?:\/\/)?(www\.|m\.|music\.)?(youtube\.com\/|youtu\.be\/)/i.test((value || '').trim());
}

let _volumeRepaintTimer = null;
function syncVolume(value, force) {
  const volume = Number(value);
  if (!Number.isFinite(volume)) return;
  volumeEl.disabled = false;
  const v = String(Math.max(0, Math.min(100, Math.round(volume))));
  // Server-pushed volume must not fight the user. The drag flag alone isn't
  // enough: it's cleared the instant the finger lifts, while the debounced
  // volume command is still in flight — a push generated before the command
  // landed still carries the OLD volume and would snap the slider back before
  // it jumps forward again. Hold server updates for a short grace window.
  if (!force && (state.volumeUserActive || Date.now() < state.volumeGraceUntil)) return;
  // Rapid repeated voice commands ("volume up" a few times quickly) can have
  // their state-report webhooks land out of order (no sequence/ordering info
  // in Alexa's webhook payload) — applying each one immediately as it arrives
  // visibly bounced the slider between values before settling. Coalesce a
  // tight burst into a single repaint of whichever value arrives last.
  clearTimeout(_volumeRepaintTimer);
  _volumeRepaintTimer = setTimeout(() => {
    volumeEl.value = v;
    const mpVol = document.getElementById('mp-volume');
    if (mpVol) mpVol.value = v;
    if (mobileVolumeEl) mobileVolumeEl.value = v;
    if (mobileVolumeValue) mobileVolumeValue.value = v;
  }, 150);
}

async function refreshVolume(force) {
  const serial = deviceEl.value;
  if (!serial) {
    return;
  }
  const now = Date.now();
  if (!force && now - state.lastVolumeRefreshAt < 15000) return;
  state.lastVolumeRefreshAt = now;
  try {
    const data = await api('/alexa/volume/?serial=' + encodeURIComponent(serial));
    if (data.available === false || data.volume === undefined || data.volume === null) {
      return;
    }
    syncVolume(data.volume);
  } catch (_) {
    // Volume reads are best-effort. The slider still sends volume commands.
  }
}

/* ---- now-playing via SSE (Server-Sent Events) ---- */

let volTimer;
volumeEl.addEventListener('pointerdown', () => { state.volumeUserActive = true; });
volumeEl.addEventListener('pointerup', () => { state.volumeUserActive = false; });
volumeEl.addEventListener('touchend', () => { state.volumeUserActive = false; });
volumeEl.addEventListener('change', () => { state.volumeUserActive = false; });
volumeEl.oninput = e => {
  state.volumeUserActive = true;
  state.volumeGraceUntil = Date.now() + state.VOLUME_GRACE_MS;
  clearTimeout(volTimer);
  // Several separate quick clicks/taps (not one continuous drag) each land
  // more than 220ms apart, so each one's timer fired independently and sent
  // its OWN real /alexa/command/ volume call -- e.g. clicking 31, 32, 34, 21,
  // 29 actually told the Echo to change volume 5 times in a row. Each of
  // those real device changes takes a moment to execute and report back, and
  // the confirmation webhooks can arrive out of order, so the slider visibly
  // hopped through several past values before landing on the last one.
  // Fix: only ever let the MOST RECENT command's result touch shared state.
  const mySeq = ++state._volCommandSeq;
  volTimer = setTimeout(() => {
    const serial = selectedSerial();
    if (!serial) {
      state.volumeUserActive = false;
      state.volumeGraceUntil = 0;
      return;
    }
    const value = +e.target.value;
    state.volumeGraceUntil = Date.now() + state.VOLUME_GRACE_MS;
    toast('Volume ' + e.target.value + '\u2026');
    api('/alexa/command/', { serial, action: 'volume', value })
      .then(() => {
        if (mySeq !== state._volCommandSeq) return; // superseded by a later click
        state.volumeUserActive = false;
        state.volumeGraceUntil = Date.now() + state.VOLUME_GRACE_MS;
        syncVolume(value, true);
        toast('Volume ' + value, 'ok');
      })
      .catch(err => {
        if (mySeq !== state._volCommandSeq) return; // superseded by a later click
        state.volumeUserActive = false;
        state.volumeGraceUntil = 0;      // let server truth restore the slider
        refreshVolume(true);
        toast(err.message, 'error');
      });
  }, 220);
};

if (mobileVolumeEl) {
  let mobileVolTimer;
  mobileVolumeEl.addEventListener('pointerdown', () => { state.volumeUserActive = true; });
  mobileVolumeEl.addEventListener('pointerup', () => { state.volumeUserActive = false; });
  mobileVolumeEl.addEventListener('touchend', () => { state.volumeUserActive = false; });
  mobileVolumeEl.addEventListener('change', () => { state.volumeUserActive = false; });
  mobileVolumeEl.oninput = e => {
    const value = +e.target.value;
    state.volumeUserActive = true;
    state.volumeGraceUntil = Date.now() + state.VOLUME_GRACE_MS;
    volumeEl.value = e.target.value;
    if (mobileVolumeValue) mobileVolumeValue.value = e.target.value;
    clearTimeout(mobileVolTimer);
    const mySeq = ++state._volCommandSeq;
    mobileVolTimer = setTimeout(() => {
      const serial = selectedSerial();
      if (!serial) {
        state.volumeUserActive = false;
        state.volumeGraceUntil = 0;
        return;
      }
      toast('Volume ' + value + '\u2026');
      api('/alexa/command/', { serial, action: 'volume', value })
        .then(() => {
          if (mySeq !== state._volCommandSeq) return;
          state.volumeUserActive = false;
          state.volumeGraceUntil = Date.now() + state.VOLUME_GRACE_MS;
          syncVolume(value, true);
          toast('Volume ' + value, 'ok');
        })
        .catch(err => {
          if (mySeq !== state._volCommandSeq) return;
          state.volumeUserActive = false;
          state.volumeGraceUntil = 0;
          refreshVolume(true);
          toast(err.message, 'error');
        });
    }, 220);
  };
}

const mobileVolumeButton = document.getElementById('mobile-player-volume');
const mobileVolumePopover = document.getElementById('mobile-volume-popover');
if (mobileVolumeButton && mobileVolumePopover) {
  const closeMobileVolume = () => {
    mobileVolumePopover.classList.remove('open');
    mobileVolumeButton.setAttribute('aria-expanded', 'false');
  };
  const positionMobileVolumePopover = () => {
    const buttonRect = mobileVolumeButton.getBoundingClientRect();
    const popoverWidth = Math.min(250, Math.max(0, window.innerWidth - 20));
    const gap = 8;
    const left = Math.max(10, Math.min(buttonRect.left, window.innerWidth - popoverWidth - 10));
    const belowTop = buttonRect.bottom + gap;
    const popoverHeight = mobileVolumePopover.offsetHeight;
    const top = belowTop + popoverHeight <= window.innerHeight - 10
      ? belowTop
      : Math.max(10, buttonRect.top - popoverHeight - gap);
    mobileVolumePopover.style.position = 'fixed';
    mobileVolumePopover.style.left = `${left}px`;
    mobileVolumePopover.style.right = 'auto';
    mobileVolumePopover.style.top = `${top}px`;
    const buttonCenterInPopover = buttonRect.left + buttonRect.width / 2 - left;
    const arrowRight = popoverWidth - buttonCenterInPopover - 5;
    mobileVolumePopover.style.setProperty('--volume-arrow-right', `${Math.max(12, Math.min(popoverWidth - 24, arrowRight))}px`);
  };
  mobileVolumeButton.addEventListener('click', e => {
    e.preventDefault();
    e.stopPropagation();
    const open = !mobileVolumePopover.classList.contains('open');
    closeMobileVolume();
    if (!open) return;
    const mobileMoreMenu = document.getElementById('np-more-menu');
    const mobileMoreButton = document.getElementById('mobile-player-more');
    if (mobileMoreMenu && mobileMoreMenu.classList.contains('mobile-open') && mobileMoreButton) {
      mobileMoreButton.click();
    }
    mobileVolumeEl.value = volumeEl.value;
    if (mobileVolumeValue) mobileVolumeValue.value = volumeEl.value;
    mobileVolumePopover.classList.add('open');
    mobileVolumeButton.setAttribute('aria-expanded', 'true');
    positionMobileVolumePopover();
    refreshVolume(true);
  });
  document.addEventListener('click', e => {
    if (!e.target.closest('.mobile-player-volume-wrap')) closeMobileVolume();
  }, true);
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeMobileVolume();
  });
}

/* ---- login gating ---- */

  window.isYoutubeLinkLike = isYoutubeLinkLike;
  window.syncVolume = syncVolume;
  window.refreshVolume = refreshVolume;
})();
