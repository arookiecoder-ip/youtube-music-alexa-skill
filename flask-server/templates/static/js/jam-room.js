/* Room state is authoritative. This audio element only follows selected output state. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id), audio = $('audio');
  let state = null, busy = false, stopped = false, listening = false, current = '', lastSuccess = Date.now();
  let device, lastAudioRetry=0;
  try { device=sessionStorage.getItem('musicbox_browser_id'); if(!device){device='browser-'+crypto.randomUUID();sessionStorage.setItem('musicbox_browser_id',device);} } catch (_) { device='browser-'+crypto.randomUUID(); }
  const error = message => { $('error').textContent=message; $('error').hidden=!message; };
  const notice = message => { $('notice').textContent=message; $('notice').hidden=!message; };
  async function api(path, body) {
    const response=await fetch(path, body === undefined ? {cache:'no-store'} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data=await response.json();
    if(!response.ok) throw Error(typeof data.error==='string'?data.error:data.error?.message || 'Could not update Jam. Try again.');
    return data;
  }
  async function control(action, fields={}) {
    if(busy || !state?.active) return;
    busy=true;
    try { await api('/api/jam/control/', {...fields,action,revision:state.playback.revision,command_id:crypto.randomUUID()});error('');await poll(); }
    catch(e) { error(e.message); } finally { busy=false; }
  }
  function button(label, run, style='') {const b=document.createElement('button');b.textContent=label;b.className=style;b.onclick=run;return b;}
  function text(tag, value) {const el=document.createElement(tag);el.textContent=value;return el;}
  function render() {
    const p=state.playback;
    $('people').textContent=`${state.member_count} people · ${p.mode==='multiple'?'Multiple selected outputs':'One selected output'} · one queue`;
    $('title').textContent=p.title || 'Your shared queue';$('artist').textContent=p.artist || (p.video_id?'Shared with the Jam':'Add a song to get started');
    $('art').hidden=!p.thumbnail;if(p.thumbnail)$('art').src=p.thumbnail;
    $('play').textContent=p.playing?'Pause':'Play';$('seek').max=String(p.duration_ms||1);$('seek').value=String(p.position_ms||0);
    $('repeat').textContent='Repeat: '+p.repeat;
    $('queue').replaceChildren(...p.queue.map((song,index)=>{
      const row=document.createElement('li');row.className='song'+(index===p.queue_index?' active':'');
      const name=text('span',song.title);name.append(text('small',song.artist));row.append(name);
      row.append(button(index===p.queue_index?'Playing':'Play',()=>control('select',{index})));
      if(index!==p.queue_index)row.append(button('Remove',()=>control('remove',{entry_id:song.entry_id})));
      return row;
    }));
    $('host').hidden=!state.host;
    if(state.host) {
      $('multiple').checked=p.mode==='multiple';
      $('outputs').replaceChildren(...state.outputs.map(output=>{
        const row=document.createElement('label');row.className='output';const name=text('span',output.name);
        name.append(text('small',!output.online?'Offline':output.error||'Connected'));row.append(name);
        const check=document.createElement('input');check.type='checkbox';check.checked=p.selected.includes(output.device);check.disabled=!output.online&&!check.checked;
        check.onchange=()=>{const selected=p.mode==='single'?(check.checked?[output.device]:[]):check.checked?[...p.selected,output.device]:p.selected.filter(id=>id!==output.device);control('configure',{mode:p.mode,selected});};row.append(check);return row;
      }));
      $('members').replaceChildren(text('p','You · Host'),...state.members.map(member=>{
        const row=document.createElement('div');row.className='member';row.append(text('span',member.name),button('Remove',async()=>{
          if(!confirm(`Remove ${member.name} from this Jam?`))return;
          try{await api('/alexa/jam/remove/',{member_id:member.id});await poll();}catch(e){error(e.message);}
        }));return row;
      }));
    }
  }
  async function syncAudio() {
    const p=state.playback;
    if(!state.listen || !p.video_id) {audio.pause();listening=false;return;}
    listening=true;
    if(!p.duration_ms && Number.isFinite(audio.duration) && audio.duration>0) control('duration',{entry_id:p.entry_id,duration_ms:Math.round(audio.duration*1000)});
    if(audio.error && Date.now()-lastAudioRetry>5000) {lastAudioRetry=Date.now();current='';}
    if(current!==p.entry_id) {current=p.entry_id;audio.src='/api/jam/audio/'+encodeURIComponent(p.video_id);audio.load();}
    if(audio.readyState>0 && Math.abs(audio.currentTime*1000-p.position_ms)>1500) audio.currentTime=p.position_ms/1000;
    if(p.playing && audio.paused) {
      try{await audio.play();notice('');}catch(_){notice('Tap Enable audio to listen on this browser.');$('enable-audio').hidden=false;}
    } else if(!p.playing)audio.pause();
  }
  async function poll() {
    if(stopped)return;
    try{
      const version=state?.playback.queue_epoch?'&queue_epoch='+encodeURIComponent(state.playback.queue_epoch)+'&queue_version='+state.playback.queue_version:'';
      const data=await api('/api/jam/state/?device_id='+encodeURIComponent(device)+version);
      if(stopped)return;
      if(state && data.active && data.url===state.url && data.playback.revision<state.playback.revision)return;
      if(!data.active){stopped=true;audio.pause();error('This Jam ended, or your access was removed.');$('people').textContent='Jam disconnected';return;}
      if(!data.playback.queue){if(data.playback.queue_epoch!==state?.playback.queue_epoch)throw Error('Jam queue needs a refresh.');data.playback.queue=state.playback.queue;}
      state=data;lastSuccess=Date.now();render();
      await api('/api/jam/presence/',{device_id:device,name:'Browser player',error:audio.error?'Audio unavailable':''});
      await syncAudio();
    }catch(e){error(e.message);if(Date.now()-lastSuccess>12000)audio.pause();}
  }
  const enable=button('Enable audio',async()=>{try{await audio.play();enable.hidden=true;notice('');}catch(e){error(e.message);}},'primary');enable.id='enable-audio';enable.hidden=true;$('notice').after(enable);
  audio.onloadedmetadata=()=>{if(state?.listen)audio.currentTime=(state.playback.position_ms||0)/1000;if(state?.playback.duration_ms===0&&Number.isFinite(audio.duration))control('duration',{entry_id:state.playback.entry_id,duration_ms:Math.round(audio.duration*1000)});};
  $('play').onclick=()=>control(state?.playback.playing?'pause':'play');$('previous').onclick=()=>control('previous');$('next').onclick=()=>control('next');$('repeat').onclick=()=>control('repeat');
  $('seek').onchange=()=>control('seek',{position_ms:Number($('seek').value)});
  $('multiple').onchange=()=>control('configure',{mode:$('multiple').checked?'multiple':'single',selected:$('multiple').checked?state.playback.selected:state.playback.selected.slice(0,1)});
  $('share').onclick=async()=>{try{if(navigator.share)await navigator.share({title:'Join my Jam',url:state.url});else{await navigator.clipboard.writeText(state.url);notice('Invite copied.');}}catch(e){if(e.name!=='AbortError')error('Could not share the invitation.');}};
  async function leave(end=false){if(!confirm(end?'End this Jam for everyone?':'Leave this Jam?'))return;try{await api(end?'/alexa/jam/stop/':'/api/jam/leave/',{});audio.pause();stopped=true;location.replace('/');}catch(e){error(e.message);}}
  $('leave').onclick=()=>leave();$('end').onclick=()=>leave(true);
  $('search').onsubmit=async event=>{event.preventDefault();try{
    const found=await api('/alexa/search/?q='+encodeURIComponent($('query').value));
    const songs=found.songs||found.all||[];$('results').replaceChildren(...songs.filter(s=>s.video_id||s.videoId).slice(0,20).map(song=>{
      const row=document.createElement('div');row.className='song';row.append(text('span',song.title||'Song'),button('Add',()=>control('append',{tracks:[song]})));return row;
    }));if(!songs.length)notice('No songs found. Try another search.');
  }catch(e){error(e.message);}};
  function requireAccount(){if(window.JAM_SIGNED_IN)return true;notice('Sign in to save songs to your own account. Reopen this invite after signing in.');return false;}
  function personalSong(){const p=state.playback;return {video_id:p.video_id,title:p.title,artist:p.artist,thumbnail:p.thumbnail,duration_ms:p.duration_ms};}
  $('like').onclick=async()=>{if(!requireAccount()||!state?.playback.video_id)return;try{await api('/alexa/like/',{...personalSong(),action:'LIKE'});notice('Saved to your liked songs.');}catch(e){error(e.message);}};
  $('playlist').onclick=async()=>{if(!requireAccount()||!state?.playback.video_id)return;const song=personalSong();try{
    const library=await api('/api/library/?type=playlists');const lists=(library.playlists||library.items||[]).filter(p=>(p.playlistId||p.playlist_id||p.id)!=='LM');
    $('playlist-list').replaceChildren(...lists.filter(p=>p.playlistId||p.playlist_id||p.id).map(p=>button(p.title||'Playlist',async()=>{
      try{await api('/api/library/playlists/'+encodeURIComponent(p.playlistId||p.playlist_id||p.id)+'/tracks',song);$('playlists').close();notice('Added to your playlist.');}catch(e){error(e.message);}
    })));if(!lists.length)$('playlist-list').append(text('p','No playlists yet. Create one in your library.'));$('playlists').showModal();
  }catch(e){error(e.message);}};
  $('close-playlists').onclick=()=>$('playlists').close();
  async function loop(){await poll();if(!stopped)setTimeout(loop,1000);}loop();
  window.addEventListener('pagehide',()=>{audio.pause();stopped=true;});
  window.addEventListener('pageshow',event=>{if(event.persisted){stopped=false;loop();}});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!stopped)poll();});
})();
