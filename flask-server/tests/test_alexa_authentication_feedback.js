const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const app = {isPlaying:true, lastActionIntent:true, lastActionAt:Date.now(), GRACE_MS:10000,
  _lastPlayAttemptVideoId:'AAAAAAAAAAA'};
const errors = [];
let canceled = 0;
const window = {
  toast:(message, severity)=>errors.push({message,severity}),
  progress:{cancelPending:()=>canceled++,update:()=>{}},
  syncPlayPause:()=>{},showNowPlaying:()=>{}
};
const context = {window,state:()=>app,document:{hidden:false,getElementById:()=>null},_lastProcessing:false,
  _lastHistoryVideoId:null,_seenPlaybackErrors:new Set(),setTimeout:()=>0};
vm.createContext(context);
const source=fs.readFileSync(path.join(__dirname,'../templates/static/js/sse.js'),'utf8');
vm.runInContext(source.slice(source.indexOf('  function handleNpUpdate(np) {'),
  source.indexOf('  function refreshQueueModalIfOpen()')),context);
const message='Your Amazon session has expired. Reconnect Amazon in your account settings, then try again.';
const snapshot={title:'Selected song',video_id:'AAAAAAAAAAA',playing:false,
  playback_confirmed:false,playback_processing:false,playback_output:'alexa',
  playback_error_id:'authentication-failure',playback_error:{
    type:'alexa_authentication_required',code:'amazon_login_required',message,terminal:true,retryable:false}};
context.handleNpUpdate(snapshot);
assert.equal(app.isPlaying,false);
assert.equal(app.lastActionIntent,false);
assert.equal(canceled,1);
assert.equal(errors.length,1);
assert.equal(errors[0].message,message);
assert.equal(errors[0].severity,'error');
context.handleNpUpdate(snapshot);
assert.equal(errors.length,1,'Repeated state polling must not repeat the same error');
console.log('PASS Alexa authentication failure: actionable error, stopped controls, canceled spinner and deduplicated feedback');
