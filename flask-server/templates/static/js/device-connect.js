/* Account device control. Phones keep ownership of their audio; the browser is a remote. */
(function () {
  'use strict';
  if (window.JAM_GUEST) return;
  const select = document.getElementById('device');
  if (!select) return;
  const rawApi = window.api;
  let output = null, phones = [], switching = false, requested = null, stopped = false;
  let echoDevices = [];
  const originalApply = window._applyDevices;
  window._applyDevices = function (devices, preferred) {
    echoDevices = devices.filter(d => !String(d.serial).startsWith('mobile:'));
    return originalApply(echoDevices.concat(phones.map(d => ({ serial: 'mobile:' + d.id, name: d.name, online: true }))), preferred);
  };
  function render() {
    let value = select.value;
    if (!switching && output) {
      value = output.playback_output === 'phone' && output.output_owner ? 'mobile:' + output.output_owner : output.output_serial || value;
    }
    window._applyDevices(window._connectEchoDevices || echoDevices, value);
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
    output = np;
    if (changed && !switching) render();
    return true;
  };
  const controls = new Set(['/alexa/command/', '/alexa/seek/', '/alexa/play/', '/alexa/play_queue/', '/alexa/queue_add/', '/alexa/queue_remove/', '/alexa/queue_reorder/', '/alexa/shuffle_queue/']);
  window.api = async function (path, body) {
    if (body && controls.has(path) && select.value.startsWith('mobile:')) {
      if (switching || !output || output.handoff_pending) throw new Error('Device switch is still in progress.');
      body = Object.assign({}, body, { serial: select.value, output_token: output.output_token });
      if (['/alexa/queue_remove/', '/alexa/queue_reorder/'].includes(path) && window._mobileQueueVersion !== undefined) body.expected_queue_version = window._mobileQueueVersion;
    }
    return rawApi(path, body);
  };
  async function switchDevice(value) {
    requested = value;
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
    } catch (error) { if (window.toast) window.toast(error.message, 'error'); }
    finally {
      switching = false;
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
      if (!switching) output = latest;
      render();
    } catch (_) { /* Playback polling provides connection/session feedback. */ }
    if (!stopped) setTimeout(refresh, document.hidden ? 8000 : 2000);
  }
  window.addEventListener('pagehide', () => { stopped = true; });
  window.addEventListener('pageshow', () => { if (stopped) { stopped = false; refresh(); } });
  refresh();
})();
