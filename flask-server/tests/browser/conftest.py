"""Allow a system Chromium in isolated browser-test containers."""
import os
import pytest
from playwright.sync_api import BrowserType


@pytest.fixture(autouse=True)
def system_chromium(monkeypatch):
    executable=os.environ.get('MUSICBOX_TEST_CHROMIUM')
    if not executable:
        return
    launch=BrowserType.launch
    def configured(self,*args,**kwargs):
        if self.name=='chromium':
            if not kwargs.get('executable_path'):
                kwargs['executable_path'] = executable
        return launch(self,*args,**kwargs)
    monkeypatch.setattr(BrowserType,'launch',configured)
