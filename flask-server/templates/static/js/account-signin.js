(() => {
  'use strict';
  const provider = document.body.dataset.provider;
  const el = id => document.getElementById(id);
  let run = 0, stopped = true, started = false, busy = false, capturing = false;
  let pollTimer, frameTimer, pollCount = 0, frameOpenedAt = 0, frameReady = false;
  let amazonAttempt = null, chain = Promise.resolve();

  async function api(path, body) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(path, {
        credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
        headers: {'Content-Type': 'application/json'},
        ...(body === undefined ? {} : {method: 'POST', body: JSON.stringify(body)})
      });
      let data;
      try { data = await response.json(); } catch (_) { data = {}; }
      if (!response.ok) {
        const message = response.status === 401 ? 'Your Music Box session has ended. Go back and sign in again.' :
          (typeof data.error === 'string' ? data.error : data.error?.message);
        const error = new Error(message || 'Connection failed. Try again.');
        error.status = response.status;
        throw error;
      }
      return data;
    } finally { clearTimeout(timeout); }
  }
  function message(text, error = false) {
    el('status').textContent = text;
    el('status').classList.toggle('error', error);
  }
  function stop() {
    stopped = true;
    clearTimeout(pollTimer);
    clearTimeout(frameTimer);
  }
  function fail(error) {
    stop();
    message(error.message || 'Could not open sign-in. Try again.', true);
    el('retry').hidden = error.status === 401;
    if (provider === 'youtube') {
      el('capture').disabled = true;
      el('browser-tools').hidden = true;
      el('browser-loading').hidden = true;
      if (!frameReady) el('surface').hidden = true;
    }
  }
  function finish() {
    stop();
    message('Connected. Returning to Music Box…');
    location.replace('/?account_connected=' + provider);
  }
  async function poll(id) {
    if (stopped || id !== run) return;
    if (pollCount++ > 165) { fail(new Error('Sign-in timed out. Start again.')); return; }
    try {
      const data = await api(provider === 'amazon' ? '/alexa/proxy_check/' : '/api/youtube/browser-session/status');
      if (stopped || id !== run) return;
      if (data.logged_in || data.state === 'connected') { finish(); return; }
      if (data.error) throw new Error(data.error);
      if (['reconnect_required', 'unavailable'].includes(data.state)) {
        fail(new Error(data.message || 'Sign-in could not be verified. Try again.')); return;
      }
      if (data.message) message(data.message);
    } catch (error) {
      if (stopped || id !== run) return;
      if ([401, 403].includes(error.status)) { fail(error); return; }
      message(error.message, true);
      el('retry').hidden = false;
    }
    pollTimer = setTimeout(() => poll(id), 1800);
  }
  async function frame(id) {
    if (stopped || id !== run || capturing) return;
    if (!document.hidden) {
      try {
        const data = await api('/api/youtube/browser-session/surface');
        if (stopped || id !== run || capturing) return;
        if (!/^data:image\/(jpeg|png);base64,/.test(data.image || '') ||
            !Number.isInteger(data.width) || !Number.isInteger(data.height) ||
            data.width <= 0 || data.height <= 0 || data.width > 4096 || data.height > 4096) {
          const error = new Error('The sign-in browser did not return a valid page. Start again.');
          error.status = 409;
          throw error;
        }
        const image = el('frame');
        // Decode before revealing the page or enabling any input controls.
        const decoded = new Image();
        decoded.src = data.image;
        try { await decoded.decode(); }
        catch (_) {
          const error = new Error('The sign-in page could not be displayed. Start again.');
          error.status = 409;
          throw error;
        }
        if (decoded.naturalWidth !== data.width || decoded.naturalHeight !== data.height) {
          const error = new Error('The sign-in page could not be displayed. Start again.');
          error.status = 409;
          throw error;
        }
        if (stopped || id !== run || capturing) return;
        image.src = data.image;
        image.dataset.width = data.width;
        image.dataset.height = data.height;
        image.hidden = false;
        el('surface').hidden = false;
        el('browser-loading').hidden = true;
        el('browser-tools').hidden = false;
        el('capture').disabled = false;
        if (!frameReady || el('status').classList.contains('error')) message('Finish signing in above, then confirm your account.');
        frameReady = true;
      } catch (error) {
        if (stopped || id !== run || capturing) return;
        if ([401, 403, 409].includes(error.status)) { fail(error); return; }
        if (!frameReady && Date.now() - frameOpenedAt < 30000) message('Opening secure browser…');
        else { fail(error); return; }
      }
    }
    frameTimer = setTimeout(() => frame(id), 650);
  }
  function send(data) {
    if (stopped || !frameReady || capturing) return Promise.resolve();
    const id = run;
    chain = chain.then(() => {
      if (!stopped && id === run && !capturing) return api('/api/youtube/browser-session/surface', data);
    }).catch(error => { if (id === run) fail(error); });
    return chain;
  }
  async function start(event) {
    event?.preventDefault();
    if (busy) return;
    stop();
    const id = ++run;
    busy = true; stopped = false; capturing = false; pollCount = 0; frameReady = false;
    chain = Promise.resolve();
    el('connect').disabled = true;
    el('retry').hidden = true;
    try {
      message('Opening sign-in…');
      if (provider === 'amazon') {
        const data = await api('/alexa/proxy_login/', {email: el('email').value.trim(), password: el('password').value, force: true});
        if (stopped || id !== run) return;
        amazonAttempt = data.attempt_id || null;
        el('password').value = '';
        const url = new URL(data.login_url, location.origin);
        if (url.origin !== location.origin || !url.pathname.startsWith('/alexa/proxy/')) throw new Error('Invalid sign-in address');
        el('amazon-page').src = url.href;
      } else {
        await api('/api/youtube/browser-session/start', {});
        if (stopped || id !== run) return;
        started = true;
        frameOpenedAt = Date.now();
        el('frame').hidden = true;
        el('frame').removeAttribute('src');
        el('surface').hidden = false;
        el('browser-loading').hidden = false;
        el('browser-tools').hidden = true;
        el('capture').disabled = true;
      }
      el('setup').hidden = true;
      el('browser').hidden = false;
      message(provider === 'amazon' ? 'Complete verification with Amazon below.' : 'Opening secure browser…');
      if (provider === 'youtube') frame(id);
      poll(id);
    } catch (error) { if (id === run) fail(error); }
    finally { busy = false; el('connect').disabled = false; }
  }
  el('amazon-form')?.addEventListener('submit', start);
  if (provider === 'youtube') el('connect').addEventListener('click', start);
  el('retry').onclick = () => {
    stop(); ++run;
    el('browser').hidden = true;
    el('setup').hidden = false;
    el('retry').hidden = true;
    message('Start again when you are ready.');
  };
  document.querySelector('.back').onclick = event => { event.preventDefault(); el('cancel').click(); };
  el('cancel').onclick = async () => {
    stop(); ++run;
    if (provider === 'youtube' && started) await api('/api/youtube/browser-session/stop', {}).catch(() => {});
    else if (amazonAttempt) await api('/alexa/proxy_cancel/', {attempt_id: amazonAttempt}).catch(() => {});
    location.replace('/');
  };
  if (provider === 'youtube') {
    const keyboard = el('remote-keyboard'), image = el('frame');
    let touch;
    image.addEventListener('pointerdown', event => { touch = {x: event.clientX, y: event.clientY}; });
    image.addEventListener('pointercancel', () => { touch = null; });
    image.addEventListener('pointerup', event => {
      if (!touch || !frameReady || stopped) return;
      const deltaY = event.clientY - touch.y;
      touch = null;
      if (Math.abs(deltaY) > 25) { send({type: 'scroll', direction: deltaY > 0 ? 'up' : 'down'}); return; }
      const bounds = image.getBoundingClientRect();
      send({type: 'click',
        x: Math.min(+image.dataset.width - 1, Math.max(0, Math.floor((event.clientX - bounds.left) / bounds.width * +image.dataset.width))),
        y: Math.min(+image.dataset.height - 1, Math.max(0, Math.floor((event.clientY - bounds.top) / bounds.height * +image.dataset.height)))});
      keyboard.focus({preventScroll: true});
    });
    keyboard.addEventListener('focus', () => { keyboard.value = '\u200b'; });
    function commitText() {
      const text = keyboard.value.replaceAll('\u200b', '');
      if (text) send({type: 'text', text});
      keyboard.value = '\u200b';
    }
    keyboard.addEventListener('compositionend', commitText);
    keyboard.addEventListener('input', event => {
      if (event.isComposing) return;
      if (event.inputType === 'deleteContentBackward') { send({type: 'key', key: 'Backspace'}); keyboard.value = '\u200b'; }
      else commitText();
    });
    keyboard.addEventListener('keydown', event => {
      if (event.isComposing) return;
      if (['Backspace', 'Enter', 'Tab', 'Escape', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) {
        event.preventDefault(); send({type: 'key', key: event.key});
      }
    });
    el('choose-account').onclick = async () => {
      keyboard.blur();
      try { await chain; await api('/api/youtube/browser-session/choose-account', {}); message('Choose an account or add another one above.'); }
      catch (error) { fail(error); }
    };
    el('keyboard').onclick = () => keyboard.focus({preventScroll: true});
    el('tab').onclick = () => send({type: 'key', key: 'Tab'});
    el('enter').onclick = () => { send({type: 'key', key: 'Enter'}); keyboard.blur(); };
    el('capture').onclick = async () => {
      if (!frameReady || stopped || capturing) return;
      el('capture').disabled = true; keyboard.blur();
      try {
        await chain;
        capturing = true; clearTimeout(frameTimer);
        await api('/api/youtube/browser-session/capture', {});
        message('Verifying your account…');
      } catch (error) { capturing = false; fail(error); }
    };
  }
  window.addEventListener('pagehide', () => {
    stop(); ++run;
    const options = {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, keepalive: true};
    if (amazonAttempt) fetch('/alexa/proxy_cancel/', {...options, body: JSON.stringify({attempt_id: amazonAttempt})}).catch(() => {});
    if (started) fetch('/api/youtube/browser-session/stop', {...options, body: '{}'}).catch(() => {});
  });
})();
