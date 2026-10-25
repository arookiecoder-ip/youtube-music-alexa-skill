# Multi-user data model

Schema version: **2**

All tables are new and namespaced in the multi-user database (`DB_FILE`). They
do **not** replace or alter the legacy single-owner tables (`web_sessions`,
`kv`, `jam_tokens`, `playlist_tracks`). Initialization creates missing tables
and applies additive migrations; it never drops a column or table.

## `users`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | TEXT PK | UUID v4 |
| `username` | TEXT UNIQUE NOCASE | 3–32 chars, `[A-Za-z0-9][A-Za-z0-9._-]*`, reserved names blocked |
| `email` | TEXT NOCASE | Optional, unique when non-empty, lower-cased |
| `display_name` | TEXT | 120 chars max |
| `password_hash` | TEXT | PBKDF2-SHA256, 600,000 iterations |
| `role` | TEXT | `owner`, `admin`, `user` |
| `status` | TEXT | `active`, `disabled`, `invited`, `deleted` |
| `must_change_password` | INTEGER | Stored for Phase 3 enforcement |
| `totp_secret_enc` | TEXT | Reserved; encrypted when enrollment lands |
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
active owner. Deleting a user is a soft delete: the email is set to `NULL`, the
username remains reserved, sessions and email tokens are revoked, and provider
credentials/preferences are removed.

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

Single-use, purpose-bound email links. Only a SHA-256 hash is stored, never the
raw token.

| Column | Notes |
| --- | --- |
| `token_hash` | TEXT PK |
| `user_id` | FK → `users.id`, cascade delete |
| `purpose` | `verify`, `reset`, `magic`, `invite` |
| `email` | Address the link was issued for |
| `created_at`, `expires_at` | Unix timestamps |
| `used_at` | `0` = unused |
| `ip`, `user_agent` | Request metadata |

Rules:

- Creating a token for a purpose deletes the previous live token for that user
  and purpose.
- A token only matches while its `email` equals the user's current email.
  Changing the address invalidates every outstanding link.
- Password reset, invitation acceptance and password changes delete all tokens
  for the user.
- TTLs: verify 24h, reset 60 min, magic 15 min, invite 72h (configurable).
- Expired rows are pruned during initialization.

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
| `action` | Short event name (login, user_updated, email_verified, …) |
| `target_user_id` | Optional affected user |
| `details_json` | Non-secret JSON details |
| `ip`, `user_agent` | Truncated request metadata |
| `created_at` | Unix timestamp |

Exports are available at `/admin/audit/export.csv` (last 5,000 events).

## `multiuser_schema_migrations`

`version` PK and `applied_at`. Current versions:

- **v1** — base tables above.
- **v2** — email verification columns + `user_email_tokens`.

Migrations are additive and idempotent. The runner checks `PRAGMA table_info`
before adding columns, so a partially applied v2 deployment can safely retry.

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
| Expired email tokens | Purged after expiry + 1 day |
| Outbox `.eml` files | Manual; configure SMTP and delete after testing |
| Backups | Operator-defined; currently no automatic rotation |
