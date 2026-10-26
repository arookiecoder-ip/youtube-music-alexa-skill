(() => {
  'use strict';
  const provider = document.body.dataset.provider;
  const asToken = document.body.dataset.as || '';
  const returnTo = document.body.dataset.returnTo || '';
  const el = id => document.getElementById(id);
  const panels = ['code-panel', 'captcha-panel', 'choice-panel', 'approval-panel'];
  // Amazon's scripted steps and Google's challenge pages can each take well
  // over a minute to answer, so the form calls get a far longer leash than
  // ordinary API traffic.
  let run = 0, busy = false, pollTimer = null, pollCount = 0;
  let answerKind = 'securitycode', choiceKind = 'claimsoption';

  async function api(path, body, timeout = 150000) {
    if (asToken && path.startsWith('/api/youtube/browser-session/')) {
      // An admin signing in for another account: the token is what makes these
      // calls target that account's profile. It never reaches the other APIs.
      path += (path.includes('?') ? '&' : '?') + 'as=' + encodeURIComponent(asToken);
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(path, {
        credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
        headers: {'Content-Type': 'application/json'},
        ...(body === undefined ? {} : {method: 'POST', body: JSON.stringify(body)})
      });
      let data;
      try { data = await response.json(); } catch (_) { data = {}; }
      if (!response.ok) {
        if (data && typeof data.state === 'string') return data;  // a form answer, not a transport error
        const message = response.status === 401 ? 'Your Music Box session has ended. Go back and sign in again.' :
          (typeof data.error === 'string' ? data.error : data.error?.message);
        const error = new Error(message || 'Connection failed. Try again.');
        error.status = response.status;
        throw error;
      }
      return data;
    } finally { clearTimeout(timer); }
  }

  function message(text, error = false) {
    el('status').textContent = text || '';
    el('status').classList.toggle('error', error);
  }
  function stopPolling() { clearTimeout(pollTimer); pollTimer = null; }
  function reset() {
    stopPolling();
    el('steps').hidden = true;
    panels.forEach(id => el(id).hidden = true);
    el('setup').hidden = false;
  }
  function finish() {
    stopPolling();
    message('Connected. Returning…');
    location.replace(returnTo || ('/account?connected=' + provider + '#panel-providers'));
  }
  function showPanel(id) {
    el('setup').hidden = true;
    el('steps').hidden = false;
    panels.forEach(panel => { el(panel).hidden = panel !== id; });
  }

  function handle(data) {
    if (!data || !data.state) {
      message('Connection failed. Try again.', true);
      reset();
      el('retry').hidden = false;
      return;
    }
    switch (data.state) {
      case 'connected':
        finish();
        return;
      case 'need_code':
        answerKind = data.answer || 'securitycode';
        el('code-message').textContent = data.message || 'Enter the code.';
        el('code').value = '';
        showPanel('code-panel');
        el('code').focus();
        message('');
        return;
      case 'need_captcha':
        answerKind = 'captcha';
        el('captcha-message').textContent = data.message || 'Type the characters you see.';
        el('captcha-image').src = '/alexa/form_login/captcha/?t=' + Date.now();
        el('captcha').value = '';
        showPanel('captcha-panel');
        el('captcha').focus();
        message('');
        return;
      case 'need_choice': {
        choiceKind = data.kind || 'claimsoption';
        el('choice-message').textContent = data.message || 'Choose how to verify.';
        const options = el('choice-options');
        options.textContent = '';
        const list = Array.isArray(data.options) ? data.options : [];
        list.forEach((option, index) => {
          const label = document.createElement('label');
          label.className = 'choice-option';
          const radio = document.createElement('input');
          radio.type = 'radio';
          radio.name = 'choice';
          radio.value = option.index;
          radio.required = true;
          if (index === 0) radio.checked = true;
          label.appendChild(radio);
          label.appendChild(document.createTextNode(' ' + option.label));
          options.appendChild(label);
        });
        const manual = el('choice-manual');
        el('choice-manual-label').hidden = manual.hidden = list.length > 0;
        if (!list.length && data.raw) {
          const pre = document.createElement('p');
          pre.className = 'step-hint';
          pre.textContent = data.raw;
          options.appendChild(pre);
        }
        showPanel('choice-panel');
        if (list.length) el('choice-submit').focus();
        else manual.focus();
        message('');
        return;
      }
      case 'need_approval':
      case 'need_prompt':
        el('approval-message').textContent = data.message || 'Approve this sign-in on your phone, then continue.';
        showPanel('approval-panel');
        message('Waiting for the approval…');
        schedulePoll();
        return;
      case 'busy':
        stopPolling();
        reset();
        el('retry').hidden = false;
        message(data.message || 'The sign-in browser is busy. Try again in a minute.', true);
        return;
      default:  // error
        stopPolling();
        reset();
        el('retry').hidden = false;
        message(data.message || 'The sign-in could not continue. Try again.', true);
    }
  }

  function schedulePoll() {
    stopPolling();
    if (pollCount++ > 80) {
      handle({state: 'error', message: 'The approval did not arrive in time. Start again.'});
      return;
    }
    const id = run;
    pollTimer = setTimeout(async () => {
      if (id !== run) return;
      try {
        const data = provider === 'amazon'
          ? await api('/alexa/form_login/step/', {kind: 'poll'})
          : await api('/api/youtube/browser-session/form/poll', {});
        if (id !== run) return;
        handle(data);
      } catch (error) {
        if (id !== run) return;
        if (error.status === 401) { handle({state: 'error', message: error.message}); return; }
        schedulePoll();
      }
    }, 4000);
  }

  async function submit(event) {
    event?.preventDefault();
    if (busy) return;
    const id = ++run;
    busy = true;
    el('connect').disabled = true;
    el('retry').hidden = true;
    pollCount = 0;
    try {
      message(provider === 'amazon' ? 'Contacting Amazon…' : 'Opening the sign-in…');
      const email = el('email').value.trim();
      const password = el('password').value;
      el('password').value = '';
      const data = provider === 'amazon'
        ? await api('/alexa/form_login/', {email, password})
        : await api('/api/youtube/browser-session/form/start', {email, password});
      if (id !== run) return;
      handle(data);
    } catch (error) {
      if (id === run) handle({state: 'error', message: error.message});
    } finally {
      busy = false;
      el('connect').disabled = false;
    }
  }

  async function answer(path, body, button) {
    if (busy) return;
    const id = run;
    busy = true;
    if (button) button.disabled = true;
    message('Checking your answer…');
    try {
      const data = await api(path, body);
      if (id !== run) return;
      handle(data);
    } catch (error) {
      if (id === run) message(error.message, true);
    } finally {
      busy = false;
      if (button) button.disabled = false;
    }
  }

  el('credentials').addEventListener('submit', submit);
  el('code-submit').onclick = () => {
    const code = el('code').value.trim();
    if (!code) { el('code').focus(); return; }
    if (provider === 'amazon') answer('/alexa/form_login/step/', {kind: answerKind, value: code}, el('code-submit'));
    else answer('/api/youtube/browser-session/form/code', {code}, el('code-submit'));
  };
  el('captcha-submit').onclick = () => {
    const value = el('captcha').value.trim();
    if (!value) { el('captcha').focus(); return; }
    answer('/alexa/form_login/step/', {kind: 'captcha', value}, el('captcha-submit'));
  };
  el('choice-submit').onclick = () => {
    const checked = el('choice-options').querySelector('input[name="choice"]:checked');
    const manual = el('choice-manual');
    const value = (checked ? checked.value : manual.value).trim();
    if (value === '') { (checked ? el('choice-submit') : manual).focus(); return; }
    answer('/alexa/form_login/step/', {kind: choiceKind, value}, el('choice-submit'));
  };
  el('approval-check').onclick = () => {
    stopPolling();
    if (provider === 'amazon') answer('/alexa/form_login/step/', {kind: 'poll'}, el('approval-check'));
    else answer('/api/youtube/browser-session/form/poll', {}, el('approval-check'));
  };
  el('retry').onclick = () => {
    ++run;
    reset();
    el('retry').hidden = true;
    message('Start again when you are ready.');
    el('email').focus();
  };
  document.querySelector('.back').onclick = event => { event.preventDefault(); el('cancel').click(); };
  el('cancel').onclick = async () => {
    ++run;
    stopPolling();
    await cancelSignIn().catch(() => {});
    location.replace(returnTo || '/account#panel-providers');
  };
  function cancelSignIn() {
    return provider === 'amazon'
      ? api('/alexa/form_login/cancel/', {}, 8000)
      : api('/api/youtube/browser-session/form/stop', {}, 8000);
  }
  window.addEventListener('pagehide', () => {
    ++run;
    stopPolling();
    const options = {method: 'POST', credentials: 'same-origin',
                     headers: {'Content-Type': 'application/json'}, keepalive: true};
    if (provider === 'amazon') fetch('/alexa/form_login/cancel/', {...options, body: '{}'}).catch(() => {});
    else fetch('/api/youtube/browser-session/form/stop', {...options, body: '{}'}).catch(() => {});
  });
  el('email').focus();
})();
