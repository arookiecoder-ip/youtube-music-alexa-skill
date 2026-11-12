/* Password, email OTP and authenticator sign-in share one destination. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let mode = 'password', busy = false, sent = false, lockedIdentifier = false, pendingToken = '', resendAt = 0;
  const credentials = $('step-creds'), signIn = $('creds-btn');
  const emailCode = $('email-code'), toggle = $('otp-toggle');
  const setError = message => { $('status').textContent = message || ''; };
  const digits = input => { input.value = input.value.replace(/[^0-9]/g, '').slice(0, 6); };
  function refresh() {
    signIn.disabled = busy || (mode === 'otp' && (!sent || !/^[0-9]{6}$/.test(emailCode.value)));
    if (!toggle) return;
    toggle.disabled = busy;
    $('username').readOnly = mode === 'otp' && lockedIdentifier;
    $('send-otp').disabled = busy || Date.now() < resendAt;
    const remaining = Math.max(0, Math.ceil((resendAt - Date.now()) / 1000));
    $('resend-otp').disabled = busy || remaining > 0;
    $('resend-otp').textContent = remaining ? `Resend (${remaining}s)` : 'Resend';
  }
  async function post(payload) {
    const response = await fetch('/login/', {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const retry = Number(response.headers.get('Retry-After'));
      if (retry > 0 && payload.step === 'send_otp') resendAt = Date.now() + retry * 1000;
      throw new Error(typeof data.error === 'string' ? data.error : data.error?.message || `HTTP ${response.status}`);
    }
    return data;
  }
  async function swap(from, to) {
    if (!matchMedia('(prefers-reduced-motion: reduce)').matches && from.animate) {
      await from.animate([{opacity: 1, transform: 'translateY(0)'}, {opacity: 0, transform: 'translateY(-6px)'}],
        {duration: 150, easing: 'ease-in'}).finished;
    }
    from.hidden = true;
    to.hidden = false;
    if (!matchMedia('(prefers-reduced-motion: reduce)').matches && to.animate) {
      await to.animate([{opacity: 0, transform: 'translateY(6px)'}, {opacity: 1, transform: 'translateY(0)'}],
        {duration: 180, easing: 'ease-out'}).finished;
    }
  }
  async function switchMode() {
    if (busy) return;
    busy = true; mode = mode === 'password' ? 'otp' : 'password'; refresh(); setError('');
    try {
      await swap($(mode === 'otp' ? 'password-section' : 'otp-section'), $(mode === 'otp' ? 'otp-section' : 'password-section'));
      toggle.textContent = mode === 'otp' ? 'Login via password' : 'Login via OTP';
      toggle.setAttribute('aria-expanded', String(mode === 'otp'));
      if (mode === 'otp') $('password').value = '';
    } finally { busy = false; refresh(); }
  }
  toggle?.addEventListener('click', switchMode);
  $('username').addEventListener('input', () => {
    if (sent && mode === 'password') {
      sent = false; lockedIdentifier = false; emailCode.value = ''; $('send-otp').hidden = false; $('otp-entry').hidden = true;
    }
  });
  async function send() {
    if (busy || Date.now() < resendAt) return;
    if (!$('username').value.trim()) { setError('Enter your email or username first.'); $('username').focus(); return; }
    busy = true; refresh(); setError('');
    try {
      const data = await post({step: 'send_otp', username: $('username').value.trim()});
      resendAt = Date.now() + Math.max(60, Number(data.retry_after) || 60) * 1000;
      emailCode.value = ''; sent = true; lockedIdentifier = true;
      $('otp-hint').textContent = data.message;
      if (!$('send-otp').hidden) await swap($('send-otp'), $('otp-entry'));
      emailCode.focus();
    } catch (error) { setError(error.message); }
    finally { busy = false; refresh(); }
  }
  $('send-otp')?.addEventListener('click', send);
  $('resend-otp')?.addEventListener('click', send);
  emailCode?.addEventListener('input', () => { digits(emailCode); refresh(); });
  function result(data) {
    if (!data.totp_required) { location.href = data.next || '/home'; return; }
    pendingToken = data.token;
    credentials.classList.remove('active'); $('step-code').classList.add('active');
    $('code').value = ''; $('code-btn').disabled = true; $('code').focus();
  }
  credentials.addEventListener('submit', async event => {
    event.preventDefault(); if (signIn.disabled || busy) return;
    busy = true; refresh(); setError('');
    try {
      result(await post({username: $('username').value.trim(), next: NEXT_TARGET,
        ...(mode === 'otp' ? {step: 'email_otp', code: emailCode.value} : {password: $('password').value})}));
    } catch (error) { setError(error.message); }
    finally { busy = false; refresh(); }
  });
  function back() {
    pendingToken = ''; $('step-code').classList.remove('active'); credentials.classList.add('active');
    setError(''); refresh();
  }
  $('back-btn').addEventListener('click', back);
  $('code-btn').disabled = true;
  $('code').addEventListener('input', () => { digits($('code')); $('code-btn').disabled = busy || !/^[0-9]{6}$/.test($('code').value); });
  $('step-code').addEventListener('submit', async event => {
    event.preventDefault(); if (busy || $('code-btn').disabled) return;
    busy = true; $('code-btn').disabled = true; $('back-btn').disabled = true; setError('');
    try { result(await post({step: 'totp', token: pendingToken, code: $('code').value})); }
    catch (error) { back(); setError(error.message); }
    finally { busy = false; $('back-btn').disabled = false; refresh(); }
  });
  if (toggle && credentials.dataset.otpStart === '1') {
    switchMode().then(() => {
      if (credentials.dataset.codeReady === '1') {
        sent = true; $('send-otp').hidden = true; $('otp-entry').hidden = false;
        $('otp-hint').textContent = 'Enter the code from your email, or request a new one.';
        refresh();
      }
    });
  }
  setInterval(refresh, 1000);
  refresh();
})();
