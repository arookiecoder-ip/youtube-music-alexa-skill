const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC = fs.readFileSync(
  path.join(__dirname, '..', 'templates', 'static', 'js', 'api.js'),
  'utf8'
);

function loadApi(jsonBody) {
  const toasts = [];
  const location = {
    href: 'https://music-box.example/home',
    pathname: '/home',
    origin: 'https://music-box.example',
    replace() {},
  };
  const sandbox = {
    console,
    URL,
    AbortController: class { constructor() { this.signal = {}; } abort() {} },
    location,
    window: {
      location,
      JAM_GUEST: false,
      addEventListener() {},
      toast(message) { toasts.push(message); },
    },
    document: { getElementById: () => null },
    requestAnimationFrame(fn) { fn(); },
    setTimeout() {},
    clearTimeout() {},
    fetch() {
      return Promise.resolve({
        status: 401,
        ok: false,
        redirected: false,
        url: 'https://music-box.example/alexa/status/',
        headers: { get: () => null },
        json: () => Promise.resolve(jsonBody),
      });
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox, { filename: 'api.js' });
  return { api: sandbox.window.api, toasts };
}

(async () => {
  const generic = loadApi({ error: { code: 'unauthorized', message: 'Amazon unavailable' } });
  let genericError = null;
  try { await generic.api('/alexa/status/'); } catch (error) { genericError = error; }
  if (!genericError || generic.toasts.length !== 0) {
    console.error('FAIL generic 401 was classified as web-session expiry');
    process.exit(1);
  }

  const session = loadApi({ error: { code: 'web_session_required', message: 'Web session required.' } });
  try { await session.api('/alexa/status/'); } catch (_) {}
  if (session.toasts.length !== 1 ||
      session.toasts[0] !== 'Session expired - please log in again.') {
    console.error('FAIL web-session 401 did not show the session-expired message');
    process.exit(1);
  }

  console.log('PASS 401 error classification');
})().catch((error) => {
  console.error('FAIL classification test threw:', error);
  process.exit(1);
});
