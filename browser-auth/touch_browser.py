"""Owner-leased X11 browser surface. No VNC or credential inspection."""
import base64
import io
import subprocess
import threading
from PIL import ImageGrab

_lock = threading.Lock()
KEYS = {'Enter':'Return', 'Backspace':'BackSpace', 'Tab':'Tab', 'Escape':'Escape',
        'ArrowLeft':'Left', 'ArrowRight':'Right', 'ArrowUp':'Up', 'ArrowDown':'Down'}


def run(*args):
    return subprocess.check_output(['xdotool', *map(str,args)], stderr=subprocess.DEVNULL, timeout=3)


def geometry():
    window = run('search', '--onlyvisible', '--class', 'chromium').splitlines()[-1].decode('ascii')
    values = dict(line.split('=',1) for line in run('getwindowgeometry','--shell',window).decode().splitlines() if '=' in line)
    return window, {k:int(values[k]) for k in ['X','Y','WIDTH','HEIGHT']}


def frame():
    with _lock:
        _, g = geometry()
        # Keep the native browser chrome out of the focused sign-in surface.
        top = 86
        image = ImageGrab.grab(xdisplay=None).crop((g['X'],g['Y']+top,g['X']+g['WIDTH'],g['Y']+g['HEIGHT']))
        out=io.BytesIO();image.convert('RGB').save(out,format='JPEG',quality=75)
        return {'image':'data:image/jpeg;base64,'+base64.b64encode(out.getvalue()).decode(),
                'width':image.width,'height':image.height}


def input_event(data):
    kind=data.get('type')
    with _lock:
        window,g=geometry()
        if kind == 'click':
            x,y=data.get('x'),data.get('y')
            if type(x) is not int or type(y) is not int or not (0 <= x < g['WIDTH'] and 0 <= y < g['HEIGHT']-86):
                raise ValueError('Invalid pointer')
            run('mousemove','--window',window,x,y+86);run('click','1')
        elif kind == 'text':
            text=data.get('text')
            if not isinstance(text,str) or not 0 < len(text) <= 1024 or '\x00' in text:
                raise ValueError('Invalid text')
            run('type','--clearmodifiers','--delay','0','--',text)
        elif kind == 'key' and data.get('key') in KEYS:
            run('key','--clearmodifiers',KEYS[data['key']])
        elif kind == 'scroll' and data.get('direction') in ['up','down']:
            run('click','--repeat','3','--delay','25','4' if data['direction']=='up' else '5')
        else:
            raise ValueError('Unsupported input')
