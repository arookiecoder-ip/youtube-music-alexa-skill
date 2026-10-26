/* Your account (/account).
 *
 * The page is rendered complete by the server, so it is readable before this script
 * runs and stays usable without it. What happens here is the refresh after an action,
 * the rail that puts one section forward at a time, and the two taps in front of
 * anything that destroys something. Calls carry same-origin cookies plus an
 * X-CSRF-Token header read from body[data-csrf] rather than written into a form, which
 * is what lets the blueprint serve `script-src 'self'` with no inline script anywhere. */
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
  // the general verdict belongs to.
  async function post(path, payload, fields, button, section) {
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
      if (result && result.message) note('bad', result.message, section);
      if (result && result.retry_after) note('warn', 'Try again in ' + result.retry_after + ' seconds.', section);
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

    if (!data.email) badge('verify-badge', 'No email address', 'bad');
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
    if (data.totp_enabled) {
      badge('totp-badge', 'On', 'ok');
      badge('totp-state', 'On', 'ok');
      shown('totp-on', true);
      shown('totp-off', false);
      shown('enroll', false);
      return;
    }
    badge('totp-badge', data.totp_pending ? 'Setup waiting' : 'Off',
      data.totp_pending ? 'warn' : '');
    badge('totp-state', data.totp_pending ? 'Setup waiting' : 'Off',
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
        const youtubeBorrowed = !youtubeOwn && !!status.youtube_server_fallback;
        badge('prov-youtube',
          youtubeOwn ? 'Connected' : (youtubeBorrowed ? 'Using the server session' : 'Not connected'),
          youtubeOwn ? 'ok' : (youtubeBorrowed ? 'warn' : ''));
        shown('link-amazon', !status.amazon_connected);
        // A stored-but-broken row still has a secret to remove, so Disconnect stays
        // available even when the live check says the service is not connected.
        shown('disconnect-amazon', !!rows.amazon || !!status.amazon_connected);
        shown('link-youtube', !youtubeOwn);
        shown('disconnect-youtube', !!rows.youtube || youtubeOwn);
        // A borrowed server session is not *this* account being linked, so the strip
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
        // The same two attributes the server drew the first list with, because the
        // click is read off the row rather than bound to it -- see `bindDeviceList`.
        button.dataset.revoke = row.id;
        button.dataset.arm = 'Tap again to sign out this device';
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
      email: value(form, 'email'),
      current_password: value(form, 'current_password'),
    }, {
      email: 'err-email',
      current_password: 'err-profile_current_password',
    }, button, 'notes-profile');
    if (!result) return;
    wipe(form, ['current_password']);
    note(result.note ? 'warn' : 'ok', result.email_changed
      ? (result.note || 'Saved. A six-digit code is on its way to the new address; type '
        + 'it below to confirm it. Until you do, the address counts as unverified.')
      : 'Saved.', 'notes-profile');
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
    }, button, 'notes-security');
    if (!result) return;
    wipe(form, ['current_password', 'password', 'confirm_password']);
    const forced = el('must-change-note');
    if (forced) forced.remove();
    note('ok', result.message || 'Password changed.', 'notes-security');
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

  bind('form-totp-begin', async (form, button) => {
    const result = await post('/api/account/totp/begin',
      { current_password: value(form, 'current_password') },
      { current_password: 'err-totp_current_password' }, button, 'notes-security');
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
    note('warn', result.message || 'Scan it, then type the code.', 'notes-security');
  });

  bind('form-totp-confirm', async (form, button) => {
    const result = await post('/api/account/totp/confirm',
      { code: value(form, 'code').replace(/[^0-9]/g, '') },
      { code: 'err-totp-code' }, button, 'notes-security');
    if (!result) return;
    wipe(form, ['code']);
    shown('enroll', false);
    note('ok', result.message || 'Two-factor sign-in is on.', 'notes-security');
    load();
  });

  on('totp-cancel', 'click', async (event) => {
    const result = await post('/api/account/totp/cancel', {}, {}, event.currentTarget,
      'notes-security');
    if (!result) return;
    shown('enroll', false);
    note('ok', 'Setup cancelled.', 'notes-security');
    load();
  });

  bind('form-totp-disable', async (form, button) => {
    const result = await post('/api/account/totp/disable', {
      current_password: value(form, 'current_password'),
      code: value(form, 'code').replace(/[^0-9]/g, ''),
    }, { current_password: 'err-disable', code: 'err-disable' }, button, 'notes-security');
    if (!result) return;
    wipe(form, ['current_password', 'code']);
    note('ok', result.message || 'Two-factor sign-in is off.', 'notes-security');
    load();
  });

  // Two taps, no dialog: the first arms the button and says what it will do, the
  // second one acts. window.confirm() gets dismissed without being read. The
  // sentence is the button's own `data-arm`, because it names what that particular
  // button destroys -- copy belongs beside the control, not in the script.
  function arm(button, run) {
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
    button.textContent = button.dataset.arm;
    setTimeout(() => {
      if (button.dataset.armed !== '1') return;
      button.dataset.armed = '';
      button.textContent = button.dataset.label;
    }, 6000);
  }

  on('signout-others', 'click', (event) => {
    arm(event.currentTarget, async () => {
      const result = await post('/api/account/sessions/revoke', { others: true }, {}, null,
        'notes-devices');
      if (!result) return;
      note('ok', 'Every other device is signed out.', 'notes-devices');
      load();
    });
  });

  // Disconnecting removes the saved session, the cookies and the browser profile on
  // this server -- not just the row that listed them. Saying so before acting is the
  // point: the old wording claimed a disconnect that left everything working.
  const PROVIDER_LABEL = { amazon: 'Amazon', youtube: 'YouTube Music' };
  ['amazon', 'youtube'].forEach((provider) => {
    on('disconnect-' + provider, 'click', (event) => {
      arm(event.currentTarget, async () => {
        const result = await post('/api/account/providers/' + provider + '/disconnect',
          {}, {}, null, 'notes-providers');
        if (!result) return;
        note(result.complete === false ? 'warn' : 'ok',
          result.message || (PROVIDER_LABEL[provider] + ' disconnected.'), 'notes-providers');
        load();
      });
    });
  });

  // The device rows come from the server on the first paint and from renderSessions()
  // after that, so the sign-out click is read off the row instead of bound to it: one
  // listener for a list that is rebuilt after every action, and both shapes behave the
  // same because both carry the same two attributes.
  (function bindDeviceList() {
    const list = el('devices');
    if (!list) return;
    list.addEventListener('click', (event) => {
      const button = event.target && event.target.closest
        ? event.target.closest('button[data-revoke]') : null;
      if (!button) return;
      arm(button, async () => {
        const result = await post('/api/account/sessions/revoke',
          { id: button.dataset.revoke }, {}, button, 'notes-devices');
        if (!result) return;
        note('ok', 'That device is signed out.', 'notes-devices');
        load();
      });
    });
  })();

  // The header's Sign out ends the session in this browser, and one tap on a link that
  // sits a hand's width from "Player" was doing it unseen. Same two taps as the buttons
  // on the page; the href stays real, so a visitor whose script never loaded signs out
  // exactly as they always did, and a modifier-click still leaves this tab alone.
  Array.prototype.forEach.call(document.querySelectorAll('a.signout-link[data-arm]'), (link) => {
    link.addEventListener('click', (event) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      arm(link, () => { window.location.href = link.href; });
    });
  });

  bind('form-delete', async (form, button) => {
    const result = await post('/api/account/delete', {
      password: value(form, 'current_password'),
      confirm_username: value(form, 'confirm_username'),
    }, {
      password: 'err-del_password',
      confirm_username: 'err-confirm_username',
    }, button, 'notes-delete');
    if (!result) return;
    note('ok', result.message || 'Account deleted.', 'notes-delete');
    document.querySelectorAll('input').forEach((input) => { input.value = ''; });
    setTimeout(() => { window.location.href = result.next || '/login/'; }, 1500);
  });

  on('profile_email', 'input', () => { touched.profile_email = true; });

  // One section at a time. The rail marks the section you are looking at and the
  // address holds its fragment: the back button walks the sections, /account#panel-security
  // means the same thing to a browser with no script, and the four panels nobody asked
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
