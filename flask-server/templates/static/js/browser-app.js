/* Player features using the existing browser More menu. */
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  if (!$('app-timer-dialog')) return;
  const current = () => window.__appState && window.__appState._currentTrack || {};
  const message = (text, kind = 'error') => window.toast && window.toast(text, kind);
  function showDialog(id) {
    const dialog = $(id);
    if (window._closeNpMoreMenu) window._closeNpMoreMenu();
    if (!dialog.open) dialog.showModal();
  }
  document.querySelectorAll('.app-sheet').forEach(dialog => {
    dialog.querySelector('[data-close-dialog]').onclick = () => dialog.close();
    dialog.addEventListener('click', event => {
      if (event.target !== dialog) return;
      const box = dialog.getBoundingClientRect();
      if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dialog.close();
    });
  });
  const more = $('np-more-menu');
  if (more && !window.JAM_GUEST) for (const [action, title] of [['devices','Playback devices'],['lyrics','Lyrics'],['timer','Sleep timer'],['language','Audio language'],['download','Download']]) {
    if (window.JAM_GUEST && action === 'download') continue;
    const button = document.createElement('button'); button.className = 'result-menu-option';
    button.setAttribute('role','menuitem'); button.dataset.appAction = action;
    const icon = document.createElementNS('http://www.w3.org/2000/svg','svg');
    icon.setAttribute('viewBox','0 0 24 24'); icon.setAttribute('fill','none'); icon.setAttribute('stroke','currentColor'); icon.setAttribute('stroke-width','1.8'); icon.setAttribute('aria-hidden','true');
    const path = document.createElementNS('http://www.w3.org/2000/svg','path');
    path.setAttribute('d', action === 'lyrics' ? 'M4 3h16v18H4ZM8 8h8M8 12h8M8 16h5' : action === 'timer' ? 'M9 2h6M12 7v6l3 2M21 13a9 9 0 1 1-18 0 9 9 0 0 1 18 0' : action === 'download' ? 'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5' : 'M3 5h12M9 2v3M5 5c0 6 4 9 9 11M13 5c0 6-4 9-9 11M15 21l4-10 4 10M16 18h6');
    icon.append(path); const caption = document.createElement('span'); caption.textContent = title;
    button.append(icon,caption); more.append(button);
  }

  function renderDevices() {
    const root = $('app-device-list'), select = $('device'); root.replaceChildren();
    for (const option of select ? select.options : []) {
      if (!option.value) continue;
      const button = document.createElement('button'); button.textContent = option.textContent;
      button.disabled = option.disabled || option.dataset.online === '0' || option.dataset.loggedOut === '1';
      button.setAttribute('aria-pressed', String(option.value === select.value));
      button.onclick = () => { select.value = option.value; select.dispatchEvent(new Event('change')); $('app-devices-dialog').close(); };
      root.append(button);
    }
    if (!root.children.length) root.textContent = 'No playback devices are available. Refresh to try again.';
  }

  let timerDeadline = 0, timerLocal = false, timerBusy = false, timerRevision = 0;
  async function refreshTimer() {
    const revision = timerRevision;
    const reply = await window.api('/api/app/sleep-timer/');
    if (revision === timerRevision && !timerBusy) acceptTimer(reply);
  }
  function acceptTimer(reply) {
    timerDeadline = reply.remaining_ms > 0 ? performance.now() + reply.remaining_ms : 0;
    timerLocal = !!window.browserPlayback && reply.playback_output === 'phone' && reply.output_owner === window.browserPlayback.id;
    updateTimer();
  }
  function updateTimer() {
    const remaining = timerDeadline ? Math.max(0, timerDeadline - performance.now()) : 0;
    const minutes = Math.ceil(remaining / 60000);
    $('app-timer-badge').textContent = minutes ? '· ' + minutes + 'm' : '';
    $('app-timer-status').textContent = minutes ? 'Playback stops in ' + minutes + ' minute' + (minutes === 1 ? '.' : 's.') : 'Stop playback after a set time.';
    if (timerDeadline && !remaining) {
      timerDeadline = 0;
      if (timerLocal && window.browserPlayback.owns()) window.browserPlayback.execute('pause', {}).catch(() => {});
    }
  }
  async function setTimer(minutes) {
    if (timerBusy) return;
    if (!Number.isInteger(minutes) || minutes < 0 || minutes > 180) throw new Error('Choose a timer between 1 and 180 minutes.');
    timerBusy = true;
    ++timerRevision;
    $('app-timer-form').querySelectorAll('button').forEach(button => { button.disabled = true; });
    try {
      const reply = await window.api('/api/app/sleep-timer/', {minutes}); acceptTimer(reply);
      $('app-timer-dialog').close(); message(minutes ? 'Sleep timer set' : 'Sleep timer cancelled', 'ok');
    } finally { timerBusy = false; $('app-timer-form').querySelectorAll('button').forEach(button => { button.disabled = false; }); }
  }
  $('app-timer-form').onsubmit = event => { event.preventDefault(); setTimer(Number($('app-timer-minutes').value)).catch(error => message(error.message)); };
  document.querySelectorAll('[data-timer-minutes]').forEach(button => { button.onclick = () => setTimer(Number(button.dataset.timerMinutes)).catch(error => message(error.message)); });

  const lyricsCache = new Map(); let lyrics = null, lyricsRequest = 0, lyricVideo = '', activeLine = -2;
  async function fetchLyrics(refresh) {
    const item = current(), video = item.video_id;
    if (!video) { $('app-lyrics-body').textContent = 'Choose a song to see lyrics.'; return; }
    const request = ++lyricsRequest;
    $('app-lyrics-track').textContent = [item.title, item.artist].filter(Boolean).join(' · ');
    $('app-lyrics-body').textContent = 'Finding lyrics…';
    $('app-lyrics-source').textContent = '';
    const result = !refresh && lyricsCache.has(video) ? lyricsCache.get(video) :
      await window.api('/api/track/' + encodeURIComponent(video) + '/lyrics' + (refresh ? '?refresh=1' : ''));
    if (request !== lyricsRequest || current().video_id !== video) return;
    lyricsCache.delete(video); lyricsCache.set(video, result);
    if (lyricsCache.size > 20) lyricsCache.delete(lyricsCache.keys().next().value);
    lyrics = result; lyricVideo = video; activeLine = -2;
    $('app-lyrics-source').textContent = result.source ? 'Lyrics · ' + result.source : '';
    const root = $('app-lyrics-body'); root.replaceChildren();
    if ($('app-lyrics-dialog').open) {
      if (Array.isArray(result.lines) && result.lines.length) {
        const fragment = document.createDocumentFragment();
        for (const line of result.lines) { const span = document.createElement('span'); span.className = 'app-lyrics-line'; span.textContent = line.text; fragment.append(span); }
        root.append(fragment);
      } else root.textContent = result.text || 'Lyrics are not available for this song yet.';
    }
    updateLyrics();
  }
  function updateLyrics() {
    if (!lyrics || lyricVideo !== current().video_id) {  return; }
    const position = window.browserPlayback && window.browserPlayback.owns() ? window.browserPlayback.audio.currentTime * 1000 : Number($('progress-track')?.getAttribute('aria-valuenow') || 0);
    const lines = lyrics.lines || [];
    let at = -1;
    for (let i = 0; i < lines.length; i++) { if (position >= lines[i].start_ms) at = i; else break; }
    if (at !== activeLine) {
      const nodes = $('app-lyrics-body').children;
      if (nodes[activeLine]) nodes[activeLine].classList.remove('current');
      if (nodes[at]) nodes[at].classList.add('current'); activeLine = at;
    }

  }

  let downloadController = null;
  async function downloadCurrent() {
    if (window.JAM_GUEST) return;
    if (downloadController) { message('A download is already in progress.'); return; }
    const item = {...current()};
    if (!item.video_id) throw new Error('Choose a song to download.');
    downloadController = new AbortController();
    message('Saving ' + (item.title || 'audio') + '…', 'ok');
    try {
      const response = await fetch(window.browserAudioUrl(item.video_id), {credentials:'same-origin', headers:{'X-MusicBox-Download':'1'}, signal:downloadController.signal});
      if (!response.ok) throw new Error('Download could not start. Check your connection and try again.');
      if (!/^audio\//i.test(response.headers.get('Content-Type') || '')) throw new Error('The server did not return an audio file.');
      const maxBytes = 32 * 1024 * 1024;
      if (Number(response.headers.get('Content-Length')) > maxBytes) throw new Error('This file is too large to save here. Use the Android app.');
      const reader = response.body.getReader(), chunks = []; let size = 0;
      while (true) {
        const {done, value} = await reader.read(); if (done) break;
        size += value.byteLength;
        if (size > maxBytes) { await reader.cancel(); throw new Error('This file is too large to save here. Use the Android app.'); }
        chunks.push(value);
      }
      const type = response.headers.get('Content-Type'), blob = new Blob(chunks, {type});
      const url = URL.createObjectURL(blob), link = document.createElement('a');
      link.href = url; link.download = (item.title || item.video_id).replace(/[\\/:*?"<>|]/g, '_').slice(0,120) + (/mpeg/.test(type) ? '.mp3' : /webm/.test(type) ? '.webm' : /wav/.test(type) ? '.wav' : /ogg/.test(type) ? '.ogg' : '.m4a');
      document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
 message('Saved to your device downloads', 'ok');
    } finally { if (downloadController) downloadController.abort(); downloadController = null; }
  }
  async function languages() {
    showDialog('app-language-dialog'); const root = $('app-language-list'); root.textContent = 'Loading available audio…';
    if (!window.browserPlayback || !window.browserPlayback.owns()) { root.textContent = 'Choose this browser as the playback device to change audio language.'; return; }
    const item = current(); if (!item.video_id) { root.textContent = 'Choose a song first.'; return; }
    const video = item.video_id;
    const reply = await window.api('/audio/?video_id=' + encodeURIComponent(video) + '&audio_tracks=1');
    if (current().video_id !== video) return;
    root.replaceChildren();
    for (const track of reply.audio_tracks || []) {
      const id = track.id || track.audio_track || track.language;
      if (!id) continue;
      const button = document.createElement('button'); button.textContent = track.display_name || track.name || track.label || track.language || id;
      button.setAttribute('aria-pressed', String(id === reply.selected));
      button.onclick = async () => { try { await window.browserPlayback.selectAudioLanguage(id); $('app-language-dialog').close(); } catch (error) { message(error.message); } };
      root.append(button);
    }
    if (!root.children.length) root.textContent = 'This song has only its original audio track.';
  }
  const actions = {
    'devices': () => { renderDevices(); showDialog('app-devices-dialog'); },
    'refresh-devices': () => { $('refresh').click(); setTimeout(renderDevices, 500); },
    'radio': () => $('np-menu-radio')?.click(),
    'timer': async () => { showDialog('app-timer-dialog'); await refreshTimer(); },
    'cancel-timer': () => setTimer(0),
    'lyrics': async () => { showDialog('app-lyrics-dialog'); await fetchLyrics(false); },
    'refresh-lyrics': () => fetchLyrics(true),
    'language': languages,
    'download': downloadCurrent,
  };
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-app-action]');
    if (!button || button.disabled || !actions[button.dataset.appAction]) return;
    event.preventDefault();
    if (window._closeNpMoreMenu) window._closeNpMoreMenu();
    Promise.resolve().then(actions[button.dataset.appAction]).catch(error => {
      message(error.message || 'Could not finish this action. Try again.');
      if ($('app-lyrics-dialog').open) $('app-lyrics-body').textContent = 'Lyrics could not load. Use Refresh lyrics to try again.';
      if ($('app-language-dialog').open) $('app-language-list').textContent = 'Audio languages could not load. Close and try again.';
    });
  });
  window.addEventListener('musicbox:track', () => { lyrics = null; ++lyricsRequest; if ($('app-lyrics-dialog').open) fetchLyrics(false).catch(error => message(error.message)); });
  $('app-lyrics-dialog').addEventListener('close', () => { $('app-lyrics-body').replaceChildren(); activeLine = -2; });
  const clock = setInterval(() => { updateTimer(); if ($('app-lyrics-dialog').open && !document.hidden) updateLyrics(); }, 1000);
  window.addEventListener('pagehide', () => { clearInterval(clock); if (downloadController) downloadController.abort(); });
  if (!window.JAM_GUEST) refreshTimer().catch(() => {});
})();
