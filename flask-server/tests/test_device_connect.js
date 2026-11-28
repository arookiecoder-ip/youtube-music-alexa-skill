const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const path = require('path');

(async () => {
  const handlers = {}, calls = [], timers = [];
  let available = true;
  const select = {value: 'mobile:phone', addEventListener: (name, handler) => handlers[name] = handler};
  let registered = false, activated = false;
  let state = {playback_output: 'phone', output_owner: 'phone', output_token: 'source',
    output_serial: 'echo', output_epoch:'epoch', output_revision:1, devices: [{id: 'phone', name: 'Phone'}]};
  const browser = {id: 'browser-local', available:()=>available, activate() { activated = true; }, async poll() {
    registered = true;
    calls.push('presence');
  }};
  const window = {
    browserPlayback: browser,
    addEventListener() {},
    _applyDevices(devices, preferred) { select.value = devices.some(d=>d.serial===preferred) ? preferred : devices[0]?.serial || ''; },
    async api(url, body) {
      if (!body) return state;
      assert(activated, 'The browser must be activated while handling the user gesture');
      assert(registered, 'Presence must register this browser before requesting a handoff');
      assert.equal(body.device_id, 'browser-local', 'Handoff controller must be the actual browser');
      assert.equal(body.target_id, 'browser-local');
      assert.equal(body.output_token, 'source');
      calls.push('transfer');
      state = {...state, output_owner: 'browser-local', output_token: 'target', output_revision:2};
      return state;
    }
  };
  const context = {window, document: {body: {dataset: {}}, getElementById: id => id === 'device' ? select : null},
    setTimeout(fn) { timers.push(fn); }, clearTimeout() {}, Date};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../templates/static/js/device-connect.js'), 'utf8'), context);
  await new Promise(setImmediate);
  select.value = 'mobile:browser-local';
  handlers.change();
  await new Promise(setImmediate);
  assert.deepEqual(calls.slice(0, 2), ['presence', 'transfer']);
  assert.equal(select.value, 'mobile:browser-local');
  available = false;
  window._applyDevices([{serial:'echo',name:'Echo',online:true}], 'mobile:browser-local');
  assert.equal(select.value,'mobile:browser-local','Minimizing must preserve the active browser even when presence hides it');
  state = {...state,playback_output:'alexa',output_owner:'',output_token:'old-echo',output_revision:1,devices:[]};
  await timers.shift()();
  assert.equal(select.value,'mobile:browser-local','A delayed old Echo poll must not replace the newer browser output');
  available = true;
  await timers.shift()();
  assert.equal(select.value,'mobile:browser-local','Returning to the tab must not flash Echo');
  assert.equal(calls.filter(call=>call==='transfer').length,1,'Visibility changes must not submit new transfers');
  console.log('PASS browser handoff: user activation, registration, controller identity and selected output');
})().catch(error => { console.error(error); process.exitCode = 1; });
