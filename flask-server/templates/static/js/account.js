/* Your account (/account).
 *
 * The page is an empty shell: everything visible comes from GET /api/account, so no
 * secret is baked into the HTML and the blueprint can serve `script-src 'self'` with
 * no inline script anywhere. Same-origin cookies plus an X-CSRF-Token header carry the
 * authority; the token is read from body[data-csrf] rather than written into a form.
 *
 * Every panel posts on its own, and a failure is reported where the field is, because
 * "Something went wrong" on a page with six forms is not information. */
(function () {
  'use strict';

  const CSRF = document.body.dataset.csrf || '';
  const el = (id) => document.getElementById(id);

  // ------------------------------------------------------------------ chrome
  function note(kind, text) {
    const stack = el('notes');
    if (!stack || !text) return;
    const box = document.createElement('p');
    box.className = 'note ' + (kind || '');
    box.textContent = text;
    stack.prepend(box);
    while (stack.children.length > 3) stack.removeChild(stack.lastChild);
    if (kind === 'ok') setTimeout(() => box.remove(), 14000);
  }

  function setError(id, message) {
    const node = el(id);
    if (!node) return;
    node.textContent = message || '';
    node.hidden = !message;
  }

  function badge(id, text, kind) {
    const node = el(id);
    if (!node) return;
    node.textContent = text;
    node.className = 'badge' + (kind ? ' ' + kind : '');
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
  // paragraphs that carry that field's complaint.
  async function post(path, payload, fields, button) {
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
      if (result && result.message) note('bad', result.message);
      if (result && result.retry_after) note('warn', 'Try again in ' + result.retry_after + ' seconds.');
      return null;
    }
    return result;
  }

  // ------------------------------------------------------------------- state
  // The two profile inputs a person may already be typing into are left alone once
  // they have been touched; a refresh of the other panels must not eat their text.
  const touched = { display_name: false, profile_email: false };

  function renderProfile(data) {
    if (!touched.display_name) el('display_name').value = data.display_name || '';
    if (!touched.profile_email) el('profile_email').value = data.email || '';

    if (!data.email) badge('verify-badge', 'No email address', 'bad');
    else if (data.email_verified) badge('verify-badge', 'Confirmed', 'ok');
    else badge('verify-badge', 'Waiting for you to click the link', 'warn');
    shown('resend-verify', Boolean(data.email) && data.verification_pending);

    const since = el('head-since');
    if (since) {
      since.textContent = (data.role === 'admin' ? 'Administrator' : 'Player')
        + ' · created ' + stamp(data.created_at)
        + (data.created_by === 'self' ? ' by you' : '');
    }
  }

  function renderTotp(data) {
    if (data.totp_enabled) {
      badge('totp-badge', 'On', 'ok');
      shown('totp-on', true);
      shown('totp-off', false);
      shown('enroll', false);
      return;
    }
    badge('totp-badge', data.totp_pending ? 'Setup waiting' : 'Off',
      data.totp_pending ? 'warn' : '');
    shown('totp-on', false);
    shown('totp-off', true);
    // The secret is handed out exactly once, at the moment it is generated. A page
    // reloaded mid-setup still has a live enrollment server-side, and the QR
    // endpoint redraws it without repeating the secret inside any JSON body.
    shown('enroll', Boolean(data.totp_pending));
    if (!data.totp_pending) return;
    el('enroll-secret').hidden = true;
    el('enroll-issuer').textContent = 'A setup is already waiting. Scan the code or confirm it, '
      + 'or cancel and start again.';
    el('qr-img').src = '/api/account/totp/qr?ref=' + Date.now();
    shown('qr-frame', true);
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
    badge('prov-cookies', 'Checking\u2026', '');

    // The stored rows say what this account was last able to save; the player's own
    // status endpoint says what actually works right now, per identity. Same account,
    // live answer -- and audio_check=0 keeps a page read from probing a 50-second
    // download.
    fetch('/api/profile_status/?audio_check=0', { credentials: 'same-origin' })
      .then((response) => (response.ok ? response.json() : null))
      .then((status) => {
        if (!status) {
          el('providers-note').hidden = false;
          el('providers-note').textContent = 'Live status could not be read. '
            + 'The badges above show the last thing saved for this account.';
          badge('prov-cookies', 'Unknown', '');
          return;
        }
        badge('prov-amazon', status.amazon_connected ? 'Connected' : 'Not connected',
          status.amazon_connected ? 'ok' : '');
        badge('prov-youtube', status.youtube_auth_working ? 'Connected' : 'Not connected',
          status.youtube_auth_working ? 'ok' : '');
        badge('prov-cookies',
          status.youtube_cookies_working ? 'Working'
            : status.youtube_cookies_present ? 'Present, unproven' : 'Missing',
          status.youtube_cookies_working ? 'ok'
            : status.youtube_cookies_present ? 'warn' : '');
        shown('link-amazon', !status.amazon_connected);
        shown('link-youtube', !status.youtube_auth_working);
      })
      .catch(() => {
        badge('prov-cookies', 'Unknown', '');
        el('providers-note').hidden = false;
        el('providers-note').textContent = 'Live status could not be read.';
      });
  }

  function renderSessions(data) {
    const list = el('devices');
    if (!list) return;
    list.textContent = '';
    const others = (data.sessions || []).filter((row) => !row.current);
    shown('devices-empty', others.length === 0);
    others.forEach((row) => {
      const item = document.createElement('li');

      const main = document.createElement('div');
      main.className = 'device-main';
      const title = document.createElement('div');
      title.className = 'device-title';
      title.textContent = row.device || 'Unlisted browser';
      const meta = document.createElement('div');
      meta.className = 'device-meta';
      meta.textContent = 'seen ' + ago(row.last_seen_at)
        + (row.ip ? ' · from ' + row.ip : '')
        + ' · signed in ' + stamp(row.created_at);
      main.appendChild(title);
      main.appendChild(meta);
      item.appendChild(main);

      const button = document.createElement('button');
      button.className = 'tiny destructive';
      button.type = 'button';
      button.textContent = 'Sign out';
      button.addEventListener('click', async () => {
        const result = await post('/api/account/sessions/revoke', { id: row.id }, {}, button);
        if (result) {
          note('ok', 'That device is signed out.');
          load();
        }
      });
      item.appendChild(button);
      list.appendChild(item);
    });
  }

  function render(data) {
    renderProfile(data);
    renderTotp(data);
    renderProviders(data);
    renderSessions(data);
    if (data.must_change_password) {
      note('warn', 'This account was created with a password you did not choose. Set your own below.');
    }
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
      handler(form, form.querySelector('[data-submit]'));
    });
  }

  function value(form, name) {
    const node = form.elements[name];
    return node ? node.value : '';
  }

  function wipe(form, names) {
    names.forEach((name) => { if (form.elements[name]) form.elements[name].value = ''; });
  }

  bind('form-profile', async (form, button) => {
    const result = await post('/api/account/profile', {
      display_name: value(form, 'display_name'),
      email: value(form, 'email'),
      current_password: value(form, 'current_password'),
    }, {
      email: 'err-email',
      display_name: 'err-display_name',
      current_password: 'err-profile_current_password',
    }, button);
    if (!result) return;
    wipe(form, ['current_password']);
    note('ok', result.email_changed
      ? 'Saved. A confirmation link is on its way to the new address, and the email is '
        + 'unverified until you click it.'
      : 'Profile saved.');
    load();
  });

  bind('form-password', async (form, button) => {
    const result = await post('/api/account/password', {
      current_password: value(form, 'current_password'),
      password: value(form, 'password'),
      confirm_password: value(form, 'confirm_password'),
    }, {
      current_password: 'err-current_password',
      password: 'err-password',
      confirm_password: 'err-confirm_password',
    }, button);
    if (!result) return;
    wipe(form, ['current_password', 'password', 'confirm_password']);
    note('ok', result.message || 'Password changed.');
    load();
  });

  on('resend-verify', 'click', async (event) => {
    const result = await post('/api/account/email/verification', {}, {}, event.currentTarget);
    if (result) note(result.note ? 'warn' : 'ok', result.note || 'That address is already confirmed.');
  });

  bind('form-totp-begin', async (form, button) => {
    const result = await post('/api/account/totp/begin',
      { current_password: value(form, 'current_password') },
      { current_password: 'err-totp_current_password' }, button);
    if (!result) return;
    wipe(form, ['current_password']);
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
    el('totp_code').focus();
    note('warn', result.message || 'Scan it, then type the code.');
  });

  bind('form-totp-confirm', async (form, button) => {
    const result = await post('/api/account/totp/confirm',
      { code: value(form, 'code').replace(/[^0-9]/g, '') },
      { code: 'err-code' }, button);
    if (!result) return;
    wipe(form, ['code']);
    shown('enroll', false);
    note('ok', result.message || 'Two-factor sign-in is on.');
    load();
  });

  on('totp-cancel', 'click', async (event) => {
    const result = await post('/api/account/totp/cancel', {}, {}, event.currentTarget);
    if (!result) return;
    shown('enroll', false);
    note('ok', 'Setup cancelled.');
    load();
  });

  bind('form-totp-disable', async (form, button) => {
    const result = await post('/api/account/totp/disable', {
      current_password: value(form, 'current_password'),
      code: value(form, 'code').replace(/[^0-9]/g, ''),
    }, { current_password: 'err-disable', code: 'err-disable' }, button);
    if (!result) return;
    wipe(form, ['current_password', 'code']);
    note('ok', result.message || 'Two-factor sign-in is off.');
    load();
  });

  // Two taps, no dialog: the first arms the button and says what it will do, the
  // second one acts. window.confirm() gets dismissed without being read.
  function arm(button, text, run) {
    if (button.dataset.armed === '1') {
      button.dataset.armed = '';
      button.textContent = button.dataset.label;
      button.disabled = true;
      Promise.resolve()
        .then(run)
        .catch(() => {})
        .then(() => { button.disabled = false; });
      return;
    }
    button.dataset.label = button.textContent;
    button.dataset.armed = '1';
    button.textContent = text;
    setTimeout(() => {
      if (button.dataset.armed !== '1') return;
      button.dataset.armed = '';
      button.textContent = button.dataset.label;
    }, 6000);
  }

  on('signout-all', 'click', (event) => {
    arm(event.currentTarget, 'Tap again to sign out every device, including this one', async () => {
      const result = await post('/api/account/sessions/revoke', { all: true }, {}, null);
      if (!result) return;
      note('ok', 'Every device is signed out, including this one.');
      setTimeout(() => { window.location.href = '/login/'; }, 1200);
    });
  });

  bind('form-delete', async (form, button) => {
    const result = await post('/api/account/delete', {
      password: value(form, 'password'),
      confirm_username: value(form, 'confirm_username'),
    }, {
      password: 'err-del_password',
      confirm_username: 'err-confirm_username',
    }, button);
    if (!result) return;
    note('ok', result.message || 'Account deleted.');
    document.querySelectorAll('input').forEach((input) => { input.value = ''; });
    setTimeout(() => { window.location.href = result.next || '/login/'; }, 1500);
  });

  on('display_name', 'input', () => { touched.display_name = true; });
  on('profile_email', 'input', () => { touched.profile_email = true; });

  load();
})();
