"""Identity boundaries shared by persistence and AudioPlayer tokens."""
import hashlib
import data


class SkillAuthorizationError(Exception):
    def __init__(self, message, code='alexa_access_denied'):
        self.code = code
        super().__init__(message)


def needs_state(envelope):
    request = envelope.request
    if request.object_type in ('System.ExceptionEncountered', 'SessionEndedRequest'):
        return False
    intent = getattr(request, 'intent', None)
    return getattr(intent, 'name', '') not in ('LinkAccountIntent', 'SetApiurlIntent')


def persistence_key(envelope):
    system = envelope.context.system
    user = system.user.user_id
    namespace = getattr(getattr(system, 'application', None), 'application_id', '') or 'private-account'
    if not namespace:
        return user
    device = getattr(getattr(system, 'device', None), 'device_id', '')
    binding = getattr(envelope, '_musicbox_binding', '')
    if not user or not device or not binding:
        raise SkillAuthorizationError('This Echo is not authorized for this account.')
    # Each device/generation occupies a distinct DynamoDB item, not just a
    # nested dictionary inside a shared item subject to whole-item overwrites.
    return 'echo-v2:' + hashlib.sha256(
        '\0'.join((namespace, user, device, binding)).encode()).hexdigest()


def scoped_token(handler_input, token):
    binding = getattr(handler_input.request_envelope, '_musicbox_binding', '')
    if getattr(data, 'ALEXA_SKILL_NAMESPACE', ''):
        if not binding:
            raise SkillAuthorizationError('Echo identity has not been verified.')
        return token + '|b' + binding
    return token
