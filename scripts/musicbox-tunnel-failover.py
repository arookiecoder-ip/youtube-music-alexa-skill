#!/usr/bin/env python3
"""Switch one pinned, proxied DNS record between Tunnel and its protected origin.

Run by a systemd timer with a DNS-only API credential. Never persist or log tokens.
A separate health-only tunnel hostname allows recovery checks while DNS uses A.
"""
from __future__ import annotations
import argparse
import fcntl
import json
import http.client
import ipaddress
import socket
import ssl
import urllib.parse
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request


def next_state(state, healthy, failure_threshold=3, recovery_threshold=3):
    state = dict(state)
    state['successes'] = state.get('successes', 0) + 1 if healthy else 0
    state['failures'] = state.get('failures', 0) + 1 if not healthy else 0
    target = state.get('mode', 'tunnel')
    if not healthy and state['failures'] >= failure_threshold:
        target = 'origin'
    elif healthy and state['successes'] >= recovery_threshold:
        target = 'tunnel'
    return state, target


def validate_record(record, config):
    if record.get('id') != config['record_id'] or record.get('name') != config['hostname']:
        raise ValueError('DNS identity mismatch; refusing to modify record')
    if record.get('proxied') is not True:
        raise ValueError('DNS proxy disabled; refusing to overwrite operator change')
    if record.get('type') == 'CNAME' and record.get('content') == config['tunnel_target']:
        return 'tunnel'
    if record.get('type') == 'A' and record.get('content') == config['origin_ip']:
        return 'origin'
    raise ValueError('Unexpected DNS target; refusing to overwrite operator change')


def ready_response(status, headers, body):
    data = json.loads(body)
    return (status == 200 and headers.get('X-MusicBox-Ingress') == 'tunnel'
            and isinstance(data, dict) and data.get('status') == 'ready')


def check_health(config):
    headers = {'User-Agent': 'MusicBox-Tunnel-Health/1.0', 'Cache-Control': 'no-cache'}
    timeout = config.get('health_timeout', 8)
    request = urllib.request.Request(config['health_url'], headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return ready_response(response.status, response.headers, response.read(65536))
    except urllib.error.URLError as exc:
        if not isinstance(exc.reason, socket.gaierror):
            return False
    except (OSError, ValueError):
        return False
    # A newly-created probe hostname can remain negatively cached by the host's
    # resolver. Resolve it independently without disabling TLS hostname checking.
    try:
        url = urllib.parse.urlsplit(config['health_url'])
        if url.scheme != 'https' or url.port not in (None, 443) or url.username:
            return False
        query = urllib.parse.urlencode({'name': url.hostname, 'type': 'A'})
        request = urllib.request.Request('https://cloudflare-dns.com/dns-query?' + query,
                                         headers={'Accept': 'application/dns-json'})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            dns = json.loads(response.read(65536))
        if dns.get('Status') != 0:
            return False
        addresses = [str(ipaddress.IPv4Address(row['data'])) for row in dns.get('Answer', [])
                     if row.get('type') == 1]
        if not addresses:
            return False
        connection = http.client.HTTPSConnection(url.hostname, timeout=timeout)
        raw = socket.create_connection((addresses[0], 443), timeout=timeout)
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        connection.sock = context.wrap_socket(raw, server_hostname=url.hostname)
        try:
            path = url.path or '/'
            if url.query:
                path += '?' + url.query
            connection.request('GET', path, headers=headers)
            response = connection.getresponse()
            return ready_response(response.status, response.headers, response.read(65536))
        finally:
            connection.close()
    except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException):
        return False


def api(config, token, payload=None):
    url = ('https://api.cloudflare.com/client/v4/zones/' + config['zone_id'] +
           '/dns_records/' + config['record_id'])
    request = urllib.request.Request(url,
        data=json.dumps(payload).encode() if payload is not None else None,
        method='PUT' if payload is not None else 'GET',
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.loads(response.read(1024 * 1024))
    if data.get('success') is not True:
        raise RuntimeError('Cloudflare DNS API rejected request')
    return data['result']


def switch(config, token, target):
    record = api(config, token)
    mode = validate_record(record, config)
    if mode == target:
        return mode
    payload = {'type': 'CNAME' if target == 'tunnel' else 'A',
               'name': config['hostname'],
               'content': config['tunnel_target'] if target == 'tunnel' else config['origin_ip'],
               'proxied': True, 'ttl': 1}
    if record.get('comment') is not None:
        payload['comment'] = record['comment']
    result = api(config, token, payload)
    if validate_record(result, config) != target:
        raise RuntimeError('Cloudflare DNS update did not match requested target')
    print('DNS switched to ' + target + ' for ' + config['hostname'], flush=True)
    return target


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='/etc/musicbox-tunnel-failover/config.json')
    parser.add_argument('--state-dir', default='/var/lib/musicbox-tunnel-failover')
    parser.add_argument('--target', choices=['auto', 'origin', 'tunnel'], default='auto',
                        help='Manual maintenance selection; tunnel still requires a healthy probe')
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text())
    credential_dir = os.environ.get('CREDENTIALS_DIRECTORY')
    if not credential_dir:
        raise RuntimeError('Run via systemd with the DNS API LoadCredential')
    token = (Path(credential_dir) / 'cloudflare-api-token').read_text().strip()
    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state_file = state_dir / 'state.json'
        if state_file.exists():
            state = json.loads(state_file.read_text())
        else:
            state = {'mode': validate_record(api(config, token), config)}
        healthy = check_health(config)
        state, target = next_state(state, healthy,
            config.get('failure_threshold', 3), config.get('recovery_threshold', 3))
        if args.target != 'auto':
            if args.target == 'tunnel' and not healthy:
                raise RuntimeError('Tunnel probe failed; refusing manual recovery')
            target = args.target
        if target != state['mode']:
            state['mode'] = switch(config, token, target)
            state['last_switch'] = time.time()
            state['failures'] = state['successes'] = 0
        elif not state_file.exists():
            state['mode'] = validate_record(api(config, token), config)
        state['checked_at'] = time.time()
        state['tunnel_healthy'] = healthy
        temp = state_dir / 'state.json.tmp'
        temp.write_text(json.dumps(state, indent=2) + '\n')
        temp.replace(state_file)
        print('mode=%s tunnel_healthy=%s failures=%s successes=%s' %
              (state['mode'], healthy, state['failures'], state['successes']), flush=True)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        # Do not print token-bearing requests or arbitrary HTTP response bodies.
        print('Tunnel failover check failed: ' + type(exc).__name__, file=sys.stderr)
        sys.exit(1)
