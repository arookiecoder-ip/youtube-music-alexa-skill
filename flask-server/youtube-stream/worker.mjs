// Private stdio protocol; there is no unauthenticated control HTTP listener.
import readline from 'node:readline';
import { createResolver } from './resolver.mjs';
import { EnvHttpProxyAgent, setGlobalDispatcher } from 'undici';
if (process.env.HTTPS_PROXY || process.env.HTTP_PROXY) setGlobalDispatcher(new EnvHttpProxyAgent());
const resolve = createResolver();
const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
input.on('line', async line => {
  let message;
  try {
    if (line.length > 8192) return;
    message = JSON.parse(line);
    const result = await resolve(message);
    process.stdout.write(JSON.stringify({ id: message.id, result })+'\n');
  } catch (error) {
    const known = new Set(['invalid_video','busy','unsupported_format','invalid_media','expired',
      'authentication_required','track_unavailable']);
    const code = known.has(error.message) ? error.message : 'resolve_failed';
    process.stdout.write(JSON.stringify({ id: message?.id, error: code })+'\n');
  }
});
input.on('close', () => process.exit(0));
