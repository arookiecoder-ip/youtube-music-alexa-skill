import json
from pathlib import Path
import tempfile
from alexa_state_migration import instruction


def test_operator_instruction_scope_expiry_permissions_and_origin():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / 'migration.json'
        valid = dict(account_id='account', skill_id='skill', expires=1100,
                     url='https://music.example/alexa/skill/migration/', token='x'*43, transfer_id='t')
        def read(value=valid, mode=0o600, account='account', skill='skill'):
            p.write_text(json.dumps(value));p.chmod(mode)
            return instruction(p, account_id=account, skill_id=skill,
                               backend_url='https://music.example/', now=1000)
        assert read() == valid
        assert read(mode=0o644) is None
        assert read(account='other') is None
        assert read(skill='other') is None
        for field, value in [('expires', 999), ('expires', 5000), ('token', 'short'),
                             ('url', 'https://evil.example/alexa/skill/migration/'),
                             ('url', 'http://music.example/alexa/skill/migration/'),
                             ('url', 'https://music.example/alexa/skill/migration/?secret=1'),
                             ('url', 'https://music.example/other')]:
            assert read(dict(valid, **{field:value})) is None
        p.unlink()
        assert instruction(p, account_id='account', skill_id='skill', backend_url='https://music.example') is None
