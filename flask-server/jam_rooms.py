"""One persistent, server-clocked queue per Jam, independent of account playback.

SQLite serializes edits across workers. Devices follow snapshots; their local
end-of-track events never advance the shared queue a second time.
"""
import copy
import json
import hashlib
import random
import secrets
import time


class JamConflict(ValueError):
    pass


class JamRooms:
    def __init__(self, database, clock=time.time):
        self.database, self.clock = database, clock

    def ensure(self):
        with self.database() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jam_rooms(token TEXT PRIMARY KEY, state TEXT NOT NULL)')
            db.execute('''CREATE TABLE IF NOT EXISTS jam_outputs(
                token TEXT NOT NULL, device TEXT NOT NULL, member TEXT, name TEXT NOT NULL,
                seen REAL NOT NULL, error TEXT NOT NULL DEFAULT '', PRIMARY KEY(token,device))''')
            db.execute('CREATE TABLE IF NOT EXISTS jam_commands(token TEXT, id TEXT, PRIMARY KEY(token,id))')
            db.commit()

    @staticmethod
    def tracks(items):
        if not isinstance(items, list) or len(items) > 5000:
            raise ValueError('Choose up to 5,000 songs.')
        tracks = []
        for raw in items:
            if not isinstance(raw, dict):
                raise ValueError('Invalid song.')
            video = raw.get('video_id') or raw.get('ytVideoId') or raw.get('videoId')
            if not isinstance(video, str) or len(video) != 11 or not all(c.isalnum() or c in '_-' for c in video) or not video.isascii():
                raise ValueError('Choose an online song for the Jam.')
            seconds = raw.get('durationSec', 0)
            duration = raw.get('duration_ms') or (seconds * 1000 if isinstance(seconds, (int, float)) else 0)
            if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 0 <= duration <= 86400000:
                duration = 0
            tracks.append({'video_id': video, 'title': str(raw.get('title') or 'Song')[:500],
                'artist': str(raw.get('artist') or '')[:500], 'thumbnail': str(raw.get('thumbnail') or raw.get('thumbnailUri') or '')[:2048],
                'duration_ms': int(duration), 'entry_id': secrets.token_hex(12)})
        return tracks

    def start(self, token, snapshot, device):
        self.ensure()
        tracks = self.tracks(snapshot.get('queue') or [])
        index = snapshot.get('queue_index', 0)
        index = index if isinstance(index, int) and 0 <= index < len(tracks) else 0
        state = dict(queue=tracks, queue_index=index if tracks else -1, playing=bool(tracks and snapshot.get('playing')),
            position_ms=max(0, int(snapshot.get('position_ms') or 0)), anchored_at=self.clock(),
            revision=1, queue_version=1, repeat='off', mode='single', selected=[device] if device else [])
        with self.database() as db:
            db.execute('INSERT OR REPLACE INTO jam_rooms VALUES(?,?)', (token, json.dumps(state)))
            db.commit()

    def _advance(self, state):
        now = self.clock()
        if state['playing']:
            state['position_ms'] += max(0, int((now - state['anchored_at']) * 1000))
            # Consume elapsed time, including while every device was disconnected.
            # Cap traversal to avoid a malformed zero-length queue spinning forever.
            for _ in range(len(state['queue']) + 1):
                item = state['queue'][state['queue_index']]
                duration = item['duration_ms']
                if not duration or state['position_ms'] < duration:
                    break
                if state['repeat'] == 'one':
                    state['position_ms'] %= duration
                    break
                state['position_ms'] -= duration
                if state['queue_index'] + 1 < len(state['queue']):
                    state['queue_index'] += 1
                elif state['repeat'] == 'all':
                    state['queue_index'] = 0
                    total = sum(row['duration_ms'] for row in state['queue'])
                    if total and all(row['duration_ms'] for row in state['queue']):
                        state['position_ms'] %= total
                else:
                    state.update(playing=False, position_ms=duration)
                    break
                state['revision'] += 1
        state['anchored_at'] = now

    def transact(self, token, action=None, body=None, member=None):
        self.ensure()
        body = body or {}
        with self.database() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM jam_tokens WHERE token=?', (token,)).fetchone():
                raise PermissionError('This Jam has ended.')
            if member:
                row = db.execute('SELECT removed,left_room FROM jam_members WHERE id=? AND token=?', (member, token)).fetchone()
                if not row or row['removed'] or row['left_room']:
                    raise PermissionError('Your Jam access has ended.')
            row = db.execute('SELECT state FROM jam_rooms WHERE token=?', (token,)).fetchone()
            if not row:
                raise JamConflict('This Jam needs a new invite. Ask the host to restart it.')
            state = json.loads(row['state'])
            clock_reversed = self.clock() < state['anchored_at']
            before = (state['queue_index'], state['playing'], state['revision'], tuple(state['selected']))
            self._advance(state)
            detached = {row['device'] for row in db.execute('''SELECT o.device FROM jam_outputs o
                JOIN jam_members m ON m.id=o.member WHERE o.token=? AND (m.removed=1 OR m.left_room=1)''', (token,))}
            state['selected'] = [device for device in state['selected'] if device not in detached]
            if action:
                command = body.get('command_id')
                if not isinstance(command, str) or not 1 <= len(command) <= 128:
                    raise ValueError('A command ID is required.')
                duplicate = db.execute('SELECT 1 FROM jam_commands WHERE token=? AND id=?', (token, command)).fetchone()
                if not duplicate:
                    if action in ('remove', 'reorder', 'queue', 'shuffle', 'next', 'previous', 'select', 'repeat', 'configure', 'sort', 'clear_played') and body.get('revision') != state['revision']:
                        raise JamConflict('The Jam changed. Refresh and try again.')
                    self._command(state, action, body)
                    state['revision'] += 1
                    db.execute('INSERT INTO jam_commands VALUES(?,?)', (token, command))
            after = (state['queue_index'], state['playing'], state['revision'], tuple(state['selected']))
            if action or before != after or clock_reversed:
                db.execute('UPDATE jam_rooms SET state=? WHERE token=?', (json.dumps(state), token))
            db.commit()
        result = copy.deepcopy(state)
        current = state['queue'][state['queue_index']] if state['queue_index'] >= 0 else {}
        result.update(current)
        result.update(queue_epoch=hashlib.sha256(token.encode()).hexdigest()[:16], duration_ms=current.get('duration_ms', 0), playback_confirmed=True, playback_processing=False,
                      playback_revision=state['revision'], server_time_ms=int(self.clock() * 1000))
        return result

    def _command(self, state, action, body):
        queue = state['queue']
        if action in ('play', 'pause', 'seek', 'next', 'previous', 'select') and not queue:
            raise ValueError('Add a song to the Jam first.')
        if action == 'play':
            state['playing'] = True
        elif action == 'pause':
            state['playing'] = False
        elif action == 'seek':
            value = body.get('position_ms')
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError('Invalid position.')
            duration = queue[state['queue_index']]['duration_ms']
            state['position_ms'] = min(value, duration) if duration else value
        elif action in ('next', 'previous', 'select'):
            index = body.get('index') if action == 'select' else state['queue_index'] + (1 if action == 'next' else -1)
            if action in ('next', 'previous') and state['repeat'] == 'all':
                index %= len(queue)
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(queue):
                raise ValueError('No song at that position.')
            state.update(queue_index=index, position_ms=0)
        elif action in ('queue', 'append', 'next_items'):
            tracks = self.tracks(body.get('tracks'))
            if not tracks:
                raise ValueError('Choose a song.')
            if action == 'queue':
                index = body.get('index', 0)
                if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(tracks):
                    raise ValueError('Invalid queue position.')
                state.update(queue=tracks, queue_index=index, position_ms=0, playing=True)
            else:
                if len(queue) + len(tracks) > 5000:
                    raise ValueError('This queue is full.')
                index = state['queue_index'] + 1 if action == 'next_items' else len(queue)
                queue[index:index] = tracks
                if state['queue_index'] < 0:
                    state.update(queue_index=0, position_ms=0)
            state['queue_version'] += 1
        elif action == 'remove':
            index = next((i for i, row in enumerate(queue) if row['entry_id'] == body.get('entry_id')), -1)
            if index < 0 or index == state['queue_index']:
                raise JamConflict('Choose a queued song other than the current song.')
            del queue[index]
            if index < state['queue_index']:
                state['queue_index'] -= 1
            state['queue_version'] += 1
        elif action == 'reorder':
            source, target = body.get('from'), body.get('to')
            if any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v < len(queue) for v in (source, target)):
                raise ValueError('Invalid queue positions.')
            current = queue[state['queue_index']]['entry_id']
            queue.insert(target, queue.pop(source))
            state['queue_index'] = next(i for i, row in enumerate(queue) if row['entry_id'] == current)
            state['queue_version'] += 1
        elif action == 'shuffle':
            upcoming = queue[state['queue_index'] + 1:]
            random.shuffle(upcoming)
            queue[state['queue_index'] + 1:] = upcoming
            state['queue_version'] += 1
        elif action in ('sort', 'clear_played'):
            index = state['queue_index']
            if action == 'sort':
                queue[index + 1:] = sorted(queue[index + 1:], key=lambda row: row['title'].casefold())
            elif index >= 0:
                state['queue'] = queue[index:]
                state['queue_index'] = 0
            state['queue_version'] += 1
        elif action == 'duration':
            index=state['queue_index']
            value=body.get('duration_ms')
            if index < 0 or queue[index]['entry_id'] != body.get('entry_id'):
                raise JamConflict('The song changed.')
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 86400000:
                raise ValueError('Invalid duration.')
            if not queue[index]['duration_ms']:
                queue[index]['duration_ms'] = value
        elif action == 'repeat':
            state['repeat'] = {'off': 'all', 'all': 'one', 'one': 'off'}[state['repeat']]
        elif action == 'configure':
            mode, selected = body.get('mode'), body.get('selected')
            if mode not in ('single', 'multiple') or not isinstance(selected, list) or len(selected) > 64 or any(not isinstance(d, str) or not 1 <= len(d) <= 128 for d in selected):
                raise ValueError('Choose valid audio outputs.')
            if mode == 'single' and len(selected) > 1:
                raise ValueError('Select one audio output in single-output mode.')
            state.update(mode=mode, selected=list(dict.fromkeys(selected)))
        else:
            raise ValueError('Unknown Jam command.')

    def presence(self, token, device, member, name, error=''):
        self.ensure()
        with self.database() as db:
            existing = db.execute('SELECT member FROM jam_outputs WHERE token=? AND device=?', (token, device)).fetchone()
            if existing and existing['member'] != member:
                raise PermissionError('This output belongs to another participant.')
            db.execute('INSERT OR REPLACE INTO jam_outputs VALUES(?,?,?,?,?,?)',
                       (token, device, member, str(name)[:100], self.clock(), str(error)[:200]))
            db.commit()

    def outputs(self, token):
        self.ensure()
        with self.database() as db:
            return [dict(row) for row in db.execute('''SELECT o.device,o.name,o.seen,o.error FROM jam_outputs o
                LEFT JOIN jam_members m ON m.id=o.member WHERE o.token=?
                AND (o.member IS NULL OR (m.removed=0 AND m.left_room=0))''', (token,))]
