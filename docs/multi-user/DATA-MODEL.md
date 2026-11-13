# Multi-user data model

Schema version: **5**

All tables are new and namespaced in the multi-user database (`DB_FILE`). They
do **not** replace or alter the legacy single-owner tables (`web_sessions`,
`kv`, `jam_tokens`, `playlist_tracks`). Initialization creates missing tables
and applies additive migrations; v5 is the first migration that removes
anything, and it removes exactly one column nobody reads any more.

## `users`

An account has **one name**: `username`. It is what the queue credits and what
the panel lists, and it signs in alongside the email address, so there is no
second, friendlier name to keep in sync.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | TEXT PK | UUID v4 |
| `username` | TEXT UNIQUE NOCASE | 3–32 chars, `[A-Za-z0-9][A-Za-z0-9._-]*`, reserved names blocked. The account's only name, and one of the two things `authenticate` accepts |
| `email` | TEXT NOCASE | Optional, unique when non-empty, lower-cased. The other sign-in handle, and the only route back via a reset code |
| `password_hash` | TEXT | PBKDF2-SHA256, 600,000 iterations |
| `role` | TEXT | `owner`, `admin`, `user` |
| `status` | TEXT | `active`, `disabled`, `invited`, `deleted` |
| `must_change_password` | INTEGER | Stored for Phase 3 enforcement |
| `totp_secret_enc` | TEXT | Fernet-encrypted shared secret; `has_totp` is derived from it being set and never returns the value |
| `totp_pending_enc` | TEXT | Added in v3 — an enrolment that has been started and not confirmed. Also encrypted, and excluded from every read that feeds `has_totp`, so a half-finished enrolment cannot be used to sign in |
| `totp_pending_at` | REAL | Added in v3; when that pending secret expires |
| `totp_last_step` | INTEGER | Added in v3; the counter window already spent, so one code is good for one login |
| `failed_login_count` | INTEGER | Reset on success; locks after `MULTIUSER_LOGIN_MAX_ATTEMPTS` |
| `locked_until` | REAL | Unix timestamp |
| `created_at`, `updated_at` | REAL | Unix timestamps |
| `last_login_at` | REAL | Unix timestamp |
| `created_by` | TEXT | Actor user ID or `system` |
| `deleted_at` | REAL | Soft-delete timestamp |
| `email_verified` | INTEGER | Added in v2 |
| `email_verified_at` | REAL | Added in v2 |
| `last_email_at` | REAL | Added in v2; internal throttling aid |

Active owners cannot be demoted, disabled or deleted while they are the last
active owner. Deleting a user from the panel is a soft delete: the email is set to
`NULL`, the TOTP secret is cleared, the username remains reserved, sessions and email
tokens are revoked, provider credentials/preferences are removed — and the artifacts
outside this table (queue snapshot, credential file, download jar, Amazon cookie
directory, Jam tokens, Chromium profile) are purged the same way, so a **Restored**
account comes back without its connected providers. `/account`'s self-delete is
different: it needs the current password and the username typed back, refuses the last
owner and the last administrator, and removes the row outright, because a person who
deleted themselves must not be resurrectable.

## `user_sessions`

Server-side admin sessions. The browser cookie contains only the random raw
SID; the database stores `sha256(raw)`.

| Column | Notes |
| --- | --- |
| `sid_hash` | TEXT PK |
| `user_id` | FK → `users.id`, cascade delete |
| `created_at`, `last_seen_at`, `expires_at` | Unix timestamps |
| `ip`, `user_agent` | Truncated client metadata |
| `revoked_at` | `0` = live |

Expiry is the smaller of `MULTIUSER_SESSION_HOURS` (sliding, default 12h) and
`MULTIUSER_SESSION_ABSOLUTE_DAYS` (default 7d). Expired/revoked rows are purged
during initialization and on session creation.

## `user_email_tokens`

Single-use, purpose-bound credentials. Two shapes share the table:

- `verify` and `reset` hold a **typed numeric code** (v4). The row still has a
  random `token_hash` primary key so one row means one live credential, but the
  six-digit code is in `code_enc` and no route or email ever builds a URL from it.
- `invite` and `magic` are **links**. Only a SHA-256 hash of the URL token is
  stored, never the raw token.

