"""Per-key cache that coalesces requests and backs off after provider errors."""
import threading
import time


class RequestCache:
    def __init__(self, ttl, failure_ttl=30):
        self.ttl = ttl
        self.failure_ttl = failure_ttl
        self.entries = {}
        self.locks = {}
        self.guard = threading.Lock()

    def get(self, key, fetch):
        with self.guard:
            lock = self.locks.setdefault(key, threading.Lock())
        if not lock.acquire(timeout=20):
            raise RuntimeError('Data refresh is busy. Please retry shortly.')
        try:
            entry = self.entries.get(key)
            if entry and time.monotonic() < entry[0]:
                if entry[2]:
                    raise RuntimeError('Data provider is temporarily unavailable.')
                return entry[1]
            try:
                value = fetch()
            except Exception:
                self.entries[key] = (time.monotonic() + self.failure_ttl, None, True)
                raise
            self.entries[key] = (time.monotonic() + self.ttl, value, False)
            return value
        finally:
            lock.release()
