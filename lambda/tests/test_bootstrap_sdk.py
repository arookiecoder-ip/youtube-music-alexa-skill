"""Isolate real bootstrap imports from legacy player test doubles."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

class ColdBootstrapTests(unittest.TestCase):
    def test_bootstrap_boundaries(self):
        result=subprocess.run([sys.executable,str(Path(__file__).with_name('bootstrap_cases.py'))],
            capture_output=True,text=True,timeout=25)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_entry_import_does_not_load_the_sdk_or_initialize_dynamodb(self):
        root=Path(__file__).resolve().parents[1]
        code="import sys; sys.path.insert(0,sys.argv[1]); import lambda_function; assert 'boto3' not in sys.modules; assert 'ask_sdk_core' not in sys.modules; assert 'skill_runtime' not in sys.modules"
        result=subprocess.run([sys.executable,'-c',code,str(root)],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_real_cold_sdk_dispatch_and_persistence_after_authorization(self):
        env=dict(os.environ,AWS_DEFAULT_REGION='us-east-1',AWS_EC2_METADATA_DISABLED='true',
            AWS_ACCESS_KEY_ID='synthetic',AWS_SECRET_ACCESS_KEY='synthetic',AWS_SESSION_TOKEN='synthetic',
            DYNAMODB_PERSISTENCE_REGION='us-east-1',DYNAMODB_PERSISTENCE_TABLE_NAME='offline')
        result=subprocess.run([sys.executable,str(Path(__file__).with_name('cold_entry_case.py'))],
            env=env,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
