"""Bounded, per-key request coalescing with provider-error backoff."""
from collections import OrderedDict
import threading
import time


class RequestCache:
    def __init__(self, ttl, failure_ttl=30, max_entries=64):
        self.ttl, self.failure_ttl = ttl, failure_ttl
        self.max_entries = max_entries
        self.entries = OrderedDict()
        self.locks = [threading.Lock() for _ in range(64)]
        self.guard = threading.Lock()

    def clear(self):
        with self.guard:
            self.entries.clear()

    def prune_expired(self):
        """Release unused payloads even when their keys are never requested again."""
        now = time.monotonic()
        with self.guard:
            expired = [key for key, entry in self.entries.items() if entry[0] <= now]
            for key in expired:
                del self.entries[key]

    def get(self, key, fetch):
        lock = self.locks[hash(key) % len(self.locks)]
        if not lock.acquire(timeout=20):
            raise RuntimeError('Data refresh is busy. Please retry shortly.')
        try:
            with self.guard:
                entry = self.entries.get(key)
                if entry:
                    self.entries.move_to_end(key)
            if entry and time.monotonic() < entry[0]:
                if entry[2]:
                    raise RuntimeError('Data provider is temporarily unavailable.')
                return entry[1]
            try:
                value = fetch()
            except Exception:
                self._store(key, (time.monotonic() + self.failure_ttl, None, True))
                raise
            self._store(key, (time.monotonic() + self.ttl, value, False))
            return value
        finally:
            lock.release()

    def _store(self, key, entry):
        with self.guard:
            self.entries[key] = entry
            self.entries.move_to_end(key)
            while len(self.entries) > self.max_entries:
                self.entries.popitem(last=False)
