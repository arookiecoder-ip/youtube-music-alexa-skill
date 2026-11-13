"""Terminal Alexa account failures, distinct from unavailable music."""
MESSAGES = {
    'alexa_request_unverified': ('Alexa did not verify this playback request. Check the skill token, linked Amazon account and Echo connection, then retry.', 'check_skill_settings'),
    'amazon_login_required': ('Your Amazon session has expired. Reconnect Amazon in your account settings, then try again.', 'reconnect_amazon'),
    'alexa_access_disabled': ('Alexa access is disabled for your account. Ask an administrator to enable access before retrying.', 'contact_admin'),
    'amazon_link_required': ('Connect Amazon in your account settings, then try again.', 'connect_amazon'),
    'amazon_link_changed': ('Your Amazon connection changed. Reconnect Amazon and try again.', 'reconnect_amazon'),
    'amazon_email_mismatch': ('Alexa authentication failed: this Echo uses a different Amazon account. Connect the same Amazon account in your account settings.', 'reconnect_amazon'),
    'musicbox_account_mismatch': ('Alexa authentication failed: MUSICBOX_ACCOUNT_ID does not match the account for this token. Copy your account ID from Account settings.', 'check_skill_settings'),
    'musicbox_email_mismatch': ('Alexa authentication failed: the legacy email setting does not match this token. Set MUSICBOX_ACCOUNT_ID from Account settings.', 'check_skill_settings'),
    'amazon_permission_required': ('Alexa could not verify your Amazon account. Allow this skill to read your email in the Alexa app, then try again.', 'allow_amazon_email'),
    'amazon_profile_unavailable': ('Amazon account verification is temporarily unavailable. Playback stopped; try again later.', 'retry_later'),
    'alexa_access_denied': ('Alexa access was denied. Check Alexa access in your account and update the skill with your active token.', 'check_alexa_access'),
    'alexa_authentication_required': ('Your Amazon session has expired or is inconsistent. Reconnect Amazon in your account settings, then try again.', 'reconnect_amazon'),
}


def authentication_failure(error, code=None):
    code = code or getattr(error, 'code', None)
    if not isinstance(code, str):
        code = None
    if code not in MESSAGES:
        text = str(error or '').casefold()
        if ('youtube' in text or 'ytmusic' in text) and 'alexa' not in text and 'amazon' not in text:
            return None
        if 'amazon access requires' in text:
            code = 'alexa_access_denied'
            message, action = MESSAGES[code]
            return {'type': 'alexa_authentication_required', 'code': code, 'message': message,
                    'action': action, 'terminal': True, 'retryable': False}
        markers = ('not signed in', 'sign in to amazon', 'amazon session',
                   'alexa authentication', 'alexa access was denied', 'access denied',
                   'authentication failed', 'unauthorized', 'unauthorised',
                   'session expired', 'invalid token', 'token expired')
        status = getattr(error, 'status', None) or getattr(getattr(error, 'response', None), 'status_code', None)
        if not any(marker in text for marker in markers) and status not in (401, 403) and type(error).__name__ != 'AlexapyLoginError':
            return None
        code = 'alexa_authentication_required'
    message, action = MESSAGES[code]
    return {'type': 'alexa_authentication_required', 'code': code, 'message': message,
            'action': action, 'terminal': True, 'retryable': False}
