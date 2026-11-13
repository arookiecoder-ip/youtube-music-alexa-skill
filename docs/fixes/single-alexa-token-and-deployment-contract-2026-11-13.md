# Alexa account-ID deployment regression and token presentation

The newer account/library deployment came from a branch without the account-ID
changes already on master. Its live authentication code still required
X-MusicBox-Email although the private skill was configured with an account ID.
Both branches also assigned different operations to migration 13. This integration
preserves the library work and the account/session changes; migration 14 repairs
either schema variant without resurrecting previously revoked credentials.

Migration 15 keeps the newest unrevoked token per account and installs a partial
unique index. Atomic rotation revokes its predecessor; direct inserts cannot
create another active credential. Tokens for other accounts remain independent.
Admin pages show only the current token's first twelve characters. History and
full-token reveal are removed. Generate/Rotate remain available and return a
CSRF-protected, no-store download of the private configuration, including the
account ID. The secret is never rendered into the page or session cookie.

Skill requests send the account-ID header when configured, with legacy email only
for older configurations. Spoken access failures say “Music Box access was denied”.
Old cached playback messages are normalized so they cannot keep instructing users
to configure MUSICBOX_EMAIL.

The exact Docker image must pass an offline capability contract both in CI and on
the server before replacement: ID-only authentication with a changed recovery
email, ownership isolation, upgrade from the alternate migration-13 state, and
single-token enforcement. An image missing this contract is rejected before the
running application is changed. The previous production image was tested and
correctly rejected by this gate.
