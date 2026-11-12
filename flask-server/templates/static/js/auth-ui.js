/* Shared progressive enhancement for authentication surfaces. */
(function () {
  'use strict';
  document.querySelectorAll('input[type=password]').forEach((input) => {
    const field = document.createElement('span');
    field.className = 'password-field';
    input.parentNode.insertBefore(field, input);
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'password-reveal';
    const eye = '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>';
    const eyeOff = '<path d="m3 3 18 18M10.6 10.6a2 2 0 0 0 2.8 2.8M9.9 5.2A12 12 0 0 1 12 5c6.5 0 10 7 10 7a18 18 0 0 1-3.1 4M6.2 6.2A20 20 0 0 0 2 12s3.5 7 10 7a12 12 0 0 0 5.8-1.8"/>';
    function show(visible) {
      input.type = visible ? 'text' : 'password';
      button.setAttribute('aria-label', visible ? 'Hide password' : 'Show password');
      button.setAttribute('aria-pressed', String(visible));
      button.innerHTML = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">' + (visible ? eyeOff : eye) + '</svg>';
    }
    show(false);
    button.addEventListener('click', () => show(input.type === 'password'));
    field.append(input, button);
    input.closest('dialog')?.addEventListener('close', () => show(false));
  });
  // Admin forms submit through their original routes after each proof succeeds.
  // Both panels share the same session; bootstrap the account CSRF token for checks.
  async function adminCheck(path, payload) {
    const account = await fetch('/account', {credentials: 'same-origin'});
    if (!account.ok || account.redirected) throw new Error('Sign in again before changing security settings.');
    const doc = new DOMParser().parseFromString(await account.text(), 'text/html');
    const csrf = doc.body.dataset.csrf;
    if (!csrf) throw new Error('Your session expired. Sign in again.');
    const response = await fetch(path, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type':'application/json','X-CSRF-Token':csrf}, body: JSON.stringify(payload)});
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error?.message || 'Verification failed. Try again.');
  }
  function adminStage(id, startSelector, validate) {
    const form = document.getElementById(id);
    const start = form?.querySelector(startSelector);
    const dialog = form?.closest('dialog');
    const submit = dialog?.querySelector('button[type=submit][form="'+id+'"]');
    if (!form || !start || !dialog || !submit) return;
    const next = document.createElement('div'); next.className = 'stack';
    let node = start;
    while (node) { const following = node.nextSibling; next.appendChild(node); node = following; }
    form.appendChild(next);
    const first = document.createElement('div'); first.className = 'stack';
    while (form.firstChild !== next) first.appendChild(form.firstChild);
    form.insertBefore(first, next);
    const gate = document.createElement('button'); gate.type = 'button'; gate.className = 'btn primary'; gate.textContent = 'Verify and continue';
    const error = document.createElement('p'); error.className = 'field-error'; error.setAttribute('role', 'alert');
    form.insertBefore(gate, next); form.insertBefore(error, next);
    let verified = false;
    function reset() {
      verified = false; first.hidden = false; next.hidden = true; gate.hidden = false; submit.hidden = true; error.textContent = '';
      next.querySelectorAll('[required]').forEach(field => { field.required = false; field.dataset.flowRequired = 'true'; });
    }
    reset();
    async function advance() {
      gate.disabled = true; error.textContent = '';
      try {
        await validate(form); verified = true; first.hidden = true; next.hidden = false; gate.hidden = true; submit.hidden = false;
        next.querySelectorAll('[data-flow-required]').forEach(field => { field.required = true; });
        next.querySelector('input')?.focus();
      } catch (err) { error.textContent = err.message || 'Could not verify. Try again.'; }
      finally { gate.disabled = false; }
    }
    gate.addEventListener('click', advance);
    form.addEventListener('submit', event => { if (!verified) { event.preventDefault(); event.stopImmediatePropagation(); advance(); } }, true);
    dialog.addEventListener('close', () => {
      form.querySelectorAll('input[autocomplete=current-password],input[autocomplete=new-password],input[autocomplete=one-time-code]').forEach(field => { field.value = ''; });
      reset();
    });
  }
  adminStage('password-form', '.field:has(#pw_new)', form => adminCheck('/api/account/identity/check', {current_password:form.elements.current_password.value}));
  adminStage('reset-form', '.field:has(#reset_new)', form => adminCheck('/api/account/code/check', {purpose:'reset',code:form.elements.code.value}));
  const emailForm = document.getElementById('email-form');
  if (emailForm) {
    const field = document.createElement('div'); field.className = 'field';
    const code = emailForm.querySelector('#email_old_code');
    const label = emailForm.querySelector('label[for=email_old_code]');
    if (code && label) {
      field.append(label, code); emailForm.insertBefore(field, emailForm.querySelector('.field'));
      adminStage('email-form', '.field:has(#new_email)', form => adminCheck('/api/account/code/check', {purpose:'reauth',code:form.elements.email_code.value}));
    }
  }

  const form = document.querySelector('form[action="/reset-password"]');
  if (!form) return;
  const start = form.querySelector('label[for=password]');
  const submit = form.querySelector('[data-submit]');
  if (!start || !submit) return;
  const next = document.createElement('div');
  let node = start;
  while (node) { const following = node.nextSibling; next.appendChild(node); node = following; }
  form.appendChild(next); next.hidden = true;
  next.querySelectorAll('[required]').forEach(field => { field.required = false; field.dataset.flowRequired = 'true'; });
  const gate = document.createElement('button'); gate.className = 'primary'; gate.type = 'button'; gate.textContent = 'Verify code';
  const error = document.createElement('p'); error.className = 'field-error'; error.setAttribute('role', 'alert');
  form.insertBefore(gate, next); form.insertBefore(error, next);
  const title = form.querySelector('.step-title'); if (title) title.textContent = 'Verify your reset code';
  const hint = form.querySelector('.step-hint'); if (hint) hint.textContent = 'Enter your account and the code from your email. Then choose a new password.';
  let verified = false;
  async function check() {
    gate.disabled = true; error.textContent = '';
    const data = new FormData(form); data.set('verify_only', '1');
    try {
      const response = await fetch(form.action, {method: 'POST', body: data, credentials: 'same-origin', headers: {'Accept': 'application/json'}});
      let result; try { result = await response.json(); } catch (_) { result = {}; }
      if (!response.ok || !result.ok) { error.textContent = result.error?.message || 'This code could not be verified. Check the account and code, or send a new one.'; return; }
      verified = true; gate.hidden = true; next.hidden = false;
      form.querySelectorAll('label[for=identifier],#identifier,label[for=code],#code').forEach(item => { item.hidden = true; });
      next.querySelectorAll('[data-flow-required]').forEach(field => { field.required = true; });
      if (title) title.textContent = 'Choose your new password'; if (hint) hint.textContent = 'Your code is verified. Set your new password to finish.';
      next.querySelector('input')?.focus();
    } catch (_) { error.textContent = 'Could not connect. Try again.'; }
    finally { gate.disabled = false; }
  }
  gate.addEventListener('click', check);
  form.addEventListener('submit', (event) => { if (!verified) { event.preventDefault(); event.stopImmediatePropagation(); check(); } }, true);
})();
