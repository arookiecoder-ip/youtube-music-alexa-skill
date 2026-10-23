const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
async function main() {
  let now = 10000, release, reject;
  const timers = [], change = {}, confirmations = [];
  const select = {value:'mobile:source', addEventListener:(name, fn)=>change[name]=fn};
  const source = {playback_output:'phone', output_owner:'source', output_token:'old', title:'Song', video_id:'AAAAAAAAAAA', playing:true, playback_confirmed:true};
  const destination = {...source, output_owner:'target', output_token:'new'};
  const app = {isPlaying:true, lastActionAt:0, lastActionIntent:null, GRACE_MS:1000};
  const window = {__appState:app, addEventListener(){}, _applyDevices(){},
    _notifyPlayPauseServerState: playing=>confirmations.push(playing),
    api:async (url, body)=> {
      if (body && body.action === 'transfer') return new Promise((yes,no)=>{release=yes;reject=no;});
      return source;
    }};
  const context = {window, document:{getElementById:id=>id==='device'?select:null},
    Date:{now:()=>now}, setTimeout:(fn,delay)=>{timers.push({fn,delay}); return timers.length;}, clearTimeout(){}};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../templates/static/js/device-connect.js'),'utf8'),context);
  const sse = fs.readFileSync(path.join(__dirname,'../templates/static/js/sse.js'),'utf8');
  Object.assign(context,{state:()=>app, _lastProcessing:false, _lastHistoryVideoId:null});
  vm.runInContext(sse.slice(sse.indexOf('  function handleNpUpdate(np) {'),sse.indexOf('  function refreshQueueModalIfOpen()')),context);
  const flush = async()=>{for(let n=0;n<20;n++) await Promise.resolve();};
  await flush();
  select.value='mobile:target'; change.change(); await flush();
  context.handleNpUpdate({...destination, handoff_pending:true, playing:false});
  assert(app.isPlaying,'Pending source stop must not flash a paused icon');
  release(destination); await flush();
  context.handleNpUpdate({...destination, playing:false});
  assert(app.isPlaying,'Initial paused destination report must not flash paused');
  assert.equal(confirmations.at(-1),false,'Visual continuity must not forge a playback confirmation');
  context.handleNpUpdate({...destination, playing:true, playback_confirmed:false, playback_processing:true});
  assert(app.isPlaying);
  context.handleNpUpdate({...destination, playing:false, playback_error:{message:'Buffering', terminal:false}});
  assert(app.isPlaying,'Informational buffering must not clear transfer continuity');
  context.handleNpUpdate(destination);
  context.handleNpUpdate({...destination,playing:false});
  assert.equal(app.isPlaying,false,'A real pause after handoff must be shown');
  // Switching while already paused must never manufacture a playing state.
  select.value='mobile:other'; change.change(); await flush();
  context.handleNpUpdate({...destination,output_owner:'other',handoff_pending:true,playing:false});
  assert.equal(app.isPlaying,false);
  release({...destination,output_owner:'other'}); await flush();
  app.isPlaying=true; select.value='mobile:target'; change.change(); await flush();
  reject(new Error('offline')); await flush();
  context.handleNpUpdate({...destination,playing:false});
  assert.equal(app.isPlaying,false,'Failed switches must release the presentation hold');
  app.isPlaying=true; select.value='mobile:target'; change.change(); await flush();
  release(destination); await flush();
  now+=8001;
  context.handleNpUpdate({...destination,playing:false});
  assert.equal(app.isPlaying,false,'Unconfirmed handoffs must expire instead of showing playing forever');
  app.isPlaying=true;
  context.handleNpUpdate({...destination,output_token:'remote-switch',handoff_pending:true,playing:false});
  assert(app.isPlaying,'Remote initiated handoff must preserve the playing presentation too');
  await window.api('/alexa/command/',{action:'pause'}).catch(()=>{});
  context.handleNpUpdate({...destination,output_token:'remote-switch',playing:false});
  assert.equal(app.isPlaying,false,'Explicit pause must cancel the handoff presentation');
  console.log('PASS handoff feedback: source stop, destination preparation, truthful confirmations, paused source, failure, expiry, remote switch and explicit pause');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
