"""Every account/admin route must remain in the reviewed policy inventory."""
import json
import sys
from pathlib import Path
sys.path[:0]=[str(Path(__file__).parent),str(Path(__file__).parents[1])]
from test_selfservice import SelfServiceTestBase


class PolicyInventoryTests(SelfServiceTestBase):
    def test_registered_routes_match_the_reviewed_inventory(self):
        document=json.loads((Path(__file__).resolve().parents[2]/'docs/api-security-contract.json').read_text())
        actual={(r.rule,tuple(sorted(r.methods-{'HEAD','OPTIONS'}))) for r in self.app.url_map.iter_rules() if r.endpoint!='static'}
        declared={(r['path'],tuple(r['methods'])) for r in document['routes']}
        self.assertEqual(actual,declared,'New or changed account routes require a security policy review')
        for row in document['routes']:
            if row['path'].startswith('/api/account/'):
                self.assertEqual(row['principal'],'account')
