(function () {
  'use strict';
  const modal = document.getElementById('download-cookies-modal');
  if (!modal || window.JAM_GUEST) return;
  const open = document.getElementById('download-cookies-replace');
  const close = document.getElementById('download-cookies-close');
  const form = document.getElementById('download-cookies-form');
  const text = document.getElementById('download-cookies-text');
  const file = document.getElementById('download-cookies-file');
  const save = document.getElementById('download-cookies-save');
  const message = document.getElementById('download-cookies-message');
  const check = document.getElementById('download-cookies-check');
  const status = document.getElementById('status-yt-cookies');
  let busy = false;
  function hide() {
    if (busy) return;
    modal.hidden = true; text.value = ''; file.value = ''; message.textContent = '';
    open.focus();
  }
  open.addEventListener('click', function () {
    modal.hidden = false; message.textContent = ''; text.focus();
  });
  close.addEventListener('click', hide);
  modal.addEventListener('click', function (e) { if (e.target === modal) hide(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !modal.hidden) hide(); });
  check.addEventListener('click', async function () {
    check.disabled = true; status.textContent = 'Testing audio…';
    try {
      const result = await window.api('/api/youtube/download-cookies/status?refresh=1');
      status.textContent = result.valid ? 'Download OK' : 'Download failed';
      status.title = result.message;
    } catch (e) { status.textContent = 'Check failed'; status.title = e.message; }
    finally { check.disabled = false; }
  });
  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    if (busy) return;
    busy = true; save.disabled = true; close.disabled = true;
    message.textContent = 'Downloading an audio sample…';
    try {
      const selected = file.files && file.files[0];
      if (selected && selected.size > 128 * 1024) throw new Error('Cookie export must be smaller than 128 KB.');
      const cookies = selected ? await selected.text() : text.value;
      if (!cookies.trim()) throw new Error('Paste cookies or choose a cookies.txt file.');
      const result = await window.api('/api/youtube/download-cookies', { cookies: cookies });
      if (!result.success) throw new Error('Cookies were not saved.');
      text.value = ''; file.value = '';
      status.textContent = 'Download OK'; status.title = result.message;
      message.textContent = 'Audio download passed. New cookies saved.';
    } catch (e) { message.textContent = e.message; }
    finally { busy = false; save.disabled = false; close.disabled = false; }
  });
})();
