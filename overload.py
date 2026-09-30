"""Bound work in the single-process deployment without trusting proxy IP headers."""
import threading
import time


class RequestBudget:
    def __init__(self, capacity=60, per_second=2, concurrent=2):
        self.capacity, self.per_second = capacity, per_second
        self.tokens = float(capacity)
        self.updated = time.monotonic()
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(concurrent)

    def acquire(self):
        with self.lock:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now-self.updated)*self.per_second)
            self.updated = now
            if self.tokens < 1:
                return False
            if not self.slots.acquire(blocking=False):
                return False
            self.tokens -= 1
            return True

    def release(self):
        self.slots.release()