| Column | Notes |
| --- | --- |
| `token_hash` | TEXT PK; SHA-256 of the URL token, or a random 256-bit value for a code row |
| `user_id` | FK → `users.id`, cascade delete |
| `purpose` | `verify`, `reset` (codes), `magic`, `invite` (links) |
| `email` | Address the credential was issued for |
| `created_at`, `expires_at` | Unix timestamps |
| `used_at` | `0` = unused |
| `ip`, `user_agent` | Request metadata |
| `code_enc` | v4 — Fernet-encrypted `{"code": "123456"}`; empty for link purposes |
| `code_attempts` | v4 — wrong guesses spent against this row |

Rules:

- Creating a credential for a purpose deletes the previous live one for that user
  and purpose, so there is exactly one code to type.
- A credential only matches while its `email` equals the user's current email.
  Changing the address invalidates every outstanding code and link.
- Password reset, invitation acceptance and password changes delete all
  credentials for the user.
- **Codes are encrypted, not hashed.** Six digits is a space of one million, so a
  digest would be precomputed from a stolen database; the Fernet key lives outside
  the database by deployment. Protection against *online* guessing comes from
  `code_attempts`: every mismatch increments it, and at
  `MULTIUSER_EMAIL_CODE_ATTEMPTS` (default 5) the row is **consumed** and the code
  is dead until a resend. The account is never locked — otherwise anyone who knew
  a username could deny that person sign-in for free.
- TTLs: verify and reset 15 minutes (`MULTIUSER_EMAIL_CODE_MINUTES`), magic 15
  minutes, invite 72h (configurable).
- Expired rows are pruned during initialization.

## `system_settings`

A small key/value table for policies an administrator may change without a
redeploy. Deliberately not per-user.

| Column | Notes |
| --- | --- |
| `setting_key` | TEXT PK |
| `setting_value` | TEXT, default `''` |
| `updated_at` | Unix timestamp |
| `updated_by` | Actor user ID; empty for a system default |

The only key today is `signup_open` (`1`/`0`). **No row means open**, so an
upgrade does not silently close the door. The switch is consulted on every
`registration_open()` call — there is no cache, so a click takes effect on the
next page view. `MULTIUSER_SELF_SIGNUP_ENABLED=0` remains the ceiling: the switch
can close a deployment that allows sign-up, but it cannot open one that does not.
Every change is audited as `signup_policy_changed` with the before and after.

## `user_provider_credentials`

| Column | Notes |
| --- | --- |
| `user_id`, `provider` | Composite PK; provider is `youtube` or `amazon` |
| `secret_enc` | Fernet token (AES-128-CBC + HMAC-SHA256) |
| `metadata_json` | Non-secret metadata (display name, expiry hints) |
| `status` | `connected`, `expired`, `error`, `revoked` |
| `last_error` | Safe, truncated error text |
| `updated_at`, `last_validated_at` | Unix timestamps |

The encryption key comes from `MULTIUSER_ENCRYPTION_KEY`. If unset,
`SECRET_KEY` is expanded with HKDF-SHA256 into a Fernet key. **Losing the key
makes every stored credential unrecoverable**; users must reconnect. Back up the
key separately from the database. The admin UI never renders a secret.

## `user_preferences`

`(user_id, pref_key)` composite key with a string value and update timestamp.
Used in Phase 2+ for per-user UI and playback preferences.

## `user_invites`

Legacy v1 invitation table retained for compatibility. New invitations use the
`invite` purpose in `user_email_tokens`. The table is unused by the current
routes and can be migrated/removed in a future schema version.

## `audit_log`

Append-only administrative journal, retained for 90 days then pruned at startup.

| Column | Notes |
| --- | --- |
| `id` | Autoincrement PK |
| `actor_user_id`, `actor_label` | NULL/system for unauthenticated events |
| `action` | Short event name. Besides the Phase 1 set, the account doors add `signup`, `signup_honeypot`, `signup_too_fast`, `password_reset_requested`, `password_reset_requested_unknown`, `password_reset_honeypot`, `email_verified`, `password_changed`, `sessions_revoked`, `totp_enrollment_started`, `totp_enrollment_cancelled`, `totp_enabled`, `totp_disabled`, `account_delete_failed`, `account_deleted_self`, `provider_disconnected` and `admin_login_2fa`. Codes add `verification_code_email_sent|failed` and `password_reset_code_email_sent|failed`, plus `signup_policy_changed`; the code itself is never written into `details_json`, because a live code is a password. A refused self-delete or a scripted sign-up is audited rather than dropped, because the attempts are the signal. `provider_disconnected` carries the provider, whether a credential row was actually removed (`was_linked`) and either `removed` or the list of artifacts that survived, so the journal cannot claim a disconnection the files contradict. |
| `target_user_id` | Optional affected user |
| `details_json` | Non-secret JSON details |
| `ip`, `user_agent` | Truncated request metadata |
| `created_at` | Unix timestamp |

