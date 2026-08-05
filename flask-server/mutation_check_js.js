// Mutation harness for the frontend rapid-click fix.
//
// Reverts each part of the play-intent fix in a scratch copy of the real source
// and confirms test_play_intent_ordering.js fails. A test that passes against
// the unfixed code proves nothing.
//
// Run: `node mutation_check_js.js`

const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const JS_DIR = path.join(__dirname, 'templates', 'static', 'js');
const TEST = path.join(__dirname, 'test_play_intent_ordering.js');

// [label, file, oldSnippet, newSnippet]
const MUTATIONS = [
  [
    'SSE guard removed (lagging snapshots re-render the old track)',
    'sse.js',
    "    if (window.playIntentSupersedes && window.playIntentSupersedes(npVideoId)) {",
    "    if (false) {",
  ],
  [
    'playIntentSupersedes always false (server state always wins)',
    'ui-state.js',
    "    if (_abandoned.has(videoId)) return true;\n"
    + "    if (!state._playIntentVideoId) return false;\n"
    + "    if (videoId === state._playIntentVideoId) return false;\n"
    + "    return (now - state._playIntentAt) < state.PLAY_INTENT_GRACE_MS;",
    "    return false;",
  ],
  [
    'isCurrentPlayIntent always true (out-of-order responses all applied)',
    'ui-state.js',
    "  window.isCurrentPlayIntent = function isCurrentPlayIntent(seq) {\n"
    + "    return window.__appState._playIntentSeq === seq;\n"
    + "  };",
    "  window.isCurrentPlayIntent = function isCurrentPlayIntent(seq) {\n"
    + "    return true;\n"
    + "  };",
  ],
  [
    'sequence no longer increments (every click looks current)',
    'ui-state.js',
    "    state._playIntentSeq += 1;",
    "    state._playIntentSeq = state._playIntentSeq;",
  ],
  [
    'intent never expires (a failed play freezes the UI)',
    'ui-state.js',
    "    return (now - state._playIntentAt) < state.PLAY_INTENT_GRACE_MS;",
    "    return true;",
  ],
  [
    'settlePlayIntent is a no-op (queue advance stays blocked)',
    'ui-state.js',
    "    if (videoId && state._playIntentVideoId === videoId) {\n"
    + "      state._playIntentVideoId = '';\n"
    + "      state._playIntentAt = 0;\n"
    + "    }",
    "    return;",
  ],
  [
    'player.js renders AFTER the await again (the original bug)',
    'player.js',
    "  state._lastPlayAttemptVideoId = item.video_id;\n"
    + "  preloadNowPlayingArtwork(item);\n"
    + "  showNowPlaying(item);",
    "  state._lastPlayAttemptVideoId = item.video_id;\n"
    + "  preloadNowPlayingArtwork(item);",
  ],
  [
    'queue.js drops its post-await supersede check',
    'queue.js',
    "    // Superseded by a later click: that click owns the UI now.\n"
    + "    if (!window.isCurrentPlayIntent(mySeq)) return;\n"
    + "    toast('Playing', 'ok');",
    "    toast('Playing', 'ok');",
  ],
  [
    'player.js failure path no longer rolls back the optimistic paint',
    'player.js',
    "    window.settlePlayIntent(item.video_id);\n"
    + "    state.isPlaying = false;\n"
    + "    state.lastActionIntent = false;\n"
    + "    syncPlayPause();\n"
    + "    schedulePollNowPlaying(0);\n"
    + "    toast(e.message, 'error');\n"
    + "  }\n}",
    "    toast(e.message, 'error');\n  }\n}",
  ],
  [
    'client stops sending intent_seq (server cannot order the burst)',
    'queue.js',
    "      queue_index: typeof queueIndex === 'number' ? queueIndex : undefined,\n"
    + "      // Lets the server drop this play if a later click supersedes it.\n"
    + "      intent_seq: mySeq,",
    "      queue_index: typeof queueIndex === 'number' ? queueIndex : undefined,",
  ],
  [
    'abandoned tracks no longer rejected (jumps back through 1,2,3,4)',
    'ui-state.js',
    "    if (_abandoned.has(videoId)) return true;",
    "    if (false) return true;",
  ],
  [
    'beginPlayIntent stops recording the abandoned track',
    'ui-state.js',
    "    if (state._playIntentVideoId && state._playIntentVideoId !== videoId) {\n"
    + "      _abandoned.set(state._playIntentVideoId, now);\n"
    + "    }",
    "    if (false) {\n"
    + "      _abandoned.set(state._playIntentVideoId, now);\n"
    + "    }",
  ],
  [
    're-clicking an abandoned track no longer revives it',
    'ui-state.js',
    "    if (videoId) _abandoned.delete(videoId);",
    "    if (false) _abandoned.delete(videoId);",
  ],
];

let undetected = [];

for (const [label, file, oldStr, newStr] of MUTATIONS) {
  const target = path.join(JS_DIR, file);
  const original = fs.readFileSync(target, 'utf8');
  const occurrences = original.split(oldStr).length - 1;
  if (occurrences !== 1) {
    console.log(`SKIP    ${label}\n         -> anchor found ${occurrences} times in ${file}`);
    undetected.push(label + ' (bad anchor)');
    continue;
  }
  fs.writeFileSync(target, original.replace(oldStr, newStr));
  let caught = false;
  let summary = '';
  try {
    const out = execFileSync('node', [TEST], { encoding: 'utf8', timeout: 60000 });
    summary = out.trim().split('\n').pop();
  } catch (err) {
    caught = true;
    const out = (err.stdout || '') + (err.stderr || '');
    summary = out.trim().split('\n').filter(Boolean).pop() || 'non-zero exit';
  } finally {
    fs.writeFileSync(target, original);
  }
  console.log(`${caught ? 'CAUGHT ' : 'MISSED '} ${label}\n         -> ${summary}`);
  if (!caught) undetected.push(label);
}

// Confirm every file is back to its original content.
let restored = true;
try {
  execFileSync('node', [TEST], { encoding: 'utf8', timeout: 60000 });
} catch (_) {
  restored = false;
}
console.log(`\nsources restored (suite green again): ${restored}`);

if (undetected.length || !restored) {
  console.log(`\nUNDETECTED (${undetected.length}): ${undetected.join('; ')}`);
  process.exit(1);
}
console.log(`\nAll ${MUTATIONS.length} frontend mutations detected by the test suite.`);