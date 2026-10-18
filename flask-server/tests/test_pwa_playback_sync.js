const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

let channel;
const sent = [], received = [], polls = [];
const sandbox = {
  console, navigator: {},
  document: { addEventListener() {} },
  BroadcastChannel: class {
    constructor() { channel = this; }
    postMessage(value) { sent.push(value); }
  },
  addEventListener() {}, matchMedia: () => ({ matches: false }),
  handleNpUpdate(value) {
    received.push(value);
    sandbox.broadcastNpUpdate(value); // receiving a message must not echo it
  },
  schedulePollNowPlaying: delay => polls.push(delay),
  __appState: { _lastQueueIndex: 2 }, _lastQueueIndex: 2,
  showQueue() { throw new Error('Unversioned queue message must not render'); }
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../templates/static/js/pwa.js'), 'utf8'), sandbox);
sandbox._initPwa();

const snapshot = {
  video_id: 'song', title: 'Song', playing: true, playback_confirmed: true,
  playback_processing: false, playback_revision: 8, queue_version: 3,
  output_epoch: 'server', output_revision: 2, playback_output: 'alexa',
  output_serial: 'echo', queue: [{video_id: 'song', entry_id: 'occurrence'}]
};
sandbox.broadcastNpUpdate(snapshot);
assert.equal(sent.length, 1);
assert.deepEqual(JSON.parse(JSON.stringify(sent[0].np)), snapshot,
  'cross-tab messages must preserve confirmation, revisions and queue occurrence identity');
channel.onmessage({ data: sent[0] });
assert.equal(received.length, 1);
assert.equal(sent.length, 1, 'received snapshots must not ping-pong between tabs');

channel.onmessage({data: {type: 'queue-update', queue: [{video_id: 'old'}], queueIndex: 0}});
assert.deepEqual(polls, [200]);
assert.equal(sandbox._lastQueueIndex, 2);
assert.equal(sandbox.__appState._lastQueueIndex, 2);
console.log('PASS PWA snapshot fidelity, queue invalidation and broadcast loop prevention');
