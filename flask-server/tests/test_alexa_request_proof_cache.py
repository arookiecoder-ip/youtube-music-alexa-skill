"""Only Amazon's profile proof is reused within an Alexa request, never access."""
import hashlib
import time
from unittest import mock
import pytest
from multiuser.alexa_access import AlexaAccess, AccessDenied


@pytest.fixture
def proof_access():
    raw = 'mbx_synthetic.profile-proof-test'
    user = dict(id='user-a', alexa_enabled=True, status='active', must_change_password=False,
                email_verified=True, email='musicbox@example.test', security_version=1)
    token = dict(id=raw.split('.')[0], user_id='user-a', token_hash=hashlib.sha256(raw.encode()).hexdigest(),
                 revoked_at=0, expires_at=time.time()+3600, email=user['email'], security_version=1)
    linked = dict(verified_by='amazon_authenticated_session', email='amazon@example.test',
                  customer_id='customer-a', generation='generation-a')
    accounts = mock.Mock()
    accounts.get_user.return_value = user
    accounts.db.fetchone.return_value = token
    accounts.get_provider_secret.return_value = linked
    access = AlexaAccess(accounts)
    headers = {'Authorization': 'Bearer '+raw, 'X-MusicBox-Email': user['email'],
        'X-Alexa-User-Id': 'subject-a', 'X-Alexa-Device-Id': 'device-a',
        'X-Alexa-Profile-Token': 'synthetic-amazon-proof',
        'X-Alexa-Api-Endpoint': 'https://api.amazonalexa.com',
        'X-Alexa-Request-Id': 'amzn1.echo-api.request.request-a'}
    with mock.patch('multiuser.alexa_access.AlexaAccess._profile_get') as lookup:
        lookup.return_value.status_code = 200
        lookup.return_value.json.return_value = linked['email']
        yield access, headers, user, token, linked, lookup


def test_preflight_and_action_share_one_profile_lookup(proof_access):
    access, headers, user, token, linked, lookup = proof_access
    access.authenticate(headers)
    access.authenticate(headers)
    assert lookup.call_count == 1
    assert access.db.fetchone.call_count == 4  # live authorization twice per HTTP call


@pytest.mark.parametrize('header,value', [
    ('X-Alexa-Request-Id', 'amzn1.echo-api.request.new-request'),
    ('X-Alexa-User-Id', 'other-subject'), ('X-Alexa-Device-Id', 'other-device'),
    ('X-Alexa-Profile-Token', 'other-proof'),
    ('X-Alexa-Api-Endpoint', 'https://api.eu.amazonalexa.com')])
def test_proof_cannot_cross_request_or_amazon_identity(proof_access, header, value):
    access, headers, *_, lookup = proof_access
    access.authenticate(headers)
    access.authenticate({**headers, header: value})
    assert lookup.call_count == 2


def test_legacy_client_without_request_id_always_verifies_amazon(proof_access):
    access, headers, *_, lookup = proof_access
    headers.pop('X-Alexa-Request-Id')
    access.authenticate(headers)
    access.authenticate(headers)
    assert lookup.call_count == 2


@pytest.mark.parametrize('field,value', [
    ('alexa_enabled', False), ('status', 'disabled'), ('must_change_password', True),
    ('email_verified', False), ('security_version', 2), ('email', 'changed@example.test')])
def test_cached_proof_never_bypasses_account_restrictions(proof_access, field, value):
    access, headers, user, token, linked, lookup = proof_access
    access.authenticate(headers)
    user[field] = value
    with pytest.raises(AccessDenied):
        access.authenticate(headers)
    assert lookup.call_count == 1


def test_rotated_or_revoked_token_cannot_reuse_cached_proof(proof_access):
    access, headers, user, token, linked, lookup = proof_access
    access.authenticate(headers)
    token['revoked_at'] = time.time()
    with pytest.raises(AccessDenied):
        access.authenticate(headers)
    assert lookup.call_count == 1


def test_relink_forces_new_proof_and_rechecks_email(proof_access):
    access, headers, user, token, linked, lookup = proof_access
    access.authenticate(headers)
    linked.update(generation='generation-b', email='other-amazon@example.test')
    with pytest.raises(AccessDenied, match='Alexa access denied'):
        access.authenticate(headers)
    assert lookup.call_count == 2


def test_request_proof_expires_without_sliding_on_cache_hits(proof_access):
    access, headers, *_, lookup = proof_access
    with mock.patch('multiuser.alexa_access.time.monotonic', return_value=0) as clock:
        access.authenticate(headers)
        clock.return_value = 8
        access.authenticate(headers)
        clock.return_value = 11
        access.authenticate(headers)
    assert lookup.call_count == 2


def test_denied_profile_is_never_cached(proof_access):
    access, headers, *_, lookup = proof_access
    lookup.return_value.status_code = 403
    for _ in range(2):
        with pytest.raises(AccessDenied):
            access.authenticate(headers)
    assert lookup.call_count == 2
    assert not access._proofs


def test_proof_store_is_bounded(proof_access):
    access, *_ = proof_access
    for i in range(520):
        access._remember_proof(('request', i), 'amazon@example.test')
    assert len(access._proofs) == 512
    assert ('request', 0) not in access._proofs


def test_proof_is_not_shared_with_another_musicbox_account(proof_access):
    access, headers, user, token, linked, lookup = proof_access
    access.authenticate(headers)
    user['id'] = token['user_id'] = 'user-b'
    access.authenticate(headers)
    assert lookup.call_count == 2


def test_unlink_denies_access_even_with_a_cached_proof(proof_access):
    access, headers, user, token, linked, lookup = proof_access
    access.authenticate(headers)
    linked.clear()
    with pytest.raises(AccessDenied):
        access.authenticate(headers)
    assert lookup.call_count == 1


def test_revoke_during_profile_network_call_is_rechecked(proof_access):
    access, headers, user, token, linked, lookup = proof_access
    response = lookup.return_value
    def revoked(*args, **kwargs):
        token['revoked_at'] = time.time()
        return response
    lookup.side_effect = revoked
    with pytest.raises(AccessDenied):
        access.authenticate(headers)
    assert not access._proofs
