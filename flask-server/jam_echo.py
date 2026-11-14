"""Drive physical Echo outputs independently of a participant's phone renderer."""
import hashlib
import json
import threading
import time


class JamEchoDriver:
    def __init__(self, rooms, send, snapshot, clock=time.time):
        self.rooms, self.send, self.snapshot, self.clock = rooms, send, snapshot, clock
        self.lock = threading.Lock()
        self.running = set()

    def ensure(self, token):
        with self.lock:
            if token in self.running:
                return
            self.running.add(token)
        threading.Thread(target=self._loop, args=(token,), daemon=True, name='jam-echo').start()

    def _loop(self, token):
        previous = {}
        try:
            while True:
                try:
                    state = self.rooms.transact(token)
                    outputs = self.rooms.outputs(token)
                except (PermissionError, ValueError):
                    state, outputs = None, []
                echoes = {o['device']: o for o in outputs if json.loads(o['details']).get('server_echo')}
                selected = set(state['selected']) if state else set()
                for device, old in previous.items():
                    if device not in selected:
                        self._dispatch(token, old, None)
                for device, output in echoes.items():
                    if device in selected:
                        self._dispatch(token, output, state)
                        for command in self.rooms.device_commands(token,device,output['member']):
                            error=self.send(token,json.loads(output['details']),dict(action='volume',value=command['value']))
                            if not error:self.rooms.echo_volume_ack(token,device,command['id'],command['value'])
                previous = {device: output for device, output in echoes.items() if device in selected}
                if state is None:
                    break
                time.sleep(.5)
        finally:
            with self.lock:
                self.running.discard(token)

    def _dispatch(self, token, output, state):
        details = json.loads(output['details'])
        if state and not state.get('video_id'):
            return
        intent = ('stop',) if state is None else (state['entry_id'],state['playing'],state['seek_revision'],state.get('transport_revision',1))
        key = hashlib.sha256(json.dumps(intent).encode()).hexdigest()
        claim=self.rooms.claim_echo(token,output['device'],key)
        if not claim:
            if state and not output.get('error'):
                report = self.snapshot(token,details,state)
                if report:
                    self.rooms.echo_report(token,output['device'],key,report)
            return
        report = self.snapshot(token,details,state) if state else None
        if claim=='initial' and state and report and report.get('confirmed') and report.get('playing')==state['playing'] and not report.get('processing') and report.get('video_id')==state['video_id'] and state['seek_revision']==0 and abs(report.get('position_ms',0)-state['position_ms'])<=1500:
            self.rooms.echo_report(token,output['device'],key,report)
            return
        self.rooms.echo_report(token,output['device'],key,dict(video_id=state.get('video_id','') if state else '',
            playing=bool(state and state['playing']),confirmed=False,processing=bool(state and state['playing']),
            position_ms=state.get('position_ms',0) if state else 0,seek_revision=state.get('seek_revision',0) if state else 0))
        try:
            error = self.send(token,details,state)
        except Exception:
            error = 'This Alexa output could not be reached. Try again shortly.'
        if error:
            self.rooms.echo_report(token,output['device'],key,dict(playing=False,confirmed=False,processing=False),str(error))
        elif not state or not state['playing']:
            self.rooms.echo_report(token,output['device'],key,dict(video_id=state.get('video_id','') if state else '',
                playing=False,confirmed=True,processing=False,position_ms=state.get('position_ms',0) if state else 0,
                seek_revision=state.get('seek_revision',0) if state else 0))
