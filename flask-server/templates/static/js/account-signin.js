(() => {
'use strict';
const provider=document.body.dataset.provider, el=id=>document.getElementById(id);
let stopped=false, pollTimer, frameTimer, started=false, busy=false, pollCount=0, chain=Promise.resolve();
async function api(path,body){const r=await fetch(path,{credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json'},...(body===undefined?{}:{method:'POST',body:JSON.stringify(body)})});let d=await r.json();if(!r.ok)throw Error(typeof d.error==='string'?d.error:(d.error?.message||'Connection failed. Try again.'));return d;}
function message(text,error=false){el('status').textContent=text;el('status').classList.toggle('error',error);}
function finish(){stopped=true;clearTimeout(pollTimer);clearTimeout(frameTimer);message('Connected. Returning to Music Box…');location.replace('/?account_connected='+provider);}
async function poll(){if(stopped)return;if(pollCount++>165){stopped=true;message('Sign-in timed out. Start again.',true);el('retry').hidden=false;return;}try{let d=await api(provider==='amazon'?'/alexa/proxy_check/':'/api/youtube/browser-session/status');if(d.logged_in||d.state==='connected')return finish();if(d.error)throw Error(d.error);if(d.state==='reconnect_required'||d.state==='unavailable'){message(d.message||'Sign-in could not be verified. Try again.',true);el('retry').hidden=false;}else if(d.message)message(d.message);}catch(e){message(e.message,true);el('retry').hidden=false;}pollTimer=setTimeout(poll,1800);}
async function frame(){if(stopped)return;if(!document.hidden){try{let d=await api('/api/youtube/browser-session/surface');if(stopped)return;el('frame').src=d.image;el('frame').dataset.width=d.width;el('frame').dataset.height=d.height;}catch(e){message(e.message,true);}}frameTimer=setTimeout(frame,650);}
function send(data){chain=chain.then(()=>api('/api/youtube/browser-session/surface',data)).catch(e=>message(e.message,true));return chain;}
async function start(event){event?.preventDefault();if(busy)return;busy=true;el('connect').disabled=true;el('retry').hidden=true;stopped=false;pollCount=0;clearTimeout(pollTimer);clearTimeout(frameTimer);try{message('Opening sign-in…');if(provider==='amazon'){const d=await api('/alexa/proxy_login/',{email:el('email').value.trim(),password:el('password').value,force:true});el('password').value='';const u=new URL(d.login_url,location.origin);if(u.origin!==location.origin||!u.pathname.startsWith('/alexa/proxy/'))throw Error('Invalid sign-in address');el('amazon-page').src=u.href;}else{await api('/api/youtube/browser-session/start',{});started=true;frame();}el('setup').hidden=true;el('browser').hidden=false;message('Complete sign-in below. Your password is not saved by Music Box.');poll();}catch(e){message(e.message,true);el('retry').hidden=false;}finally{busy=false;el('connect').disabled=false;}}
el('amazon-form')?.addEventListener('submit',start);if(provider==='youtube')el('connect').addEventListener('click',start);
el('retry').onclick=()=>{el('browser').hidden=true;el('setup').hidden=false;stopped=true;clearTimeout(pollTimer);clearTimeout(frameTimer);message('Start again when you are ready.');};
document.querySelector('.back').onclick=e=>{e.preventDefault();el('cancel').click();};
el('cancel').onclick=async()=>{stopped=true;clearTimeout(pollTimer);clearTimeout(frameTimer);if(provider==='youtube')await api('/api/youtube/browser-session/stop',{}).catch(()=>{});location.replace('/');};
if(provider==='youtube'){
 const keyboard=el('remote-keyboard'),image=el('frame');let touch;
 image.addEventListener('pointerdown',e=>{touch={x:e.clientX,y:e.clientY};});
 image.addEventListener('pointerup',e=>{if(!touch)return;const dy=e.clientY-touch.y;if(Math.abs(dy)>25){send({type:'scroll',direction:dy>0?'up':'down'});touch=null;return;}touch=null;const r=image.getBoundingClientRect();send({type:'click',x:Math.min(+image.dataset.width-1,Math.max(0,Math.floor((e.clientX-r.left)/r.width*+image.dataset.width))),y:Math.min(+image.dataset.height-1,Math.max(0,Math.floor((e.clientY-r.top)/r.height*+image.dataset.height)))});keyboard.focus({preventScroll:true});});
 image.addEventListener('wheel',e=>{e.preventDefault();send({type:'scroll',direction:e.deltaY>0?'down':'up'});},{passive:false});
 keyboard.addEventListener('focus',()=>{keyboard.value='\u200b';});
 keyboard.addEventListener('input',e=>{if(e.isComposing)return;const value=keyboard.value.replaceAll('\u200b','');if(e.inputType==='deleteContentBackward')send({type:'key',key:'Backspace'});else if(value)send({type:'text',text:value});keyboard.value='\u200b';});
 keyboard.addEventListener('keydown',e=>{if(['Backspace','Enter','Tab','Escape','ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();send({type:'key',key:e.key});}});
 el('choose-account').onclick=async()=>{keyboard.blur();try{await api('/api/youtube/browser-session/choose-account',{});message('Choose an account or add another one in the sign-in page.');}catch(e){message(e.message,true);}};
 el('keyboard').onclick=()=>keyboard.focus();el('tab').onclick=()=>send({type:'key',key:'Tab'});el('enter').onclick=()=>{send({type:'key',key:'Enter'});keyboard.blur();};
 el('capture').onclick=async()=>{el('capture').disabled=true;keyboard.blur();try{await chain;await api('/api/youtube/browser-session/capture',{});message('Verifying the selected account…');}catch(e){message(e.message,true);}finally{el('capture').disabled=false;}};
}
window.addEventListener('pagehide',()=>{stopped=true;clearTimeout(pollTimer);clearTimeout(frameTimer);if(started)fetch('/api/youtube/browser-session/stop',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:'{}',keepalive:true}).catch(()=>{});});
})();
