/* In-player lyrics: optional, timestamp-free, and synchronized to playback. */
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const tab = $('np-tab-lyrics'), queueTab = $('np-tab-queue');
  if (!tab || !queueTab || window.JAM_GUEST) return;
  const panel = $('np-lyrics-panel'), queue = $('np-queue-list'), lines = $('np-lyrics-lines');
  const romanizeButton = $('np-romanize');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const cache = new Map();
  let video = '', revision = 0, data = null, active = -1, selected = false, followAfter = 0;
  let originals = [], romanized = null, showingRomanized = false, romanizerLoading = null;
  function select(lyrics) {
    selected = lyrics && !tab.disabled;
    tab.setAttribute('aria-selected', String(selected)); queueTab.setAttribute('aria-selected', String(!selected));
    tab.tabIndex = selected ? 0 : -1; queueTab.tabIndex = selected ? -1 : 0;
    panel.hidden = !selected; queue.hidden = selected;
    romanizeButton.hidden = !selected;
    if (selected) followAfter = 0;
    if (selected) { active = -1; tick(); }
  }
  tab.onclick = () => select(true); queueTab.onclick = () => select(false);
  for (const button of [tab, queueTab]) button.addEventListener('keydown', event => {
    if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    event.preventDefault();
    const target = event.key === 'Home' ? queueTab : event.key === 'End' ? tab : button === tab ? queueTab : tab;
    if (!target.disabled) { select(target === tab); target.focus(); }
  });
  panel.addEventListener('wheel', () => { followAfter = performance.now() + 8000; }, {passive:true});
  panel.addEventListener('touchstart', () => { followAfter = performance.now() + 8000; }, {passive:true});
  panel.addEventListener('pointerdown', () => { followAfter = performance.now() + 8000; }, {passive:true});
  panel.addEventListener('keydown', event => {
    if (['ArrowDown','ArrowUp','PageDown','PageUp','Home','End'].includes(event.key)) followAfter = performance.now() + 8000;
  });
  const scriptRanges = [
    [/\p{Script=Devanagari}+/gu, 'devanagari'], [/\p{Script=Bengali}+/gu, 'bengali'],
    [/\p{Script=Gurmukhi}+/gu, 'gurmukhi'], [/\p{Script=Gujarati}+/gu, 'gujarati'],
    [/\p{Script=Tamil}+/gu, 'tamil'], [/\p{Script=Telugu}+/gu, 'telugu'],
    [/\p{Script=Kannada}+/gu, 'kannada'], [/\p{Script=Malayalam}+/gu, 'malayalam'],
    [/\p{Script=Oriya}+/gu, 'oriya'], [/\p{Script=Sinhala}+/gu, 'sinhala']
  ];
  function supportsRomanization(text) { return scriptRanges.some(([pattern]) => { pattern.lastIndex=0; return pattern.test(text); }); }
  function loadRomanizer() {
    if (window.Sanscript) return Promise.resolve(window.Sanscript);
    if (!romanizerLoading) romanizerLoading = new Promise((resolve,reject) => {
      const script = document.createElement('script'); script.src = romanizeButton.dataset.romanizerSrc;
      script.onload = () => { if (window.Sanscript) resolve(window.Sanscript); else reject(new Error('Romanization could not load.')); };
      script.onerror = () => { script.remove(); romanizerLoading=null; reject(new Error('Romanization could not load. Try again.')); };
      document.head.append(script);
    });
    return romanizerLoading;
  }
  romanizeButton.onclick = async () => {
    const request = revision;
    romanizeButton.disabled = true; romanizeButton.setAttribute('aria-busy','true');
    try {
      if (!romanized) {
        const engine = await loadRomanizer();
        if (request !== revision) return;
        romanized = originals.map(text => {
          let result = text;
          for (const [pattern, scheme] of scriptRanges) result = result.replace(pattern, run => engine.t(run.normalize('NFC').replace(/[कखगजडढफय]़/g, pair => String.fromCharCode(0x958 + 'कखगजडढफय'.indexOf(pair[0]))), scheme, 'itrans', {
            preferred_alternates:{itrans:{A:'aa',I:'ee',U:'oo'}}
          }));
          return result;
        });
      }
      if (request !== revision) return;
      showingRomanized = !showingRomanized;
      const text = showingRomanized ? romanized : originals;
      [...lines.children].forEach((line,index) => { line.textContent=text[index]; });
      romanizeButton.setAttribute('aria-pressed',String(showingRomanized));
      romanizeButton.textContent = showingRomanized ? 'Original' : 'Romanize';
      romanizeButton.setAttribute('aria-label', showingRomanized ? 'Show original lyrics' : 'Show lyrics in English letters');
    } catch (error) { window.toast?.(error.message, 'error'); }
    finally {
      if (request === revision) { romanizeButton.disabled=false; romanizeButton.removeAttribute('aria-busy'); }
    }
  };
  function paint(result) {
    data = result; active = -1; lines.replaceChildren();
    const timed = Array.isArray(result.lines) ? result.lines : [];
    const text = typeof result.text === 'string' ? result.text : '';
    const content = timed.length ? timed.map(line => line.text) : text.split('\n').filter(Boolean);
    originals = content; romanized = null; showingRomanized = false;
    romanizeButton.textContent = 'Romanize'; romanizeButton.setAttribute('aria-pressed','false');
    romanizeButton.removeAttribute('aria-busy');
    romanizeButton.setAttribute('aria-label','Show lyrics in English letters');
    romanizeButton.disabled = !content.some(supportsRomanization);
    romanizeButton.title = romanizeButton.disabled ? 'English-letter conversion is not needed or unavailable for this script' : 'Show words in English letters';
    tab.disabled = !content.some(line => typeof line === 'string' && line.trim());
    tab.title = tab.disabled ? 'Lyrics unavailable for this song' : 'Show lyrics';
    $('np-lyrics-credit').textContent = [!timed.length && 'Synced timing is unavailable for this song.', result.source].filter(Boolean).join(' · ');
    if (tab.disabled) { select(false); return; }
    const fragment = document.createDocumentFragment();
    content.forEach((text, index) => {
      const line = document.createElement(timed.length ? 'button' : 'p');
      line.className = 'np-lyric-line'; line.textContent = text;
      if (timed.length) {
        line.type = 'button'; line.title = 'Play from this line';
        const lineVideo = video;
        line.onclick = async () => {
          if (lineVideo !== video || !window.progress?.seekTo) return;
          followAfter = 0;
          await window.progress.seekTo(timed[index].start_ms);
          tick();
        };
      }
      fragment.append(line);
    });
    lines.append(fragment); panel.classList.toggle('untimed', !timed.length);
    panel.scrollTop = 0;
    tick();
  }
  async function trackChanged() {
    const next = window.__appState?._currentTrack?.video_id || '';
    if (next === video) return;
    video = next; const request = ++revision;
    data = null; active = -1; tab.disabled = true; select(false); lines.replaceChildren();
    if (!video) return;
    try {
      const result = cache.get(video) || await window.api('/api/track/' + encodeURIComponent(video) + '/lyrics');
      if (request !== revision) return;
      cache.set(video, result); if (cache.size > 20) cache.delete(cache.keys().next().value);
      paint(result);
    } catch (_) { if (request === revision) { tab.disabled = true; tab.title = 'Lyrics unavailable for this song'; } }
  }
  function tick() {
    if (!selected || document.hidden || !data?.lines?.length) return;
    const local = window.browserPlayback;
    const position = local?.owns() ? local.audio.currentTime * 1000 : window.progress?.livePosition ? window.progress.livePosition() : Number($('progress-track')?.getAttribute('aria-valuenow') || 0) * 1000;
    let next = -1;
    for (let i = 0; i < data.lines.length; i++) { if (position >= data.lines[i].start_ms) next = i; else break; }
    if (next !== active) {
      if (lines.children[active]) { lines.children[active].classList.remove('current'); lines.children[active].removeAttribute('aria-current'); }
      active = next;
      if (lines.children[active]) { lines.children[active].classList.add('current'); lines.children[active].setAttribute('aria-current','true'); }
    }
    const line = lines.children[active];
    if (line && performance.now() >= followAfter) {
      const target = Math.max(0, line.offsetTop - panel.clientHeight * .38);
      if (Math.abs(panel.scrollTop - target) > 8) panel.scrollTo({top:target, behavior:reduced.matches ? 'instant' : 'smooth'});
    }
  }
  const clock = setInterval(tick, 250);
  window.addEventListener('musicbox:track', trackChanged);
  window.addEventListener('pagehide', () => clearInterval(clock));
  trackChanged();
})();
