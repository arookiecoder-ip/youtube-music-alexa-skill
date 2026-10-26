/* The panel's behaviour, in one external file.
 *
 * It used to be a `<script>` in the shell plus a script per page, which meant the
 * admin CSP had to allow inline script for the whole door. Nothing here needs
 * that: the templates name what a control does with data attributes, and this
 * file reads them.
 *
 * Progressive enhancement is deliberate. A confirmation asked for here is a
 * courtesy, not a permission check -- the server still requires the CSRF token
 * and the password or second factor a given action needs -- so a browser that
 * runs none of this can still use the panel, it just gets no dialogs.
 */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const dialog = $('admin-dialog');
  const title = $('admin-dialog-title');
  const body = $('admin-dialog-body');
  const foot = $('admin-dialog-foot');

  /* ---------------------------------------------------------------- drawer */
  const navToggle = $('nav-toggle');
  const scrim = document.querySelector('.nav-scrim');

  function setDrawer(open) {
    document.body.classList.toggle('nav-open', open);
    if (navToggle) navToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (scrim) scrim.hidden = !open;
  }
  if (navToggle) {
    navToggle.addEventListener('click', () => setDrawer(!document.body.classList.contains('nav-open')));
  }
  if (scrim) scrim.addEventListener('click', () => setDrawer(false));

  /* ----------------------------------------------------------- the dialog */
  let resolvePending = null;

  function closeDialog() {
    if (dialog && dialog.open) dialog.close();
  }

  function showDialog(heading, build, actions) {
    if (!dialog) return false;
    title.textContent = heading;
    body.textContent = '';
    foot.textContent = '';
    build(body);
    (actions || []).forEach((action) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'btn ' + (action.className || 'ghost');
      button.textContent = action.label;
      button.addEventListener('click', () => {
        if (resolvePending) {
          const settle = resolvePending;
          resolvePending = null;
          settle(!action.cancel);
        }
        closeDialog();
      });
      foot.append(button);
    });
    dialog.showModal();
    return true;
  }

  /* A promise, so the caller's shape stays "ask, then act" rather than a
     callback wired to whichever button happened to exist. */
  function ask(heading, message, confirmLabel, danger) {
    return new Promise((resolve) => {
      if (!showDialog(heading, (target) => {
        const text = document.createElement('p');
        text.textContent = message;
        target.append(text);
      }, [
        { label: 'Cancel', className: 'ghost', cancel: true },
        { label: confirmLabel || 'Confirm', className: danger ? 'danger' : 'primary' },
      ])) {
        resolve(false);
        return;
      }
      resolvePending = resolve;
    });
  }

  if (dialog) {
    dialog.addEventListener('close', () => {
      if (resolvePending) {
        const settle = resolvePending;
        resolvePending = null;
        settle(false);
      }
    });
  }

  /* ---------------------------------------------------- dialogs in the pages
   * The account page brings its own `<dialog>` elements: a re-authentication step
   * is a real form with a CSRF token, so the markup is server-rendered and this
   * only opens and closes it.  Native `showModal()` gives the focus trap, the
   * Escape key and -- since a hidden panel holds no focusable control -- the
   * right field focused for whichever verification tab is showing. */
  const pageDialogs = Array.from(document.querySelectorAll('dialog'));

  /* Clicking the backdrop, or pressing Escape, is a refusal -- never the
     action the dialog was asking about. */
  pageDialogs.forEach((box) => {
    box.addEventListener('click', (event) => {
      if (event.target !== box) return;
      const rect = box.getBoundingClientRect();
      const outside = event.clientX < rect.left || event.clientX > rect.right ||
        event.clientY < rect.top || event.clientY > rect.bottom;
      if (outside) box.close();
    });
  });

  document.addEventListener('click', (event) => {
    const closer = event.target instanceof Element ? event.target.closest('[data-dialog-close]') : null;
    if (!closer) return;
    const box = closer.closest('dialog');
    if (box && box.open) box.close();
  });

  document.querySelectorAll('[data-dialog-open]').forEach((button) => {
    button.addEventListener('click', () => {
      const box = $(button.getAttribute('data-dialog-open'));
      if (box && typeof box.showModal === 'function') box.showModal();
    });
  });

  /* A dialog that outlived a page load: "send code" posts, comes back, and the
     person should find the box they were typing in still open. */
  pageDialogs.forEach((box) => {
    if (box.hasAttribute('data-autopen')) box.showModal();
  });

  /* ---------------------------------------------------- confirm a real form
     The submit event fires from the button, so validation runs first and a form
     that is not ready is never asked about. `requestSubmit()` on the second pass
     is what actually sends it, and the flag stops the loop. */
  document.addEventListener('submit', (event) => {
    const form = event.target.closest('form[data-confirm]');
    if (!form || form.dataset.confirmed === '1') return;
    event.preventDefault();
    ask(
      form.getAttribute('data-confirm-heading') || 'Confirm this action',
      form.getAttribute('data-confirm'),
      form.getAttribute('data-confirm-button') || 'Yes, continue',
      form.hasAttribute('data-confirm-danger')
    ).then((yes) => {
      if (!yes) return;
      form.dataset.confirmed = '1';
      form.requestSubmit();
    });
  }, true);

  /* ---------------------------------------------------------- payload views
     A row's detail lives in a `<template>` beside it: the server renders the
     text once, and there is exactly one dialog on the page to show it in. */
  document.querySelectorAll('[data-dialog-source]').forEach((button) => {
    button.addEventListener('click', () => {
      const source = $(button.getAttribute('data-dialog-source'));
      if (!source) return;
      showDialog(
        button.getAttribute('data-dialog-heading') || 'Details',
        (target) => target.append(source.content.cloneNode(true)),
        [{ label: 'Close', className: 'ghost', cancel: true }]
      );
    });
  });

  /* ------------------------------------------------------- password makers
     Two pages ask for a generated password. The alphabet skips the shapes that
     get retyped wrong (1/l, 0/O) and the dialog is a form field, not a secret
     kept in a script. */
  const ALPHABET = 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$%^&*-_=+';
  document.querySelectorAll('[data-generate]').forEach((button) => {
    button.addEventListener('click', () => {
      const field = $(button.getAttribute('data-generate'));
      if (!field) return;
      const values = new Uint32Array(20);
      crypto.getRandomValues(values);
      field.value = Array.from(values, (v) => ALPHABET[v % ALPHABET.length]).join('');
      field.disabled = false;
      field.focus();
      field.select();
    });
  });

  /* A checkbox that makes a field meaningless -- an invitation asks the user for
     a password, so the administrator's own is not sent. */
  document.querySelectorAll('[data-disables]').forEach((toggle) => {
    const field = $(toggle.getAttribute('data-disables'));
    if (!field) return;
    const sync = () => {
      field.disabled = toggle.checked;
      field.placeholder = toggle.checked
        ? (toggle.getAttribute('data-disables-note') || 'Not used')
        : '';
    };
    toggle.addEventListener('change', sync);
    sync();
  });

  /* Bars are drawn from a number on the element rather than a `style` attribute,
     which is what lets the shell's CSP say `style-src 'self'`. */
  document.querySelectorAll('[data-width-pct]').forEach((element) => {
    element.style.width = Math.max(0, Math.min(100, Number(element.dataset.widthPct) || 0)) + '%';
  });
  document.querySelectorAll('[data-height-pct]').forEach((element) => {
    element.style.height = Math.max(0, Math.min(100, Number(element.dataset.heightPct) || 0)) + '%';
  });

  /* Escape closes the drawer as well as the dialog, and only when the dialog is
     not the thing that owns the key. */
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !document.querySelector('dialog[open]')) setDrawer(false);
  });
})();
