#!/usr/bin/env python3
"""Forced SSH command: load one GitHub-built image and replace only the backend.

Install only with a restricted, dedicated CI SSH key. No builds are run here.
Expected SSH_ORIGINAL_COMMAND: deploy <40-character commit SHA>.
"""
from pathlib import Path
import fcntl
import gzip
import http.client
import json
import os
import re
import subprocess
import sys
import time

ROOT = Path('/home/ubuntu/youtube-music-alexa-skill')
STATE = Path('/home/ubuntu/musicbox-ci')

def command(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()

def healthy():
    try:
        client = http.client.HTTPConnection('127.0.0.1', 8080, timeout=3)
        client.request('GET', '/health/ready', headers={'Host': 'testalexa.synthora.in'})
        response = client.getresponse()
        body = json.loads(response.read(65536))
        client.close()
        return response.status == 200 and body.get('status') == 'ready'
    except Exception:
        return False

def main():
    match = re.fullmatch(r'deploy ([a-f0-9]{40})', os.environ.get('SSH_ORIGINAL_COMMAND', ''))
    if not match:
        raise RuntimeError('Only a pinned image deployment command is allowed')
    sha = match[1]
    tag = 'musicbox-signup:' + sha
    STATE.mkdir(mode=0o700, exist_ok=True)
    with (STATE / 'deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = command('docker', 'inspect', 'ytmusic', '--format', '{{.Image}}')
        process = subprocess.Popen(['docker', 'load'], stdin=subprocess.PIPE, cwd=ROOT)
        size = 0
        try:
            with gzip.GzipFile(fileobj=sys.stdin.buffer) as source:
                while block := source.read(1024 * 1024):
                    size += len(block)
                    if size > 4 * 1024 ** 3:
                        raise RuntimeError('Image exceeds deployment size limit')
                    process.stdin.write(block)
            process.stdin.close()
            if process.wait(timeout=180):
                raise RuntimeError('Docker could not load the prebuilt image')
        except Exception:
            process.kill()
            process.wait()
            raise
        info = json.loads(command('docker', 'image', 'inspect', tag))[0]
        if (info['Config'].get('Labels', {}).get('org.opencontainers.image.revision') != sha or
                info['Config'].get('User') != '10001:10001'):
            raise RuntimeError('Image revision or runtime user does not match deployment policy')
        override = STATE / 'compose.image.json'
        def write_override(image):
            temporary = STATE / 'compose.image.json.tmp'
            temporary.write_text(json.dumps({'services': {'ytmusic': {'image': image}}}))
            temporary.replace(override)
        compose = ['docker', 'compose', '--env-file', str(ROOT / '.env.testalexa'),
                   '-p', 'musicbox-test', '-f', str(ROOT / 'docker-compose.yml'),
                   '-f', str(ROOT / 'docker-compose.tunnel.yml'), '-f', str(override)]
        def replace():
            subprocess.run(compose + ['up', '-d', '--no-deps', '--no-build', '--pull', 'never', 'ytmusic'],
                           cwd=ROOT, check=True, timeout=120)
        try:
            write_override(tag)
            replace()
            for attempt in range(45):
                if healthy():
                    break
                time.sleep(2)
            else:
                raise RuntimeError('New backend did not become ready')
        except Exception:
            write_override(previous)
            replace()
            raise
        # Keep future ordinary compose operations on the same CI-built image.
        envfile = ROOT / '.env.testalexa'
        lines = envfile.read_text().splitlines()
        value = 'COMPOSE_FILE=docker-compose.yml:docker-compose.tunnel.yml:' + str(override)
        found = False
        for index, line in enumerate(lines):
            if line.startswith('COMPOSE_FILE='):
                lines[index], found = value, True
        if not found:
            lines.append(value)
        with envfile.open('w') as stream:
            stream.write('\n'.join(lines) + '\n')
        (STATE / 'last-deployment.json').write_text(json.dumps({'sha': sha, 'image': tag, 'previous': previous, 'at': time.time()}))
        print('Deployed prebuilt backend ' + sha + '; readiness passed.')

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Deployment failed: ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
