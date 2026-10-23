import { Innertube, Platform, Log } from 'youtubei.js';
import vm from 'node:vm';
import fs from 'node:fs';

Log.setLevel(); // Never log signed URLs, signatures or cookie values.
Platform.shim.eval = async data => vm.runInNewContext(
  '(function(){' + data.output + '})()', {}, { timeout: 2000 });

export function cookiesFromNetscape(text, now = Date.now() / 1000) {
  return text.split('\n').flatMap(line => {
    if (line.startsWith('#HttpOnly_')) line = line.slice(10);
    if (!line || line.startsWith('#')) return [];
    const v = line.trimEnd().split('\t');
    if (v.length < 7 || !/(^|\.)youtube\.com$/.test(v[0].replace(/^\./, '')) ||
        (Number(v[4]) > 0 && Number(v[4]) <= now)) return [];
    return [v[5] + '=' + v[6]];
  }).join('; ');
}

export function createResolver({ createClient = options => Innertube.create(options),
  readFile = path => fs.readFileSync(path, 'utf8'),
  stat = path => fs.statSync(path), now = () => Date.now(), capacity = 128 } = {}) {
  let session, credential = '', pending = new Map(), cache = new Map(), active = 0, lastPlayerReset = -Infinity;
  const reset = () => { session = undefined; pending = new Map(); cache = new Map(); };
  return async function resolve(message) {
    if (message.action !== 'init' && !/^[A-Za-z0-9_-]{11}$/.test(message.video_id || ''))
      throw new Error('invalid_video');
    const path = message.cookie_file || '';
    const file = path ? stat(path) : null;
    const version = path + ':' + (file ? `${file.ino}:${file.mtimeMs}:${file.size}` : 'anonymous');
    if (version !== credential) { credential = version; reset(); }
    if (!session) {
      const cookie = path ? cookiesFromNetscape(readFile(path), now()/1000) : '';
      session = Promise.resolve(createClient({ cookie, fetch: (url, init) => fetch(url, {
        ...init, signal: AbortSignal.timeout(10000)
      }) }));
      // Failed initialization must be retryable; never retain a rejected promise.
      const current = session;
      session.catch(() => { if (session === current) reset(); });
    }
    const initializing = session, generation = credential;
    const client = await initializing;
    // A cookie replacement during initialization must not publish an old
    // account's formats into the new session's URL cache.
    if (generation !== credential || initializing !== session) return resolve(message);
    if (message.action === 'init') return { ready: true };
    const video = message.video_id;
    const key = credential + ':' + video;
    if (message.refresh) cache.delete(key);
    const hit = cache.get(key);
    if (hit && hit.expires_at > now()) return hit;
    if (pending.has(key)) return pending.get(key);
    if (active >= 4) throw new Error('busy');
    active++;
    const currentCache = cache, currentPending = pending;
    const task = (async () => {
      const info = await client.getBasicInfo(video, { client: 'YTMUSIC' });
      const status = info.playability_status?.status;
      if (status === 'LOGIN_REQUIRED') throw new Error('authentication_required');
      if (status === 'UNPLAYABLE' || status === 'ERROR') throw new Error('track_unavailable');
      // Pin AAC itag 140 across requests/restarts: byte ranges cannot resume
      // against a different encoding selected by a later "best" decision.
      const format = info.chooseFormat({ itag: 140, type: 'audio', format: 'mp4' });
      if (format.itag !== 140 || format.has_video || !format.has_audio ||
          format.drm_families?.length || !format.content_length)
        throw new Error('unsupported_format');
      const media = await format.decipher(client.session.player);
      const url = new URL(media);
      if (url.protocol !== 'https:' || !url.hostname.endsWith('.googlevideo.com') ||
          (url.port && url.port !== '443') || url.username || url.password)
        throw new Error('invalid_media');
      const expiry = Number(url.searchParams.get('expire')) * 1000;
      const expires_at = Math.min(now()+300000, expiry ? expiry-60000 : now()+60000);
      if (expires_at <= now()) throw new Error('expired');
      const result = { url: media, itag: 140, length: format.content_length,
        modified: format.last_modified_ms, mime: 'audio/mp4', expires_at };
      // A credential rotation while this awaited must not pollute the new cache.
      currentCache.set(key, result);
      while (currentCache.size > capacity) currentCache.delete(currentCache.keys().next().value);
      return result;
    })();
    currentPending.set(key, task);
    try { return await task; }
    catch (error) {
      if (error.info?.status === 'ERROR' || error.info?.status === 'UNPLAYABLE')
        throw new Error('track_unavailable');
      if (error.info?.status === 'LOGIN_REQUIRED') throw new Error('authentication_required');
      // YouTube can deploy a new player while this process remains alive.
      // Refresh on the next request, but do not restart repeatedly for a
      // genuinely unavailable song or a network/rate-limit error.
      if (/decipher|signature|nsig/i.test(error.message) && now()-lastPlayerReset > 30000) {
        lastPlayerReset = now();
        if (currentCache === cache) reset();
      }
      throw error;
    }
    finally { currentPending.delete(key); active--; }
  };
}
