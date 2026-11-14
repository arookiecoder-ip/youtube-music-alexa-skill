"""Real Chromium exercises consent, host management and small-screen layout."""
from pathlib import Path
import os
import threading
import time
import io
import wave
from flask import send_file
import unittest
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright, expect
import test_jam_routes as route_tests


class JamBrowserTests(unittest.TestCase):
    def setUp(self):
        self.case=route_tests.JamRouteTests('test_invitation_get_shows_consent_without_joining')
        self.case.setUp()
        # Keep the fixture queue paused; audio providers are outside UI tests.
        self.case.rooms.transact('room','pause',{'command_id':'fixture-pause'})
        self.case.rooms.clock=time.time
        audio=io.BytesIO()
        with wave.open(audio,'wb') as fixture:
            fixture.setnchannels(1);fixture.setsampwidth(2);fixture.setframerate(8000)
            fixture.writeframes(b'\0\0'*80000)
        self.case.ns['_serve_youtube_stream']=lambda video:send_file(io.BytesIO(audio.getvalue()),mimetype='audio/wav',conditional=True,download_name='fixture.wav')
        revision=self.case.rooms.transact('room')['revision']
        self.case.rooms.transact('room','configure',{'command_id':'fixture-output','revision':revision,'mode':'single','selected':[]})
        self.server=make_server('127.0.0.1',0,self.case.app,threaded=True)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.origin='http://127.0.0.1:'+str(self.server.server_port)
        self.playwright=sync_playwright().start()
        self.browser=self.playwright.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        self.context=self.browser.new_context(extra_http_headers={'Test-User':'host'},viewport={'width':390,'height':844})
        self.page=self.context.new_page()
        self.errors=[]
        self.page.on('pageerror',lambda error:self.errors.append(str(error)))
        self.artifacts=Path(os.environ.get('JAM_SCREENSHOTS','/tmp/jam-screenshots'))
        self.artifacts.mkdir(parents=True,exist_ok=True)

    def tearDown(self):
        self.browser.close();self.playwright.stop()
        self.server.shutdown();self.thread.join(timeout=5);self.case.tearDown()

    def test_host_switches_modes_selects_outputs_and_removes_member(self):
        self.case.join(self.case.alice,'alice','alice-phone')
        self.case.alice.post('/api/jam/presence/',json={'device_id':'alice-phone','name':'Alice phone'},headers={'Test-User':'alice'})
        self.page.goto(self.origin+'/jam-room/')
        expect(self.page.locator('#host')).to_be_visible()
        expect(self.page.locator('#outputs')).to_contain_text('Alice phone')
        self.page.locator('#multiple').check()
        expect(self.page.locator('#people')).to_contain_text('Multiple selected outputs')
        self.page.locator('#outputs label').filter(has_text='Alice phone').locator('input').check()
        self.page.wait_for_function("document.querySelector('#outputs label input').checked")
        state=self.case.rooms.transact('room')
        self.assertIn('alice-phone',state['selected'])
        self.page.screenshot(path=str(self.artifacts/'host-phone.png'),full_page=True)
        self.page.on('dialog',lambda dialog:dialog.accept())
        self.page.locator('#members').get_by_role('button',name='Remove').click()
        expect(self.page.locator('#members')).not_to_contain_text('alice')
        self.assertEqual(self.case.rooms.transact('room')['selected'],[])
        self.assertFalse(self.errors,self.errors)

    def test_guest_must_confirm_join_and_cannot_see_host_management(self):
        guest=self.browser.new_context(viewport={'width':375,'height':812})
        page=guest.new_page();errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(self.origin+'/j/room')
        expect(page.get_by_role('button',name='Join Jam')).to_be_visible()
        page.get_by_role('button',name='Join Jam').click()
        expect(page).to_have_url(self.origin+'/jam-room/')
        expect(page.locator('#host')).to_be_visible()
        expect(page.locator('#end')).to_be_hidden()
        expect(page.locator('#members button')).to_have_count(0)
        expect(page.locator('#title')).to_have_text('First')
        page.get_by_role('button',name='Like',exact=True).click()
        expect(page.locator('#notice')).to_contain_text('Sign in')
        page.screenshot(path=str(self.artifacts/'guest-phone.png'),full_page=True)
        for width in (320,375,768):
            page.set_viewport_size({'width':width,'height':900})
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'),f'Guest overflow at {width}')
        self.assertFalse(errors,errors)
        guest.close()

    def test_two_selected_browser_outputs_follow_shared_play_pause_and_seek(self):
        member=self.browser.new_context(extra_http_headers={'Test-User':'alice'})
        friend=member.new_page()
        friend.goto(self.origin+'/j/room')
        friend.get_by_role('button',name='Join Jam').click()
        expect(friend.locator('#title')).to_have_text('First')
        self.page.goto(self.origin+'/jam-room/')
        expect(self.page.locator('#outputs input')).to_have_count(2)
        self.page.locator('#multiple').check()
        expect(self.page.locator('#people')).to_contain_text('Multiple selected outputs')
        self.page.locator('#outputs input').nth(0).check()
        self.page.wait_for_function("document.querySelectorAll('#outputs input:checked').length === 1")
        self.page.locator('#outputs input').nth(1).check()
        self.page.wait_for_function("document.querySelectorAll('#outputs input:checked').length === 2")
        self.page.locator('#play').click()
        self.page.wait_for_function("!document.querySelector('#audio').paused")
        friend.wait_for_function("!document.querySelector('#audio').paused")
        expect(friend.locator('#play')).to_have_text('Pause')
        friend.locator('#play').click()
        self.page.wait_for_function("document.querySelector('#audio').paused")
        friend.wait_for_function("document.querySelector('#audio').paused")
        expect(friend.locator('#seek')).to_be_enabled()
        with friend.expect_response(lambda response: '/api/jam/control/' in response.url and response.request.post_data_json.get('action') == 'seek'):
            friend.locator('#seek').evaluate("node=>{node.value='3000';node.dispatchEvent(new Event('change',{bubbles:true}));}")
        expected=self.case.rooms.transact('room')
        self.assertEqual(expected['position_ms'],3000)
        try:
            self.page.wait_for_function("Math.abs(document.querySelector('#audio').currentTime-3)<0.4",timeout=7000)
            friend.wait_for_function("Math.abs(document.querySelector('#audio').currentTime-3)<0.4",timeout=7000)
        except Exception:
            print('ROOM SEEK:',{key:expected[key] for key in ('position_ms','playing','seek_revision')},flush=True)
            for name,page in [('host',self.page),('member',friend)]:
                print(name,page.evaluate("()=>{const a=document.querySelector('#audio');return {position:a.currentTime,duration:a.duration,ready:a.readyState,paused:a.paused,seekable:Array.from({length:a.seekable.length},(_,i)=>[a.seekable.start(i),a.seekable.end(i)]),error:a.error?.message,uiError:document.querySelector('#error').textContent};}"),flush=True)
                page.screenshot(path=str(self.artifacts/(name+'-seek-failure.png')),full_page=True)
            raise
        member.close()
        self.assertFalse(self.errors,self.errors)

    def test_management_and_queue_fit_phone_tablet_and_desktop(self):
        self.page.goto(self.origin+'/jam-room/')
        expect(self.page.locator('#host')).to_be_visible()
        for width in (320,390,768,1280):
            self.page.set_viewport_size({'width':width,'height':900})
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'),f'Horizontal overflow at {width}')
            self.page.screenshot(path=str(self.artifacts/f'host-{width}.png'),full_page=True)
        self.assertFalse(self.errors,self.errors)


if __name__ == '__main__':unittest.main()
