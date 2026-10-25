"""Per-identity YouTube Music credential files.

ytmusicapi takes a path, not a blob, so every identity needs its own JSON file.
The owner keeps using ``YTMUSIC_AUTH_FILE`` untouched -- an existing install must
not lose its session on upgrade. Accounts get one file per user id under
``<data dir>/youtube_auth/``, and the encrypted ``user_provider_credentials`` row
is the source of truth: the file is a derived cache that is rebuilt on demand
after a volume wipe or a restore from backup.

An identity that is neither ``owner`` nor ``user:<id>`` resolves to ``None``, which
callers treat as "public catalog only". Falling back to the owner's file would
hand one account's session cookies to a stranger.
"""
from __future__ import annotations

import os
import re
import stat
import tempfile

OWNER = "owner"

# users.id is a TEXT uuid4 (multiuser/db.py); anything else is not an account.
_ACCOUNT_IDENTITY = re.compile(r"^user:([0-9a-fA-F-]{8,64})$")


def account_user_id(identity: str) -> str:
    """The user id inside a ``user:<id>`` identity, or "" for any other value."""
    match = _ACCOUNT_IDENTITY.match(identity or "")
    return match.group(1) if match else ""


class YouTubeCredentialStore:
    def __init__(self, account_dir: str, owner_path):
        self.account_dir = str(account_dir)
        self._owner_path = owner_path

    def path_for(self, identity: str) -> str | None:
        """Where this identity's browser-auth JSON lives (None = no private access)."""
        if identity == OWNER:
            return self._owner_path()
        user_id = account_user_id(identity)
        return os.path.join(self.account_dir, user_id + ".json") if user_id else None

    def read(self, identity: str) -> str:
        path = self.path_for(identity)
        if not path or not os.path.isfile(path):
            return ""
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return handle.read()
        except OSError:
            return ""

    def write(self, identity: str, payload: str) -> str:
        """Atomically store auth JSON for one identity, readable only by the app."""
        path = self.path_for(identity)
        if not path:
            raise ValueError("No credential store for this identity.")
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".yt-auth-", suffix=".json", dir=parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.chmod(temp_path, stat.S_IRUSR | stat.S_IWUSR)
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        return path

    def remove(self, identity: str) -> bool:
        """Forget an identity's derived file. Owner files are never deleted here."""
        if identity == OWNER:
            return False
        path = self.path_for(identity)
        if not path or not os.path.isfile(path):
            return False
        try:
            os.unlink(path)
        except OSError:
            return False
        return True

    def identities(self) -> list[str]:
        """Identities with a credential file on disk, for background keepalive."""
        found = []
        if os.path.isfile(self._owner_path()):
            found.append(OWNER)
        try:
            names = os.listdir(self.account_dir)
        except OSError:
            return found
        for name in names:
            # <uuid>.json is 36 characters of id plus the extension.
            if len(name) == 41 and name.endswith(".json"):
                found.append("user:" + name[:-5])
        return found
