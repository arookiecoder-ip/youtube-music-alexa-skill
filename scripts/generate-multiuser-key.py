#!/usr/bin/env python3
"""Generate a Fernet key for MULTIUSER_ENCRYPTION_KEY.

Run this once per deployment, store the output in the deployment's secret
environment file, and back it up. Losing the key makes every stored per-user
YouTube/Amazon credential unrecoverable (accounts must reconnect).
"""
from __future__ import annotations

import sys

try:
    from cryptography.fernet import Fernet
except ImportError:
    sys.exit("Install the 'cryptography' package first: pip install cryptography")

if __name__ == "__main__":
    print(Fernet.generate_key().decode("ascii"))
