"""Small in-process rate limiter for public endpoints."""

import time
from collections import defaultdict, deque
from functools import wraps

from flask import jsonify, request

_hits: dict = defaultdict(deque)


def client_ip() -> str:
    forwarded = request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def allow(key: str, limit: int, window_seconds: int) -> bool:
    now = time.monotonic()
    bucket = _hits[key]
    while bucket and now - bucket[0] > window_seconds:
        bucket.popleft()
    if len(bucket) >= limit:
        return False
    bucket.append(now)
    return True


def rate_limit(name: str, limit: int, window_seconds: int):
    """Decorator: throttle by endpoint name + client IP."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not allow(f"{name}:{client_ip()}", limit, window_seconds):
                return jsonify({"error": "Too many requests. Try again later."}), 429
            return func(*args, **kwargs)
        return wrapper
    return decorator
