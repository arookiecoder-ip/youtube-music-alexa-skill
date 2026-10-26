"""Gating for tests that read the browser-auth sidecar's sources.

Those files ship in the sidecar image, not the application image, so a test that
execs them cannot run there. Reporting that as a failure would train everyone to
ignore a red suite, so such tests stand down instead -- on the host, where the
checkout is complete, and in the sidecar image they still run in full.
"""
from pathlib import Path
import unittest

BROWSER_AUTH = Path(__file__).resolve().parents[2] / "browser-auth"


def _gate(*names):
    missing = [name for name in names if not (BROWSER_AUTH / name).is_file()]
    return unittest.skipUnless(
        not missing,
        'browser-auth/' + ', '.join(missing) + ' is not present in this deployment')


def requires_sidecar(*names):
    """Class or method decorator: run only where these sidecar files exist."""
    return _gate(*(names or ("service.py",)))
