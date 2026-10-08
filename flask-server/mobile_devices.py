"""Account-scoped app presence and acknowledged remote control delivery."""
import copy
import threading
import time
import uuid


class MobileDevices:
    def __init__(self, clock=time.monotonic, ttl=15):
        self.clock, self.ttl = clock, ttl
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.revision = 0
        self.devices = {}
        self.retired = {}

    def _prune(self):
        now = self.clock()
        for key in list(self.devices):
            if self.devices[key]['until'] <= now:
                del self.devices[key]
                self.wake()

    def list(self):
        with self.lock:
            self._prune()
            return [{'id': key, 'name': value['name'], 'volume': value.get('volume'), 'volume_steps': value.get('volume_steps')} for key, value in self.devices.items()]

    def require_online(self, device_id):
        with self.lock:
            self._prune()
            if device_id not in self.devices:
                raise ValueError('This device is offline. Refresh the device list.')

    def online(self, device_id, session_id, name, ack=(), volume=None, volume_steps=None):
        if not isinstance(session_id, str) or not 1 <= len(session_id) <= 128:
            raise ValueError('session_id required')
        if not isinstance(ack, list) or len(ack) > 32:
            raise ValueError('invalid command acknowledgements')
        if volume is not None and (isinstance(volume, bool) or not isinstance(volume, int) or not 0 <= volume <= 100):
            raise ValueError('invalid device volume')
        if volume_steps is not None and (isinstance(volume_steps, bool) or not isinstance(volume_steps, int) or not 1 <= volume_steps <= 1000):
            raise ValueError('invalid volume steps')
        with self.lock:
            self._prune()
            device = self.devices.get(device_id)
            retired = self.retired.setdefault(device_id, [])
            if session_id in retired:
                raise ValueError('This app session was replaced by a newer one.')
            if device is not None and device['session'] != session_id:
                retired.append(device['session'])
                del retired[:-8]
            new_session = device is None or device['session'] != session_id
            if new_session:
                device = self.devices[device_id] = {'session': session_id, 'commands': [], 'name': ''}
            device['name'] = str(name or 'Android device')[:100]
            old_volume = (device.get('volume'), device.get('volume_steps'))
            if volume is not None:
                device['volume'] = volume
            if volume_steps is not None:
                device['volume_steps'] = volume_steps
            if new_session or old_volume != (device.get('volume'), device.get('volume_steps')):
                self.wake()
            device['until'] = self.clock() + self.ttl
            device['commands'] = [c for c in device['commands'] if c['id'] not in ack and c['until'] > self.clock()]
            return copy.deepcopy(device['commands'])

    def pending_commands(self, device_id, session_id):
        """A waiting HTTP request is not a new heartbeat from the phone."""
        with self.lock:
            self._prune()
            device = self.devices.get(device_id)
            if device is None or device['session'] != session_id:
                raise ValueError('This app session was closed or replaced.')
            device['commands'] = [c for c in device['commands'] if c['until'] > self.clock()]
            return copy.deepcopy(device['commands'])

    def offline(self, device_id, session_id):
        with self.lock:
            device = self.devices.get(device_id)
            if device and device['session'] == session_id:
                retired = self.retired.setdefault(device_id, [])
                retired.append(session_id)
                del retired[:-8]
                del self.devices[device_id]
                self.wake()
                return True
            return False

    def command(self, target, token, action, payload, command_id=None):
        with self.lock:
            self.require_online(target)
            commands = self.devices[target]['commands']
            command_id = str(command_id or uuid.uuid4())[:128]
            if any(c['id'] == command_id for c in commands):
                return
            if len(commands) >= 20:
                raise ValueError('This device is busy. Retry shortly.')
            commands.append({'id': command_id, 'token': token, 'action': action,
                             'payload': copy.deepcopy(payload), 'until': self.clock() + 30})

            self.wake()

    def wake(self):
        with self.changed:
            self.revision += 1
            self.changed.notify_all()

    def wait(self, revision, timeout=2):
        # Stay below presence TTL with headroom for request/response latency.
        # Commands, explicit close, output and volume changes wake immediately.
        with self.changed:
            self.changed.wait_for(lambda: self.revision != revision, timeout=min(8, max(0, timeout)))
