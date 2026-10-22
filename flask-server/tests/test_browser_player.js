const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const path = require('path');

async function main() {
  let reportGate = null;
  let now = 1000, owner = 'browser-id1', token = 'lease1', sequence = 0, commands = [];
  const calls = [], timers = [], events = {};
  const audio = {
    paused: true, ended: false, currentTime: 0, volume: 1, readyState: 4, src: '',
    pause() { this.paused = true; },
    async play() { this.paused = false; },
    addEventListener(name, handler) { (events[name] ||= []).push(handler); }, removeAttribute(name) { this[name] = ''; }
  };
  const state = () => ({ playback_output: 'phone', output_owner: owner,
    output_token: token, handoff_pending: false, phone_lease_ms: 20000 });
  const api = async (url, body) => {
    calls.push({url, body});
    if (url === '/api/app/devices/') return { ...state(), commands, now_playing: {queue: []} };
    if (url === '/api/app/output/') return state();
    if (url === '/api/app/queue/' && reportGate) await reportGate;
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
  await window.api('/alexa/command/', {action: 'volume', value: 35});
  assert.equal(audio.volume, 0.35);
  assert.equal((await window.api('/alexa/volume/?serial=mobile:browser-id1')).volume, 35);
  await player.execute('queue', {queue_items: [{video_id: 'AAAAAAAAAAA'}, {video_id: 'BBBBBBBBBBB'}]});
  assert.equal(audio.src, '/audio/?video_id=AAAAAAAAAAA&wait=1');
  assert.equal(audio.paused, false);
  await player.execute('seek', {position_ms: 4000});
  assert.equal(audio.currentTime, 4);
  // Metadata can be unavailable during a cold audio download. Retain the
  // requested seek until the media becomes seekable, including paused seeks.
  audio.readyState = 0;
  await player.execute('seek', {position_ms: 9000});
  assert.equal(audio.currentTime, 4);
  assert.equal(calls.filter(call => call.url === '/api/app/queue/').at(-1).body.position_ms, 9000);
  audio.readyState = 4; audio.duration = 20;
  for (const listener of events.loadedmetadata) listener();
  await Promise.resolve();
  assert.equal(audio.currentTime, 9);
  await player.execute('pause', {});
  await player.execute('seek', {position_ms: 6000});
  assert.equal(audio.currentTime, 6);
  assert(audio.paused, 'Seeking while paused must not resume playback');
  await player.execute('seek', {position_ms: 999999});
  assert.equal(audio.currentTime, 20, 'Seeks must clamp to the media duration');
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
  await player.execute('pause', {});
  assert(audio.paused, 'Pause must finish even when play() is still waiting for audio');
  const newPlay = player.execute('queue', {queue_items: [{video_id: 'BBBBBBBBBBB'}]});
  await Promise.all([oldPlay, newPlay]);
  assert.equal(audio.src, '/audio/?video_id=BBBBBBBBBBB&wait=1');
  assert.equal(audio.paused, false, 'Superseded playback failure must not pause the latest selection');
  audio.play = originalPlay; audio.pause = originalPause;
  await player.execute('queue', {queue_items: [{video_id: 'AAAAAAAAAAA'}]});
  // A slow old report must complete before a newer seek reaches the server.
  let releaseReport;
  reportGate = new Promise(resolve => { releaseReport = resolve; });
  const beforeReports = calls.filter(call => call.url === '/api/app/queue/').length;
  const seekOne = player.execute('seek', {position_ms: 2000});
  for (let n = 0; n < 10; n++) await Promise.resolve();
  const seekTwo = player.execute('seek', {position_ms: 8000});
  for (let n = 0; n < 10; n++) await Promise.resolve();
  assert.equal(calls.filter(call => call.url === '/api/app/queue/').length, beforeReports + 1);
  reportGate = null; releaseReport();
  await Promise.all([seekOne, seekTwo]);
  assert.deepEqual(calls.filter(call => call.url === '/api/app/queue/').slice(-2).map(call => call.body.position_ms), [2000, 8000]);
  await player.execute('queue', {queue_items: [{video_id: 'AAAAAAAAAAA'}, {video_id: 'BBBBBBBBBBB'}, {video_id: 'CCCCCCCCCCC'}]});
  commands = [{id:'remote-next', token:'lease1', action:'next', payload:{}}];
  await player.poll();
  for (let n = 0; n < 20; n++) await Promise.resolve();
  assert.equal(audio.src, '/audio/?video_id=BBBBBBBBBBB&wait=1');
  await player.poll();
  for (let n = 0; n < 20; n++) await Promise.resolve();
  assert.equal(audio.src, '/audio/?video_id=BBBBBBBBBBB&wait=1', 'Repeated delivery must not execute next twice');
  commands = [];
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
