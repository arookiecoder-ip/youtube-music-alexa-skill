"""Persistent Jam membership. Invitations are not membership credentials."""
import secrets
import time


class JamMembers:
    def __init__(self, database):
        self.database = database

    def ensure(self):
        with self.database() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS jam_members (
                id TEXT PRIMARY KEY, token TEXT NOT NULL, user_id TEXT,
                name TEXT NOT NULL, device_id TEXT NOT NULL DEFAULT '',
                removed INTEGER NOT NULL DEFAULT 0, left_room INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS jam_members_room ON jam_members(token)')
            db.commit()

    def join(self, token, user_id, name, device_id='', previous=None):
        self.ensure()
        with self.database() as db:
            if user_id and db.execute('SELECT 1 FROM jam_members WHERE token=? AND user_id=? AND removed=1', (token, user_id)).fetchone():
                raise PermissionError('The host removed you from this Jam.')
            if previous:
                row = db.execute('SELECT * FROM jam_members WHERE id=? AND token=?', (previous, token)).fetchone()
                if row and row['user_id'] == user_id:
                    if row['removed']:
                        raise PermissionError('The host removed you from this Jam.')
                    db.execute('UPDATE jam_members SET left_room=0 WHERE id=?', (previous,))
                    db.commit()
                    return previous
            member = secrets.token_urlsafe(24)
            db.execute('INSERT INTO jam_members(id,token,user_id,name,device_id,created_at) VALUES(?,?,?,?,?,?)',
                       (member, token, user_id, str(name or 'Guest')[:80], device_id, time.time()))
            db.commit()
            return member

    def get(self, member, token, user_id):
        self.ensure()
        with self.database() as db:
            row = db.execute('SELECT * FROM jam_members WHERE id=? AND token=?', (member, token)).fetchone()
        if not row or row['removed'] or row['left_room'] or row['user_id'] != user_id:
            return None
        return dict(row)

    def list(self, token):
        self.ensure()
        with self.database() as db:
            return [dict(row) for row in db.execute('SELECT id,name,device_id,user_id FROM jam_members WHERE token=? AND removed=0 AND left_room=0 ORDER BY created_at', (token,))]

    def remove(self, token, member):
        self.ensure()
        with self.database() as db:
            row = db.execute('SELECT user_id FROM jam_members WHERE token=? AND id=?', (token, member)).fetchone()
            if not row:
                return False
            if row['user_id']:
                db.execute('UPDATE jam_members SET removed=1 WHERE token=? AND user_id=?', (token, row['user_id']))
            else:
                db.execute('UPDATE jam_members SET removed=1 WHERE token=? AND id=?', (token, member))
            db.commit()
        return True

    def leave(self, member):
        self.ensure()
        with self.database() as db:
            db.execute('UPDATE jam_members SET left_room=1 WHERE id=?', (member,))
            db.commit()
