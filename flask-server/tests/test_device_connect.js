const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const path = require('path');

(async () => {
  const handlers = {}, calls = [];
  const select = {value: 'mobile:phone', addEventListener: (name, handler) => handlers[name] = handler};
  let registered = false, activated = false;
  let state = {playback_output: 'phone', output_owner: 'phone', output_token: 'source',
    output_serial: 'echo', devices: [{id: 'phone', name: 'Phone'}]};
  const browser = {id: 'browser-local', activate() { activated = true; }, async poll() {
    registered = true;
    calls.push('presence');
  }};
  const window = {
    browserPlayback: browser,
    addEventListener() {},
    _applyDevices(devices, preferred) { select.value = preferred; },
    async api(url, body) {
      if (!body) return state;
      assert(activated, 'The browser must be activated while handling the user gesture');
      assert(registered, 'Presence must register this browser before requesting a handoff');
      assert.equal(body.device_id, 'browser-local', 'Handoff controller must be the actual browser');
      assert.equal(body.target_id, 'browser-local');
      assert.equal(body.output_token, 'source');
      calls.push('transfer');
      state = {...state, output_owner: 'browser-local', output_token: 'target'};
      return state;
    }
  };
  const context = {window, document: {body: {dataset: {}}, getElementById: id => id === 'device' ? select : null},
    setTimeout() {}, clearTimeout() {}, Date};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../templates/static/js/device-connect.js'), 'utf8'), context);
  await new Promise(setImmediate);
  select.value = 'mobile:browser-local';
  handlers.change();
  await new Promise(setImmediate);
  assert.deepEqual(calls.slice(0, 2), ['presence', 'transfer']);
  assert.equal(select.value, 'mobile:browser-local');
  console.log('PASS browser handoff: user activation, registration, controller identity and selected output');
})().catch(error => { console.error(error); process.exitCode = 1; });
