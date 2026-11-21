"""Explicit browser identity replacement and action restrictions."""
from flask import session


def replace_identity():
    # Never retain legacy owner credentials, Jam capabilities, pending proofs or
    # browser leases when authenticating another account.
    session.clear()
    session.permanent = True


def password_change_allowed(path, method):
    path = path.rstrip('/') or '/'
    return (path in {'/logout', '/admin/logout', '/account', '/api/account',
                     '/api/account/password', '/api/account/password/reset',
                     '/api/account/password/reset/send', '/api/account/code/check', '/api/account/identity/check', '/admin/account',
                     '/admin/account/password/reset', '/admin/account/password/reset/send'}
            or path.startswith('/static/'))


def complete_login(layer, user, ip):
    """Only full authentication clears guessing budgets."""
    from multiuser.netids import identifier_bucket, ip_bucket
    for identifier in (user.get('username'), user.get('email')):
        if identifier:
            layer.rate_limiter.reset('login-account', identifier_bucket(identifier))
    layer.rate_limiter.reset('login-ip', ip_bucket(ip))


def create_browser_session(accounts, user, ip="", user_agent=""):
    """Rotate authentication while keeping one live session per browser installation.

    Device identity is random and signed, never inferred from shared IPs or UAs.
    The cookie conveys no authentication and survives logout/account replacement.
    """
    import secrets
    from flask import request, current_app, after_this_request
    from itsdangerous import URLSafeSerializer, BadSignature
    signer = URLSafeSerializer(current_app.secret_key, salt='musicbox-device-v1')
    try:
        device = signer.loads(request.cookies.get('musicbox_device', ''))
        if not isinstance(device, str) or len(device) != 64:
            raise BadSignature('invalid device')
    except BadSignature:
        device = secrets.token_hex(32)
    raw = accounts.create_session(user, ip, user_agent, device_id=device,
                                  previous_sid=session.get('mu_sid', ''))
    @after_this_request
    def remember_device(response):
        response.set_cookie('musicbox_device', signer.dumps(device), max_age=31536000,
                            httponly=True, secure=request.is_secure, samesite='Lax', path='/')
        return response
    return raw
