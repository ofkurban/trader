"""Small in-process TTL cache for Yahoo calls.

Concurrent callers asking for the same key wait on one fetch instead of each
hitting Yahoo. Exceptions are not cached. Size is bounded because any ticker can
be requested now.
"""

from __future__ import annotations

import functools
import threading
import time
from typing import Any, Callable


def ttl_cache(seconds: float, maxsize: int = 256) -> Callable:
    def decorator(fn: Callable) -> Callable:
        entries: dict[tuple, tuple[float, Any]] = {}
        key_locks: dict[tuple, threading.Lock] = {}
        guard = threading.Lock()

        def fresh(key: tuple) -> tuple[bool, Any]:
            hit = entries.get(key)
            if hit and time.monotonic() - hit[0] < seconds:
                return True, hit[1]
            return False, None

        def prune() -> None:
            now = time.monotonic()
            for key in [k for k, (t, _) in list(entries.items()) if now - t >= seconds]:
                entries.pop(key, None)
                key_locks.pop(key, None)
            # Still too big: drop oldest (dicts keep insertion order)
            while len(entries) > maxsize:
                key = next(iter(entries))
                entries.pop(key, None)
                key_locks.pop(key, None)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            ok, value = fresh(key)
            if ok:
                return value

            with guard:
                lock = key_locks.setdefault(key, threading.Lock())
            with lock:
                # Another thread may have filled it while we waited
                ok, value = fresh(key)
                if ok:
                    return value
                value = fn(*args, **kwargs)
                with guard:
                    entries.pop(key, None)  # re-insert so order tracks age
                    entries[key] = (time.monotonic(), value)
                    if len(entries) > maxsize:
                        prune()
                return value

        wrapper.cache_clear = entries.clear  # type: ignore[attr-defined]
        return wrapper

    return decorator
