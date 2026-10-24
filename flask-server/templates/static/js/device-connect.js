/* Account device control for acknowledged browser, phone and Alexa outputs. */
(function () {
  'use strict';
  if (window.JAM_GUEST) return;
  const select = document.getElementById('device');
  if (!select) return;
  const rawApi = window.api;
  let output = null, phones = [], switching = false, requested = null, stopped = false;
  let echoDevices = [], presentation = null, presentationTimer = null;
  window.clearHandoffFeedback = function () {
    presentation = null; clearTimeout(presentationTimer);
  };
  function beginHandoffFeedback(target) {
    window.clearHandoffFeedback();
    if (!window.__appState || !window.__appState.isPlaying) return;
    presentation = { target, until: Date.now() + 8000 };
    presentationTimer = setTimeout(() => {
      window.clearHandoffFeedback();
      if (window.schedulePollNowPlaying) window.schedulePollNowPlaying(0);
    }, 8000);
  }
  window.preserveHandoffPlayback = function (np) {
    if (!presentation) return false;
    const target = np.playback_output === 'phone' ? 'mobile:' + np.output_owner : np.output_serial;
    const failure = np.playback_error && (typeof np.playback_error !== 'object' || np.playback_error.terminal !== false);
    if (Date.now() >= presentation.until || failure ||
        (!switching && target !== presentation.target) ||
        (target === presentation.target && !np.handoff_pending && np.playing === true && np.playback_confirmed === true)) {
      window.clearHandoffFeedback(); return false;
    }
    return true;
  };
  const originalApply = window._applyDevices;
  window._applyDevices = function (devices, preferred) {
    echoDevices = devices.filter(d => !String(d.serial).startsWith('mobile:'));
    const browser = window.browserPlayback;
    const local = browser ? 'mobile:' + browser.id : '';
    const devicesWithPhones = echoDevices.filter(d => d.online).concat(phones.map(d => ({ serial: 'mobile:' + d.id,
      name: browser && d.id === browser.id ? 'This browser' : d.name, online: true })));
    if (local && !devicesWithPhones.some(d => d.serial === local)) {
      devicesWithPhones.push({ serial: local, name: 'This browser', online: true });
    }
    const active = output && (output.playback_output === 'phone' && output.output_owner
      ? 'mobile:' + output.output_owner : output.output_serial);
    const available = serial => devicesWithPhones.some(d => d.serial === serial && d.online);
    const orphan = output && output.playback_output === 'phone' &&
      (!output.output_owner || !phones.some(d => d.id === output.output_owner));
    // An abandoned browser session stays idle. Rendering a picker never grants
    // another online device permission to take over its saved queue.
    const value = switching ? select.value : orphan && local ? local : available(active) ? active :
      available(preferred) ? preferred : local || '';
    return originalApply(devicesWithPhones, value);
  };
  function render() {
    const previous = select.value;
    window._applyDevices(window._connectEchoDevices || echoDevices, previous);
    if (select.value !== previous) {
      if (window.connectSSE) window.connectSSE();
      if (window.refreshVolume) window.refreshVolume(true);
    }
  }
  window.activeAlexaSerial = () => output && output.output_serial || echoDevices.find(d => d.online)?.serial || "";
  window.mobileOutputSelected = function (np) {
    return np.playback_output === 'phone' && select.value === 'mobile:' + np.output_owner;
  };
  window.observeSharedOutput = function (np) {
    if (!np || !np.output_token) return;
    if (output && np.output_epoch && np.output_epoch === output.output_epoch && np.output_revision < output.output_revision) return false;
    if (np.queue_version !== undefined) window._mobileQueueVersion = np.queue_version;
    const changed = !output || output.output_token !== np.output_token;
    if (changed && !presentation && (np.handoff_pending || np.playback_processing)) {
      beginHandoffFeedback(np.playback_output === 'phone' ? 'mobile:' + np.output_owner : np.output_serial);
    }
    output = np;
    if (changed && !switching) render();
    return true;
  };
  const controls = new Set(['/alexa/command/', '/alexa/seek/', '/alexa/play/', '/alexa/play_queue/', '/alexa/queue_add/', '/alexa/queue_remove/', '/alexa/queue_reorder/', '/alexa/shuffle_queue/']);
  window.api = async function (path, body, requestOptions) {
    if (body && controls.has(path) && !(path === '/alexa/command/' && body.action === 'volume')) window.clearHandoffFeedback();
    if (body && controls.has(path) && select.value.startsWith('mobile:')) {
      if (switching || !output || output.handoff_pending) throw new Error('Device switch is still in progress.');
      body = Object.assign({}, body, { serial: select.value, output_token: output.output_token });
      if (['/alexa/queue_remove/', '/alexa/queue_reorder/'].includes(path) && window._mobileQueueVersion !== undefined) body.expected_queue_version = window._mobileQueueVersion;
    }
    return rawApi(path, body, requestOptions);
  };
  async function switchDevice(value) {
    requested = value;
    beginHandoffFeedback(value);
    if (switching) return;
    switching = true;
    const trigger = document.getElementById('device-trigger');
    if (trigger) trigger.setAttribute('aria-busy', 'true');
    try {
      while (requested !== null) {
        const target = requested; requested = null;
        const latest = await rawApi('/api/app/devices/');
        output = latest; phones = latest.devices || [];
        const same = target.startsWith('mobile:') ? latest.playback_output === 'phone' && latest.output_owner === target.slice(7)
          : latest.playback_output === 'alexa' && latest.output_serial === target;
        if (!same) output = await rawApi('/api/app/devices/', { action: target.startsWith('mobile:') ? 'transfer' : 'alexa',
          device_id: 'web', target_id: target.startsWith('mobile:') ? target.slice(7) : '', serial: target.startsWith('mobile:') ? latest.output_serial : target,
          output_token: latest.output_token });
      }
    } catch (error) { window.clearHandoffFeedback(); if (window.toast) window.toast(error.message, 'error'); }
    finally {
      switching = false;
      if (window.browserPlayback && select.value === 'mobile:' + window.browserPlayback.id) {
        await window.browserPlayback.poll().catch(() => {});
      }
      if (trigger) trigger.removeAttribute('aria-busy');
      render();
      if (window.connectSSE) window.connectSSE();
      if (window.refreshVolume) window.refreshVolume(true);
    }
  }
  select.addEventListener('change', () => { if (select.value) switchDevice(select.value); });
  async function refresh() {
    if (stopped) return;
    try {
      const latest = await rawApi('/api/app/devices/');
      phones = latest.devices || [];
      if (!switching) {
        if (output && latest.output_token !== output.output_token && latest.handoff_pending) {
          beginHandoffFeedback(latest.playback_output === 'phone' ? 'mobile:' + latest.output_owner : latest.output_serial);
        }
        output = latest;
      }
      render();
    } catch (_) { /* Playback polling provides connection/session feedback. */ }
    if (!stopped) setTimeout(refresh, document.hidden ? 8000 : 2000);
  }
  window.addEventListener('pagehide', () => { stopped = true; });
  window.addEventListener('pageshow', () => { if (stopped) { stopped = false; refresh(); } });
  refresh();
})();
