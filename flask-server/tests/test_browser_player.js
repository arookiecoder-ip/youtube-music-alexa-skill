const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const path = require('path');

async function main() {
  let now = 1000, owner = 'browser-id1', token = 'lease1', sequence = 0, commands = [];
  const calls = [], timers = [];
  const audio = {
    paused: true, ended: false, currentTime: 0, volume: 1, readyState: 4, src: '',
    pause() { this.paused = true; },
    async play() { this.paused = false; },
    addEventListener() {}, removeAttribute(name) { this[name] = ''; }
  };
  const state = () => ({ playback_output: 'phone', output_owner: owner,
    output_token: token, handoff_pending: false, phone_lease_ms: 20000 });
  const api = async (url, body) => {
    calls.push({url, body});
    if (url === '/api/app/devices/') return { ...state(), commands, now_playing: {queue: []} };
    if (url === '/api/app/output/') return state();
    return {ok: true};
  };
  const window = { api, crypto: {}, Audio: function () { return audio; }, addEventListener() {},
    browserAudioUrl: id => '/audio/?video_id=' + id + '&wait=1' };
  const context = { window, Audio: window.Audio, crypto: { randomUUID: () => 'id' + (++sequence) },
    Date: { now: () => now }, setInterval: fn => { timers.push(fn); return 1; },
    clearInterval() {}, setTimeout() {}, document: {getElementById: () => ({value: 'mobile:browser-id1'})} };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../templates/static/js/browser-player.js'), 'utf8'), context);
  const player = window.browserPlayback;
  await player.poll();
  assert(player.owns());
  await player.execute('queue', {queue_items: [{video_id: 'AAAAAAAAAAA'}, {video_id: 'BBBBBBBBBBB'}]});
  assert.equal(audio.src, '/audio/?video_id=AAAAAAAAAAA&wait=1');
  assert.equal(audio.paused, false);
  await player.execute('seek', {position_ms: 4000});
  assert.equal(audio.currentTime, 4);
  await player.execute('next', {});
  assert.equal(audio.src, '/audio/?video_id=BBBBBBBBBBB&wait=1');
  await player.execute('queue', {queue_index: 0});
  assert.equal(audio.src, '/audio/?video_id=AAAAAAAAAAA&wait=1');
  await player.execute('next', {});
  await player.execute('remove', {index: 0});
  assert.equal(audio.src, '/audio/?video_id=BBBBBBBBBBB&wait=1');
  await player.execute('remove', {index: 0});
  assert.equal(audio.paused, true);
  assert(calls.some(call => call.body && call.body.action === 'clear' && call.body.output_token === 'lease1'));
  const originalPlay = audio.play, originalPause = audio.pause;
  let rejectPending = null, playCalls = 0;
  audio.play = function () {
    this.paused = false;
    if (++playCalls === 1) return new Promise((resolve, reject) => { rejectPending = reject; });
    return originalPlay.call(this);
  };
  audio.pause = function () {
    this.paused = true;
    if (rejectPending) { const reject = rejectPending; rejectPending = null; reject(new Error('superseded')); }
  };
  const oldPlay = player.execute('queue', {queue_items: [{video_id: 'AAAAAAAAAAA'}]});
  for (let attempt = 0; !rejectPending && attempt < 20; attempt++) await Promise.resolve();
  assert(rejectPending, 'First selection must reach the pending media play');
  const newPlay = player.execute('queue', {queue_items: [{video_id: 'BBBBBBBBBBB'}]});
  await Promise.all([oldPlay, newPlay]);
  assert.equal(audio.src, '/audio/?video_id=BBBBBBBBBBB&wait=1');
  assert.equal(audio.paused, false, 'Superseded playback failure must not pause the latest selection');
  audio.play = originalPlay; audio.pause = originalPause;
  await player.execute('queue', {queue_items: [{video_id: 'AAAAAAAAAAA'}]});
  now += 17000; timers[0]();
  assert.equal(audio.paused, true, 'Expired lease must stop sound without an HTTP response');
  await assert.rejects(player.execute('play', {}), /no longer owns/);
  await player.poll();
  assert(player.owns());
  owner = 'another-phone'; token = 'lease2';
  await player.poll();
  assert(!player.owns());
  assert(audio.paused);
  console.log('PASS browser playback: local queue, seek, removal, fenced clear, lease expiry and handoff');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
