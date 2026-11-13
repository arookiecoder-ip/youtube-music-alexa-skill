# Account library and device isolation

The server Google credential is download infrastructure only. `_yt_auth_file`
returns a user's own credential or no credential. Neither personalized reads,
likes, playlists nor history borrow the server credential, regardless of
`YOUTUBE_SERVER_SESSION`. Download-cookie preference and automatic cookie renewal
remain separate.

Device presence, commands, retired sessions and wake conditions now resolve through
the same authenticated identity context as playback output. Playlist-track cache
keys include identity, including the `LM` virtual playlist. Like-change versions
belong to the account's playback state. Phones cap active devices and recently
retired sessions; retired device records expire after two minutes of inactivity.

## Unlinked libraries

`staged_library` in the application SQLite database uses the primary key
`(identity, kind, id)` with `WITHOUT ROWID`. Every read, update and deletion binds
the trusted request identity; callers cannot supply another account's identity in
JSON or query parameters. SQLite does not provide PostgreSQL-style native RLS.
These are server-enforced row ownership checks, not a claim of database-native RLS.
Database files must remain private to the service; no client receives direct SQL
access.

Likes and user playlists work without linking Google. Limits are 5,000 staged
records, 100 playlists per identity, 5,000 songs per playlist and 2 MB per playlist
payload. Playlist listings extract only summary fields in SQL. Individual playlist
lookups use the complete composite key. Sync loads one playlist at a time. Locks
use a fixed-size stripe array rather than an ever-growing per-user lock map.

Connecting Google starts staging sync; linked profile/library reads retry pending
sync. One sync worker runs at a time. Failed operations retain staging. Successful
Google rating responses delete their corresponding staging rows. Playlists store
the created Google playlist ID before uploading tracks and delete staging only
after successful item upload. Retries compare Google's existing tracks before
adding missing songs. Pending playlists remain visible during retries.

A process failure precisely between Google creating a playlist and persisting its
returned ID can still leave a duplicate remote playlist on retry: Google does not
provide a transaction shared with SQLite. This implementation does not promise
exactly-once remote playlist creation. Staging is deliberately retained on errors
rather than deleting unsynced user data. Whole-account deletion purges staging
through the existing durable artifact-purge retry path.

## Phone history

Phone plays are staged in encrypted local preferences partitioned by a hash of the
Music Box account ID, retaining the latest 100 distinct songs. Switching accounts
keeps pending history private for a later return to the original account. Local
history is combined with Google history when displayed. Reconnecting Google,
viewing history or playing a song retries sync. Only a confirmed Google history
HTTP 204 removes the local item. Unsigned Google accounts receive HTTP 403 from
the history-write endpoint; the server does not persist their history or redirect
it to download credentials.

## Retention

Schema version 13 adds expiry indexes for invites, Alexa pair codes/access tokens
and signup challenges. The existing account sweeper now periodically prunes
expired authentication data and the 90-day audit journal in indexed batches of
500 rows per table. Active rows and pending staging are preserved. SQLite reuses
freed pages; automatic full-database VACUUM is intentionally avoided because it
holds the writer and temporarily requires another copy of the database.

Audio is already stored in anonymous current-track temporary files, capped at
64 MiB and 512 ranges per relay. The bounded relay pool closes files on eviction;
track changes, stream invalidation and account deletion close them as well.
Process exit releases anonymous files even after a crash. No retained collection
of downloaded songs is introduced by these changes.

## Validation

Regression coverage includes cross-account library CRUD, guest denial, failed and
successful Google sync, retrying an existing remote playlist, indexed query plans,
bounded expiry deletion, device presence/control isolation, account purge,
security boundaries and current-track audio lifecycle. Live Google account data
was not used or modified in these tests. Android compilation and emulator tests
run in the MusicBox-APP GitHub Actions workflow, not on this server.
