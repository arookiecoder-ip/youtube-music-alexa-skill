"""Shared snapshot rules. Callers hold the playback state lock."""
import time


def restore_snapshot(state, previous):
    """A failed command restores content, but is itself a newer state change.

    Reusing the old revision makes clients correctly rejecting stale polls
    also reject this rollback, leaving them stuck on the pending command.
    """
    revision = max(int(state.get('playback_revision', 0)),
                   int(previous.get('playback_revision', 0))) + 1
    state.clear()
    state.update(previous)
    state.update(playback_revision=revision, updated_at=time.time())
