import test from 'node:test';
import assert from 'node:assert/strict';
import { createResolver, cookiesFromNetscape } from './resolver.mjs';

const video = 'dQw4w9WgXcQ';
function fixture(options = {}) {
  const state = { calls:0, sessions:0, mtime:1, now:1000000, failure:false };
  const format = { itag:140, has_audio:true, has_video:false, content_length:4000000,
    last_modified_ms:'12345', decipher:async()=> 'https://rr1.googlevideo.com/videoplayback?expire=9000' };
  const client = { session:{player:{}}, getBasicInfo:async(id, profile)=>{
    state.calls++; assert.equal(profile.client,'YTMUSIC');
    if(state.failure)throw new Error('secret signed URL must never be logged');
    return {basic_info:{title:'Resolved title',author:'Artist',duration:123,thumbnail:[{url:'https://images.example/cover.jpg'}]},chooseFormat:opts=>{assert.equal(opts.itag,140);return format;}};
  }};
  const resolve = createResolver({createClient:async()=>{state.sessions++;return client;},
    readFile:()=>'.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tprivate',
    stat:()=>({ino:1,mtimeMs:state.mtime,size:100}), now:()=>state.now,...options});
  return {state, format, resolve, request:{video_id:video,cookie_file:'/cookies'}};
}
test('cookie parser ignores unrelated domains, comments and expired cookies',()=>{
  assert.equal(cookiesFromNetscape('#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tgood\n'+
   '.evil-youtube.com\tTRUE\t/\tTRUE\t0\tX\tbad\n'+
   '.youtube.com\tTRUE\t/\tTRUE\t1\tOLD\tbad\n',100),'SID=good');
});
test('one persistent session, coalesced duplicate requests, bounded URL TTL',async()=>{
  const {state,resolve,request}=fixture();
  const [a,b]=await Promise.all([resolve(request),resolve(request)]);
  assert.equal(a,b);assert.equal(state.calls,1);assert.equal(state.sessions,1);
  await resolve(request);assert.equal(state.calls,1);
  state.now+=300001;await resolve(request);assert.equal(state.calls,2);
});
test('403 refresh bypasses cached URL',async()=>{
 const {state,resolve,request}=fixture();await resolve(request);
 await resolve({...request,refresh:true});assert.equal(state.calls,2);
});
test('credential replacement resets the player/session and URLs',async()=>{
 const {state,resolve,request}=fixture();await resolve(request);
 state.mtime++;await resolve(request);assert.equal(state.sessions,2);assert.equal(state.calls,2);
});
test('cookie replacement during initialization cannot contaminate the new session',async()=>{
 let finishOld, calls=0, mtime=1, oldCalls=0, newCalls=0;
 const makeClient=old=>({session:{},getBasicInfo:async()=>{
  old ? oldCalls++ : newCalls++;
  return {chooseFormat:()=>({itag:140,has_audio:true,content_length:12,
   decipher:async()=> 'https://rr1.googlevideo.com/a'})};
 }});
 const resolve=createResolver({stat:()=>({ino:1,mtimeMs:mtime,size:1}),readFile:()=>'',
  createClient:()=>++calls===1?new Promise(r=>finishOld=r):Promise.resolve(makeClient(false))});
 const request={cookie_file:'/cookies',video_id:video};
 const first=resolve(request);mtime++;
 await resolve(request);finishOld(makeClient(true));await first;
 assert.equal(oldCalls,0);assert.equal(newCalls,1);
});
test('failed session initialization can recover',async()=>{
 let count=0;const f=fixture({createClient:async()=>{if(++count===1)throw new Error('failed');return {session:{}};}});
 await assert.rejects(f.resolve({action:'init'}));
 assert.deepEqual(await f.resolve({action:'init'}),{ready:true});assert.equal(count,2);
});
test('failures are not cached as dead songs',async()=>{
 const {state,resolve,request}=fixture();state.failure=true;
 await assert.rejects(resolve(request));state.failure=false;await resolve(request);assert.equal(state.calls,2);
});
test('a failed player decipher triggers a bounded session refresh',async()=>{
 const {state,format,resolve,request}=fixture();
 const decipher=format.decipher;format.decipher=async()=>{throw new Error('Failed to decipher nsig');};
 await assert.rejects(resolve(request));format.decipher=decipher;
 await resolve(request);assert.equal(state.sessions,2);
});
test('provider-thrown unavailable status remains a permanent track error',async()=>{
 const {resolve,request}=fixture({createClient:async()=>({session:{},getBasicInfo:async()=>{
  const error=new Error('provider failure');error.info={status:'ERROR'};throw error;
 }})});
 await assert.rejects(resolve(request),/track_unavailable/);
});
test('rejects malicious IDs before creating a session',async()=>{
 const {state,resolve}=fixture();await assert.rejects(resolve({video_id:'../../data'}));assert.equal(state.sessions,0);
});
test('rejects changing encodings, video formats and unsafe upstream hosts',async()=>{
 for (const mutation of [{itag:141},{has_video:true},{drm_families:['widevine']},
   {decipher:async()=> 'https://127.0.0.1/admin'},
   {decipher:async()=> 'https://googlevideo.com.evil.test/a'}]) {
  const {format,resolve,request}=fixture();Object.assign(format,mutation);await assert.rejects(resolve(request));
 }
});
test('URL cache evicts oldest entries at capacity',async()=>{
 const {state,resolve,request}=fixture({capacity:1});await resolve(request);
 await resolve({...request,video_id:'kJQP7kiw5Fk'});await resolve(request);assert.equal(state.calls,3);
});
test('bounds simultaneous distinct resolutions instead of unbounded queueing',async()=>{
 let release;const gate=new Promise(r=>release=r);
 const {resolve,request}=fixture({createClient:async()=>({session:{},getBasicInfo:async()=>{await gate;throw new Error('done');}})});
 const tasks=['aaaaaaaaaaa','bbbbbbbbbbb','ccccccccccc','ddddddddddd'].map(video_id=>resolve({...request,video_id}).catch(()=>{}));
 await new Promise(r=>setImmediate(r));
 await assert.rejects(resolve(request),/busy/);release();await Promise.all(tasks);
});

test('resolver returns public title/duration alongside an already-resolved stream',async()=>{
  const {resolve,request}=fixture();
  const result=await resolve(request);
  assert.equal(result.metadata.title,'Resolved title');
  assert.equal(result.metadata.duration_ms,123000);
  assert(!Object.hasOwn(result.metadata,'url'));
});
