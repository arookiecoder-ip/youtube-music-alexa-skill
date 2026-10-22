/* A browser is an acknowledged playback device, sharing the phone ownership protocol. */
(function () {
  'use strict';
  if (window.JAM_GUEST || !window.Audio || !window.crypto) return;
  const api = window.api;
  const uuid = () => typeof crypto.randomUUID === 'function' ? crypto.randomUUID() :
    Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
  const id = 'browser-' + uuid();
  const session = uuid();
  const audio = new Audio();
  audio.preload = 'auto';
  let output = null, accepted = '', leaseUntil = 0, queue = [], index = -1;
  let ack = [], stopped = false, polling = null, generation = 0, lastReport = 0;
  let repeat = false, commandChain = Promise.resolve(), reportChain = Promise.resolve();
  let changingSource = false, pendingSeek = null;
  const processing = new Set(), completed = new Set();
  const ownSerial = 'mobile:' + id;
  const owns = () => !!output && output.playback_output === 'phone' && output.output_owner === id &&
    !output.handoff_pending && accepted === output.output_token && Date.now() < leaseUntil;
  const track = item => ({
    video_id: item.video_id || item.videoId || item.ytVideoId || '',
    title: item.title || '', artist: item.artist || '',
    thumbnail: item.thumbnail || item.thumbnailUri || '',
    duration_ms: item.duration_ms || Number(item.durationSec || item.duration_seconds || 0) * 1000,
    entry_id: item.entry_id || item.uuid || uuid()
  });
  function notify(message) { if (window.toast) window.toast(message, 'error'); }
  function publish(replace = false) {
    if (!owns() || !queue[index] || changingSource) return Promise.resolve();
    const token = accepted, capturedGeneration = generation;
    const body = {
      action: replace ? 'start' : 'current', tracks: replace ? queue.map(item => ({...item})) : [],
      after: queue[index].video_id, queue_index: index, output_owner: id, output_token: token,
      position_ms: pendingSeek ? pendingSeek.position : Math.max(0, Math.round((audio.currentTime || 0) * 1000)),
      playing: !audio.paused && !audio.ended, buffering: !audio.paused && (audio.readyState < 3 || audio.seeking === true)
    };
    // Keep pre-seek/pre-pause HTTP reports from landing after newer positions.
    const operation = reportChain.then(() => {
      if (owns() && token === accepted && capturedGeneration === generation)
        return api('/api/app/queue/', body);
    });
    reportChain = operation.catch(() => {});
    return operation;
  }
  function applySeek() {
    if (!pendingSeek || pendingSeek.generation !== generation || audio.readyState < 1) return;
    const target = pendingSeek.position / 1000;
    const duration = Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : target;
    audio.currentTime = Math.min(target, duration);
    pendingSeek = null;
  }
  function seek(position) {
    pendingSeek = { position, generation };
    applySeek();
  }
  function resume() {
    const playGeneration = generation;
    // A cold download must not block pause, seeking, or the next selection.
    Promise.resolve(audio.play()).then(() => {
      if (generation !== playGeneration) return;
      if (!owns()) { audio.pause(); return; }
      return publish();
    }).catch(error => {
      if (generation !== playGeneration || error.name === 'AbortError') return;
      audio.pause(); publish().catch(() => {});
      notify(error.name === 'NotAllowedError' ? 'Tap Play to allow audio in this browser.' : 'Could not load this track. Tap Play to retry.');
    });
  }
  async function start(nextIndex, playing = true, position = 0) {
    if (!owns()) throw new Error('Playback moved to another device. Choose this browser again.');
    if (!queue[nextIndex]) return;
    const selected = queue[nextIndex];
    if (!/^[A-Za-z0-9_-]{11}$/.test(selected.video_id)) throw new Error('This track has no playable video.');
    ++generation;
    changingSource = true;
    audio.pause();
    index = nextIndex;
    pendingSeek = null;
    audio.src = window.browserAudioUrl(selected.video_id);
    changingSource = false;
    seek(Math.max(0, position));
    if (playing) resume();
    await publish();
    prefetchNext();
  }
  function prefetchNext() {
    const next = queue[index + 1];
    if (!owns() || audio.paused || !next || !window.fetch ||
        window.navigator && navigator.connection && navigator.connection.saveData) return;
    const url = new URL(window.browserAudioUrl(next.video_id), window.location.href);
    url.searchParams.delete('wait'); url.searchParams.set('prefetch', '1');
    fetch(url, { credentials: 'same-origin' }).catch(() => {});
  }
  function seedRadio(seed) {
    const capturedGeneration = generation, capturedToken = accepted;
    api('/get_radio/?update_queue=0&video_id=' + encodeURIComponent(seed)).then(async result => {
      if (!owns() || generation !== capturedGeneration || accepted !== capturedToken) return;
      const seen = new Set(queue.map(item => item.video_id));
      const extra = (result.playlist || []).map(track).filter(item => {
        if (!/^[A-Za-z0-9_-]{11}$/.test(item.video_id) || seen.has(item.video_id)) return false;
        seen.add(item.video_id); return true;
      });
      if (!extra.length || !queue[index]) return;
      await api('/api/app/queue/', { action: 'extend', after: queue[index].video_id, tracks: extra,
        output_owner: id, output_token: capturedToken });
      if (!owns() || generation !== capturedGeneration || accepted !== capturedToken) return;
      queue.push(...extra); prefetchNext();
    }).catch(() => { /* The selected song remains usable if recommendations fail. */ });
  }
  async function items(payload) {
    const collection = payload.collection;
    const playlist = payload.playlist_id || collection && (collection.playlistId || collection.id);
    if (playlist) {
      const path = collection && collection.kind === 'album' ? '/api/album/' : '/api/library/playlists/';
      const result = await api(path + encodeURIComponent(playlist) + '?playback=1&limit=5000');
      return (result.tracks || []).map(track).filter(t => /^[A-Za-z0-9_-]{11}$/.test(t.video_id));
    }
    const supplied = payload.queue_items || payload.tracks || (payload.track ? [payload.track] : [payload]);
    let resolved = supplied.map(track).filter(t => /^[A-Za-z0-9_-]{11}$/.test(t.video_id));
    if (!resolved.length && payload.query) {
      const video = String(payload.query).match(/(?:[?&]v=|youtu\.be\/)([A-Za-z0-9_-]{11})/);
      if (video) resolved = [track({ video_id: video[1], title: 'YouTube track' })];
      else {
        const result = await api('/alexa/search/?q=' + encodeURIComponent(payload.query));
        resolved = (result.songs || []).slice(0, 1).map(track);
      }
    }
    return resolved;
  }
  async function execute(action, payload) {
    if (!owns()) throw new Error('This browser no longer owns playback.');
    if (action === 'clear') {
      generation++; pendingSeek = null; changingSource = true; audio.pause(); audio.removeAttribute('src'); queue = []; index = -1; changingSource = false;
      await api('/api/app/queue/', { action: 'clear', output_owner: id, output_token: accepted });
      return;
    }
    if (action === 'song' || action === 'queue') {
      if (action === 'queue' && payload.queue_index !== undefined && !payload.queue_items &&
          !payload.tracks && !payload.playlist_id && !payload.collection) {
        const at = Number(payload.queue_index);
        if (!Number.isInteger(at) || !queue[at]) throw new Error('Queue changed. Choose the track again.');
        await start(at); return;
      }
      const requestedGeneration = ++generation;
      audio.pause();
      const resolved = await items(payload);
      if (!owns() || generation !== requestedGeneration) return;
      if (!resolved.length) throw new Error('This collection has no playable tracks.');
      queue = resolved;
      if (payload.shuffle) shuffleTail(0);
      const targetVideo = payload.target_video_id || payload.video_id;
      const targetIndex = targetVideo ? queue.findIndex(item => item.video_id === targetVideo) : -1;
      index = Math.max(0, Math.min(queue.length - 1, targetIndex >= 0 ? targetIndex :
        Number(payload.queue_index ?? payload.start_index ?? payload.index ?? 0)));
      if (!Number.isInteger(index)) throw new Error('Invalid queue position.');
      await publish(true);
      await start(index);
      if (owns() && queue === resolved && queue.length === 1 && !payload.playlist_id && !payload.collection && payload.radio !== false) seedRadio(queue[index].video_id);
    } else if (action === 'play') {
      if (!queue[index]) return;
      if (!audio.src || audio.error || audio.ended) await start(index);
      else { resume(); await publish(); }
    } else if (action === 'pause') { audio.pause(); await publish(); }
    else if (action === 'next' || action === 'previous') {
      if (action === 'previous' && audio.currentTime > 3) { seek(0); await publish(); }
      else if (queue[index + (action === 'next' ? 1 : -1)]) await start(index + (action === 'next' ? 1 : -1));
    } else if (action === 'seek') {
      const value = Number(payload.position_ms !== undefined ? payload.position_ms : Number(payload.position_seconds || 0) * 1000);
      if (!Number.isFinite(value) || value < 0) throw new Error('Invalid seek position.');
      if (!queue[index]) throw new Error('Choose a track before seeking.');
      seek(value);
      await publish();
    } else if (action === 'volume') {
      const value = Number(payload.value);
      if (!Number.isFinite(value)) throw new Error('Invalid volume.');
      audio.volume = Math.max(0, Math.min(1, value / 100));
    } else if (action === 'append' || action === 'next_items') {
      const editGeneration = generation, editToken = accepted;
      const resolved = await items(payload);
      if (!owns() || generation !== editGeneration || accepted !== editToken) return;
      queue.splice(action === 'next_items' ? index + 1 : queue.length, 0, ...resolved);
      await publish(true);
    } else if (action === 'remove') {
      const at = Number(payload.index);
      if (!Number.isInteger(at) || at < 0 || at >= queue.length) throw new Error('Queue changed. Retry removal.');
      const removedCurrent = at === index;
      if (removedCurrent) { generation++; changingSource = true; audio.pause(); changingSource = false; pendingSeek = null; }
      queue.splice(at, 1);
      if (at < index) index--;
      if (!queue.length) {
        audio.pause(); audio.removeAttribute('src'); index = -1;
        await api('/api/app/queue/', { action: 'clear', output_owner: id, output_token: accepted });
        return;
      }
      if (index >= queue.length) index = queue.length - 1;
      await publish(true);
      if (removedCurrent) await start(index);
    } else if (action === 'reorder') {
      const from = Number(payload.from_index ?? payload.from), to = Number(payload.to_index ?? payload.to);
      if (![from, to].every(n => Number.isInteger(n) && n >= 0 && n < queue.length)) throw new Error('Queue changed. Retry reordering.');
      const current = queue[index];
      queue.splice(to, 0, queue.splice(from, 1)[0]); index = queue.indexOf(current);
      await publish(true);
    } else if (action === 'shuffle') { shuffleTail(index + 1); await publish(true); }
    else if (action === 'repeat') { repeat = !repeat; }
    else throw new Error('This control is not supported by browser playback yet.');
  }
  function shuffleTail(begin) {
    for (let at = queue.length - 1; at > begin; at--) {
      const next = begin + Math.floor(Math.random() * (at - begin + 1));
      [queue[at], queue[next]] = [queue[next], queue[at]];
    }
  }
  function enqueue(action, payload, token = accepted) {
    const run = async () => {
      if (token === accepted && owns()) await execute(action, payload);
    };
    // A new selection cancels a pending play instead of waiting behind a cold
    // audio request; metadata responses are fenced by the selection generation.
    const operation = action === 'song' || action === 'queue' ? run() : commandChain.then(run);
    commandChain = operation.catch(() => {});
    return operation;
  }
  async function poll() {
    if (polling) return polling;
    polling = (async () => {
      const sentAcks = ack.splice(0, 32);
      let response;
      try { response = await api('/api/app/devices/', {
        action: 'online', device_id: id, session_id: session, name: 'Browser player',
        ack: sentAcks, volume: Math.round(audio.volume * 100), include_handoff_state: true, accepted_token: accepted
      }); } catch (error) { ack.unshift(...sentAcks); throw error; }
      const previous = output;
      output = response;
      if (response.output_owner !== id || response.handoff_pending || response.playback_output !== 'phone') {
        generation++; audio.pause(); leaseUntil = 0;
        if (previous && previous.output_owner === id && response.handoff_pending) {
          await api('/api/app/output/', { action: 'ack', output_owner: id, output_token: response.output_token,
            position_ms: Math.round((audio.currentTime || 0) * 1000), playing: false });
        }
        accepted = '';
      } else {
        const heartbeat = await api('/api/app/output/', { action: 'heartbeat', output_owner: id, output_token: response.output_token });
        output = heartbeat;
        leaseUntil = Date.now() + Math.max(0, heartbeat.phone_lease_ms - 4000);
        const changed = accepted !== response.output_token;
        accepted = response.output_token;
        if (changed && response.now_playing) {
          queue = (response.now_playing.queue || []).map(track);
          index = response.now_playing.queue_index ?? 0;
          if (queue[index]) {
            const snapshot = response.now_playing;
            const handoffToken = accepted, handoffIndex = index, handoffGeneration = generation;
            commandChain = commandChain.then(() => {
              if (owns() && accepted === handoffToken && generation === handoffGeneration) return start(handoffIndex, !!snapshot.playing, snapshot.position_ms || 0);
            })
              .catch(error => notify(error.message));
          }
        }
        for (const command of response.commands || []) {
          if (command.output_token !== accepted && command.token !== accepted) { ack.push(command.id); continue; }
          if (completed.has(command.id)) { ack.push(command.id); continue; }
          if (processing.has(command.id)) continue;
          processing.add(command.id);
          const commandToken = accepted;
          enqueue(command.action, command.payload || {}, commandToken).catch(error => notify(error.message)).finally(() => {
            ack.push(command.id); processing.delete(command.id); completed.add(command.id);
            if (completed.size > 128) completed.delete(completed.values().next().value);
          });
        }
        if (Date.now() - lastReport > 4000) { lastReport = Date.now(); await publish(); }
      }
    })().finally(() => { polling = null; });
    return polling;
  }
  const controls = { '/alexa/play/': 'song', '/alexa/play_queue/': 'queue', '/alexa/seek/': 'seek',
    '/alexa/queue_add/': 'append', '/alexa/queue_remove/': 'remove', '/alexa/queue_reorder/': 'reorder', '/alexa/shuffle_queue/': 'shuffle', '/alexa/clear/': 'clear' };
  window.api = async function (path, body) {
    const select = document.getElementById('device');
    const local = select && select.value === ownSerial;
    if (local && !body && path.startsWith('/alexa/volume/')) {
      return { available: true, volume: Math.round(audio.volume * 100) };
    }
    if (local && body && path === '/alexa/command/' && body.action === 'volume') {
      const value = Number(body.value);
      if (!Number.isFinite(value)) throw new Error('Invalid volume.');
      audio.volume = Math.max(0, Math.min(1, value / 100));
      return { ok: true, volume: Math.round(audio.volume * 100) };
    }
    if (local && body && (controls[path] || path === '/alexa/command/')) {
      await poll();
      const action = path === '/alexa/command/' ? body.action : path === '/alexa/queue_add/' && body.position === 'next' ? 'next_items' : controls[path];
      if (!owns() && ['song', 'queue', 'play'].includes(action) && output && !output.handoff_pending) {
        await api('/api/app/devices/', { action: 'transfer', device_id: id, target_id: id,
          output_token: output.output_token, include_state: true });
        await poll();
      }
      if (!owns()) throw new Error('Device switch is still in progress. Choose this browser and retry.');
      const operation = enqueue(action, body);
      await operation;
      if (window.schedulePollNowPlaying) window.schedulePollNowPlaying(0);
      return { ok: true, paused: audio.paused, now_playing: queue[index] || {},
        video_id: queue[index] && queue[index].video_id };
    }
    return api(path, body);
  };
  audio.addEventListener('ended', () => {
    if (!owns()) return;
    const next = queue[index + 1] ? index + 1 : repeat ? 0 : -1;
    if (next >= 0) start(next).catch(error => notify(error.message));
    else publish().catch(() => {});
  });
  for (const event of ['loadedmetadata', 'durationchange']) audio.addEventListener(event, () => {
    if (!owns()) return;
    applySeek();
    if (queue[index] && Number.isFinite(audio.duration) && audio.duration > 0 &&
        !queue[index].duration_ms) {
      queue[index].duration_ms = Math.round(audio.duration * 1000);
      publish(true).catch(() => {});
    } else publish().catch(() => {});
  });
  for (const event of ['playing', 'pause', 'waiting', 'seeked']) audio.addEventListener(event, () => publish().catch(() => {}));
  audio.addEventListener('error', () => { audio.pause(); publish().catch(() => {}); notify('Audio could not load. Tap Play to retry.'); });
  // Independent safety clock: hung HTTP requests cannot extend audible ownership.
  const watchdog = setInterval(() => { if (!owns()) audio.pause(); }, 500);
  async function tick() {
    if (stopped) return;
    try { await poll(); } catch (_) { if (!owns()) audio.pause(); }
    if (!stopped) setTimeout(tick, 2000);
  }
  window.addEventListener('pagehide', () => {
    stopped = true; generation++; audio.pause(); clearInterval(watchdog);
    api('/api/app/devices/', { action: 'offline', device_id: id, session_id: session }).catch(() => {});
  });
  window.addEventListener('pageshow', event => { if (event.persisted) window.location.reload(); });
  window.browserPlayback = { id, audio, owns, execute, poll };
  tick();
})();
