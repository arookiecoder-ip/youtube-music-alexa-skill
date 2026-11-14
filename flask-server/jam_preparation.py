"""Prepare Jam audio and recommendations without delaying shared transport commands."""
import threading
import time


class JamPreparation:
    def __init__(self, rooms, valid, radio, warm, spawn, clock=time.monotonic):
        self.rooms, self.valid, self.radio = rooms, valid, radio
        self.warm, self.spawn, self.clock = warm, spawn, clock
        self.lock = threading.Lock()
        self.pending = set()
        self.recent = {}

    def schedule(self, token, state, identity, expand=False):
        video = state.get('video_id')
        if not video or (not state.get('selected') and not expand):
            return
        key = (token, state['queue_version'], video, expand)
        with self.lock:
            now = self.clock()
            self.recent = {k: v for k, v in self.recent.items() if now-v < 10}
            if key in self.pending or key in self.recent or len(self.pending) >= 32:
                return
            self.pending.add(key)
        def prepare():
            try:
                if not self.valid(token):
                    return
                if state.get('selected'):self.warm(video)
                queue = state.get('queue', [])
                index = state.get('queue_index', 0)
                if state.get('selected') and index+1 < len(queue):
                    self.warm(queue[index+1]['video_id'])
                if expand:
                    tracks = self.radio(video) or []
                    if self.valid(token) and self.rooms.extend_radio(token, state['queue_version'], video, tracks):
                        fresh = self.rooms.transact(token)
                        if fresh['selected'] and fresh['queue_index']+1 < len(fresh['queue']):
                            self.warm(fresh['queue'][fresh['queue_index']+1]['video_id'])
            except (PermissionError, ValueError):
                pass
            finally:
                with self.lock:
                    self.pending.discard(key)
                    self.recent[key] = self.clock()
        try:
            self.spawn(identity, prepare)
        except Exception:
            with self.lock:
                self.pending.discard(key)
            raise
