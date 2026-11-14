(() => {
  'use strict';
  const provider = document.body.dataset.provider;
  const asToken = document.body.dataset.as || '';
  const returnTo = document.body.dataset.returnTo || '';
  const el = id => document.getElementById(id);
  const panels = ['code-panel', 'captcha-panel', 'choice-panel', 'approval-panel', 'method-panel'];
  // Amazon's scripted steps and Google's challenge pages can each take well
  // over a minute to answer, so the form calls get a far longer leash than
  // ordinary API traffic.
  let run = 0, busy = false, pollTimer = null, pollCount = 0;
  let answerKind = 'securitycode', choiceKind = 'claimsoption';

  let attemptId = "", proxyMode = false, proxyAttempt = "";
  let captchaPoints = [];
  async function api(path, body, timeout = 150000) {
    const requestRun = run;
    const starts = path.endsWith("/form/start") || path === "/alexa/form_login/";
    if (starts) attemptId = "";
    else if (body !== undefined && attemptId) body = {attempt_id: attemptId, ...body};
    if (body === undefined && attemptId) path += (path.includes("?") ? "&" : "?") + "attempt_id=" + encodeURIComponent(attemptId);
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
      if (data.attempt_id && requestRun === run) attemptId = data.attempt_id;
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
  function working(text) {
    el('working-text').textContent = text;
    el('working').hidden = false;
    el('setup').hidden = el('steps').hidden = true;
    el('fallback').hidden = el('retry').hidden = true;
    document.querySelector('.card').setAttribute('aria-busy', 'true');
  }
  function stopWorking() {
    el('working').hidden = true;
    document.querySelector('.card').removeAttribute('aria-busy');
  }
  function reset() {
    stopWorking();
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
    stopWorking();
    el('fallback').hidden = true;
    if (!data || !data.state) {
      message('Connection failed. Try again.', true);
      reset();
      el('retry').hidden = false;
      el('fallback').hidden = provider !== 'amazon';
      return;
    }
    switch (data.state) {
      case 'connected':
        finish();
        return;
      case 'need_code':
        answerKind = data.answer || 'securitycode';
        el('code-message').textContent = data.message || 'Enter the code.';
        // Amazon's two-step page and its one-time-password page each say what
        // they want; Google's states have no label of their own.
        el('code-label').textContent = data.label || 'Code';
        el('code-hint').textContent = data.hint || '';
        el('code-hint').hidden = !data.hint;
        el('code').value = '';
        el('code-different').hidden = !data.different;
        showPanel('code-panel');
        el('code').focus();
        message('');
        return;
      case 'need_method': {
        el('method-message').textContent = data.message || 'Choose how to verify.';
        const options = el('method-options');
        options.textContent = '';
        (data.options || []).forEach((option, index) => {
          const label = document.createElement('label');
          label.className = 'choice-option';
          const radio = document.createElement('input');
          radio.type = 'radio'; radio.name = 'method'; radio.value = option.value; radio.checked = index === 0;
          label.append(radio, document.createTextNode(' ' + (option.label || option.value)));
          options.appendChild(label);
        });
        showPanel('method-panel');
        message('');
        return;
      }
      case 'need_captcha':
        answerKind = 'captcha';
        el('captcha-message').textContent = data.message || 'Type the characters you see.';
        captchaPoints = [];
        el('captcha-selection').textContent = '';
        el('captcha-clear').hidden = true;
        el('captcha').hidden = el('captcha-label').hidden = data.text === false;
        el('captcha-image').src = (provider === 'amazon' ? '/alexa/form_login/captcha/?t=' : '/api/youtube/browser-session/form/captcha?t=') + Date.now();
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
        el('approval-different').hidden = !data.different;
        message('Waiting for the approval…');
        schedulePoll();
        return;
      case 'busy':
        stopPolling();
        reset();
        el('retry').hidden = false;
        el('fallback').hidden = provider !== 'amazon';
        message(data.message || 'The sign-in browser is busy. Try again in a minute.', true);
        return;
      default:  // error
        stopPolling();
        reset();
        el('retry').hidden = false;
        el('fallback').hidden = provider !== 'amazon';
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
      message('');
      working(provider === 'amazon' ? 'Contacting Amazon…' : 'Signing in to Google…');
      const email = el('email').value.trim();
      const password = el('password').value;
      el('password').value = '';
      if (proxyMode && provider === 'amazon') {
        const result = await api('/alexa/proxy_login/', {email, password});
        if (id !== run) return;
        const target = new URL(result.login_url, location.href);
        if (target.origin !== location.origin || !target.pathname.startsWith('/alexa/proxy/')) throw new Error('Amazon returned an invalid sign-in page.');
        proxyAttempt = result.attempt_id || '';
        stopWorking();
        el('interactive').hidden = false;
        el('amazon-page').src = target.href;
        message('Finish the sign-in on the Amazon page below.');
        pollProxy(id);
        return;
      }
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
    working('Checking your answer…');
    message('');
    try {
      const data = await api(path, body);
      if (id !== run) return;
      handle(data);
    } catch (error) {
      if (id === run) handle({state: 'error', message: error.message});
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
    if (provider === 'youtube' && captchaPoints.length) {
      answer('/api/youtube/browser-session/form/captcha', {points: captchaPoints}, el('captcha-submit'));
    } else {
      if (!value) { el('captcha').focus(); return; }
      if (provider === 'amazon') answer('/alexa/form_login/step/', {kind: 'captcha', value}, el('captcha-submit'));
      else answer('/api/youtube/browser-session/form/captcha', {text: value}, el('captcha-submit'));
    }
  };
  el('captcha-image').onclick = event => {
    if (!el('captcha').hidden || captchaPoints.length >= 20) return;
    const rect = event.currentTarget.getBoundingClientRect();
    captchaPoints.push([Number(((event.clientX - rect.left) / rect.width).toFixed(4)), Number(((event.clientY - rect.top) / rect.height).toFixed(4))]);
    el('captcha-selection').textContent = captchaPoints.length + ' pictures selected';
    el('captcha-clear').hidden = false;
  };
  el('captcha-clear').onclick = () => { captchaPoints = []; el('captcha-selection').textContent = ''; el('captcha-clear').hidden = true; };
  el('method-submit').onclick = () => {
    const checked = el('method-options').querySelector('input:checked');
    if (checked) answer('/api/youtube/browser-session/form/method', {method: checked.value}, el('method-submit'));
  };
  ['code-different', 'approval-different'].forEach(id => {
    el(id).onclick = () => { stopPolling(); answer('/api/youtube/browser-session/form/methods', {}, el(id)); };
  });
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
  function pollProxy(id) {
    stopPolling();
    pollTimer = setTimeout(async () => {
      if (id !== run) return;
      try {
        const result = await api('/alexa/proxy_check/');
        if (id !== run) return;
        if (result.logged_in) { finish(); return; }
        if (result.error || !result.running || ++pollCount > 150) {
          el('interactive').hidden = true;
          handle({state: 'error', message: result.error || 'Amazon sign-in ended. Try again.'});
          return;
        }
        pollProxy(id);
      } catch (error) {
        if (id === run) { el('interactive').hidden = true; handle({state: 'error', message: error.message}); }
      }
    }, 2000);
  }
  el('captcha-image').onerror = () => handle({state: 'error', message: provider === 'amazon' ? 'The security picture could not load. Retry sign-in or use the Amazon page instead.' : 'The security picture could not load. Retry sign-in.'});
  el('fallback').onclick = () => {
    ++run;
    reset();
    proxyMode = true;
    el('fallback').hidden = el('retry').hidden = true;
    el('connect').textContent = 'Open Amazon sign-in';
    message('Enter your password again to continue on Amazon’s interactive page.');
    el('password').focus();
  };
  el('interactive-cancel').onclick = async () => {
    ++run;
    stopPolling();
    await api('/alexa/proxy_cancel/', {attempt_id: proxyAttempt}, 8000).catch(() => {});
    proxyAttempt = '';
    el('interactive').hidden = true;
    el('amazon-page').removeAttribute('src');
    reset();
  };
  el('retry').onclick = () => {
    ++run;
    reset();
    proxyMode = false;
    el('connect').textContent = 'Continue';
    el('fallback').hidden = true;
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
    if (proxyAttempt) return api('/alexa/proxy_cancel/', {attempt_id: proxyAttempt}, 8000);
    return provider === 'amazon'
      ? api('/alexa/form_login/cancel/', {}, 8000)
      : api('/api/youtube/browser-session/form/stop', {}, 8000);
  }
  window.addEventListener('pagehide', () => {
    ++run;
    stopPolling();
    const options = {method: 'POST', credentials: 'same-origin',
                     headers: {'Content-Type': 'application/json'}, keepalive: true};
    if (provider === 'amazon') fetch(proxyAttempt ? '/alexa/proxy_cancel/' : '/alexa/form_login/cancel/', {...options, body: JSON.stringify({attempt_id: proxyAttempt || attemptId})}).catch(() => {});
    else fetch('/api/youtube/browser-session/form/stop', {...options, body: JSON.stringify({attempt_id: attemptId})}).catch(() => {});
  });
  if (provider === 'amazon' && new URLSearchParams(location.search).get('interactive') === '1') {
    proxyMode = true;
    el('connect').textContent = 'Open Amazon sign-in';
    message('Enter your credentials to continue on Amazon’s interactive page.');
  }
  el('email').focus();
})();
