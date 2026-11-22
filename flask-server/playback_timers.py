"""Persistent, account-scoped sleep deadlines shared by server workers."""
import json
import secrets
import time


class PlaybackTimers:
    def __init__(self, database, clock=time.time):
        self.database, self.clock = database, clock

    def ensure(self):
        with self.database() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS playback_sleep_timers (
                identity TEXT PRIMARY KEY, id TEXT NOT NULL, deadline REAL NOT NULL,
                target TEXT NOT NULL, retry_at REAL NOT NULL DEFAULT 0)''')
            db.commit()

    def get(self, identity):
        self.ensure()
        with self.database() as db:
            row = db.execute('SELECT * FROM playback_sleep_timers WHERE identity=?', (identity,)).fetchone()
        return dict(row) if row else None

    def set(self, identity, minutes, target):
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 180:
            raise ValueError('Choose a timer between 1 and 180 minutes.')
        self.ensure()
        with self.database() as db:
            db.execute('INSERT OR REPLACE INTO playback_sleep_timers VALUES(?,?,?,?,0)',
                       (identity, secrets.token_urlsafe(24), self.clock()+minutes*60, json.dumps(target)))
            db.commit()
        return self.get(identity)

    def cancel(self, identity, timer_id=None):
        self.ensure()
        with self.database() as db:
            if timer_id is None:
                db.execute('DELETE FROM playback_sleep_timers WHERE identity=?', (identity,))
            else:
                db.execute('DELETE FROM playback_sleep_timers WHERE identity=? AND id=?', (identity, timer_id))
            db.commit()

    def due(self):
        self.ensure()
        with self.database() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute('SELECT * FROM playback_sleep_timers WHERE deadline<=? AND retry_at<=?',
                              (self.clock(), self.clock())).fetchall()
            # A lease prevents every WSGI worker from dispatching the same pause.
            for row in rows:
                db.execute('UPDATE playback_sleep_timers SET retry_at=? WHERE id=?', (self.clock()+30, row['id']))
            db.commit()
        return [dict(row, target=json.loads(row['target'])) for row in rows]


def same_output(target, current):
    if current.get('handoff_pending') or target['playback_output'] != current['playback_output']:
        return False
    if target['playback_output'] == 'phone':
        return bool(target.get('output_owner')) and target['output_owner'] == current.get('output_owner')
    # A freshly restarted server may not yet have polled Amazon's current serial.
    return bool(target.get('output_serial')) and current.get('output_serial') in ('', target['output_serial'])
