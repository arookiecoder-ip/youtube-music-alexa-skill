/* Door pages: sign-up, resend, forgot, reset.
 *
 * Everything here is progressive enhancement -- the forms are plain POSTs and
 * work with JavaScript disabled. This file adds the password meter (the same two
 * rules the server applies, so it never promises what /signup would refuse) and
 * stops a double submission from spending two of the sign-up rate-limit slots.
 *
 * Configuration arrives as body[data-*] attributes rather than an inline script,
 * which is what lets the blueprint serve `script-src 'self'`. */
(function () {
  'use strict';

  var body = document.body;
  var MIN_LENGTH = parseInt(body.dataset.minPassword || '10', 10);

  function classesUsed(value) {
    var classes = 0;
    if (/[a-z]/.test(value)) classes += 1;
    if (/[A-Z]/.test(value)) classes += 1;
    if (/[0-9]/.test(value)) classes += 1;
    if (/[^A-Za-z0-9]/.test(value)) classes += 1;
    return classes;
  }

  // Mirrors multiuser/security.validate_password: length, not the username, not
  // a handful of known-common strings, at least two character classes.
  function grade(value, username) {
    if (!value) return { state: 'idle', fill: 0, label: '' };
    var classes = classesUsed(value);
    var tooShort = value.length < MIN_LENGTH;
    var isUsername = username && value.trim().toLowerCase() === username.trim().toLowerCase();
    var common = /^(password|password\d+|1234567890|qwerty\d+)$/i.test(value);
    if (tooShort || isUsername || common || classes < 2) {
      return {
        state: 'weak',
        fill: 18,
        label: tooShort ? 'at least ' + MIN_LENGTH + ' characters'
          : isUsername ? 'not your username'
          : common ? 'too common'
          : 'two of: lower, upper, digit, symbol'
      };
    }
    var score = value.length + classes * 4;
    if (score >= 30) return { state: 'good', fill: 100, label: 'strong enough' };
    return { state: 'fair', fill: 62, label: 'works, longer is better' };
  }

  Array.prototype.forEach.call(document.querySelectorAll('input[data-strength]'), function (input) {
    var meter = document.querySelector('.pw-meter[data-meter-for="' + input.id + '"]');
    if (!meter) return;
    var text = meter.querySelector('.pw-meter-text');
    var form = input.form;
    var usernameField = form ? form.querySelector('input[name="username"]') : null;

    function render() {
      var result = grade(input.value, usernameField ? usernameField.value : '');
      meter.dataset.state = result.state;
      // CSS custom properties written through the CSSOM are not a `style`
      // attribute in the markup, so this stays legal under `style-src 'self'`.
      meter.style.setProperty('--fill', result.fill + '%');
      text.textContent = result.label;
    }
    input.addEventListener('input', render);
    if (usernameField) usernameField.addEventListener('input', render);
    render();
  });

  Array.prototype.forEach.call(document.querySelectorAll('form[data-password-form]'), function (form) {
    form.addEventListener('submit', function () {
      var button = form.querySelector('[data-submit]');
      if (!button) return;
      button.disabled = true;
      // If the browser rejects the submit (an invalid field, or a lost
      // connection) the page never leaves, so the button must come back.
      setTimeout(function () { button.disabled = false; }, 12000);
    });
  });
})();
