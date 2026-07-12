(function () {
  'use strict';

  let _deviceSerial = '';

  function state() { return window.__appState; }

  function showJamEnded(title, msg) {
    if (window.stopSSE) window.stopSSE();
    const overlay = document.getElementById('jam-ended-overlay');
    if (!overlay) return;
    if (title) document.getElementById('jam-ended-title').textContent = title;
    if (msg) document.getElementById('jam-ended-msg').textContent = msg;
    overlay.style.display = 'flex';
  }

  async function leaveJam() {
    if (window.stopSSE) window.stopSSE();
    try { await window.api('/logout/', {}); } catch (_) {}
    showJamEnded('You left the jam',
      "Open the jam link again to rejoin while it's still live.");
  }

  function selectedSerial() {
    if (!_deviceSerial) return null;
    return _deviceSerial;
  }

  function selectedDeviceOnline() {
    return true;
  }

  async function initJamPage() {
    const deviceEl = document.getElementById('device');
    try {
      const data = await window.api('/alexa/init/');
      const status = data.status || {};
      if (!status.configured || !status.logged_in) {
        showJamEnded('Jam unavailable', 'The host\'s server is not fully configured.');
        return;
      }
      const devices = data.devices || [];
      if (devices.length > 0) {
        _deviceSerial = devices[0].serial;
        if (deviceEl) {
          deviceEl.innerHTML = '';
          for (const d of devices) {
            const o = document.createElement('option');
            o.value = d.serial;
            o.textContent = d.name;
            deviceEl.appendChild(o);
          }
          deviceEl.value = _deviceSerial;
        }
      }
      if (data.now_playing && window.handleNpUpdate) {
        window.handleNpUpdate(data.now_playing);
      }
      if (_deviceSerial && window.connectSSE) window.connectSSE();
      if (window.refreshVolume) window.refreshVolume(true);
      const mainEl = document.querySelector('main');
      if (mainEl) mainEl.classList.remove('idle');
      if (window.syncUiState) window.syncUiState();
    } catch (e) {
      showJamEnded('Connection failed',
        'Could not reach the host. Check that the server is running.');
    }
  }

  const leaveBtn = document.getElementById('jam-leave-btn');
  if (leaveBtn) {
    leaveBtn.addEventListener('click', () => {
      if (confirm('Leave this jam?')) leaveJam();
    });
  }

  const playBtn = document.getElementById('play-query');
  if (playBtn) {
    playBtn.onclick = () => {
      const queryEl = document.getElementById('query');
      const query = queryEl.value.trim();
      if (!query) return;
      queryEl.blur();
      if (window.runSearch) window.runSearch(query);
    };
  }

  function syncHistoryTriggerVisibility() {}
  function getRoute() { return ''; }
  function navigateTo() {}

  window.showJamEnded = showJamEnded;
  window.leaveJam = leaveJam;
  window.selectedSerial = selectedSerial;
  window.selectedDeviceOnline = selectedDeviceOnline;
  window.initJamPage = initJamPage;
  window.syncHistoryTriggerVisibility = syncHistoryTriggerVisibility;
  window.getRoute = getRoute;
  window.navigateTo = navigateTo;
})();
