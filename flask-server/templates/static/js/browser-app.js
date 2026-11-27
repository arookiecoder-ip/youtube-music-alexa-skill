/* Native-app destinations and browser-capable player features. */
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  if (!$('app-player-tools')) return;
  const preferenceKey = 'musicbox.browser.preferences.v1';
  const defaults = {theme:'dark', haptics:false, miniLyrics:true, preload:true, mobilePreload:true, reduceMotion:false, boost:0, bass:0, libraryLayout:'list'};
  let preferences = {...defaults};
  try {
    const saved = JSON.parse(localStorage.getItem(preferenceKey) || '{}');
    for (const key of Object.keys(defaults)) if (typeof saved[key] === typeof defaults[key]) preferences[key] = saved[key];
  } catch (_) {}
  if (!['system','dark','light'].includes(preferences.theme)) preferences.theme = 'system';
  preferences.boost = Math.max(0, Math.min(100, preferences.boost));
  preferences.bass = Math.max(0, Math.min(12, preferences.bass));
  window.browserPreferences = preferences;
  const current = () => window.__appState && window.__appState._currentTrack || {};
  const message = (text, kind = 'error') => window.toast && window.toast(text, kind);
  const accountKey = 'musicbox.browser.' + encodeURIComponent(window.REMOTE_USERNAME || 'owner');
  function readList(key) { try { const value = JSON.parse(localStorage.getItem(accountKey + key) || '[]'); return Array.isArray(value) ? value.slice(0, 50) : []; } catch (_) { return []; } }
  function saveList(key, value) { try { localStorage.setItem(accountKey + key, JSON.stringify(value.slice(0,50))); } catch (_) {} }
  function applyPreferences() {
    const light = preferences.theme === 'light' || preferences.theme === 'system' && matchMedia('(prefers-color-scheme: light)').matches;
    document.documentElement.dataset.theme = light ? 'light' : 'dark';
    document.documentElement.classList.toggle('app-reduce-motion', preferences.reduceMotion);
    document.querySelectorAll('[data-pref]').forEach(input => {
      const value = preferences[input.dataset.pref];
      if (input.type === 'checkbox') input.checked = value; else input.value = value;
    });
    if (!preferences.miniLyrics) $('app-mini-lyrics').hidden = true;
  }
  applyPreferences();
  matchMedia('(prefers-color-scheme: light)').addEventListener('change', applyPreferences);
  document.querySelectorAll('[data-pref]').forEach(input => input.addEventListener('change', () => {
    preferences[input.dataset.pref] = input.type === 'checkbox' ? input.checked : input.type === 'range' ? Number(input.value) : input.value;
    try { localStorage.setItem(preferenceKey, JSON.stringify(preferences)); } catch (_) {}
    applyPreferences();
    if (input.dataset.pref === 'bass' || input.dataset.pref === 'boost') applySound();
    if (input.dataset.pref === 'miniLyrics' && preferences.miniLyrics) fetchLyrics(false).catch(() => {});
  }));

  function navigate(route) { if (window.navigateTo) window.navigateTo(route); }
  function routeChanged(route) {
    if (route === '#now-playing') return;
    const destination = route.startsWith('#search') || route.startsWith('#artist') ? '#search' :
      ['#library','#downloads'].includes(route) || route.startsWith('#playlist') ? '#library' : '#home';
    document.querySelectorAll('.app-dock a').forEach(link => {
      if (link.dataset.appRoute === destination) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
    });
    document.body.dataset.appView = route.slice(1).split('?')[0];
    for (const [target, id] of [['#search','browser-search-page'],['#settings','browser-settings-page'],['#downloads','browser-downloads-page']]) {
      if ($(id)) $(id).hidden = route !== target;
    }
    if (route.startsWith('#search?')) {
      const query = new URLSearchParams(route.slice(8)).get('q');
      if (query) saveList('.searches', [query, ...readList('.searches').filter(item => item !== query)].slice(0,10));
    }
    if (route === '#search') renderRecentSearches();
    if (route === '#downloads') renderDownloads();
    if (route === '#settings') storageUsage().catch(() => {});
    document.querySelectorAll('.sidebar-nav-btn[data-app-route]').forEach(button => {
      if (button.dataset.appRoute === route) button.setAttribute('aria-current','page'); else button.removeAttribute('aria-current');
    });
  }
  window.addEventListener('musicbox:route', event => routeChanged(event.detail.route));
  let libraryFilter = 'all';
  function applyLibraryFilter() {
    const page = $('library-page'); if (!page) return;
    page.classList.toggle('app-library-list', preferences.libraryLayout !== 'grid');
    const query = ($('app-library-query')?.value || '').trim().toLocaleLowerCase();
    page.querySelectorAll('.library-card').forEach(card => {
      const kind = card.classList.contains('library-card-artist') ? 'artists' : card.classList.contains('library-card-album') ? 'albums' : 'playlists';
      card.hidden = !(libraryFilter === 'all' || libraryFilter === kind) || !card.textContent.toLocaleLowerCase().includes(query);
    });
  }
  window.applyBrowserLibraryFilter = applyLibraryFilter;
  $('app-library-query')?.addEventListener('input', applyLibraryFilter);
  document.querySelectorAll('[data-library-filter]').forEach(button => button.addEventListener('click', () => {
    libraryFilter = button.dataset.libraryFilter;
    document.querySelectorAll('[data-library-filter]').forEach(item => item.setAttribute('aria-pressed',String(item === button)));
    applyLibraryFilter();
  }));
  applyLibraryFilter();
  document.addEventListener('click', event => {
    const link = event.target.closest('a[data-app-route], button[data-app-route]');
    if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (window._closeSidebar) window._closeSidebar();
    $('profile-menu-wrap')?.classList.remove('open'); $('profile-menu-trigger')?.setAttribute('aria-expanded','false');
    navigate(link.dataset.appRoute);
  });
  function renderRecentSearches() {
    const root = $('app-recent-searches'); root.replaceChildren();
    const recent = readList('.searches').filter(item => typeof item === 'string');
    if (!recent.length) return;
    const title = document.createElement('h2'); title.textContent = 'Recent searches'; root.append(title);
    for (const query of recent) {
      const button = document.createElement('button'); button.textContent = query;
      button.onclick = () => navigate('#search?q=' + encodeURIComponent(query)); root.append(button);
    }
  }
  for (const [label, route] of [['Search','#search'],['Downloads','#downloads'],['Settings','#settings']]) {
    const navigation = document.querySelector('.sidebar-nav');
    if (!navigation || window.JAM_GUEST && route !== '#search') continue;
    const button = document.createElement('button'); button.className = 'sidebar-nav-btn'; button.dataset.appRoute = route;
    button.setAttribute('aria-label', label); button.title = label;
    const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    icon.setAttribute('viewBox','0 0 24 24'); icon.setAttribute('fill','none'); icon.setAttribute('stroke','currentColor'); icon.setAttribute('stroke-width','1.8'); icon.setAttribute('aria-hidden','true');
    const paths = label === 'Search' ? ['M16 16l5 5','M18 10.5a7.5 7.5 0 1 1-15 0 7.5 7.5 0 0 1 15 0'] :
      label === 'Downloads' ? ['M12 3v12m-5-5 5 5 5-5','M4 16v5h16v-5'] : ['M12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2','M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0'];
    for (const d of paths) { const path = document.createElementNS('http://www.w3.org/2000/svg','path'); path.setAttribute('d',d); icon.append(path); }
    const caption = document.createElement('span'); caption.className = 'nav-label'; caption.textContent = label;
    button.append(icon,caption); navigation.append(button);
  }

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
  const tools = $('app-player-tools');
  const playerLeft = document.querySelector('.np-page-left');
  if (playerLeft) playerLeft.append(tools);
  const compact = document.querySelector('.player-section');
  if (compact) compact.querySelector('.np-info').append($('app-mini-lyrics'));
  const transport = document.querySelector('.mobile-np-controls');
  if (transport && !window.JAM_GUEST) {
    const shuffle = $('mobile-np-shuffle'); if (shuffle) transport.prepend(shuffle);
    const repeat = document.createElement('button'); repeat.type = 'button'; repeat.className = 'ghost-btn';
    repeat.dataset.appAction = 'repeat'; repeat.textContent = '↻'; repeat.setAttribute('aria-label','Repeat on this browser'); repeat.setAttribute('aria-pressed','false');
    repeat.disabled = !window.browserPlayback?.owns(); transport.append(repeat);
  }
  const more = $('np-more-menu');
  if (more && !window.JAM_GUEST) for (const [action, title] of [['lyrics','Lyrics'],['timer','Sleep timer'],['language','Audio language'],['download','Download']]) {
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
    if (!lyrics || lyricVideo !== current().video_id) { $('app-mini-lyrics').hidden = true; return; }
    const position = window.browserPlayback && window.browserPlayback.owns() ? window.browserPlayback.audio.currentTime * 1000 : Number($('progress-track')?.getAttribute('aria-valuenow') || 0);
    const lines = lyrics.lines || [];
    let at = -1;
    for (let i = 0; i < lines.length; i++) { if (position >= lines[i].start_ms) at = i; else break; }
    if (at !== activeLine) {
      const nodes = $('app-lyrics-body').children;
      if (nodes[activeLine]) nodes[activeLine].classList.remove('current');
      if (nodes[at]) nodes[at].classList.add('current'); activeLine = at;
    }
    const text = lines[at]?.text || lyrics.text?.split('\n').find(Boolean) || '';
    $('app-mini-lyrics').hidden = !preferences.miniLyrics || !text;
    if ($('app-mini-lyrics').textContent !== text) $('app-mini-lyrics').textContent = text;
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
      saveList('.downloads', [{video_id:item.video_id, title:item.title || item.video_id, artist:item.artist || '', bytes:size, at:Date.now()}, ...readList('.downloads').filter(row => row.video_id !== item.video_id)]);
      renderDownloads(); message('Saved to your device downloads', 'ok');
    } finally { if (downloadController) downloadController.abort(); downloadController = null; }
  }
  function renderDownloads() {
    const root = $('app-download-list'); if (!root) return; root.replaceChildren();
    for (const item of readList('.downloads')) {
      const row = document.createElement('div'); row.className = 'app-download-row';
      const text = document.createElement('div'), title = document.createElement('strong'), detail = document.createElement('small');
      title.textContent = item.title; detail.textContent = [item.artist, (Number(item.bytes) / 1048576).toFixed(1) + ' MB', 'Saved to device'].filter(Boolean).join(' · ');
      text.append(title,detail); row.append(text); root.append(row);
    }
    if (!root.children.length) root.textContent = 'No downloads saved from this browser yet. Play a song, then choose Download.';
  }

  let audioContext = null, bass = null, gain = null;
  function applySound() {
    const browser = window.browserPlayback;
    if (!browser || !browser.owns()) return;
    if (!audioContext && !(preferences.boost || preferences.bass)) return;
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) { if ($('app-sound-note')) $('app-sound-note').textContent = 'Audio effects are unavailable in this browser.'; return; }
    try {
      if (!audioContext) {
        audioContext = new AudioContext({latencyHint:'playback'});
        const source = audioContext.createMediaElementSource(browser.audio);
        bass = audioContext.createBiquadFilter(); bass.type = 'lowshelf'; bass.frequency.value = 160;
        gain = audioContext.createGain(); const limiter = audioContext.createDynamicsCompressor();
        limiter.threshold.value = 0; limiter.knee.value = 0; limiter.ratio.value = 20;
        source.connect(bass).connect(gain).connect(limiter).connect(audioContext.destination);
      }
      bass.gain.value = preferences.bass; gain.gain.value = 1 + preferences.boost / 100;
      audioContext.resume().catch(() => {});
    } catch (_) { if ($('app-sound-note')) $('app-sound-note').textContent = 'Audio effects could not be activated. Reload and try again.'; }
  }
  document.addEventListener('pointerdown', () => { if (preferences.boost || preferences.bass) applySound(); }, {passive:true});

  async function storageUsage() {
    if (!$('app-storage-usage')) return;
    if (!navigator.storage || !navigator.storage.estimate) { $('app-storage-usage').textContent = 'Storage estimates are unavailable in this browser'; return; }
    const usage = await navigator.storage.estimate();
    $('app-storage-usage').textContent = (Number(usage.usage || 0) / 1048576).toFixed(1) + ' MB used by this browser app';
  }
  let installPrompt = null;
  window.addEventListener('beforeinstallprompt', event => { event.preventDefault(); installPrompt = event; });
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
    'repeat': async () => {
      if (!window.browserPlayback?.owns()) return;
      await window.browserPlayback.execute('repeat', {});
      const button = document.querySelector('[data-app-action="repeat"]');
      button.setAttribute('aria-pressed',String(button.getAttribute('aria-pressed') !== 'true'));
    },
    'create-playlist': () => $('sidebar-new-playlist-btn')?.click(),
    'refresh-library': () => window.openLibraryPage && window.openLibraryPage(true),
    'library-layout': () => {
      preferences.libraryLayout = preferences.libraryLayout === 'grid' ? 'list' : 'grid';
      try { localStorage.setItem(preferenceKey, JSON.stringify(preferences)); } catch (_) {}
      applyLibraryFilter();
    },
    'focus-search': () => { $('mobile-search-toggle')?.click(); $('query').focus(); },
    'devices': () => { renderDevices(); showDialog('app-devices-dialog'); },
    'refresh-devices': () => { $('refresh').click(); setTimeout(renderDevices, 500); },
    'radio': () => $('np-menu-radio')?.click(),
    'timer': async () => { showDialog('app-timer-dialog'); await refreshTimer(); },
    'cancel-timer': () => setTimer(0),
    'lyrics': async () => { showDialog('app-lyrics-dialog'); await fetchLyrics(false); },
    'refresh-lyrics': () => fetchLyrics(true),
    'language': languages,
    'download': downloadCurrent,
    'reset-sound': () => { preferences.boost = 0; preferences.bass = 0; try { localStorage.setItem(preferenceKey, JSON.stringify(preferences)); } catch (_) {} applyPreferences(); applySound(); },
    'storage': storageUsage,
    'install': async () => { if (installPrompt) { await installPrompt.prompt(); installPrompt = null; } else message('Use your browser menu to install Music Box or add it to your home screen.', 'ok'); },
    'clear-cache': async () => {
      if (!confirm('Clear cached artwork and page data? Your account and downloaded files will stay.')) return;
      if (window.caches) for (const key of await caches.keys()) if (key.startsWith('mb-static-') || key.startsWith('mb-pages-')) await caches.delete(key);
      ['cachedDevices','cachedNowPlaying'].forEach(key => localStorage.removeItem(key)); await storageUsage(); message('Display cache cleared', 'ok');
    },
    'diagnostics': async () => {
      const audio = window.browserPlayback?.audio;
      const text = JSON.stringify({browser:navigator.userAgent, online:navigator.onLine, track:current().video_id || null,
        localPlayback:!!window.browserPlayback?.owns(), media:audio ? {state:audio.readyState, network:audio.networkState, error:audio.error?.code || null, position:audio.currentTime} : null}, null, 2);
      await navigator.clipboard.writeText(text); message('Playback diagnostics copied', 'ok');
    },
    'signout': async () => { if (confirm('Sign out of Music Box?')) { await window.api('/logout/', {}); location.assign('/login/'); } }
  };
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-app-action]');
    if (!button || button.disabled || !actions[button.dataset.appAction]) return;
    event.preventDefault();
    if (window._closeNpMoreMenu) window._closeNpMoreMenu();
    if (window._closeNpMoreMenu) window._closeNpMoreMenu();
    if (preferences.haptics && navigator.vibrate) navigator.vibrate(8);
    Promise.resolve().then(actions[button.dataset.appAction]).catch(error => {
      message(error.message || 'Could not finish this action. Try again.');
      if ($('app-lyrics-dialog').open) $('app-lyrics-body').textContent = 'Lyrics could not load. Use Refresh lyrics to try again.';
      if ($('app-language-dialog').open) $('app-language-list').textContent = 'Audio languages could not load. Close and try again.';
    });
  });
  let seenVideo = '';
  function trackChanged() {
    const video = current().video_id || ''; tools.hidden = !video || !!window.JAM_GUEST;
    const repeat = document.querySelector('[data-app-action="repeat"]');
    if (repeat) repeat.disabled = !window.browserPlayback?.owns();
    const deviceLabel = document.querySelector('.mobile-np-actions [data-app-action="devices"] span');
    if (deviceLabel) {
      const selected = $('device')?.value || '';
      deviceLabel.textContent = selected === 'mobile:' + window.browserPlayback?.id ? 'Browser' : selected.startsWith('mobile:') ? 'Phone' : selected ? 'Alexa' : 'Devices';
    }
    if (seenVideo === video) return;
    seenVideo = video; lyrics = null; ++lyricsRequest; $('app-mini-lyrics').hidden = true;
    if (!window.JAM_GUEST && video && (preferences.miniLyrics || $('app-lyrics-dialog').open)) fetchLyrics(false).catch(() => {
      if ($('app-lyrics-dialog').open) $('app-lyrics-body').textContent = 'Lyrics could not load. Use Refresh lyrics to try again.';
    });
  }
  window.addEventListener('musicbox:track', trackChanged);
  $('app-lyrics-dialog').addEventListener('close', () => { $('app-lyrics-body').replaceChildren(); activeLine = -2; });
  function connectivity() { $('app-connectivity').hidden = navigator.onLine !== false; }
  window.addEventListener('online', connectivity); window.addEventListener('offline', connectivity);
  const clock = setInterval(() => { updateTimer(); if (!document.hidden) updateLyrics(); }, 1000);
  window.addEventListener('pagehide', () => { clearInterval(clock); if (downloadController) downloadController.abort(); if (audioContext) audioContext.close().catch(() => {}); });
  routeChanged(window.getRoute ? window.getRoute() : '#home'); trackChanged(); connectivity();
  if (!window.JAM_GUEST) refreshTimer().catch(() => {});
})();
