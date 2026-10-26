"""The container's WSGI server, started so the app can judge proxy headers itself.

waitress 3 deletes ``X-Forwarded-For`` from the environ unless its own
``trusted_proxy`` option names the exact proxy address -- unusable on a docker
network, where Caddy's address is ephemeral -- and a header the server dropped is
gone before Flask exists. ``multiuser.netids`` does that judgement properly (a
forwarded chain counts only when the immediate peer is itself a proxy, and the
address used is the one our proxy appended), so the server has to hand it over.
"""
from __future__ import annotations

import waitress

import server

# The waitress-serve arguments this replaced, with the clearing switched off.
ARGS = {
    "host": "0.0.0.0",
    "port": 5000,
    "threads": 16,
    "clear_untrusted_proxy_headers": False,
}


def main():
    # The app object, not "server:app" -- only the command-line runner resolves that.
    waitress.serve(server.app, **ARGS)


if __name__ == "__main__":
    main()
