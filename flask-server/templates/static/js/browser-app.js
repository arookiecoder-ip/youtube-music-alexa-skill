/* Optional audio languages in the existing player menu. */
(function () {
  'use strict';
  const dialog = document.getElementById('app-language-dialog');
  const menu = document.getElementById('np-more-menu');
  if (!dialog || !menu || window.JAM_GUEST) return;
  const root = document.getElementById('app-language-list');
  const currentVideo = () => window.__appState?._currentTrack?.video_id || '';
  const button = document.createElement('button');
  button.className = 'result-menu-option'; button.type = 'button';
  button.setAttribute('role', 'menuitem'); button.dataset.appAction = 'language'; button.hidden = true;
  button.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M3 5h12M9 2v3M5 5c0 6 4 9 9 11M13 5c0 6-4 9-9 11M15 21l4-10 4 10M16 18h6"/></svg><span>Audio language</span>';
  menu.append(button);
  const cache = new Map();
  let revision = 0, pendingVideo = '', failedVideo = '', seenVideo = '';
  function tracks(reply) {
    const unique = new Map();
    for (const track of reply.audio_tracks || []) {
      const id = track.id || track.audio_track || track.language;
      if (id && !unique.has(id)) unique.set(id, {...track, id});
    }
    return [...unique.values()];
  }
  function render(video) {
    const reply = cache.get(video);
    button.hidden = !window.browserPlayback?.owns() || !reply || tracks(reply).length < 2;
    if (button.hidden && dialog.open) dialog.close();
    if (!dialog.open || button.hidden) return;
    root.replaceChildren();
    for (const track of tracks(reply)) {
      const option = document.createElement('button'); option.type = 'button';
      option.textContent = track.display_name || track.name || track.label || track.language || track.id;
      option.setAttribute('aria-pressed', String(track.id === reply.selected));
      option.onclick = async () => {
        if (video !== currentVideo() || !window.browserPlayback?.owns()) { dialog.close(); return; }
        root.querySelectorAll('button').forEach(item => { item.disabled = true; });
        try {
          await window.browserPlayback.selectAudioLanguage(track.id);
          if (video === currentVideo()) { reply.selected = track.id; dialog.close(); }
        } catch (error) {
          window.toast?.(error.message || 'Could not change audio language. Try again.', 'error');
        } finally { root.querySelectorAll('button').forEach(item => { item.disabled = false; }); }
      };
      root.append(option);
    }
  }
  async function update() {
    const video = currentVideo();
    render(video);
    if (!video || !window.browserPlayback?.owns() || cache.has(video) || pendingVideo === video || failedVideo === video) return;
    const request = ++revision; pendingVideo = video;
    try {
      const reply = await window.api('/audio/?video_id=' + encodeURIComponent(video) + '&audio_tracks=1');
      if (request !== revision || video !== currentVideo()) return;
      cache.set(video, reply);
      if (cache.size > 20) cache.delete(cache.keys().next().value);
      render(video);
    } catch (_) { if (request === revision) failedVideo = video; }
    finally { if (request === revision) pendingVideo = ''; }
  }
  button.onclick = () => {
    if (button.hidden) return;
    window._closeNpMoreMenu?.();
    dialog.showModal(); render(currentVideo());
  };
  dialog.querySelector('[data-close-dialog]').onclick = () => dialog.close();
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const box = dialog.getBoundingClientRect();
    if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dialog.close();
  });
  window.addEventListener('musicbox:track', () => {
    const video = currentVideo();
    if (video === seenVideo) { update(); return; }
    seenVideo = video;
    ++revision; pendingVideo = ''; failedVideo = ''; button.hidden = true;
    if (dialog.open) dialog.close();
    update();
  });
  // Recheck ownership when the existing player menu opens, without polling.
  new MutationObserver(() => { update(); }).observe(menu, {attributes:true, attributeFilter:['class']});
  window.addEventListener('online', () => { failedVideo = ''; update(); });
  update();
})();
