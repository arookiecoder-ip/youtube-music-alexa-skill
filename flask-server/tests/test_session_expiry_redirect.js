// Regression test for the startup session-expiry redirect storm.
//
// Several startup requests can receive 401 together when the Flask session
// cookie is missing or expired. api.js must show one expiry error and schedule
// one login redirect, not one redirect timer per failed request.
//
// Run: node flask-server/tests/test_session_expiry_redirect.js

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const API_PATH = path.join(__dirname, '..', 'templates', 'static', 'js', 'api.js');
const SRC = fs.readFileSync(API_PATH, 'utf8');

const timers = [];
const toasts = [];
const location = {
  href: 'https://music-box.example/home',
  pathname: '/home',
  origin: 'https://music-box.example',
  replaceCalls: [],
  replace(value) { this.replaceCalls.push(value); this.pathname = '/login/'; },
};

const toastElement = {
  classList: {
    contains: () => false,
    add: () => {},
    remove: () => {},
  },
};

const sandbox = {
  console,
  URL,
  AbortController: class {
    constructor() { this.signal = {}; }
    abort() {}
  },
  location,
  window: {
    location,
    JAM_GUEST: false,
    addEventListener() {},
    toast(message, kind) { toasts.push([message, kind]); },
  },
  document: {
    getElementById(id) {
      return id === 'toast' ? toastElement : null;
    },
  },
  requestAnimationFrame(callback) { callback(); },
  setTimeout(callback, delay) {
    timers.push({ callback, delay });
    return timers.length;
  },
  clearTimeout() {},
  fetch() {
    return Promise.resolve({
      status: 401,
      ok: false,
      redirected: false,
      url: 'https://music-box.example/api/home/',
      headers: { get: () => null },
      json: () => Promise.resolve({
        error: { code: 'web_session_required', message: 'Web session required.' },
      }),
    });
  },
};

vm.createContext(sandbox);
vm.runInContext(SRC, sandbox, { filename: 'api.js' });

async function main() {
  await Promise.all([
    sandbox.window.api('/api/home/'),
    sandbox.window.api('/alexa/status/'),
  ].map((promise) => promise.catch(() => null)));

  const redirectTimers = timers.filter((timer) => timer.delay === 2000);
  if (redirectTimers.length !== 0) {
    console.error('FAIL background 401 scheduled a login redirect timer');
    process.exit(1);
  }
  if (toasts.length !== 1) {
    console.error('FAIL expected one session-expiry toast, got', toasts.length);
    process.exit(1);
  }
  if (location.replaceCalls.length !== 0) {
    console.error('FAIL background 401 changed the page location', location.replaceCalls);
    process.exit(1);
  }

  // Calling the handler again after the session has been marked expired must
  // not schedule or execute another redirect.
  const timerCount = timers.length;
  sandbox.window._onUnauthorized();
  if (timers.length !== timerCount) {
    console.error('FAIL a later 401 scheduled another timer');
    process.exit(1);
  }

  console.log('PASS session-expiry redirect is single-flight');
}

main().catch((error) => {
  console.error('FAIL session-expiry test threw:', error);
  process.exit(1);
});