Exports are available at `/admin/audit/export.csv` (last 5,000 events).

## `multiuser_schema_migrations`

`version` PK and `applied_at`. Current versions:

- **v1** — base tables above.
- **v2** — email verification columns + `user_email_tokens`.
- **v3** — `totp_pending_enc` / `totp_pending_at` / `totp_last_step` on `users`,
  and `idx_users_created_by` `(created_by, created_at)`, because the daily
  self-signup cap is counted on every anonymous registration.
- **v4** — `code_enc` / `code_attempts` on `user_email_tokens` (verification and
  reset become typed codes), and `system_settings` (the administrator's sign-up
  switch). `verify` and `reset` cease to be link purposes; `magic` and `invite`
  are unchanged.
- **v5** — `ALTER TABLE users DROP COLUMN display_name`. Every account had two
  names, and the second one only ever duplicated or contradicted the handle the
  queue and the panel already show. The drop is guarded on `PRAGMA table_info`
  and on SQLite's 3.35+ support for `DROP COLUMN`, so an older build leaves an
  unread empty column behind instead of failing to start.

Migrations are additive and idempotent. The runner checks `PRAGMA table_info`
before adding or dropping, so a partially applied deployment can safely retry.

## Tables the player layer owns in the same file

`MULTI_USER_ENABLED=0` creates neither of them; both are the price of per-account
playback and account deletion, not of the admin panel.

**`playback_dead`** — `identity TEXT PRIMARY KEY, expires_at REAL`. The durable half
of the deletion tombstone (`PlaybackStore.tombstone`, 900s by default). `save()` and
`PlaybackPersistence.submit()` refuse an identity listed here, which is what stops a
heartbeat that was already in flight when the account died from re-creating its
snapshot row a moment after the purge deleted it. The in-process mirror is primed from
this table at startup, so the refusal survives a restart; expired rows are pruned
lazily. `playback_snapshot` is keyed by the same `identity`
(`owner`, `user:<uuid>`, or the Jam host's).

**`pending_purges`** — `user_id TEXT PRIMARY KEY, attempts, last_error, created_at,
next_at, scope`. One row per account whose artifacts are not gone yet. Written by
`server._forget_account_artifacts` through `account_purge.PendingPurges`, drained
by the `account-purge-sweeper` daemon with 30s→2h exponential backoff, and dropped
with a logged error after 24 hours — a queue must not grow just because one
filesystem is unwritable. `scope` is a comma-separated set of `youtube` /
`amazon` / `all`, merged when a second attempt targets the same account, so a
provider disconnect that 409s on a still-open Chromium profile retries that
provider instead of re-running a whole deletion. `last_error` is truncated to 400
characters and never holds a secret.

## Backup and recovery

- `Database.backup_to()` uses SQLite's online backup API and writes `0600`.
- Manual backups are created from **System → Create backup now** and stored in
  `MULTIUSER_BACKUP_DIR` (default `/data/backups` inside the container).
- Required recovery material:
  1. A database backup.
  2. `MULTIUSER_ENCRYPTION_KEY` (or the old `SECRET_KEY` if the derived-key
     fallback was used).
  3. `SECRET_KEY` (invalidates all sessions if lost, but not stored data).
  4. SMTP credentials (for email flows).
- Restore into a new container/volume, verify `PRAGMA quick_check`, then point
  Caddy at it. Never restore over a running SQLite file.

## Retention

| Data | Retention |
| --- | --- |
| Audit log | 90 days |
| Expired sessions | Purged after expiry + 1 day |
| Expired email codes and links | Purged after expiry + 1 day |
| Spent code rows (`code_attempts` at the cap) | Dead in place until the next resend replaces or expiry prunes the row |
| `playback_dead` tombstones | 900 seconds after the deletion, pruned lazily |
| `pending_purges` rows | Retried with backoff for 24 hours, then dropped with a logged error |
| Outbox `.eml` files | Manual; configure SMTP and delete after testing |
| Backups | Operator-defined; currently no automatic rotation |
