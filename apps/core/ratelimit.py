"""Minimal fixed-window rate limiting, backed by Django's cache
framework (in-process `LocMemCache` by default — no Redis/Celery
dependency, per ASSUMPTIONS.md A3). Used to give the login view and the
public share-link view "reasonable protection" against brute-force/
scripted hammering (spec requirement), without adding external
infrastructure this pilot-scale deployment doesn't otherwise need.

Known limitation, recorded honestly rather than hidden: `LocMemCache` is
per-process, so a multi-worker Gunicorn deployment enforces the limit
per worker, not globally across the whole server. Acceptable for this
pilot's scale (single small server, ~8 named users) — see
`docs/KNOWN_LIMITATIONS.md`.
"""

from django.core.cache import cache


def is_rate_limited(key: str, *, limit: int, window_seconds: int) -> bool:
    """Returns True (and does NOT count this call) if `key` has already
    hit `limit` within the current `window_seconds` window. Call
    `record_attempt` separately for whichever attempts should actually
    count toward the limit (e.g. only failed login attempts, but every
    hit on a public share link)."""
    return (cache.get(key) or 0) >= limit


def record_attempt(key: str, *, window_seconds: int) -> None:
    """Increments the counter for `key`, starting a new window if none
    is running. Fixed-window, not sliding — a burst right at the window
    boundary can technically allow up to 2x `limit` in rapid succession;
    acceptable for this pilot's threat model (deterring casual
    brute-force, not a hardened WAF)."""
    if cache.get(key) is None:
        cache.set(key, 1, timeout=window_seconds)
    else:
        try:
            cache.incr(key)
        except ValueError:
            # Lost a race with the window expiring between get() and incr().
            cache.set(key, 1, timeout=window_seconds)
