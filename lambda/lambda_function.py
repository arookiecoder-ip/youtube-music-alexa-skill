"""Light Lambda entry point: overlap first-use SDK loading with authorization."""
import importlib
import time
from concurrent.futures import ThreadPoolExecutor
import skill_bootstrap
import data

_runtime = None

def _load_runtime():
    global _runtime
    if _runtime is None:
        started = time.monotonic()
        _runtime = importlib.import_module('skill_runtime')
        elapsed = (time.monotonic()-started)*1000
        skill_bootstrap.runtime_ready(elapsed)
        _runtime.logger.info('[skill-bootstrap] runtime_load_ms=%.0f', elapsed)
    return _runtime

def __getattr__(name):
    # Preserve programmatic access to the SDK handlers and adapters.
    if name.startswith('__'):
        raise AttributeError(name)
    return getattr(_load_runtime(), name)

def _sdk_lambda_handler(event, context):
    return _load_runtime().lambda_handler(event, context)

def lambda_handler(event, context):
    if getattr(data, 'MUSICBOX_RUNTIME_SECRET', ''):
        import runtime_relay
        try:
            return runtime_relay.invoke(event, context)
        except runtime_relay.RelayAuthorizationDenied as exc:
            skill_bootstrap.install_response(event, exc.response)
            try:
                return _sdk_lambda_handler(event, context)
            finally:
                skill_bootstrap.clear()
        except Exception as exc:
            # Do not fall back after a possibly dispatched remote command.
            # Log only the class: exception messages can contain capabilities.
            import logging
            logging.getLogger(__name__).error('Persistent skill relay failed: %s', type(exc).__name__)
            return {'version': '1.0', 'response': {'shouldEndSession': True}}
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
