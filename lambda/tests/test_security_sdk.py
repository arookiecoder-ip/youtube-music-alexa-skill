"""Run security boundaries with the real ASK SDK, isolated from legacy stubs."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


class RealSdkSecurityTests(unittest.TestCase):
    def test_security_boundaries(self):
        root = Path(__file__).resolve().parents[2]
        env = dict(os.environ, AWS_DEFAULT_REGION='us-east-1',
                   AWS_EC2_METADATA_DISABLED='true', AWS_ACCESS_KEY_ID='synthetic',
                   AWS_SECRET_ACCESS_KEY='synthetic', DYNAMODB_PERSISTENCE_REGION='us-east-1',
                   DYNAMODB_PERSISTENCE_TABLE_NAME='offline-security-tests')
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('security_sdk_cases.py'))],
                                cwd=root, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
