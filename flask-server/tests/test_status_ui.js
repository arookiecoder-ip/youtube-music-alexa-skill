'use strict';
const assert = require('node:assert/strict');
const {bytes, duration, inventoryRows, Poller} = require('../templates/static/js/status.js');
assert.equal(bytes(0), '0 B'); assert.equal(bytes(null), '—'); assert.equal(bytes(-1), '—'); assert.equal(bytes(1024), '1 KiB');
assert.equal(duration(3600), '1 hours'); assert.equal(duration(undefined), '—');
const inventory = [{video_id:'AAAAAAAAAAA',format:'m4a',bytes:10,state:'ready',pool:'warmup',pinned:true,modified_at:1,idle_seconds:10},
 {video_id:'BBBBBBBBBBB',format:'webm',bytes:20,state:'partial',pool:'requested',pinned:false,modified_at:2,idle_seconds:20}];
assert.equal(inventoryRows(inventory,'m4a','pinned').length,1);
assert.equal(inventoryRows(inventory,'','partial')[0].video_id,'BBBBBBBBBBB');
assert.equal(inventoryRows(inventory,'','all','size')[0].bytes,20);
assert.equal(inventory[0].bytes,10,'sorting must not mutate stored snapshot');

(async () => {
 let visible=true, loads=0, data=[], errors=[], tasks=new Map(), serial=0, resolve;
 const poller = new Poller({visible:()=>visible,interval:()=>15000,
   schedule:(fn,ms)=>{const id=++serial;tasks.set(id,{fn,ms});return id;},cancel:id=>tasks.delete(id),
   onData:value=>data.push(value),onError:error=>errors.push(error),load:()=>{loads++;return new Promise(done=>{resolve=done;});}});
 const first=poller.refresh(); poller.refresh(true); poller.refresh(true);
 assert.equal(loads,1,'rapid refreshes never overlap'); resolve({id:1}); await first;
 assert.equal(data.length,1);assert.equal([...tasks.values()][0].ms,0,'one coalesced refresh');
 tasks.clear(); poller.load=async()=>{loads++;return {id:2};}; await poller.refresh();
 assert.equal([...tasks.values()][0].ms,15000);
 poller.setPaused(true);assert.equal(tasks.size,0); await poller.refresh();assert.equal(loads,2);
 await poller.refresh(true);assert.equal(loads,3,'manual refresh works while paused');assert.equal(tasks.size,0);
 visible=false;await poller.refresh(true);assert.equal(loads,3);
 visible=true;poller.paused=false;poller.load=async()=>{throw new Error('offline');};await poller.refresh();
 assert.equal(data.length,3,'failed fetch keeps last successful data');assert.equal(errors.length,1);
 assert.equal([...tasks.values()][0].ms,30000,'outages back off');
 poller.load=async()=>{const error=new Error('expired');error.status=401;throw error;};await poller.refresh(true);
 assert.equal(poller.stopped,true);assert.equal(tasks.size,0,'expired auth stops polling');
 // A response started before hiding must not overwrite fresh state after returning.
 let shown=true, pending, staleData=[], staleErrors=[], signal;
 const visibilityPoller=new Poller({visible:()=>shown,interval:()=>15000,
   schedule:(fn,ms)=>{const id=++serial;tasks.set(id,{fn,ms});return id;},cancel:id=>tasks.delete(id),
   onData:value=>staleData.push(value),onError:error=>staleErrors.push(error),
   load:input=>{signal=input;return new Promise(done=>{pending=done;});}});
 tasks.clear(); const outstanding=visibilityPoller.refresh();shown=false;visibilityPoller.visibilityChanged();
 assert.equal(signal.aborted,true);shown=true;visibilityPoller.visibilityChanged();pending({stale:true});await outstanding;
 assert.equal(staleData.length,0);assert.equal(staleErrors.length,0);
 assert.equal([...tasks.values()][0].ms,0,'returning viewer gets a fresh request');
 tasks.clear();visibilityPoller.again=false;
 visibilityPoller.load=input=>new Promise((resolve,reject)=>input.addEventListener('abort',()=>{const error=new Error('timeout');error.name='AbortError';reject(error);}));
 const timed=visibilityPoller.refresh();[...tasks.values()][0].fn();await timed;
 assert.equal(staleErrors.length,1,'visible request timeout is reported');
 assert.equal([...tasks.values()][0].ms,30000,'timeout retries with backoff');
 console.log('PASS formatting, immutable filters, overlap prevention, coalescing, pause/manual refresh, visibility, stale data, backoff and auth expiry');
})().catch(error=>{console.error(error);process.exitCode=1;});
