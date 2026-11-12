"""Reuse TLS sockets while cookies and bearer headers stay request-local."""
import threading
from types import SimpleNamespace
from unittest import mock
from multiuser.alexa_access import AlexaAccess


def test_workers_have_separate_sessions_over_one_connection_pool():
    access = AlexaAccess(SimpleNamespace(db=mock.Mock()))
    first, second = mock.Mock(), mock.Mock()
    with mock.patch('multiuser.alexa_access.requests.Session', side_effect=[first, second]) as sessions:
        access._profile_get('https://api.amazonalexa.com/profile', headers={'Authorization': 'Bearer a'})
        access._profile_get('https://api.amazonalexa.com/profile', headers={'Authorization': 'Bearer b'})
        worker = threading.Thread(target=access._profile_get,
            args=('https://api.amazonalexa.com/profile',), kwargs={'headers': {'Authorization': 'Bearer c'}})
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive()
    assert sessions.call_count == 2
    first.mount.assert_called_once_with('https://', access._profile_transport)
    second.mount.assert_called_once_with('https://', access._profile_transport)
    assert first.cookies.clear.call_count == 4
    assert second.cookies.clear.call_count == 2
    assert first.get.call_args_list[0].kwargs['headers']['Authorization'] == 'Bearer a'
    assert first.get.call_args_list[1].kwargs['headers']['Authorization'] == 'Bearer b'


def test_failed_profile_request_also_clears_cookies():
    access = AlexaAccess(SimpleNamespace(db=mock.Mock()))
    client = mock.Mock()
    client.get.side_effect = OSError('synthetic connection failure')
    with mock.patch('multiuser.alexa_access.requests.Session', return_value=client):
        try:
            access._profile_get('https://api.amazonalexa.com/profile')
        except OSError:
            pass
        else:
            raise AssertionError('Failure should propagate to the authentication gate')
    assert client.cookies.clear.call_count == 2
