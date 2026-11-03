/* Your account (/account).
 *
 * The page is rendered complete by the server, so it is readable before this script
 * runs and stays usable without it. What happens here is the refresh after an action,
 * the rail that puts one section forward at a time, and the one question in a popup
 * that stands in front of anything which cannot be undone. Calls carry same-origin
 * cookies plus an X-CSRF-Token header read from body[data-csrf] rather than written
 * into a form, which is what lets the blueprint serve `script-src 'self'` with no
 * inline script anywhere. */
(function () {
  'use strict';

  const CSRF = document.body.dataset.csrf || '';
  const el = (id) => document.getElementById(id);

  // ------------------------------------------------------------------ chrome
  // A result belongs to the section that produced it: a message about a bad
  // password at the top of a page with six forms is not information. Each panel
  // owns a role="status" container, and a new result replaces that section's
  // previous one. Nothing is removed on a timer -- a message you did not read is
  // still the answer to the question you just asked.
  function note(kind, text, target) {
    const stack = el(target || 'notes');
    if (!stack || !text) return;
    const box = document.createElement('p');
    box.className = 'note ' + (kind || '');
    box.textContent = text;
    if (stack.id === 'notes') {
      stack.prepend(box);
      while (stack.children.length > 3) stack.removeChild(stack.lastChild);
    } else {
      stack.textContent = '';
      stack.appendChild(box);
    }
  }

  function setError(id, message) {
    const node = el(id);
    if (!node) return;
    node.textContent = message || '';
    node.hidden = !message;
  }

  // A note inside the dialog that asked the question: the panel's own `notes-*`
  // stack sits behind the modal, where a person in the middle of an action cannot
  // see it. `post()` writes here instead of there when it is given an id.
  function inlineNote(id, kind, text) {
    const box = el(id);
    if (!box) return;
    box.className = 'note ' + (kind || '');
    box.textContent = text || '';
    box.hidden = !text;
  }

  // A refusal in the delete flow is said over the popup that asked, because by the
  // time the server answers the flow may already be on its next question. The
  // stack has to live inside the open dialog: a native dialog paints in the top
  // layer, above anything appended to <body>. The newest answer replaces the last.
  function toast(text) {
    if (!text) return;
    const dialog = document.querySelector('dialog[open]');
    const host = (dialog && dialog.querySelector('.dialog-body')) || document.body;
    let stack = host.querySelector('.toast-stack');
    if (!stack) {
      stack = document.createElement('div');
      stack.className = 'toast-stack';
      stack.setAttribute('role', 'alert');
      host.appendChild(stack);
    }
    stack.textContent = '';
    const line = document.createElement('p');
    line.className = 'toast';
    line.textContent = text;
    stack.appendChild(line);
    setTimeout(() => {
      if (line.parentNode === stack) stack.removeChild(line);
      if (!stack.children.length && stack.parentNode) stack.removeChild(stack);
    }, 6000);
  }

  function badge(id, text, kind) {
    const host = el(id);
    if (!host) return;
    // The server renders <span id="prov-x"><span class="badge">..</span></span>, so
    // update the badge inside it rather than overwrite the wrapper.
    const node = host.classList.contains('badge') ? host : host.querySelector('.badge');
    const tag = node || document.createElement('span');
    tag.textContent = text;
    tag.className = 'badge' + (kind ? ' ' + kind : '');
    if (!node) host.appendChild(tag);
  }

  function shown(id, visible) {
    const node = el(id);
    if (node) node.hidden = !visible;
  }

  function on(id, event, handler) {
    const node = el(id);
    if (node) node.addEventListener(event, handler);
  }

  function stamp(seconds) {
    if (!seconds) return 'unknown';
    return new Date(seconds * 1000).toLocaleString(undefined, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  }

  function ago(seconds) {
    if (!seconds) return 'never';
    const delta = Math.max(0, Date.now() / 1000 - seconds);
    if (delta < 90) return 'just now';
    if (delta < 5400) return Math.round(delta / 60) + ' minutes ago';
    if (delta < 129600) return Math.round(delta / 3600) + ' hours ago';
    return Math.round(delta / 86400) + ' days ago';
  }

  // ------------------------------------------------------------------ client
  async function call(path, payload) {
    const response = await fetch(path, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF },
      body: JSON.stringify(payload || {}),
    });
    let json = {};
    try { json = await response.json(); } catch (err) { json = {}; }
    if (response.status === 401) {
      // The session ended under an open page. Explaining that and then failing
      // every later click would be worse than just going to the sign-in door.
      window.location.href = '/login/?next=' + encodeURIComponent('/account');
      throw new Error('signed-out');
    }
    return json.error || json;
  }

  // Posts one panel's form. `fields` maps the server's field names onto the ids of the
  // paragraphs that carry that field's complaint; `section` names the note container
  // the general verdict belongs to, or `inlineId` a note inside the dialog instead.
  async function post(path, payload, fields, button, section, inlineId) {
    const ids = fields || {};
    Object.keys(ids).forEach((key) => setError(ids[key], ''));
    if (button) button.disabled = true;
    let result;
    try {
      result = await call(path, payload);
    } catch (err) {
      if (button) button.disabled = false;
      return null;
    }
    if (button) button.disabled = false;
    if (!result || result.code) {
      const mapped = (result && result.fields) || {};
      Object.keys(mapped).forEach((key) => { if (ids[key]) setError(ids[key], mapped[key]); });
      const message = (result && result.message) || '';
      const wait = result && result.retry_after
        ? 'Try again in ' + result.retry_after + ' seconds.' : '';
      if (inlineId) {
        inlineNote(inlineId, 'bad', [message, wait].filter(Boolean).join(' '));
      } else {
        if (message) note('bad', message, section);
        if (wait) note('warn', wait, section);
      }
      return null;
    }
    return result;
  }

  // ------------------------------------------------------------------- state
  // The profile input a person may already be typing into is left alone once they
  // have touched it; a refresh of the other panels must not eat their text.
  const touched = { profile_email: false };

  function renderProfile(data) {
    if (!touched.profile_email) el('profile_email').value = data.email || '';
    const summary = el('profile-email');
    if (summary) summary.textContent = data.email || 'No email address';

    if (data.pending_email) badge('verify-badge', 'Confirm ' + data.pending_email, 'warn');
    else if (!data.email) badge('verify-badge', 'No email address', 'bad');
    else if (data.email_verified) badge('verify-badge', 'Confirmed', 'ok');
    else badge('verify-badge', 'Waiting for your code', 'warn');
    const pending = Boolean(data.email) && data.verification_pending;
    shown('resend-verify', pending);
    shown('form-verify-code', pending);
    if (pending && data.verification && data.verification.attempts_left !== undefined) {
      // Say how many tries are left before the code dies, rather than let a person
      // discover the ceiling by hitting it with a typo.
      const hint = el('verify-tries');
      if (hint) {
        hint.hidden = false;
        hint.textContent = data.verification.attempts_left
          ? data.verification.attempts_left + ' tries left on this code'
          : 'This code is spent. Send a new one.';
      }
    } else {
      shown('verify-tries', false);
    }

    const since = el('head-since');
    if (since) {
      since.textContent = (data.role_text || (data.is_admin ? 'Administrator' : 'Player'))
        + ' · joined ' + (data.created_text || stamp(data.created_at))
        + (data.created_by === 'self' ? ' · created by you' : '');
    }
  }

  function renderTotp(data) {
    const recoveryInput = el('begin_email_code');
    if (recoveryInput) recoveryInput.required = Boolean(data.totp_enabled);
    const words = data.totp_enabled ? 'On' : (data.totp_pending ? 'Setup waiting' : 'Off');
    const kind = data.totp_enabled ? 'ok' : (data.totp_pending ? 'warn' : '');
    badge('totp-badge', words, kind);
    badge('totp-state', words, kind);
    badge('totp-dialog-state', words, kind);

    // The begin form is both the way in and the way to a new app, so it is the
    // pending enrollment that hides it -- not the factor being on.
    shown('totp-begin-block', !data.totp_pending);
    shown('totp-disable-block', Boolean(data.totp_enabled));
    shown('enroll', Boolean(data.totp_pending));
    const changeLink = el('totp-change-link');
    if (changeLink) {
      changeLink.textContent = data.totp_pending ? 'Finish setup'
        : (data.totp_enabled ? 'Change authenticator' : 'Turn on authenticator');
      changeLink.setAttribute('data-view', data.totp_pending ? 'enroll' : 'change');
    }
    const removeLink = el('totp-remove-link');
    if (removeLink) removeLink.hidden = !data.totp_enabled;
    const title = el('totp-begin-title');
    if (title) title.textContent = data.totp_enabled ? 'Use a new authenticator app' : 'Turn it on';
    const begin = el('totp-begin-submit');
    if (begin) begin.textContent = data.totp_enabled ? 'Set up the new app' : 'Start setup';

    // The secret is handed out exactly once, at the moment it is generated. A page
    // reloaded mid-setup still has a live enrollment server-side, and the QR
    // endpoint redraws it without repeating the secret inside any JSON body.
    if (!data.totp_pending) return;
    el('enroll-secret').hidden = true;
    el('enroll-issuer').textContent = 'A setup is already waiting. Scan the code or confirm it, '
      + 'or cancel and start again.';
    el('qr-img').src = '/api/account/totp/qr?ref=' + Date.now();
    shown('qr-frame', true);
  }

  // The strip at the top answers "is this account ready to play?" so a person does
  // not have to read four panels to find out. It reports the same thing the panels
  // say, only nearer the top.
  function summaryServices(amazon, youtube) {
    const linked = (amazon ? 1 : 0) + (youtube ? 1 : 0);
    badge('sum-services',
      linked === 2 ? 'Both linked' : (linked ? '1 of 2 linked' : 'None linked'),
      linked === 2 ? 'ok' : (linked ? 'warn' : ''));
  }

  function renderProviders(data) {
    const rows = {};
    (data.providers || []).forEach((row) => { rows[row.provider] = row; });
    [['amazon', 'prov-amazon'], ['youtube', 'prov-youtube']].forEach(([name, id]) => {
      const row = rows[name];
      if (!row) badge(id, 'Not connected', '');
      else if (row.status === 'connected') badge(id, 'Connected', 'ok');
      else if (row.status === 'expired') badge(id, 'Needs signing in again', 'warn');
      else badge(id, row.status || 'Problem', 'bad');
    });
    summaryServices(
      Boolean(rows.amazon && rows.amazon.status === 'connected'),
      Boolean(rows.youtube && rows.youtube.status === 'connected'));

    // The stored rows say what this account was last able to save; the player's own
    // status endpoint says what actually works right now, per identity.
    fetch('/api/profile_status/?audio_check=0', { credentials: 'same-origin' })
      .then((response) => (response.ok ? response.json() : null))
      .then((status) => {
        if (!status) {
          el('providers-note').hidden = false;
          el('providers-note').textContent = 'Live status could not be read. '
            + 'The badges above show the last thing saved for this account.';
          return;
        }
        badge('prov-amazon', status.amazon_connected ? 'Connected' : 'Not connected',
          status.amazon_connected ? 'ok' : '');
        const youtubeOwn = !!status.youtube_own_session;
        badge('prov-youtube', youtubeOwn ? 'Connected' : 'Not connected',
          youtubeOwn ? 'ok' : '');
        shown('link-amazon', !status.amazon_connected);
        // A stored-but-broken row still has a secret to remove, so Disconnect stays
        // available even when the live check says the service is not connected.
        shown('disconnect-amazon', !!rows.amazon || !!status.amazon_connected);
        shown('link-youtube', !youtubeOwn);
        shown('disconnect-youtube', !!rows.youtube || youtubeOwn);
        // The server's own session is not *this* account being linked, so the strip
        // counts only what belongs to the person reading it.
        summaryServices(Boolean(status.amazon_connected), youtubeOwn);
      })
      .catch(() => {
        el('providers-note').hidden = false;
        el('providers-note').textContent = 'Live status could not be read.';
      });
  }

  function renderSessions(data) {
    const list = el('devices');
    if (!list) return;
    // This device first, marked, and never offered for sign-out: the button says
    // "sign out other devices" and must not take this one with it.
    const rows = (data.sessions || []).slice().sort((a, b) =>
      (b.current ? 1 : 0) - (a.current ? 1 : 0)
      || ((b.last_seen_at || 0) - (a.last_seen_at || 0)));
    list.textContent = '';
    rows.forEach((row) => {
      const item = document.createElement('li');

      const main = document.createElement('div');
      main.className = 'device-main';
      const line = document.createElement('div');
      line.className = 'device-line';
      const title = document.createElement('div');
      title.className = 'device-title';
      // `device` is the readable name the server read out of the header; the raw
      // header is the evidence for it and stays on the row as a secondary line.
      title.textContent = row.device || 'Unlisted browser';
      line.appendChild(title);
      if (row.current) {
        line.appendChild(document.createTextNode(' '));
        const mark = document.createElement('span');
        mark.className = 'this-one';
        mark.textContent = 'This device';
        line.appendChild(mark);
      }
      const meta = document.createElement('div');
      meta.className = 'device-meta';
      meta.textContent = 'seen ' + (row.seen_text || ago(row.last_seen_at))
        + (row.ip ? ' · from ' + row.ip : '')
        + ' · signed in ' + (row.signed_in_text || stamp(row.created_at));
      main.appendChild(line);
      main.appendChild(meta);
      if (row.device_ua) {
        const ua = document.createElement('div');
        ua.className = 'device-ua';
        ua.textContent = row.device_ua;
        main.appendChild(ua);
      }
      item.appendChild(main);

      if (!row.current) {
        const button = document.createElement('button');
        button.className = 'tiny destructive';
        button.type = 'button';
        button.textContent = 'Sign out';
        // The id the click reads off the row rather than a listener bound to it --
        // see `bindDeviceList`.
        button.dataset.revoke = row.id;
        item.appendChild(button);
      }
      list.appendChild(item);
    });
    const others = rows.filter((row) => !row.current).length;
    shown('devices-empty', others === 0);
    badge('sum-devices', others ? (rows.length + ' signed in') : 'This one only', '');
    const all = el('signout-others');
    if (all) all.disabled = others === 0;
  }

  function render(data) {
    renderProfile(data);
    renderTotp(data);
    renderProviders(data);
    renderSessions(data);
  }

  async function load() {
    try {
      const response = await fetch('/api/account', { credentials: 'same-origin' });
      if (response.status === 401) {
        window.location.href = '/login/?next=' + encodeURIComponent('/account');
        return;
      }
      const json = await response.json();
      if (json.error) { note('bad', json.error.message); return; }
      render(json);
    } catch (err) {
      note('bad', 'Could not read your account details. Reload the page.');
    }
  }

  // ----------------------------------------------------------------- actions
  function bind(id, handler) {
    on(id, 'submit', (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      // The submit control may sit in the dialog's foot, outside the form, and
      // reach it by the `form` attribute.
      const button = form.querySelector('[data-submit]')
        || document.querySelector('button[data-submit][form="' + form.id + '"]');
      handler(form, button);
    });
  }

  function value(form, name) {
    const node = form.elements[name];
    return node ? node.value : '';
  }

  function wipe(form, names) {
    names.forEach((name) => { if (form.elements[name]) form.elements[name].value = ''; });
  }

  function closeOwner(node) {
    const box = node && node.closest ? node.closest('dialog') : null;
    if (box && box.open) box.close();
  }

  // ---- password, changed with the one you have
  bind('form-password', async (form, button) => {
    const result = await post('/api/account/password', {
      current_password: value(form, 'current_password'),
      password: value(form, 'password'),
      confirm_password: value(form, 'confirm_password'),
    }, {
      current_password: 'err-current_password',
      password: 'err-password',
      confirm_password: 'err-confirm_password',
    }, button, 'notes-profile');
    if (!result) return;
    wipe(form, ['current_password', 'password', 'confirm_password']);
    const forced = el('must-change-note');
    if (forced) forced.remove();
    closeOwner(form);
    note('ok', result.message || 'Password changed.', 'notes-profile');
    load();
  });

  // ---- password, reset with a code when the old one is gone
  on('reset-send', 'click', async (event) => {
    const result = await post('/api/account/password/reset/send', {}, {},
      event.currentTarget, 'notes-profile', 'reset-note');
    if (!result) return;
    inlineNote('reset-note', result.note ? 'warn' : 'ok', result.note
      || 'A code is on its way to ' + result.sent_to
      + '. It works once and expires in a few minutes.');
    const code = el('reset_code');
    if (code) code.focus();
  });

  bind('form-password-reset', async (form, button) => {
    const tick = form.elements.sign_out_others;
    const result = await post('/api/account/password/reset', {
      code: value(form, 'code').replace(/[^0-9]/g, ''),
      password: value(form, 'password'),
      confirm_password: value(form, 'confirm_password'),
      sign_out_others: tick ? tick.checked : true,
    }, {
      code: 'err-reset_code',
      password: 'err-reset_password',
      confirm_password: 'err-reset_confirm',
    }, button, 'notes-profile', 'reset-note');
    if (!result) return;
    wipe(form, ['code', 'password', 'confirm_password']);
    inlineNote('reset-note', '', '');
    const forced = el('must-change-note');
    if (forced) forced.remove();
    closeOwner(form);
    note('ok', result.message || 'Password changed.', 'notes-profile');
    load();
  });

  // ---- email: one code proves the mailbox, for the change and for two-factor
  async function sendMailboxCode(button, target, codeId) {
    const result = await post('/api/account/email/reauth', {}, {}, button, null, target);
    if (!result) return;
    inlineNote(target, result.note ? 'warn' : 'ok', result.note
      || 'A code is on its way to ' + result.sent_to + '. Only the newest one works.');
    // The next thing the person has to do is type what just arrived.
    const input = el(codeId);
    if (input) input.focus();
  }

  on('reauth-send', 'click', (event) =>
    sendMailboxCode(event.currentTarget, 'reauth-note', 'email_change_code'));
  on('reauth-send-totp', 'click', (event) =>
    sendMailboxCode(event.currentTarget, 'totp-note', 'disable_email_code'));

  bind('form-email-change', async (form, button) => {
    const result = await post('/api/account/email/change', {
      email_code: value(form, 'email_code').replace(/[^0-9]/g, ''),
      current_password: value(form, 'current_password'),
      email: value(form, 'email'),
    }, {
      email_code: 'err-email_change_code',
      current_password: 'err-email_change_password',
      email: 'err-new_email',
    }, button, 'notes-profile', 'reauth-note');
    if (!result) return;
    wipe(form, ['email_code', 'current_password']);
    inlineNote('reauth-note', '', '');
    touched.profile_email = false;
    closeOwner(form);
    note(result.note ? 'warn' : 'ok',
      result.note || result.message || 'Email address changed.', 'notes-profile');
    load();
  });

  on('resend-verify', 'click', async (event) => {
    const result = await post('/api/account/email/verification', {}, {}, event.currentTarget,
      'notes-profile');
    if (result) note(result.note ? 'warn' : 'ok', result.note || 'That address is already confirmed.',
      'notes-profile');
  });

  bind('form-verify-code', async (form, button) => {
    const result = await post('/api/account/email/verification/confirm',
      { code: value(form, 'code') }, { code: 'err-verify-code' }, button, 'notes-profile');
    if (!result) return;
    wipe(form, ['code']);
    note('ok', result.message || 'Email confirmed.', 'notes-profile');
    load();
  });

  on('reauth-send-begin', 'click', (event) =>
    sendMailboxCode(event.currentTarget, 'totp-begin-note', 'begin_email_code'));

  bind('form-totp-begin', async (form, button) => {
    const result = await post('/api/account/totp/begin', {
      current_password: value(form, 'current_password'),
      email_code: value(form, 'email_code').replace(/[^0-9]/g, ''),
    }, {
      current_password: 'err-totp_current_password',
      email_code: 'err-begin_code',
    }, button, null, 'totp-begin-note');
    if (!result) return;
    wipe(form, ['current_password', 'email_code']);
    inlineNote('totp-begin-note', '', '');
    el('enroll-secret').hidden = false;
    el('enroll-secret').textContent = result.secret;
    el('enroll-issuer').textContent = result.account + ' · ' + result.issuer
      + ' · this setup expires in a few minutes.';
    // The image is a same-origin GET carrying the session cookie, and it is the only
    // way the provisioning URI leaves the server: an <img> src never lands in a JSON
    // body an extension can read.
    el('qr-img').src = result.qr_url + '?ref=' + Date.now();
    shown('qr-frame', true);
    shown('enroll', true);
    // The QR and the three steps are the answer, so no sentence is stacked on top.
    shown('totp-begin-block', false);
    el('totp_code').focus();
  });

  bind('form-totp-confirm', async (form, button) => {
    const result = await post('/api/account/totp/confirm',
      { code: value(form, 'code').replace(/[^0-9]/g, '') },
      { code: 'err-totp-code' }, button, 'notes-profile');
    if (!result) return;
    wipe(form, ['code']);
    shown('enroll', false);
    closeOwner(form);
    note('ok', result.message || 'Two-factor sign-in is on.', 'notes-profile');
    load();
  });

  on('totp-cancel', 'click', async (event) => {
    const result = await post('/api/account/totp/cancel', {}, {}, event.currentTarget,
      'notes-profile');
    if (!result) return;
    shown('enroll', false);
    shown('totp-begin-block', true);
    note('ok', 'Setup cancelled.', 'notes-profile');
    load();
  });

  bind('form-totp-disable', async (form, button) => {
    const result = await post('/api/account/totp/disable', {
      current_password: value(form, 'current_password'),
      email_code: value(form, 'email_code').replace(/[^0-9]/g, ''),
    }, {
      current_password: 'err-disable_password',
      email_code: 'err-disable_code',
    }, button, 'notes-profile', 'totp-note');
    if (!result) return;
    wipe(form, ['current_password', 'email_code']);
    inlineNote('totp-note', '', '');
    closeOwner(form);
    note('ok', result.message || 'Two-factor sign-in is off.', 'notes-profile');
    load();
  });

  // ---- one question before anything that cannot be undone
  // The provider disconnects and both sign-out controls ask here rather than through
  // window.confirm(), which is dismissed without being read and cannot name what the
  // particular button is about to do. The trigger supplies the sentence, the confirm
  // control performs exactly the request it names, and a refusal is said over the
  // popup instead of in a panel the modal is covering.
  let confirmAction = null;

  function askConfirm(details) {
    const box = el('dialog-confirm');
    if (!box) return;
    el('dialog-confirm-title').textContent = details.title;
    el('dialog-confirm-message').textContent = details.message;
    el('dialog-confirm-yes').textContent = details.label;
    confirmAction = details;
    openDialog(box);
  }

  on('dialog-confirm', 'close', () => { confirmAction = null; });

  on('dialog-confirm-yes', 'click', async (event) => {
    // The element is read before the await: event.currentTarget is null once the
    // dispatch that started this handler has finished.
    const button = event.currentTarget;
    const action = confirmAction;
    if (!action) return;
    const result = await sendStep(action.path, action.payload, button);
    if (!result || result.code) {
      toast((result && result.message) || 'That did not work.');
      return;
    }
    confirmAction = null;
    closeOwner(button);
    const [kind, text] = action.done(result);
    note(kind, text, action.section);
    load();
  });

  on('signout-others', 'click', () => {
    askConfirm({
      title: 'Sign out other devices?',
      message: 'Every other browser and phone has to sign in again. '
        + 'This device stays signed in.',
      label: 'Sign out other devices',
      path: '/api/account/sessions/revoke',
      payload: { others: true },
      section: 'notes-devices',
      done: () => ['ok', 'Every other device is signed out.'],
    });
  });

  // Disconnecting removes the saved session, the cookies and the browser profile on
  // this server -- not just the row that listed them. Saying so before acting is the
  // point: the old wording claimed a disconnect that left everything working.
  const PROVIDER_LABEL = { amazon: 'Amazon', youtube: 'YouTube Music' };
  ['amazon', 'youtube'].forEach((provider) => {
    on('disconnect-' + provider, 'click', () => {
      const label = PROVIDER_LABEL[provider];
      askConfirm({
        title: 'Disconnect ' + label + '?',
        message: provider === 'amazon'
          ? 'This removes the saved Alexa session and the browser profile Music Box '
            + 'keeps for it. Your Echo devices stop taking requests from Music Box '
            + 'until you sign in again.'
          : 'This removes the saved YouTube Music session and the browser profile '
            + 'Music Box keeps for it. Your playlists and likes stay in your Google '
            + 'account.',
        label: 'Disconnect',
        path: '/api/account/providers/' + provider + '/disconnect',
        payload: {},
        section: 'notes-providers',
        done: (result) => [result.complete === false ? 'warn' : 'ok',
                           result.message || (label + ' disconnected.')],
      });
    });
  });

  // account-connect.js runs the provider's sign-in inside a popup instead of the
  // standalone page. When it succeeds the session is already saved, so this only
  // repaints the rows -- a reload would throw away the page the person was on.
  document.addEventListener('account:provider-connected', (event) => {
    const provider = event.detail && event.detail.provider;
    if (!provider) return;
    note('ok', (PROVIDER_LABEL[provider] || 'Service') + ' connected.', 'notes-providers');
    load();
  });

  // The device rows come from the server on the first paint and from renderSessions()
  // after that, so the sign-out click is read off the row instead of bound to it: one
  // listener for a list that is rebuilt after every action.
  (function bindDeviceList() {
    const list = el('devices');
    if (!list) return;
    list.addEventListener('click', (event) => {
      const button = event.target && event.target.closest
        ? event.target.closest('button[data-revoke]') : null;
      if (!button) return;
      // The popup names the device, because "this device" in a modal is the one
      // thing the row no longer says once the row is behind the backdrop.
      const row = button.closest('li');
      const name = row && row.querySelector('.device-title');
      const device = (name && name.textContent.trim()) || 'That device';
      askConfirm({
        title: 'Sign out this device?',
        message: device + ' has to sign in again. This device stays signed in.',
        label: 'Sign out',
        path: '/api/account/sessions/revoke',
        payload: { id: button.dataset.revoke },
        section: 'notes-devices',
        done: () => ['ok', 'That device is signed out.'],
      });
    });
  })();

  // The header's Sign out is a real link with a real popup, so it is bound by the
  // generic `a[data-dialog]` handler below: a click opens the question, the confirm
  // control follows the same href, and a modifier-click still opens a new tab.

  // --------------------------------------------------------------------- popups
  // Every option in the panel opens its popup in place. The link keeps a real href,
  // so a browser whose script never ran follows it to /account?step=... and the server
  // renders the same dialog as a plain block, and a modifier-click still opens a tab.
  // A popup can hold more than one task -- change and reset a password, use or
  // remove an authenticator -- and the button that opened it names which one to
  // show. Without a view the server's own state decides, which is what a direct
  // /account?step=... link relies on.
  function showDialogView(box, view) {
    if (!view) return;
    box.setAttribute('data-view', view);
    Array.prototype.forEach.call(box.querySelectorAll('[data-dialog-block]'), (block) => {
      block.hidden = block.getAttribute('data-dialog-block') !== view;
    });
  }

  function openDialog(box, view) {
    if (!box || box.open) return;
    // The no-script fallback must not put the dialog back on the page after the first
    // close, so the server's flag retires the moment the real popup opens.
    box.removeAttribute('data-autopen');
    showDialogView(box, view);
    if (typeof box.showModal !== 'function') { box.setAttribute('open', ''); return; }
    box.showModal();
    // showModal() would focus the close button in the head, and the first field is
    // not always the one to start in: a step that begins with "send a code" marks
    // that button instead.
    const preferred = box.querySelector('[data-autofocus]');
    if (preferred && preferred.offsetParent !== null) { preferred.focus(); return; }
    const first = Array.prototype.find.call(box.querySelectorAll('.dialog-body input'),
      (node) => node.offsetParent !== null);
    if (first) first.focus();
  }

  Array.prototype.forEach.call(document.querySelectorAll('a[data-dialog]'), (link) => {
    link.addEventListener('click', (event) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const box = el(link.getAttribute('data-dialog'));
      if (!box) return;
      event.preventDefault();
      openDialog(box, link.getAttribute('data-view') || '');
    });
  });

  Array.prototype.forEach.call(document.querySelectorAll('dialog.account-dialog'), (box) => {
    // A click on the backdrop targets the dialog element itself, and so does a drag
    // that starts on an input and ends outside it -- which must not close a popup
    // someone is halfway through. Only a press that began on the backdrop closes.
    let pressedOutside = false;
    box.addEventListener('pointerdown', (event) => {
      pressedOutside = event.target === box;
    });
    box.addEventListener('click', (event) => {
      if (!pressedOutside || event.target !== box) return;
      const rect = box.getBoundingClientRect();
      const inBox = event.clientX >= rect.left && event.clientX <= rect.right
        && event.clientY >= rect.top && event.clientY <= rect.bottom;
      if (!inBox) box.close();
    });
    if (box.hasAttribute('data-autopen')) openDialog(box, box.getAttribute('data-default-view') || '');
  });

  Array.prototype.forEach.call(document.querySelectorAll('[data-dialog-close]'), (button) => {
    button.addEventListener('click', () => closeOwner(button));
  });

  // ---- deletion: four questions, each in its own popup
  // The first popup proves who is asking and keeps the pair here, so closing it
  // empties the boxes while the last step can still send the same proof.
  const deleteProof = { password: '', username: '' };

  on('dialog-delete-check', 'close', () => {
    const form = el('form-delete-check');
    if (form) wipe(form, ['current_password', 'confirm_username']);
  });

  // Every step of the flow answers the same way: the button rests while the server
  // thinks, a refusal is said in a toast, and the caller still gets the code so it
  // can return to the question that can fix it.
  async function sendStep(path, payload, button) {
    if (button) button.disabled = true;
    try {
      return await call(path, payload);
    } catch (err) {
      return null;
    } finally {
      if (button) button.disabled = false;
    }
  }

  function refused(result) {
    if (result && result.code) toast(result.message || 'That did not work.');
    return !result || Boolean(result.code);
  }

  bind('form-delete-check', async (form, button) => {
    const result = await sendStep('/api/account/delete', {
      password: value(form, 'current_password'),
      confirm_username: value(form, 'confirm_username'),
      verify_only: true,
    }, button);
    if (refused(result)) return;
    deleteProof.password = value(form, 'current_password');
    deleteProof.username = value(form, 'confirm_username');
    closeOwner(form);
    openDialog(el('dialog-delete-confirm'));
  });

  on('delete-confirm-yes', 'click', async (event) => {
    const button = event.currentTarget;
    const box = el('dialog-delete-confirm');
    if (!box || box.getAttribute('data-has-email') !== '1') {
      closeOwner(button);
      openDialog(el('dialog-delete-final'));
      return;
    }
    const result = await sendStep('/api/account/email/reauth', {}, button);
    if (refused(result)) return;
    closeOwner(button);
    openDialog(el('dialog-delete-code'));
    inlineNote('del-code-note', '', result.note || ('Code sent to ' + result.sent_to + '.'));
  });

  bind('form-delete-code', (form) => {
    closeOwner(form);
    openDialog(el('dialog-delete-final'));
  });

  on('delete-final-yes', 'click', async (event) => {
    const button = event.currentTarget;
    const code = el('form-delete-code');
    const result = await sendStep('/api/account/delete', {
      password: deleteProof.password,
      confirm_username: deleteProof.username,
      email_code: code ? value(code, 'code').replace(/[^0-9]/g, '') : '',
    }, button);
    if (result && result.code) {
      // The proof from step one can stop being true while the flow is open, so a
      // refusal returns to the popup that asked for it. The toast is raised after
      // the switch, because a stack left inside a closed popup goes with it.
      if (result.code === 'invalid_code' || result.code === 'code_required') {
        if (el('dialog-delete-code')) {
          closeOwner(button);
          openDialog(el('dialog-delete-code'));
        }
      } else if (result.code === 'bad_password' || result.code === 'username_mismatch') {
        closeOwner(button);
        openDialog(el('dialog-delete-check'));
      }
      toast(result.message || 'That did not work.');
      return;
    }
    if (!result) return;
    inlineNote('del-final-note', 'ok', result.message || 'Account deleted.');
    document.querySelectorAll('input').forEach((input) => { input.value = ''; });
    setTimeout(() => { window.location.href = result.next || '/login/'; }, 1500);
  });

  on('profile_email', 'input', () => { touched.profile_email = true; });

  // One section at a time. The rail marks the section you are looking at and the
  // address holds its fragment: the back button walks the sections, /account#panel-sessions
  // means the same thing to a browser with no script, and the panels nobody asked
  // for stop stacking into one endless column.
  (function oneSectionAtATime() {
    const links = Array.prototype.slice.call(
      document.querySelectorAll('.section-nav a[href^="#panel-"]'));
    const panes = links.map((link) => document.getElementById(link.hash.slice(1)));
    if (!links.length || panes.some((pane) => !pane)) return;

    function indexOf(id) {
      for (let position = 0; position < panes.length; position += 1) {
        if (panes[position].id === id) return position;
      }
      return -1;
    }

    function show(position, moveFocus) {
      links.forEach((link, other) => {
        const on = position === other;
        panes[other].hidden = !on;
        link.classList.toggle('active', on);
        if (on) link.setAttribute('aria-current', 'true');
        else link.removeAttribute('aria-current');
      });
      if (!moveFocus) return;
      // A pointer keeps the place it pressed. A keyboard that asked for Devices needs
      // telling which section it got, and Tab has to carry on from inside it instead of
      // from the top of the page all over again.
      panes[position].focus();
    }

    function fromHash() {
      const chosen = indexOf((location.hash || '').replace(/^#/, ''));
      return chosen >= 0 ? chosen : 0;
    }

    links.forEach((link, position) => {
      link.addEventListener('click', (event) => {
        event.preventDefault();
        show(position, event.detail === 0);
        // Writing the fragment is what makes Back walk the sections. The hashchange it
        // fires re-applies the same choice, which asks for no focus a second time.
        if (location.hash !== link.hash) location.hash = link.hash;
      });
    });

    window.addEventListener('hashchange', () => show(fromHash(), false));
    show(fromHash(), false);
  })();

  load();
})();

(() => {
  const code = document.getElementById('alexa-link-code');
  const unlink = document.getElementById('alexa-unlink');
  const result = document.getElementById('alexa-link-result');
  const send = async (path) => {
    const response = await fetch(path, {method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json','X-CSRF-Token':document.body.dataset.csrf || ''},body:'{}'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.message || 'Try again.');
    return data;
  };
  code?.addEventListener('click', async () => { try {result.textContent=(await send('/api/account/alexa/link-code')).code;} catch(e) {result.textContent=e.message;} });
  unlink?.addEventListener('click', async () => { try {await send('/api/account/alexa/unlink');result.textContent='Echo devices unlinked.';} catch(e) {result.textContent=e.message;} });
})();
