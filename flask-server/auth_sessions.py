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
                     '/api/account/password/reset/send', '/admin/account',
                     '/admin/account/password/reset', '/admin/account/password/reset/send'}
            or path.startswith('/static/'))


def complete_login(layer, user, ip):
    """Only full authentication clears guessing budgets."""
    from multiuser.netids import identifier_bucket, ip_bucket
    for identifier in (user.get('username'), user.get('email')):
        if identifier:
            layer.rate_limiter.reset('login-account', identifier_bucket(identifier))
    layer.rate_limiter.reset('login-ip', ip_bucket(ip))
