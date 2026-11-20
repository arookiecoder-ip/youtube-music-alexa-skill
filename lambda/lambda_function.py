"""Light Lambda entry point: overlap first-use SDK loading with authorization."""
import importlib
import time
from concurrent.futures import ThreadPoolExecutor
import skill_bootstrap

_runtime = None

def _load_runtime():
    global _runtime
    if _runtime is None:
        started = time.monotonic()
        _runtime = importlib.import_module('skill_runtime')
        _runtime.logger.info('[skill-bootstrap] runtime_load_ms=%.0f',
                             (time.monotonic()-started)*1000)
    return _runtime

def __getattr__(name):
    # Preserve programmatic access to the SDK handlers and adapters.
    if name.startswith('__'):
        raise AttributeError(name)
    return getattr(_load_runtime(), name)

def _sdk_lambda_handler(event, context):
    return _load_runtime().lambda_handler(event, context)

def lambda_handler(event, context):
    # No timers, synthetic Alexa commands or cached authorization proofs.
    if (_runtime is None and getattr(context, 'aws_request_id', None)
            and skill_bootstrap.can_prefetch(event)):
        with ThreadPoolExecutor(max_workers=1) as executor:
            skill_bootstrap.start(event, executor)
            try:
                return _sdk_lambda_handler(event, context)
            finally:
                skill_bootstrap.clear()
    return _sdk_lambda_handler(event, context)
