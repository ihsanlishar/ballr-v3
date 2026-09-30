"""Bounded in-memory worker telemetry; never write monitoring history to disk."""
import threading
from datetime import datetime, timezone

_lock = threading.Lock()
_workers = {}


def report(name, state='ok'):
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        old = _workers.get(name, {})
        _workers[name] = {'state': state, 'last_check': now,
                          'last_success': now if state == 'ok' else old.get('last_success')}


def snapshot():
    with _lock:
        return {name: dict(value) for name, value in _workers.items()}
