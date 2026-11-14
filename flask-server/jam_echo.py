"""Drive physical Echo outputs independently of a participant's phone renderer."""
import hashlib
import json
import threading
import time
from alexa_playback_errors import authentication_failure


class JamEchoDriver:
    def __init__(self, rooms, send, snapshot, clock=time.time, valid=None):
        self.rooms, self.send, self.snapshot, self.clock = rooms, send, snapshot, clock
        self.valid=valid or (lambda token:True)
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
                    if not self.valid(token):raise PermissionError('Jam ended')
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
                            try:error=self.send(token,dict(json.loads(output['details']),device=device),dict(action='volume',value=command['value']))
                            except Exception:error='This device could not change volume. Retry shortly.'
                            if not error:self.rooms.echo_volume_ack(token,device,command['id'],command['value'])
                            else:self.rooms.echo_volume_failed(token,device,command['id'],str(error))
                previous = {device: output for device, output in echoes.items() if device in selected}
                if state is None:
                    break
                time.sleep(.5)
        finally:
            with self.lock:
                self.running.discard(token)

    def _dispatch(self, token, output, state):
        details = dict(json.loads(output['details']),device=output['device'])
        if state and not state.get('video_id'):
            return
        intent = ('stop',) if state is None else (state['entry_id'],state['playing'],state['seek_revision'],state.get('transport_revision',1))
        key = hashlib.sha256(json.dumps(intent).encode()).hexdigest()
        claim=self.rooms.claim_echo(token,output['device'],key)
        if not claim:
            status=json.loads(output['status'])
            if status.get('processing') and not status.get('confirmed') and self.clock()-status.get('dispatch_at',self.clock())>15:
                self.rooms.echo_report(token,output['device'],key,dict(playing=False,confirmed=False,processing=False),"Alexa did not confirm audio. Retry this output.")
                return
            if state and state['playing']:
                report = self.snapshot(token,details,state)
                if report and (not output.get('error') or report.get('entry_id')!=state.get('entry_id')):
                    self.rooms.echo_report(token,output['device'],key,report,report.pop('error',''))
            return
        report = self.snapshot(token,details,state) if state else None
        if state and report and (claim=='initial' or report.get('preserve')) and report.get('confirmed') and report.get('playing')==state['playing'] and not report.get('processing') and report.get('video_id')==state['video_id'] and (state['seek_revision']==0 or report.get('preserve')) and abs(report.get('position_ms',0)-state['position_ms'])<=1500:
            self.rooms.echo_report(token,output['device'],key,report)
            return
        self.rooms.echo_report(token,output['device'],key,dict(video_id=state.get('video_id','') if state else '',
            playing=bool(state and state['playing']),confirmed=False,ended=False,processing=bool(state and state['playing']),
            retryable=True,dispatch_at=self.clock(),position_ms=state.get('position_ms',0) if state else 0,seek_revision=state.get('seek_revision',0) if state else 0))
        try:
            error = self.send(token,details,state)
        except Exception:
            error = 'This Alexa output could not be reached. Try again shortly.'
        if error:
            self.rooms.echo_report(token,output['device'],key,dict(playing=False,confirmed=False,processing=False,retryable=authentication_failure(error) is None),str(error))
        elif not state or not state['playing']:
            self.rooms.echo_report(token,output['device'],key,dict(video_id=state.get('video_id','') if state else '',
                playing=False,confirmed=True,processing=False,position_ms=state.get('position_ms',0) if state else 0,
                seek_revision=state.get('seek_revision',0) if state else 0))
