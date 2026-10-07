"""Exercise the real presence fetch method without Amazon credentials."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

SOURCE = Path(__file__).resolve().parents[1] / 'alexa_remote.py'

def controller(online, age):
    tree = ast.parse(SOURCE.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and any(isinstance(f, ast.AsyncFunctionDef) and f.name == '_devices' for f in n.body))
    fn = next(f for f in cls.body if isinstance(f, ast.AsyncFunctionDef) and f.name == '_devices')
    ns = {'time': SimpleNamespace(monotonic=lambda: 100), '_DEVICE_STATE_TTL': 30}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(SOURCE), 'exec'), ns)
    raw = {'serialNumber': 'echo', 'online': online, 'capabilities': ['MUSIC_SKILL']}
    obj = SimpleNamespace(_ensure_login=AsyncMock(return_value=None), _devices_raw=[raw],
        _devices_fetched_at=100-age, _fetch_devices=AsyncMock(return_value=[dict(raw, online=True)]))
    return obj, ns['_devices']

def test_offline_echo_recovers_after_five_seconds():
    obj, fetch = controller(False, 5)
    devices, error = asyncio.run(fetch(obj, False))
    assert error is None and devices[0]['online']
    obj._fetch_devices.assert_awaited_once()

def test_fresh_offline_cache_avoids_duplicate_amazon_requests():
    obj, fetch = controller(False, 4)
    devices, _ = asyncio.run(fetch(obj, False))
    assert not devices[0]['online']
    obj._fetch_devices.assert_not_awaited()

def test_online_cache_also_expires():
    obj, fetch = controller(True, 30)
    asyncio.run(fetch(obj, False))
    obj._fetch_devices.assert_awaited_once()

def test_backend_failure_preserves_last_known_presence():
    obj, fetch = controller(False, 5)
    obj._fetch_devices.return_value = None
    devices, error = asyncio.run(fetch(obj, False))
    assert devices is None and error
    assert not obj._devices_raw[0]['online']
    assert obj._devices_fetched_at == 95
