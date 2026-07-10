"""Live-data failure signals. A LiveDataError propagating out of the
BarSource halts (and flattens) the LiveLoop -- the broker is still
authoritative on a data loss, so flattening is safe and desired.
"""
from __future__ import annotations


class LiveDataError(RuntimeError):
    pass


class DataOutageError(LiveDataError):
    """No bar arrived when one was expected (feed stalled or gapped)."""


class DataIntegrityError(LiveDataError):
    """A bar that cannot be trusted: wrong contract, or non-monotonic time."""
