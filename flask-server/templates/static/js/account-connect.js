/* Connecting Amazon or YouTube Music from /account.
 *
 * The provider sign-in runs on this server in a background browser: Music Box
 * submits each answer into the provider's own flow and asks here only for the
 * things that must come from the person. It is the same flow as
 * /accounts/<provider>, in a popup on the page the connection belongs to. The
 * trigger links keep that page as their href, so without this script they still
 * go there and the standalone flow is unchanged. */
(function () {
  'use strict';

  const dialog = document.getElementById('dialog-connect');
  if (!dialog) return;

  const q = (id) => document.getElementById(id);
  const PROVIDERS = {
    amazon: {
      title: 'Connect Amazon',
      label: 'Amazon email or mobile number',
      intro: 'Use the Amazon account for your Echo and private skill in Developer Console. Your Music Box email can differ.',
      hint: 'The sign-in runs on this server in the background. Music Box keeps only '
        + 'the resulting Alexa session — your Amazon password is never stored.',
    },
    youtube: {
      title: 'Connect YouTube Music',
      label: 'Google email',
      intro: 'Sign in for your library, likes and recommendations.',
      hint: 'The sign-in runs on this server in the background. Music Box keeps only '
        + 'the resulting YouTube Music session — your Google password is never stored.',
    },
  };
  const PANELS = ['connect-code-panel', 'connect-captcha-panel', 'connect-method-panel',
                  'connect-choice-panel', 'connect-approval-panel'];
  let provider = 'amazon';
  let run = 0, busy = false, active = false, pollTimer = null, pollCount = 0;
  // Set when the popup closes while the opening request is still in flight: the
  // answer may reveal a flow that this page never got to mark active.
  let abandoned = false;
  let answerKind = 'securitycode', choiceKind = 'claimsoption';
  // Google's picture grid: each tap is kept as a fraction of the picture, so the
  // same marks survive every width the popup is drawn at.
  let captchaPoints = [];

  // Amazon's scripted steps and Google's challenge pages can each take well over
  // a minute, so these calls get a far longer leash than ordinary API traffic.
  let attemptId = "";
  async function api(path, body, timeout = 150000) {
    const requestRun = run;
    const starts = path.endsWith("/form/start") || path === "/alexa/form_login/";
    if (starts) attemptId = "";
    else if (body !== undefined && attemptId) body = {attempt_id: attemptId, ...body};
    if (body === undefined && attemptId) path += (path.includes("?") ? "&" : "?") + "attempt_id=" + encodeURIComponent(attemptId);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(path, {
        credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
        headers: { 'Content-Type': 'application/json' },
        ...(body === undefined ? {} : { method: 'POST', body: JSON.stringify(body) }),
      });
      let data;
      try { data = await response.json(); } catch (_) { data = {}; }
      if (data.attempt_id && requestRun === run) attemptId = data.attempt_id;
      if (!response.ok) {
        // A step the provider is waiting on comes back as a state, not a failure.
        if (data && typeof data.state === 'string') return data;
        const message = response.status === 401
          ? 'Your Music Box session has ended. Sign in again.'
          : (typeof data.error === 'string' ? data.error
            : (data.error && data.error.message));
        const error = new Error(message || 'Connection failed. Try again.');
        error.status = response.status;
        throw error;
      }
      return data;
    } finally {
      clearTimeout(timer);
    }
  }

  function say(text, error) {
    const box = q('connect-status');
    box.textContent = text || '';
    box.classList.toggle('error', Boolean(error));
  }

  // What the server is doing right now, with the ring that proves it is still
  // doing it. The status line stays for the answer; this line is the wait.
  function working(text) {
    q('connect-working-text').textContent = text || '';
    q('connect-working').hidden = false;
  }

  function stopWorking() {
    q('connect-working').hidden = true;
    q('connect-working-text').textContent = '';
  }

  function stopPolling() {
    clearTimeout(pollTimer);
    pollTimer = null;
  }

  function clearCaptchaPoints() {
    captchaPoints = [];
    const frame = q('connect-captcha-frame');
    Array.prototype.forEach.call(frame.querySelectorAll('.captcha-mark'),
      (mark) => mark.remove());
    q('connect-captcha-clear').hidden = true;
  }

  function reset() {
    stopPolling();
    stopWorking();
    q('connect-intro').hidden = false;
    q('connect-steps').hidden = true;
    PANELS.forEach((id) => { q(id).hidden = true; });
    q('connect-setup').hidden = false;
    q('connect-code-different').hidden = true;
    q('connect-approval-different').hidden = true;
    clearCaptchaPoints();
  }

  function showPanel(id) {
    // Once the provider is asking a question, the sentence that described signing
    // in is behind the person, not ahead of them; it comes back with `reset`.
    q('connect-intro').hidden = true;
    q('connect-setup').hidden = true;
    q('connect-steps').hidden = false;
    PANELS.forEach((panel) => { q(panel).hidden = panel !== id; });
  }

  function renderMethods(data) {
    const options = q('connect-method-options');
    options.textContent = '';
    const list = Array.isArray(data.options) ? data.options : [];
    list.forEach((option, index) => {
      const label = document.createElement('label');
      label.className = 'connect-choice';
      const radio = document.createElement('input');
      radio.type = 'radio';
      radio.name = 'method';
      radio.value = String(option.value || '');
      radio.required = true;
      if (index === 0) radio.checked = true;
      label.appendChild(radio);
      label.appendChild(document.createTextNode(' ' + (option.label || option.value)));
      options.appendChild(label);
    });
    q('connect-method-message').textContent = data.message || 'Choose how to verify.';
    showPanel('connect-method-panel');
    if (list.length) q('connect-method-submit').focus();
  }

  function cancelSignIn() {
    // Closing the popup must not leave a browser sitting on a provider page. The
    // request is fire-and-forget so the dialog can close immediately.
    const path = provider === 'amazon'
      ? '/alexa/form_login/cancel/'
      : '/api/youtube/browser-session/form/stop';
    fetch(path, {
      method: 'POST', credentials: 'same-origin', keepalive: true,
      headers: { 'Content-Type': 'application/json' }, body: '{}',
    }).catch(() => {});
  }

  function finish() {
    stopPolling();
    active = false;
    say('');
    if (dialog.open && typeof dialog.close === 'function') dialog.close();
    document.dispatchEvent(new CustomEvent('account:provider-connected', {
      detail: { provider },
    }));
  }

  function handle(data) {
    if (!data || !data.state) {
      reset();
      q('connect-retry').hidden = false;
      say('Connection failed. Try again.', true);
      return;
    }
    // An approval is still being waited on while the poll runs; hiding and
    // showing the ring every four seconds would read as a stutter, not progress.
    if (data.state !== 'need_prompt' && data.state !== 'need_approval') stopWorking();
    switch (data.state) {
      case 'connected':
        finish();
        return;
      case 'need_code':
        answerKind = data.answer || 'securitycode';
        q('connect-code-message').textContent = data.message || 'Enter the code.';
        // Amazon may ask for a two-step code or a one-time password; the label
        // and hint say which one Amazon is waiting for.
        q('connect-code-label').textContent = data.label || 'Code';
        q('connect-code-hint').textContent = data.hint || '';
        q('connect-code-hint').hidden = !data.hint;
        q('connect_code').value = '';
        q('connect-code-different').hidden = !data.different;
        showPanel('connect-code-panel');
        q('connect_code').focus();
        say('');
        return;
      case 'need_captcha': {
        answerKind = 'captcha';
        q('connect-captcha-message').textContent =
          data.message || 'Type the characters you see.';
        q('connect_captcha_image').src = provider === 'amazon'
          ? '/alexa/form_login/captcha/?t=' + Date.now()
          : '/api/youtube/browser-session/form/captcha?t=' + Date.now();
        q('connect_captcha').value = '';
        clearCaptchaPoints();
        // Amazon asks for letters; Google usually asks for pictures. A text box
        // that the check cannot use is worse than none, so it follows the answer.
        const typed = data.text !== false;
        q('connect-captcha-label').hidden = !typed;
        q('connect_captcha').hidden = !typed;
        q('connect-captcha-hint').hidden = !data.hint;
        q('connect-captcha-hint').textContent = data.hint || '';
        q('connect-captcha-frame').classList.toggle('tappable', !typed);
        showPanel('connect-captcha-panel');
        if (typed) q('connect_captcha').focus();
        else q('connect-captcha-submit').focus();
        say('');
        return;
      }
      case 'need_method':
        renderMethods(data);
        say('');
        return;
      case 'need_choice': {
        choiceKind = data.kind || 'claimsoption';
        q('connect-choice-message').textContent =
          data.message || 'Choose how to verify.';
        const options = q('connect-choice-options');
        options.textContent = '';
        const list = Array.isArray(data.options) ? data.options : [];
        list.forEach((option, index) => {
          const label = document.createElement('label');
          label.className = 'connect-choice';
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
        const manual = q('connect_choice_manual');
        q('connect-choice-manual-label').hidden = manual.hidden = list.length > 0;
        if (!list.length && data.raw) {
          const pre = document.createElement('p');
          pre.className = 'field-help';
          pre.textContent = data.raw;
          options.appendChild(pre);
        }
        showPanel('connect-choice-panel');
        if (list.length) q('connect-choice-submit').focus();
        else manual.focus();
        say('');
        return;
      }
      case 'need_approval':
      case 'need_prompt':
        q('connect-approval-message').textContent =
          data.message || 'Approve this sign-in on your phone, then continue.';
        q('connect-approval-different').hidden = !data.different;
        showPanel('connect-approval-panel');
        working('Waiting for you to confirm on your phone…');
        say('');
        schedulePoll();
        return;
      case 'busy':
        stopPolling();
        reset();
        q('connect-retry').hidden = false;
        say(data.message || 'The sign-in browser is busy. Try again in a minute.', true);
        return;
      default:
        stopPolling();
        reset();
        q('connect-retry').hidden = false;
        say(data.message || 'The sign-in could not continue. Try again.', true);
    }
  }

  function schedulePoll() {
    stopPolling();
    if (pollCount++ > 80) {
      handle({ state: 'error', message: 'The approval did not arrive in time. Start again.' });
      return;
    }
    const id = run;
    pollTimer = setTimeout(async () => {
      if (id !== run) return;
      try {
        const data = provider === 'amazon'
          ? await api('/alexa/form_login/step/', { kind: 'poll' })
          : await api('/api/youtube/browser-session/form/poll', {});
        if (id !== run) return;
        handle(data);
      } catch (error) {
        if (id !== run) return;
        if (error.status === 401) { handle({ state: 'error', message: error.message }); return; }
        schedulePoll();
      }
    }, 4000);
  }

  async function submit(event) {
    event.preventDefault();
    if (busy) return;
    const id = ++run;
    busy = true;
    q('connect-submit').disabled = true;
    q('connect-retry').hidden = true;
    pollCount = 0;
    try {
      say('');
      working(provider === 'amazon' ? 'Contacting Amazon…' : 'Signing in to Google…');
      const email = q('connect_email').value.trim();
      const password = q('connect_password').value;
      q('connect_password').value = '';
      const data = provider === 'amazon'
        ? await api('/alexa/form_login/', { email, password })
        : await api('/api/youtube/browser-session/form/start', { email, password });
      if (id !== run) {
        // The popup closed while this request was in flight. If it started a flow
        // after all, stop it now; busy/error/connected mean there is nothing to stop.
        if (abandoned && data && data.state && data.state !== 'busy'
            && data.state !== 'error' && data.state !== 'connected') {
          cancelSignIn();
        }
        abandoned = false;
        return;
      }
      abandoned = false;
      // Only a live flow needs cancelling on close. `connected` has already
      // saved the session and `error`/`busy` left nothing running on the server.
      if (data && data.state && data.state !== 'busy'
          && data.state !== 'error' && data.state !== 'connected') active = true;
      handle(data);
    } catch (error) {
      if (id === run) handle({ state: 'error', message: error.message });
    } finally {
      busy = false;
      q('connect-submit').disabled = false;
    }
  }

  async function answer(path, body, button) {
    if (busy) return;
    const id = run;
    busy = true;
    if (button) button.disabled = true;
    say('');
    working('Checking your answer…');
    try {
      const data = await api(path, body);
      if (id !== run) return;
      handle(data);
    } catch (error) {
      if (id === run) {
        stopWorking();
        say(error.message, true);
      }
    } finally {
      busy = false;
      if (button) button.disabled = false;
    }
  }

  // Google's first challenge page can list only the phone prompt. This asks the
  // server to open the full chooser, then the popup offers its three answers.
  async function askMethods(button) {
    if (busy) return;
    const id = run;
    busy = true;
    stopPolling();
    if (button) button.disabled = true;
    say('');
    working('Finding other ways to verify…');
    try {
      const data = await api('/api/youtube/browser-session/form/methods', {});
      if (id !== run) return;
      handle(data);
    } catch (error) {
      if (id === run) {
        stopWorking();
        say(error.message, true);
      }
    } finally {
      busy = false;
      if (button) button.disabled = false;
    }
  }

  function open(name) {
    if (name === 'amazon' && document.body.dataset.alexaEnabled !== 'true') return;
    provider = name;
    const words = PROVIDERS[name];
    ++run;
    busy = false;
    active = false;
    abandoned = false;
    stopPolling();
    say('');
    q('dialog-connect-title').textContent = words.title;
    q('connect-intro').textContent = words.intro;
    q('connect-email-label').textContent = words.label;
    // Amazon also accepts mobile-number sign-ins; Google uses an email.
    q('connect_email').type = name === 'amazon' ? 'text' : 'email';
    q('connect-hint').textContent = words.hint;
    q('connect-credentials').reset();
    q('connect-retry').hidden = true;
    reset();
    if (typeof dialog.showModal === 'function') {
      if (!dialog.open) dialog.showModal();
    } else {
      dialog.setAttribute('open', '');
    }
    q('connect_email').focus();
  }

  Array.prototype.forEach.call(document.querySelectorAll('a[data-connect]'), (link) => {
    link.addEventListener('click', (event) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey
          || event.altKey) return;
      const name = link.getAttribute('data-connect');
      if (!PROVIDERS[name]) return;
      event.preventDefault();
      open(name);
    });
  });

  dialog.addEventListener('close', () => {
    ++run;
    stopPolling();
    if (active) cancelSignIn();
    if (busy) abandoned = true;
    active = false;
    busy = false;
    say('');
    q('connect-credentials').reset();
    reset();
  });

  window.addEventListener('pagehide', () => {
    if (!active) return;
    ++run;
    stopPolling();
    cancelSignIn();
    active = false;
  });

  q('connect-credentials').addEventListener('submit', submit);
  q('connect-code-submit').addEventListener('click', () => {
    const code = q('connect_code').value.trim();
    if (!code) { q('connect_code').focus(); return; }
    if (provider === 'amazon') {
      answer('/alexa/form_login/step/', { kind: answerKind, value: code },
        q('connect-code-submit'));
    } else {
      answer('/api/youtube/browser-session/form/code', { code },
        q('connect-code-submit'));
    }
  });
  q('connect-captcha-submit').addEventListener('click', () => {
    const button = q('connect-captcha-submit');
    const value = q('connect_captcha').value.trim();
    if (provider === 'amazon') {
      if (!value) { q('connect_captcha').focus(); return; }
      answer('/alexa/form_login/step/', { kind: 'captcha', value }, button);
      return;
    }
    if (captchaPoints.length) {
      answer('/api/youtube/browser-session/form/captcha', { points: captchaPoints }, button);
    } else if (value) {
      answer('/api/youtube/browser-session/form/captcha', { text: value }, button);
    } else if (!q('connect_captcha').hidden) {
      q('connect_captcha').focus();
    }
  });
  q('connect_captcha_image').addEventListener('click', (event) => {
    const frame = q('connect-captcha-frame');
    if (!frame.classList.contains('tappable')) return;
    const image = event.currentTarget;
    const rect = image.getBoundingClientRect();
    const x = (event.clientX - rect.left) / rect.width;
    const y = (event.clientY - rect.top) / rect.height;
    if (x < 0 || x > 1 || y < 0 || y > 1 || captchaPoints.length >= 20) return;
    captchaPoints.push([Number(x.toFixed(4)), Number(y.toFixed(4))]);
    const mark = document.createElement('span');
    mark.className = 'captcha-mark';
    mark.textContent = String(captchaPoints.length);
    mark.style.left = (x * 100).toFixed(2) + '%';
    mark.style.top = (y * 100).toFixed(2) + '%';
    frame.appendChild(mark);
    q('connect-captcha-clear').hidden = false;
  });
  q('connect-captcha-clear').addEventListener('click', clearCaptchaPoints);
  q('connect-method-submit').addEventListener('click', () => {
    const checked = q('connect-method-options')
      .querySelector('input[name="method"]:checked');
    if (!checked) return;
    answer('/api/youtube/browser-session/form/method', { method: checked.value },
      q('connect-method-submit'));
  });
  q('connect-code-different').addEventListener('click', (event) => {
    askMethods(event.currentTarget);
  });
  q('connect-approval-different').addEventListener('click', (event) => {
    askMethods(event.currentTarget);
  });
  q('connect-choice-submit').addEventListener('click', () => {
    const checked = q('connect-choice-options')
      .querySelector('input[name="choice"]:checked');
    const manual = q('connect_choice_manual');
    const value = (checked ? checked.value : manual.value).trim();
    if (value === '') { (checked ? q('connect-choice-submit') : manual).focus(); return; }
    answer('/alexa/form_login/step/', { kind: choiceKind, value },
      q('connect-choice-submit'));
  });
  q('connect-approval-check').addEventListener('click', () => {
    stopPolling();
    if (provider === 'amazon') {
      answer('/alexa/form_login/step/', { kind: 'poll' }, q('connect-approval-check'));
    } else {
      answer('/api/youtube/browser-session/form/poll', {}, q('connect-approval-check'));
    }
  });
  q('connect-retry').addEventListener('click', () => {
    ++run;
    reset();
    q('connect-retry').hidden = true;
    say('Start again when you are ready.');
    q('connect_email').focus();
  });
})();
