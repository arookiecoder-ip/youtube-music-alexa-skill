"""Validate the remote browser input boundary without launching X11."""
import importlib.util,sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import unittest

class TouchInputTests(unittest.TestCase):
    def setUp(self):
        path=Path(__file__).resolve().parents[2]/'browser-auth'/'touch_browser.py'
        spec=importlib.util.spec_from_file_location('touch_browser_test',path);self.module=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'PIL':SimpleNamespace(ImageGrab=Mock())}):spec.loader.exec_module(self.module)
        self.module.geometry=Mock(return_value=('window',{'WIDTH':480,'HEIGHT':900,'X':0,'Y':0}))
        self.module.run=Mock()
    def test_input_text_is_an_argument_not_a_shell_command(self):
        text='$(do-not-execute) --literal `text`'
        self.module.input_event({'type':'text','text':text})
        self.module.run.assert_called_once_with('type','--clearmodifiers','--delay','0','--',text)
    def test_text_length_and_nul_are_rejected(self):
        for text in ['', 'x'*1025, 'a\x00b']:
            with self.assertRaises(ValueError):self.module.input_event({'type':'text','text':text})
        self.module.run.assert_not_called()
    def test_invalid_coordinates_cannot_interact_outside_the_browser(self):
        for x,y in [(-1,5),(480,5),(5,814),(5.2,5),(True,5)]:
            with self.assertRaises(ValueError):self.module.input_event({'type':'click','x':x,'y':y})
        self.module.run.assert_not_called()
    def test_keys_are_allowlisted_and_valid_pointer_accounts_for_toolbar(self):
        with self.assertRaises(ValueError):self.module.input_event({'type':'key','key':'alt+F4'})
        self.module.input_event({'type':'click','x':20,'y':30})
        self.module.run.assert_any_call('mousemove','--window','window',20,116)
