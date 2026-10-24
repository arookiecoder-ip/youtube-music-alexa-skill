#!/usr/bin/env python3
"""Run all server/browser-logic regressions with integration stubs isolated.

Legacy test modules install different provider stubs at import time. Running
each file in a fresh process prevents test-order-dependent production imports.
Physical devices and the Playwright suite are separate checks.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, required=True)
    args = parser.parse_args()
    results = args.results.resolve()
    results.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    server = root / 'flask-server'
    failures = []
    tests = sorted((server / 'tests').glob('test_*.py')) + sorted((server / 'tests').glob('test_*.js'))
    for test in tests:
        with tempfile.TemporaryDirectory(prefix='musicbox-test-') as directory:
            env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(server), str(server / 'tests'))),
                       DB_FILE=str(Path(directory) / 'state.db'),
                       AUDIO_CACHE_DIR=str(Path(directory) / 'audio'),
                       YOUTUBE_STREAMING='0',
                       YOUTUBE_NODE='/not-used-in-offline-regressions',
                       SECRET_KEY='isolated-regression-secret',
                       STATUS_JOURNAL_FILE=str(Path(directory) / 'journal.db'))
            Path(env['AUDIO_CACHE_DIR']).mkdir()
            command = ([sys.executable, '-m', 'pytest', '-q', str(test),
                        f'--junitxml={results / (test.stem + ".xml")}'] if test.suffix == '.py'
                       else ['node', str(test)])
            try:
                outcome = subprocess.run(command, cwd=server, env=env, text=True,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
                output, code = outcome.stdout, outcome.returncode
            except subprocess.TimeoutExpired as error:
                output, code = str(error), 1
            (results / (test.name + '.log')).write_text(output)
            print(f'{"PASS" if code == 0 else "FAIL"} {test.name}', flush=True)
            if code:
                failures.append(test.name)
                print(output[-12000:], flush=True)
    print(f'{len(tests) - len(failures)}/{len(tests)} regression files passed', flush=True)
    return int(bool(failures))


if __name__ == '__main__':
    raise SystemExit(main())
