"""Exercise private configuration without reading real credentials or using CWD."""
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / 'data.py'
NAMES = ('MUSICBOX_SERVER_URL', 'MUSICBOX_EMAIL', 'MUSICBOX_ALEXA_TOKEN')


class PrivateEnvTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.module = self.root / 'skill' / 'data.py'
        self.module.parent.mkdir()
        self.module.write_bytes(SOURCE.read_bytes())

    def load(self, content=None, environment=None):
        if content is not None:
            self.module.with_name('.env').write_text(content, encoding='utf-8')
        with mock.patch.dict(os.environ, environment or {}, clear=True):
            return runpy.run_path(str(self.module))

    def test_private_file_loads_all_three_values(self):
        data = self.load('MUSICBOX_SERVER_URL=https://private.example\n'
                         'MUSICBOX_EMAIL=listener@example.test\nMUSICBOX_ALEXA_TOKEN=mbx_test.token\n')
        self.assertEqual([data[n] for n in NAMES],
                         ['https://private.example', 'listener@example.test', 'mbx_test.token'])
        self.assertEqual(data['DEFAULT_API_URL'], data['MUSICBOX_SERVER_URL'])

    def test_environment_takes_precedence_including_explicit_empty_values(self):
        data = self.load('MUSICBOX_EMAIL=file@example.test\nMUSICBOX_ALEXA_TOKEN=file-token',
                         {'MUSICBOX_EMAIL':'environment@example.test', 'MUSICBOX_ALEXA_TOKEN':''})
        self.assertEqual(data['MUSICBOX_EMAIL'], 'environment@example.test')
        self.assertEqual(data['MUSICBOX_ALEXA_TOKEN'], '')

    def test_example_file_is_never_loaded(self):
        self.module.with_name('.env.example').write_text('MUSICBOX_ALEXA_TOKEN=example-token')
        self.assertEqual([self.load()[n] for n in NAMES], ['', '', ''])

    def test_parent_file_and_old_python_config_are_never_loaded(self):
        (self.root / '.env').write_text('MUSICBOX_ALEXA_TOKEN=parent-token')
        self.module.with_name('api_key.py').write_text('raise RuntimeError("must not execute")')
        self.assertEqual(self.load()['MUSICBOX_ALEXA_TOKEN'], '')

    def test_quotes_comments_bom_and_optional_export(self):
        data = self.load('\ufeff# private settings\nexport MUSICBOX_EMAIL = "a@example.test" # account\n'
                         "MUSICBOX_ALEXA_TOKEN='literal#token'\nMUSICBOX_SERVER_URL= # unset\n")
        self.assertEqual(data['MUSICBOX_EMAIL'], 'a@example.test')
        self.assertEqual(data['MUSICBOX_ALEXA_TOKEN'], 'literal#token')
        self.assertEqual(data['MUSICBOX_SERVER_URL'], '')

    def test_values_do_not_expand_environment_variables(self):
        data = self.load("MUSICBOX_ALEXA_TOKEN='${OTHER_TOKEN}'\nAPI_KEY=ignored", {'OTHER_TOKEN':'private'})
        self.assertEqual(data['MUSICBOX_ALEXA_TOKEN'], '${OTHER_TOKEN}')
        self.assertEqual(data['_file_config'], {'MUSICBOX_ALEXA_TOKEN':'${OTHER_TOKEN}'})

    def test_malformed_setting_error_does_not_disclose_value(self):
        for content in ('MUSICBOX_ALEXA_TOKEN="sensitive-unclosed', 'MUSICBOX_ALEXA_TOKEN=sensitive invalid'):
            with self.subTest(content=content), self.assertRaisesRegex(RuntimeError, '^Invalid private .env setting on line 1.$') as failure:
                self.load(content)
            self.assertNotIn('sensitive', str(failure.exception))

    def test_environment_only_deployment_needs_no_file(self):
        data = self.load(environment=dict(zip(NAMES, ['https://private.example', 'a@example.test', 'private-token'])))
        self.assertEqual(data['MUSICBOX_ALEXA_TOKEN'], 'private-token')


if __name__ == '__main__':
    unittest.main()
