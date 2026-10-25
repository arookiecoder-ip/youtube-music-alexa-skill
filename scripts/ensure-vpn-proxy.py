#!/usr/bin/env python3
"""Reattach only Music Box's VPN endpoint after Gluetun restart/recreation.

The application and direct fallback gateway never depend on this timer.
No VPN configuration, credentials or unrelated containers are modified.
"""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SERVICE = 'musicbox-vpn-proxy'


def needs_recreate(vpn, endpoint):
    if not vpn or not vpn.get('State', {}).get('Running'):
        return False
    if not endpoint or not endpoint.get('State', {}).get('Running'):
        return True
    target = endpoint.get('HostConfig', {}).get('NetworkMode', '')
    if target not in ('container:gluetun', 'container:' + vpn['Id']):
        return True
    return endpoint['State']['StartedAt'] < vpn['State']['StartedAt']


def inspect(name):
    result = subprocess.run(['docker', 'inspect', '--format',
        '{{json .Id}} {{json .State.Running}} {{json .State.StartedAt}} {{json .HostConfig.NetworkMode}}', name],
        capture_output=True, text=True, timeout=10)
    if result.returncode:
        return None
    # Values contain no whitespace; inspect only non-secret lifecycle fields.
    ident, running, started, mode = (json.loads(value) for value in result.stdout.split())
    return {'Id': ident, 'State': {'Running': running, 'StartedAt': started}, 'HostConfig': {'NetworkMode': mode}}


def main():
    vpn = inspect('gluetun')
    if not vpn or not vpn['State']['Running']:
        return
    result = subprocess.run(['docker', 'compose', '--profile', 'vpn', 'ps', '-a', '-q', SERVICE],
        cwd=ROOT, capture_output=True, text=True, timeout=15, check=True)
    endpoint = inspect(result.stdout.strip()) if result.stdout.strip() else None
    if needs_recreate(vpn, endpoint):
        subprocess.run(['docker', 'compose', '--profile', 'vpn', 'up', '-d', '--no-deps', '--force-recreate', SERVICE],
            cwd=ROOT, timeout=60, check=True)


if __name__ == '__main__':
    main()
