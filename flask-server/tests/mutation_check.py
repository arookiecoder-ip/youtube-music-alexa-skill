"""Mutation harness: revert each fix in a scratch copy of server.py and confirm
the new test suite fails. A test that passes against the *unfixed* code proves
nothing, so every fix gets an explicit "this test would have caught it" check.

Usage: ../../.venv/bin/python3 flask-server/tests/mutation_check.py
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = HERE.parent / "server.py"
TESTS = HERE / "test_download_backpressure.py"
PYTHON = str(HERE.parent.parent / ".venv" / "bin" / "python3")

# (label, old_snippet, new_snippet, test selector expected to fail)
MUTATIONS = [
    (
        "Fix 4a: 429 no longer excluded from permanent-unavailability",
        "    if _rate_limit_warning_present(stderr):\n"
        "        return False\n"
        "    return any(err in stderr for err in _VIDEO_UNAVAILABLE_ERRORS)",
        "    return any(err in stderr for err in _VIDEO_UNAVAILABLE_ERRORS)",
        "RateLimitClassification or EnsureDownloadedRateLimit",
    ),
    (
        "Fix 4b: fatal 429 no longer aborts the remaining client fallbacks",
        "                        if _is_rate_limited_error(stderr):\n"
        "                            logger.warning(\n"
        "                                \"yt-dlp: aborting remaining client fallbacks for %s \"",
        "                        if False:\n"
        "                            logger.warning(\n"
        "                                \"yt-dlp: aborting remaining client fallbacks for %s \"",
        "EnsureDownloadedRateLimit",
    ),
    (
        "Fix 4e: recoverable 429 warning treated as fatal again (truncates fallbacks)",
        "    for line in stderr.splitlines():\n"
        "        stripped = line.strip()\n"
        "        if not stripped.startswith('ERROR'):\n"
        "            continue\n"
        "        if any(err in stripped for err in _RATE_LIMIT_ERRORS):\n"
        "            return True\n"
        "    return False",
        "    return any(err in stderr for err in _RATE_LIMIT_ERRORS)",
        "RateLimitClassification or EnsureDownloadedRateLimit",
    ),
    (
        "Fix 6: warm cache hit goes back through the download semaphore",
        "    path = Supporting.cached_audio_path(video_id)\n"
        "    if not path:\n"
        "        # Raced with the cache sweep between the check above and here; fall back\n"
        "        # to a real download.\n"
        "        path = Supporting.ensure_downloaded(video_id)",
        "    path = Supporting.ensure_downloaded(video_id)",
        "WarmCacheNeverBlocks",
    ),
    (
        "Fix 7: streams share the prewarm download budget again",
        "    permit = _SemaphorePermit(_stream_semaphore)",
        "    permit = _SemaphorePermit(_download_semaphore)",
        "StreamBudgetIsSeparate",
    ),
    (
        "Fix 4c: prefetch no longer suppressed before taking a permit",
        "        if prefetch and _ytdlp_rate_limited():\n"
        "            logger.info(\"yt-dlp: skipping prefetch of %s (rate-limit cooldown %.0fs)\",",
        "        if False:\n"
        "            logger.info(\"yt-dlp: skipping prefetch of %s (rate-limit cooldown %.0fs)\",",
        "EnsureDownloadedRateLimit",
    ),
    (
        "Fix 4d: queued prefetch no longer re-checks the cooldown after the semaphore",
        "                if prefetch and _ytdlp_rate_limited():\n"
        "                    logger.info(\"yt-dlp: dropping queued prefetch of %s (rate-limited)\",",
        "                if False:\n"
        "                    logger.info(\"yt-dlp: dropping queued prefetch of %s (rate-limited)\",",
        "EnsureDownloadedRateLimit",
    ),
    (
        "Fix 1a: track change no longer bumps the playback generation",
        "    if track_changed:\n"
        "        # Invalidate background download work started for the previous track:",
        "    if False:\n"
        "        # Invalidate background download work started for the previous track:",
        "PlaybackGeneration or RapidClickScenario",
    ),
    (
        "Fix 1b: queued download no longer re-checks generation after the semaphore",
        "                if _generation_superseded(generation):\n"
        "                    logger.info(\"yt-dlp: dropping queued download of %s, playback moved on\",",
        "                if False:\n"
        "                    logger.info(\"yt-dlp: dropping queued download of %s, playback moved on\",",
        "PlaybackGeneration",
    ),
    (
        "Fix 1c: prewarm no longer aborts when playback moves on",
        "        if _generation_superseded(generation):\n"
        "            logger.info(\"prewarm aborted: playback moved on after %d warm(s)\", warmed)\n"
        "            break",
        "        if _generation_superseded(generation):\n"
        "            pass",
        "PrewarmBackpressure",
    ),
    (
        "Fix 5: prewarm no longer consults _download_backpressure()",
        "        if not _download_backpressure():\n"
        "            logger.info(\"prewarm aborted: download queue saturated (%d queued)\",\n"
        "                        _download_queue_size())\n"
        "            break",
        "        if not _download_backpressure():\n"
        "            pass",
        "PrewarmBackpressure",
    ),
    (
        "Fix 3a: concurrent /proxy/ requests for one id no longer deduplicated",
        "    with _stream_inflight_lock:\n"
        "        if video_id in _stream_inflight:\n"
        "            return False\n"
        "        _stream_inflight[video_id] = time.time()\n"
        "        return True",
        "    with _stream_inflight_lock:\n"
        "        _stream_inflight[video_id] = time.time()\n"
        "        return True",
        "StreamInflightRegistry",
    ),
    (
        "Fix 3b: playback watchdog blind to streaming downloads again",
        "    if _stream_is_inflight(video_id):\n"
        "        return True\n"
        "    with _locks_guard:\n"
        "        lock = _download_locks.get(video_id)",
        "    with _locks_guard:\n"
        "        lock = _download_locks.get(video_id)",
        "StreamInflightRegistry or RapidClickScenario",
    ),
    (
        "Fix 2a: superseded streams no longer killed",
        "        elif now - superseded_since >= _STREAM_SUPERSEDE_GRACE:\n"
        "            logger.info(\"proxy: killing superseded stream of %s (playback moved on)\",\n"
        "                        video_id)\n"
        "            _kill_process(proc)\n"
        "            return",
        "        elif now - superseded_since >= _STREAM_SUPERSEDE_GRACE:\n"
        "            pass",
        "StreamSupersede",
    ),
    (
        "Fix 2b: no hard cap on a single stream's lifetime",
        "        if now >= hard_deadline:\n"
        "            logger.warning(\"proxy: killing stream of %s after %.0fs hard cap\",\n"
        "                           video_id, _STREAM_MAX_SECONDS)\n"
        "            _kill_process(proc)\n"
        "            return",
        "        if now >= hard_deadline:\n"
        "            pass",
        "StreamSupersede",
    ),
    (
        "Fix 2c: the Echo's legitimate next-track prefetch is treated as superseded",
        "    prefetched = _prefetched_next or {}\n"
        "    if prefetched.get('video_id') == video_id:\n"
        "        return False\n"
        "    return True",
        "    return True",
        "StreamSupersede",
    ),
    (
        "Fix 3c: semaphore permit release is no longer idempotent",
        "    def release(self):\n"
        "        with self._lock:\n"
        "            if not self._held:\n"
        "                return\n"
        "            self._held = False\n"
        "        self._semaphore.release()",
        "    def release(self):\n"
        "        self._held = False\n"
        "        self._semaphore.release()",
        "SemaphorePermitRelease",
    ),
    (
        "Fix 8: prefetch can occupy the whole download pool again (starves the click)",
        "            if prefetch:\n"
        "                # Cap prefetch below the pool size so the song the user is\n"
        "                # waiting on always has permits available. Best-effort: if no\n"
        "                # prefetch slot is free, drop this one instead of queueing.\n"
        "                prefetch_slot = _SemaphorePermit(_prefetch_semaphore)",
        "            if False:\n"
        "                prefetch_slot = _SemaphorePermit(_prefetch_semaphore)",
        "ForegroundDownloadPriority",
    ),
    (
        "Fix 9: superseded rapid click still dispatches (Echo plays all 5 songs)",
        "    _intent_seq = _play_intent_seq_arg(body)\n"
        "    if not _claim_play_intent(_effective_serial(body.get(\"serial\")), _intent_seq):",
        "    _intent_seq = _play_intent_seq_arg(body)\n"
        "    if False:",
        "PlayIntentClaim or PlayQueueSuperseded",
    ),
    (
        "Fix 9b: claim accepts older sequences (burst ordering lost)",
        "        if previous is not None and seq <= previous:\n"
        "            return False",
        "        if False:\n"
        "            return False",
        "PlayIntentClaim or PlayQueueSuperseded",
    ),
    (
        "Fix 10: coalescing removed (both cancel AND the stale-timer token check)",
        [
            ("        previous = _PENDING_DISPATCH.get(key)\n"
             "        if previous is not None:\n"
             "            previous['timer'].cancel()",
             "        previous = _PENDING_DISPATCH.get(key)\n"
             "        if False:\n"
             "            previous['timer'].cancel()"),
            ("            if not pending or pending['token'] != token:\n"
             "                return",
             "            if not pending:\n"
             "                return"),
            ("            del _PENDING_DISPATCH[key]",
             "            _PENDING_DISPATCH.pop(key, None)"),
        ],
        None,
        "DispatchCoalescing",
    ),
    (
        "Fix 10b: endpoint dispatches immediately instead of coalescing",
        "    replaced = _schedule_play_dispatch(serial, video_id)",
        "    replaced = _dispatch_play_with_retry(serial, video_id)",
        "DispatchCoalescing",
    ),
    (
        "Fix 11: in-flight trigger suppression removed (a trigger per click)",
        "        if _trigger_in_flight(serial):",
        "        if False:",
        "DispatchCoalescing",
    ),
    (
        "Fix 11b: armed_play no longer clears the in-flight marker (playback wedges)",
        "    _clear_trigger_inflight(serial)\n"
        "    return jsonify({'video_id': video_id, 'offset_ms': offset_ms})",
        "    return jsonify({'video_id': video_id, 'offset_ms': offset_ms})",
        "DispatchCoalescing",
    ),
    (
        "Fix 11c: in-flight marker never expires (a dropped trigger blocks playback)",
        "        if time.time() - sent_at > TRIGGER_INFLIGHT_TTL:\n"
        "            del _TRIGGER_INFLIGHT[key]\n"
        "            return False",
        "        if False:\n"
        "            del _TRIGGER_INFLIGHT[key]\n"
        "            return False",
        "DispatchCoalescing",
    ),
]


def main():
    original = SERVER.read_text()
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        backup = Path(tmp) / "server.py.orig"
        shutil.copy2(SERVER, backup)
        try:
            for label, old, new, selector in MUTATIONS:
                # A mutation may need several coordinated edits when two
                # independent mechanisms each enforce the same invariant
                # (removing only one is masked by the other).
                edits = old if isinstance(old, list) else [(old, new)]
                mutated = original
                bad_anchor = False
                for edit_old, edit_new in edits:
                    if mutated.count(edit_old) != 1:
                        print(f"SKIP (anchor not unique: {mutated.count(edit_old)}): {label}")
                        failures.append(label)
                        bad_anchor = True
                        break
                    mutated = mutated.replace(edit_old, edit_new, 1)
                if bad_anchor:
                    continue
                SERVER.write_text(mutated)
                try:
                    proc = subprocess.run(
                        [PYTHON, "-m", "pytest", str(TESTS), "-x", "-q", "-k", selector],
                        cwd=HERE, capture_output=True, text=True, timeout=180)
                except subprocess.TimeoutExpired:
                    # A hang detects the mutation, but a suite that deadlocks
                    # instead of failing is a bad suite: flag it loudly.
                    print(f"HUNG    {label}\n"
                          f"         -> test suite deadlocked; make this "
                          f"mutation fail fast instead")
                    failures.append(f"{label} (hang, not a clean failure)")
                    continue
                caught = proc.returncode != 0
                status = "CAUGHT " if caught else "MISSED "
                tail = [ln for ln in proc.stdout.strip().splitlines() if ln][-1:]
                print(f"{status} {label}\n         -> {tail[0] if tail else ''}")
                if not caught:
                    failures.append(label)
        finally:
            shutil.copy2(backup, SERVER)
    restored = SERVER.read_text() == original
    print(f"\nserver.py restored byte-identical: {restored}")
    if failures or not restored:
        print(f"\nUNDETECTED MUTATIONS ({len(failures)}): " + "; ".join(failures))
        return 1
    print(f"\nAll {len(MUTATIONS)} mutations detected by the test suite.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
