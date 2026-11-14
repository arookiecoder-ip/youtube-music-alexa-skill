"""Network recovery discovers proxy aliases and never reconnects existing endpoints."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'ensure-bgutil-network.sh'

class BgutilNetworkRecovery(unittest.TestCase):
    def run_fixture(self, attached, gateway=True, exists=True, repeats=1):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / 'state.json'
            state.write_text(json.dumps(dict(attached=attached, gateway=gateway, exists=exists, connects=[])))
            docker = root / 'docker'
            docker.write_text('''#!/usr/bin/env python3
import os,json,re,sys
from pathlib import Path
p=Path(os.environ['DOCKER_FIXTURE_STATE']);s=json.loads(p.read_text());a=sys.argv[1:]
if a[0]=='ps': print('gateway' if s['gateway'] else '');sys.exit(0)
if a[0]=='inspect':
 if a[1]=='bgutil-provider':
  if not s['exists']:sys.exit(1)
  if '--format' not in a:print('{}');sys.exit(0)
  name=re.search(r'Networks "([^"]+)"',a[-1]).group(1)
  print('yes' if name in s['attached'] else 'no');sys.exit(0)
 if a[1]=='gateway':print('custom-project_private-egress');sys.exit(0)
if a[:2]==['network','inspect']:sys.exit(0 if a[2] in ['web','custom-project_private-egress'] else 1)
if a[:2]==['network','connect']:
 s['attached'].append(a[2]);s['connects'].append(a[2]);p.write_text(json.dumps(s));sys.exit(0)
raise RuntimeError(a)
''')
            docker.chmod(0o755)
            env = dict(os.environ, PATH=str(root)+os.pathsep+os.environ['PATH'], DOCKER_FIXTURE_STATE=str(state))
            for _ in range(repeats):
                subprocess.run(['bash', str(SCRIPT)], env=env, check=True, capture_output=True, text=True)
            return json.loads(state.read_text())

    def test_existing_web_attachment_also_gets_private_proxy_network(self):
        state = self.run_fixture(['web'])
        self.assertEqual(state['connects'], ['custom-project_private-egress'])

    def test_repeated_recovery_is_idempotent(self):
        state = self.run_fixture([], repeats=2)
        self.assertEqual(state['connects'], ['web', 'custom-project_private-egress'])

    def test_optional_missing_gateway_keeps_original_web_access(self):
        self.assertEqual(self.run_fixture([], gateway=False)['connects'], ['web'])

    def test_missing_helper_is_a_safe_noop(self):
        self.assertEqual(self.run_fixture([], exists=False)['connects'], [])
